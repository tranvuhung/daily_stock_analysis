# -*- coding: utf-8 -*-
"""
===================================
EfinanceFetcher - preferred data source (Priority 0)
===================================

Data source: East Money scraper (via the efinance library)
Features: free, no token required, comprehensive data, simple API
Repository: https://github.com/Micro-sheep/efinance

Similar to AkshareFetcher, but the efinance library:
1. Has a simpler, easier-to-use API
2. Supports batch data fetching
3. Wraps the endpoints more stably

Anti-ban strategy:
1. Random sleep of 1.5-3.0 seconds before each request
2. Random User-Agent rotation
3. Exponential backoff retries via tenacity
4. Circuit breaker: automatic cooldown after consecutive failures
"""

import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd
import requests  # Import requests to catch its exceptions
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

# Timeout (seconds) for efinance library calls that go through eastmoney APIs
# with no built-in timeout.  Prevents indefinite hangs when hosts are unreachable.
try:
    _EF_CALL_TIMEOUT = int(os.environ.get("EFINANCE_CALL_TIMEOUT", "30"))
except (ValueError, TypeError):
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "EFINANCE_CALL_TIMEOUT is not a valid integer, using default 30s"
    )
    _EF_CALL_TIMEOUT = 30

from src.patches.eastmoney_patch import eastmoney_patch
from src.config import get_config
from src.services.stock_list_parser import ParseStatus, parse_analysis_target
from .base import (
    BaseFetcher,
    DataFetchError,
    RateLimitError,
    STANDARD_COLUMNS,
    is_bse_code,
    is_st_stock,
    is_kc_cy_stock,
    normalize_stock_code,
    _is_hk_market,
    _is_etf_code as _is_a_share_etf_code,
)
from .realtime_types import (
    UnifiedRealtimeQuote, RealtimeSource,
    get_realtime_circuit_breaker,
    safe_float, safe_int  # Use the shared type conversion helpers
)


# Keep the old type alias for backward compatibility
@dataclass
class EfinanceRealtimeQuote:
    """
    Realtime quote data (from efinance) - backward-compatible alias

    New code should use UnifiedRealtimeQuote
    """
    code: str
    name: str = ""
    price: float = 0.0           # Latest price
    change_pct: float = 0.0      # Change percent (%)
    change_amount: float = 0.0   # Change amount
    
    # Volume/price indicators
    volume: int = 0              # Volume
    amount: float = 0.0          # Turnover amount
    turnover_rate: float = 0.0   # Turnover rate (%)
    amplitude: float = 0.0       # Amplitude (%)
    
    # Price range
    high: float = 0.0            # High price
    low: float = 0.0             # Low price
    open_price: float = 0.0      # Open price
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dict"""
        return {
            'code': self.code,
            'name': self.name,
            'price': self.price,
            'change_pct': self.change_pct,
            'change_amount': self.change_amount,
            'volume': self.volume,
            'amount': self.amount,
            'turnover_rate': self.turnover_rate,
            'amplitude': self.amplitude,
            'high': self.high,
            'low': self.low,
            'open': self.open_price,
        }


logger = logging.getLogger(__name__)

EASTMONEY_HISTORY_ENDPOINT = "push2his.eastmoney.com/api/qt/stock/kline/get"


# User-Agent pool for random rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


# Realtime quote cache (avoids repeated requests)
# TTL set to 10 minutes (600s): avoids repeated fetches during batch analysis
_realtime_cache: Dict[str, Any] = {
    'data': None,
    'timestamp': 0,
    'ttl': 600  # 10-minute cache TTL
}

# ETF realtime quote cache (kept separate from the stock cache)
_etf_realtime_cache: Dict[str, Any] = {
    'data': None,
    'timestamp': 0,
    'ttl': 600  # 10-minute cache TTL
}

_ETF_SH_PREFIXES = ('51', '52', '56', '58')
_ETF_SZ_PREFIXES = ('15', '16', '18')


def _is_etf_code(stock_code: str) -> bool:
    """
    Check whether a code is an ETF fund

    ETF code rules:
    - SSE ETFs: 51xxxx, 52xxxx, 56xxxx, 58xxxx
    - SZSE ETFs: 15xxxx, 16xxxx, 18xxxx

    Args:
        stock_code: Stock/fund code

    Returns:
        True if it is an ETF code, False if it is a regular stock code
    """
    return _is_a_share_etf_code(stock_code)


def _build_eastmoney_etf_secid(stock_code: str) -> str:
    """Build Eastmoney secid for A-share ETF historical K-line queries."""
    code = normalize_stock_code(stock_code)
    if not _is_etf_code(code):
        raise DataFetchError(f"无法识别 ETF 代码 {stock_code}")
    if code.startswith(_ETF_SH_PREFIXES):
        return f"1.{code}"
    if code.startswith(_ETF_SZ_PREFIXES):
        return f"0.{code}"
    raise DataFetchError(f"无法确定 ETF {stock_code} 的 Eastmoney 市场前缀")


def _is_us_code(stock_code: str) -> bool:
    """
    Check whether a code is a US stock

    US stock code rules:
    - 1-5 uppercase letters, e.g. 'AAPL', 'TSLA'
    - May contain '.', e.g. 'BRK.B'
    """
    code = stock_code.strip().upper()
    return bool(re.match(r'^[A-Z]{1,5}(\.[A-Z])?$', code))


def _ef_call_with_timeout(func, *args, timeout=None, **kwargs):
    """Run an efinance library call in a thread with a timeout.

    efinance internally uses requests/urllib3 with no timeout, so when
    eastmoney hosts are unreachable the call can hang for many minutes.
    This helper caps the *calling thread's* wait time.  Note: Python threads
    cannot be forcibly killed, so the worker thread may continue running in
    the background until the OS-level TCP timeout fires or the process exits.
    This is acceptable — the calling thread returns promptly on timeout.
    """
    if timeout is None:
        timeout = _EF_CALL_TIMEOUT
    # Do NOT use 'with ThreadPoolExecutor(...)' here: the context manager calls
    # shutdown(wait=True) on __exit__, which would re-block on the hung thread.
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(func, *args, **kwargs)
        return future.result(timeout=timeout)
    finally:
        # wait=False: calling thread returns immediately; worker cleans up later
        executor.shutdown(wait=False)


def _classify_eastmoney_error(exc: Exception) -> Tuple[str, str]:
    """
    Classify Eastmoney request failures into stable log categories.
    """
    message = str(exc).strip()
    lowered = message.lower()

    remote_disconnect_keywords = (
        'remotedisconnected',
        'remote end closed connection without response',
        'connection aborted',
        'connection broken',
        'protocolerror',
    )
    timeout_keywords = (
        'timeout',
        'timed out',
        'readtimeout',
        'connecttimeout',
    )
    rate_limit_keywords = (
        'banned',
        'blocked',
        '频率',
        'rate limit',
        'too many requests',
        '429',
        '限制',
        'forbidden',
        '403',
    )

    if any(keyword in lowered for keyword in remote_disconnect_keywords):
        return "remote_disconnect", message
    if isinstance(exc, (TimeoutError, requests.exceptions.Timeout)) or any(
        keyword in lowered for keyword in timeout_keywords
    ):
        return "timeout", message
    if any(keyword in lowered for keyword in rate_limit_keywords):
        return "rate_limit_or_anti_bot", message
    if isinstance(exc, requests.exceptions.RequestException):
        return "request_error", message
    return "unknown_request_error", message


class EfinanceFetcher(BaseFetcher):
    """
    Efinance data source implementation

    Priority: 0 (highest, ahead of AkshareFetcher)
    Data source: East Money (wrapped by the efinance library)
    Repository: https://github.com/Micro-sheep/efinance

    Main APIs:
    - ef.stock.get_quote_history(): fetch historical K-line data
    - ef.stock.get_base_info(): fetch basic stock info
    - ef.stock.get_realtime_quotes(): fetch realtime quotes

    Key strategies:
    - Random sleep of 1.5-3.0 seconds before each request
    - Random User-Agent rotation
    - Exponential backoff retries on failure (up to 3 times)
    """
    
    name = "EfinanceFetcher"
    priority = int(os.getenv("EFINANCE_PRIORITY", "0"))  # Highest priority, ahead of AkshareFetcher
    
    def __init__(self, sleep_min: float = 1.5, sleep_max: float = 3.0):
        """
        Initialize EfinanceFetcher

        Args:
            sleep_min: Minimum sleep time (seconds)
            sleep_max: Maximum sleep time (seconds)
        """
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self._last_request_time: Optional[float] = None
        # Only apply the patch when the East Money patch is enabled
        if get_config().enable_eastmoney_patch:
            eastmoney_patch()

    @staticmethod
    def _build_history_failure_message(
        stock_code: str,
        beg_date: str,
        end_date: str,
        exc: Exception,
        elapsed: float,
        is_etf: bool = False,
    ) -> Tuple[str, str]:
        category, detail = _classify_eastmoney_error(exc)
        instrument_type = "ETF" if is_etf else "stock"
        message = (
            "Eastmoney 历史K线接口失败: "
            f"endpoint={EASTMONEY_HISTORY_ENDPOINT}, stock_code={stock_code}, "
            f"market_type={instrument_type}, range={beg_date}~{end_date}, "
            f"category={category}, error_type={type(exc).__name__}, elapsed={elapsed:.2f}s, detail={detail}"
        )
        return category, message

    def _set_random_user_agent(self) -> None:
        """
        Set a random User-Agent

        Implemented by modifying the requests Session headers
        This is one of the key anti-scraping strategies
        """
        try:
            random_ua = random.choice(USER_AGENTS)
            logger.debug(f"设置 User-Agent: {random_ua[:50]}...")
        except Exception as e:
            logger.debug(f"设置 User-Agent 失败: {e}")
    
    def _enforce_rate_limit(self) -> None:
        """
        Enforce rate limiting

        Strategy:
        1. Check the interval since the last request
        2. If the interval is too short, sleep for the remainder
        3. Then perform a random jitter sleep
        """
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            min_interval = self.sleep_min
            if elapsed < min_interval:
                additional_sleep = min_interval - elapsed
                logger.debug(f"补充休眠 {additional_sleep:.2f} 秒")
                time.sleep(additional_sleep)
        
        # Perform random jitter sleep
        self.random_sleep(self.sleep_min, self.sleep_max)
        self._last_request_time = time.time()
    
    @retry(
        stop=stop_after_attempt(1),  # Reduced to 1 attempt to avoid triggering rate limits
        wait=wait_exponential(multiplier=1, min=4, max=60),  # Keep the wait time settings
        retry=retry_if_exception_type((
            ConnectionError,
            TimeoutError,
            requests.exceptions.RequestException,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch raw data from efinance

        Automatically picks the API based on the code type:
        - US stocks: not supported; raise so DataFetcherManager switches to another source
        - Regular stocks: use ef.stock.get_quote_history()
        - ETF funds: use ef.stock.get_quote_history() (ETFs are exchange-traded securities, so the stock K-line API is used)

        Flow:
        1. Determine the code type (US stock/stock/ETF)
        2. Set a random User-Agent
        3. Enforce rate limiting (random sleep)
        4. Call the corresponding efinance API
        5. Process the returned data
        """
        # US stocks not supported; raise so DataFetcherManager switches to AkshareFetcher/YfinanceFetcher
        if _is_us_code(stock_code):
            raise DataFetchError(f"EfinanceFetcher 不支持美股 {stock_code}，请使用 AkshareFetcher 或 YfinanceFetcher")

        # efinance's historical K-line API may return data for the wrong market for HK codes,
        # so skip explicitly and fall back to HK paths such as AkShare/Tushare/YFinance/Longbridge.
        if _is_hk_market(stock_code):
            raise DataFetchError(f"EfinanceFetcher 不支持港股日线 {stock_code}，请使用 AkshareFetcher 或其他港股数据源")
        
        # Choose the fetch method based on code type
        if _is_etf_code(stock_code):
            return self._fetch_etf_data(stock_code, start_date, end_date)
        else:
            return self._fetch_stock_data(stock_code, start_date, end_date)
    
    def _fetch_stock_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch historical data for regular A-shares

        Data source: ef.stock.get_quote_history()

        API parameters:
        - stock_codes: Stock code
        - beg: Start date, format 'YYYYMMDD'
        - end: End date, format 'YYYYMMDD'
        - klt: Period, 101 = daily
        - fqt: Price adjustment, 1 = forward-adjusted
        """
        import efinance as ef
        
        # Anti-ban strategy 1: random User-Agent
        self._set_random_user_agent()
        
        # Anti-ban strategy 2: enforced sleep
        self._enforce_rate_limit()
        
        # Format dates (efinance uses YYYYMMDD)
        beg_date = start_date.replace('-', '')
        end_date_fmt = end_date.replace('-', '')
        
        logger.info(f"[API调用] ef.stock.get_quote_history(stock_codes={stock_code}, "
                   f"beg={beg_date}, end={end_date_fmt}, klt=101, fqt=1)")
        
        api_start = time.time()
        try:
            # Call efinance to fetch A-share daily data
            # klt=101 fetches daily data
            # fqt=1 fetches forward-adjusted data
            df = _ef_call_with_timeout(
                ef.stock.get_quote_history,
                stock_codes=stock_code,
                beg=beg_date,
                end=end_date_fmt,
                klt=101,  # Daily
                fqt=1,    # Forward-adjusted
                timeout=60,
            )
            
            api_elapsed = time.time() - api_start
            
            # Log a summary of the returned data
            if df is not None and not df.empty:
                logger.info(
                    "[API返回] Eastmoney 历史K线成功: "
                    f"endpoint={EASTMONEY_HISTORY_ENDPOINT}, stock_code={stock_code}, "
                    f"range={beg_date}~{end_date_fmt}, rows={len(df)}, elapsed={api_elapsed:.2f}s"
                )
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                if '日期' in df.columns:
                    logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(
                    "[API返回] Eastmoney 历史K线为空: "
                    f"endpoint={EASTMONEY_HISTORY_ENDPOINT}, stock_code={stock_code}, "
                    f"range={beg_date}~{end_date_fmt}, elapsed={api_elapsed:.2f}s"
                )
            
            return df
            
        except Exception as e:
            api_elapsed = time.time() - api_start
            category, failure_message = self._build_history_failure_message(
                stock_code=stock_code,
                beg_date=beg_date,
                end_date=end_date_fmt,
                exc=e,
                elapsed=api_elapsed,
            )

            if category == "rate_limit_or_anti_bot":
                logger.warning(failure_message)
                raise RateLimitError(f"efinance 可能被限流: {failure_message}") from e

            logger.error(failure_message)
            raise DataFetchError(f"efinance 获取数据失败: {failure_message}") from e
    
    def _fetch_etf_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch ETF fund historical data

        Exchange-traded ETFs have OHLCV data just like regular stocks, so we use
        ef.stock.get_quote_history (the stock K-line API) which returns full
        open/high/low/close/volume data.

        Previously this method used ef.fund.get_quote_history which only returns
        NAV data (unit NAV / accumulated NAV) without volume or OHLC, causing:
        - Issue #541: 'got an unexpected keyword argument beg'
        - Issue #527: ETF volume/turnover always showing 0

        Args:
            stock_code: ETF code, e.g. '512400', '159883', '515120'
            start_date: Start date, format 'YYYY-MM-DD'
            end_date: End date, format 'YYYY-MM-DD'

        Returns:
            ETF historical OHLCV DataFrame
        """
        import efinance as ef

        # Anti-ban strategy 1: random User-Agent
        self._set_random_user_agent()

        # Anti-ban strategy 2: enforce rate limit
        self._enforce_rate_limit()

        # Format dates (efinance uses YYYYMMDD)
        beg_date = start_date.replace('-', '')
        end_date_fmt = end_date.replace('-', '')
        secid = _build_eastmoney_etf_secid(stock_code)

        logger.info(
            f"[API调用] ef.stock.get_quote_history(stock_codes={secid}, "
            f"beg={beg_date}, end={end_date_fmt}, klt=101, fqt=1, "
            f"quote_id_mode=True, use_id_cache=False)  [ETF stock_code={stock_code}]"
        )

        api_start = time.time()
        try:
            # ETFs are exchange-traded securities; use the stock API to get full OHLCV data
            df = _ef_call_with_timeout(
                ef.stock.get_quote_history,
                stock_codes=secid,
                beg=beg_date,
                end=end_date_fmt,
                klt=101,  # daily
                fqt=1,    # forward-adjusted
                quote_id_mode=True,
                use_id_cache=False,
                timeout=60,
            )

            api_elapsed = time.time() - api_start

            if df is not None and not df.empty:
                logger.info(
                    "[API返回] Eastmoney 历史K线成功 [ETF]: "
                    f"endpoint={EASTMONEY_HISTORY_ENDPOINT}, stock_code={stock_code}, secid={secid}, "
                    f"range={beg_date}~{end_date_fmt}, rows={len(df)}, elapsed={api_elapsed:.2f}s"
                )
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                if '日期' in df.columns:
                    logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(
                    "[API返回] Eastmoney 历史K线为空 [ETF]: "
                    f"endpoint={EASTMONEY_HISTORY_ENDPOINT}, stock_code={stock_code}, secid={secid}, "
                    f"range={beg_date}~{end_date_fmt}, elapsed={api_elapsed:.2f}s"
                )

            return df

        except Exception as e:
            api_elapsed = time.time() - api_start
            category, failure_message = self._build_history_failure_message(
                stock_code=stock_code,
                beg_date=beg_date,
                end_date=end_date_fmt,
                exc=e,
                elapsed=api_elapsed,
                is_etf=True,
            )

            if category == "rate_limit_or_anti_bot":
                logger.warning(failure_message)
                raise RateLimitError(f"efinance 可能被限流: {failure_message}") from e

            logger.error(failure_message)
            raise DataFetchError(f"efinance 获取 ETF 数据失败: {failure_message}") from e
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        Normalize efinance data

        Column names returned by efinance (in Chinese):
        stock name, stock code, date, open, close, high, low, volume, amount, amplitude, change percent, change amount, turnover rate

        Must be mapped to the standard column names:
        date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()
        
        # Column mapping (efinance Chinese column names -> standard English column names)
        column_mapping = {
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
            '股票代码': 'code',
            '股票名称': 'name',
        }
        
        # Rename columns
        df = df.rename(columns=column_mapping)
        
        # Fallback: if OHLC columns are missing (e.g. very old data path), fill from close
        if 'close' in df.columns and 'open' not in df.columns:
            df['open'] = df['close']
            df['high'] = df['close']
            df['low'] = df['close']
            
        # Fill volume and amount if missing
        if 'volume' not in df.columns:
            df['volume'] = 0
        if 'amount' not in df.columns:
            df['amount'] = 0

        
        # Add the code column manually if missing
        if 'code' not in df.columns:
            df['code'] = stock_code
        
        # Keep only the required columns
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]
        
        return df
    
    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch realtime quote data

        Data source: ef.stock.get_realtime_quotes()
        ETF data source: ef.stock.get_realtime_quotes(['ETF'])
        Registered indices: East Money single-security secid API (get_index_realtime_quote)

        Args:
            stock_code: Stock code

        Returns:
            UnifiedRealtimeQuote object, or None on failure
        """
        # Registered indices use the East Money single-security secid API (Story 1.5)
        target = parse_analysis_target(stock_code)
        if target.asset_type == ParseStatus.INDEX:
            return self.get_index_realtime_quote(target.canonical_id)

        # ETFs need a separate request to the ETF realtime quote API
        if _is_etf_code(stock_code):
            return self._get_etf_realtime_quote(stock_code)

        import efinance as ef
        circuit_breaker = get_realtime_circuit_breaker()
        source_key = "efinance"
        
        # Check circuit breaker state
        if not circuit_breaker.is_available(source_key):
            logger.info(f"[熔断] 数据源 {source_key} 处于熔断状态，跳过")
            return None
        
        try:
            # Check cache
            current_time = time.time()
            if (_realtime_cache['data'] is not None and 
                current_time - _realtime_cache['timestamp'] < _realtime_cache['ttl']):
                df = _realtime_cache['data']
                cache_age = int(current_time - _realtime_cache['timestamp'])
                logger.debug(f"[缓存命中] 实时行情(efinance) - 缓存年龄 {cache_age}s/{_realtime_cache['ttl']}s")
            else:
                # Trigger a full refresh
                logger.info(f"[缓存未命中] 触发全量刷新 实时行情(efinance)")
                # Anti-ban strategy
                self._set_random_user_agent()
                self._enforce_rate_limit()
                
                logger.info(f"[API调用] ef.stock.get_realtime_quotes() 获取实时行情...")
                import time as _time
                api_start = _time.time()
                
                # efinance realtime quote API (with timeout to avoid indefinite hangs)
                df = _ef_call_with_timeout(ef.stock.get_realtime_quotes)
                
                api_elapsed = _time.time() - api_start
                logger.info(f"[API返回] ef.stock.get_realtime_quotes 成功: 返回 {len(df)} 只股票, 耗时 {api_elapsed:.2f}s")
                circuit_breaker.record_success(source_key)
                
                # Update cache
                _realtime_cache['data'] = df
                _realtime_cache['timestamp'] = current_time
                logger.info(f"[缓存更新] 实时行情(efinance) 缓存已刷新，TTL={_realtime_cache['ttl']}s")
            
            # Find the requested stock
            # efinance may return the column as the Chinese "stock code" name or 'code'
            code_col = '股票代码' if '股票代码' in df.columns else 'code'
            row = df[df[code_col] == stock_code]
            if row.empty:
                logger.info(f"[API返回] 未找到股票 {stock_code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # Use the shared conversion helpers from realtime_types.py
            # Get column names (may be Chinese or English)
            name_col = '股票名称' if '股票名称' in df.columns else 'name'
            price_col = '最新价' if '最新价' in df.columns else 'price'
            pct_col = '涨跌幅' if '涨跌幅' in df.columns else 'pct_chg'
            chg_col = '涨跌额' if '涨跌额' in df.columns else 'change'
            vol_col = '成交量' if '成交量' in df.columns else 'volume'
            amt_col = '成交额' if '成交额' in df.columns else 'amount'
            turn_col = '换手率' if '换手率' in df.columns else 'turnover_rate'
            amp_col = '振幅' if '振幅' in df.columns else 'amplitude'
            high_col = '最高' if '最高' in df.columns else 'high'
            low_col = '最低' if '最低' in df.columns else 'low'
            open_col = '开盘' if '开盘' in df.columns else 'open'
            # efinance also returns fields such as volume ratio, P/E ratio and market cap
            vol_ratio_col = '量比' if '量比' in df.columns else 'volume_ratio'
            pe_col = '市盈率' if '市盈率' in df.columns else 'pe_ratio'
            total_mv_col = '总市值' if '总市值' in df.columns else 'total_mv'
            circ_mv_col = '流通市值' if '流通市值' in df.columns else 'circ_mv'
            
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=str(row.get(name_col, '')),
                source=RealtimeSource.EFINANCE,
                price=safe_float(row.get(price_col)),
                change_pct=safe_float(row.get(pct_col)),
                change_amount=safe_float(row.get(chg_col)),
                volume=safe_int(row.get(vol_col)),
                amount=safe_float(row.get(amt_col)),
                turnover_rate=safe_float(row.get(turn_col)),
                amplitude=safe_float(row.get(amp_col)),
                high=safe_float(row.get(high_col)),
                low=safe_float(row.get(low_col)),
                open_price=safe_float(row.get(open_col)),
                volume_ratio=safe_float(row.get(vol_ratio_col)),  # Volume ratio
                pe_ratio=safe_float(row.get(pe_col)),  # P/E ratio
                total_mv=safe_float(row.get(total_mv_col)),  # Total market cap
                circ_mv=safe_float(row.get(circ_mv_col)),  # Float market cap
            )
            
            logger.info(f"[实时行情-efinance] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"量比={quote.volume_ratio}, 换手率={quote.turnover_rate}%")
            return quote
            
        except FuturesTimeoutError:
            logger.info(f"[超时] ef.stock.get_realtime_quotes() 超过 {_EF_CALL_TIMEOUT}s，跳过 {stock_code}")
            circuit_breaker.record_failure(source_key, "timeout")
            return None
        except Exception as e:
            logger.info(f"[API错误] 获取 {stock_code} 实时行情(efinance)失败: {e}")
            circuit_breaker.record_failure(source_key, str(e))
            return None

    def get_index_realtime_quote(
        self, stock_code: str
    ) -> Optional[UnifiedRealtimeQuote]:
        """Fetch a registered CN index quote via the Eastmoney single-stock secid API.

        Supports SH (``1.{code}``), SZ (``0.{code}``) and CSI (``2.{code}``)
        secid forms. This is the only realtime source for CSI indices and the
        fallback for SH/SZ indices when Tencent/Sina fail (Story 1.5).
        """
        target = parse_analysis_target(stock_code)
        if target.asset_type != ParseStatus.INDEX or target.matched_index is None:
            return None
        entry = target.matched_index
        exchange = entry.exchange.upper()
        if exchange == "SH":
            secid = f"1.{entry.bare_code}"
        elif exchange == "SZ":
            secid = f"0.{entry.bare_code}"
        elif exchange == "CSI":
            secid = f"2.{entry.bare_code}"
        else:
            return None

        circuit_breaker = get_realtime_circuit_breaker()
        source_key = "efinance_index"
        if not circuit_breaker.is_available(source_key):
            logger.info(f"[熔断] 数据源 {source_key} 处于熔断状态，跳过")
            return None

        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()
            response = requests.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "secid": secid,
                    "fltt": "2",
                    "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f168,f169,f170,f171",
                },
                timeout=10,
            )
            if response.status_code != 200:
                circuit_breaker.record_failure(source_key, f"HTTP {response.status_code}")
                return None
            payload = response.json()
            data = payload.get("data") or {}
            price = safe_float(data.get("f43"))
            if price is None or price <= 0:
                circuit_breaker.record_failure(source_key, "empty quote payload")
                return None
            circuit_breaker.record_success(source_key)
            quote = UnifiedRealtimeQuote(
                code=target.canonical_id,
                name=str(data.get("f58") or entry.display_name or ""),
                source=RealtimeSource.EFINANCE,
                price=price,
                change_pct=safe_float(data.get("f170")),
                change_amount=safe_float(data.get("f169")),
                volume=safe_int(data.get("f47")),
                amount=safe_float(data.get("f48")),
                amplitude=safe_float(data.get("f171")),
                high=safe_float(data.get("f44")),
                low=safe_float(data.get("f45")),
                open_price=safe_float(data.get("f46")),
                pre_close=safe_float(data.get("f60")),
                volume_ratio=safe_float(data.get("f168")),
            )
            logger.info(
                "[实时行情-东财指数] %s %s: 价格=%s, 涨跌=%s%%, secid=%s",
                target.canonical_id,
                quote.name,
                quote.price,
                quote.change_pct,
                secid,
            )
            return quote
        except Exception as e:
            logger.info(f"[API错误] 获取 {target.canonical_id} 指数实时行情(东财)失败: {e}")
            circuit_breaker.record_failure(source_key, str(e))
            return None

    def _get_etf_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch ETF realtime quotes

        efinance's default realtime API only returns stocks; ETFs require passing ['ETF'] explicitly.
        """
        import efinance as ef
        circuit_breaker = get_realtime_circuit_breaker()
        source_key = "efinance_etf"

        if not circuit_breaker.is_available(source_key):
            logger.info(f"[熔断] 数据源 {source_key} 处于熔断状态，跳过")
            return None

        try:
            current_time = time.time()
            if (
                _etf_realtime_cache['data'] is not None and
                current_time - _etf_realtime_cache['timestamp'] < _etf_realtime_cache['ttl']
            ):
                df = _etf_realtime_cache['data']
                cache_age = int(current_time - _etf_realtime_cache['timestamp'])
                logger.debug(f"[缓存命中] ETF实时行情(efinance) - 缓存年龄 {cache_age}s/{_etf_realtime_cache['ttl']}s")
            else:
                self._set_random_user_agent()
                self._enforce_rate_limit()

                logger.info("[API调用] ef.stock.get_realtime_quotes(['ETF']) 获取ETF实时行情...")
                import time as _time
                api_start = _time.time()
                df = _ef_call_with_timeout(ef.stock.get_realtime_quotes, ['ETF'])
                api_elapsed = _time.time() - api_start

                if df is not None and not df.empty:
                    logger.info(f"[API返回] ETF 实时行情成功: {len(df)} 条, 耗时 {api_elapsed:.2f}s")
                    circuit_breaker.record_success(source_key)
                else:
                    logger.info(f"[API返回] ETF 实时行情为空, 耗时 {api_elapsed:.2f}s")
                    df = pd.DataFrame()

                _etf_realtime_cache['data'] = df
                _etf_realtime_cache['timestamp'] = current_time

            if df is None or df.empty:
                logger.info(f"[实时行情] ETF实时行情数据为空(efinance)，跳过 {stock_code}")
                return None

            code_col = '股票代码' if '股票代码' in df.columns else 'code'
            code_series = df[code_col].astype(str).str.zfill(6)
            target_code = str(stock_code).strip().zfill(6)
            row = df[code_series == target_code]
            if row.empty:
                logger.info(f"[API返回] 未找到 ETF {stock_code} 的实时行情(efinance)")
                return None

            row = row.iloc[0]
            name_col = '股票名称' if '股票名称' in df.columns else 'name'
            price_col = '最新价' if '最新价' in df.columns else 'price'
            pct_col = '涨跌幅' if '涨跌幅' in df.columns else 'pct_chg'
            chg_col = '涨跌额' if '涨跌额' in df.columns else 'change'
            vol_col = '成交量' if '成交量' in df.columns else 'volume'
            amt_col = '成交额' if '成交额' in df.columns else 'amount'
            turn_col = '换手率' if '换手率' in df.columns else 'turnover_rate'
            amp_col = '振幅' if '振幅' in df.columns else 'amplitude'
            high_col = '最高' if '最高' in df.columns else 'high'
            low_col = '最低' if '最低' in df.columns else 'low'
            open_col = '开盘' if '开盘' in df.columns else 'open'

            quote = UnifiedRealtimeQuote(
                code=target_code,
                name=str(row.get(name_col, '')),
                source=RealtimeSource.EFINANCE,
                price=safe_float(row.get(price_col)),
                change_pct=safe_float(row.get(pct_col)),
                change_amount=safe_float(row.get(chg_col)),
                volume=safe_int(row.get(vol_col)),
                amount=safe_float(row.get(amt_col)),
                turnover_rate=safe_float(row.get(turn_col)),
                amplitude=safe_float(row.get(amp_col)),
                high=safe_float(row.get(high_col)),
                low=safe_float(row.get(low_col)),
                open_price=safe_float(row.get(open_col)),
            )

            logger.info(
                f"[ETF实时行情-efinance] {target_code} {quote.name}: "
                f"价格={quote.price}, 涨跌={quote.change_pct}%, 换手率={quote.turnover_rate}%"
            )
            return quote
        except Exception as e:
            logger.info(f"[API错误] 获取 ETF {stock_code} 实时行情(efinance)失败: {e}")
            circuit_breaker.record_failure(source_key, str(e))
            return None

    def get_main_indices(self, region: str = "cn") -> Optional[List[Dict[str, Any]]]:
        """
        Fetch realtime quotes for major indices (efinance), A-shares only
        """
        if region != "cn":
            return None
        import efinance as ef

        indices_map = {
            '000001': ('上证指数', 'sh000001'),
            '399001': ('深证成指', 'sz399001'),
            '399006': ('创业板指', 'sz399006'),
            '000688': ('科创50', 'sh000688'),
            '000016': ('上证50', 'sh000016'),
            '000300': ('沪深300', 'sh000300'),
        }

        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            logger.info("[API调用] ef.stock.get_realtime_quotes(['沪深系列指数']) 获取指数行情...")
            import time as _time
            api_start = _time.time()
            df = _ef_call_with_timeout(ef.stock.get_realtime_quotes, ['沪深系列指数'])
            api_elapsed = _time.time() - api_start

            if df is None or df.empty:
                logger.warning(f"[API返回] 指数行情为空, 耗时 {api_elapsed:.2f}s")
                return None

            logger.info(f"[API返回] 指数行情成功: {len(df)} 条, 耗时 {api_elapsed:.2f}s")
            code_col = '股票代码' if '股票代码' in df.columns else 'code'
            code_series = df[code_col].astype(str).str.zfill(6)

            results: List[Dict[str, Any]] = []
            for code, (name, full_code) in indices_map.items():
                row = df[code_series == code]
                if row.empty:
                    continue
                item = row.iloc[0]

                price_col = '最新价' if '最新价' in df.columns else 'price'
                pct_col = '涨跌幅' if '涨跌幅' in df.columns else 'pct_chg'
                chg_col = '涨跌额' if '涨跌额' in df.columns else 'change'
                open_cols = [column for column in ('今开', '开盘', 'open') if column in df.columns]
                high_col = '最高' if '最高' in df.columns else 'high'
                low_col = '最低' if '最低' in df.columns else 'low'
                vol_col = '成交量' if '成交量' in df.columns else 'volume'
                amt_col = '成交额' if '成交额' in df.columns else 'amount'
                amp_col = '振幅' if '振幅' in df.columns else 'amplitude'

                current = safe_float(item.get(price_col, 0))
                change_amount = safe_float(item.get(chg_col, 0))
                open_price = 0.0
                for column in open_cols:
                    candidate = safe_float(item.get(column), default=None)
                    if candidate not in (None, 0.0):
                        open_price = candidate
                        break
                if open_price == 0.0 and open_cols:
                    open_price = safe_float(item.get(open_cols[0], 0), 0)

                results.append({
                    'code': full_code,
                    'name': name,
                    'current': current,
                    'change': change_amount,
                    'change_pct': safe_float(item.get(pct_col, 0)),
                    'open': open_price,
                    'high': safe_float(item.get(high_col, 0)),
                    'low': safe_float(item.get(low_col, 0)),
                    'prev_close': current - change_amount if current or change_amount else 0,
                    'volume': safe_float(item.get(vol_col, 0)),
                    'amount': safe_float(item.get(amt_col, 0)),
                    'amplitude': safe_float(item.get(amp_col, 0)),
                })

            if results:
                logger.info(f"[efinance] 获取到 {len(results)} 个指数行情")
            return results if results else None
        except Exception as e:
            logger.error(f"[efinance] 获取指数行情失败: {e}")
            return None

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        """
        Fetch market advance/decline statistics (efinance)
        """
        import efinance as ef

        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            current_time = time.time()
            if (
                _realtime_cache['data'] is not None and
                current_time - _realtime_cache['timestamp'] < _realtime_cache['ttl']
            ):
                df = _realtime_cache['data']
                logger.info(
                    "[MarketStats] component=market_stats provider=EfinanceFetcher "
                    "api=ef.stock.get_realtime_quotes action=cache_hit cache_age=%.0fs",
                    current_time - _realtime_cache['timestamp'],
                )
            else:
                started_at = time.monotonic()
                logger.info(
                    "[MarketStats] component=market_stats provider=EfinanceFetcher "
                    "api=ef.stock.get_realtime_quotes action=request_start"
                )
                df = _ef_call_with_timeout(ef.stock.get_realtime_quotes)
                elapsed = time.monotonic() - started_at
                logger.info(
                    "[MarketStats] component=market_stats provider=EfinanceFetcher "
                    "api=ef.stock.get_realtime_quotes action=request_complete elapsed=%.2fs",
                    elapsed,
                )
                _realtime_cache['data'] = df
                _realtime_cache['timestamp'] = current_time

            if df is None or df.empty:
                logger.warning(
                    "[MarketStats] component=market_stats provider=EfinanceFetcher "
                    "api=ef.stock.get_realtime_quotes action=parse status=empty"
                )
                return None

            return self._calc_market_stats(df)
        except Exception as e:
            logger.error(
                "[MarketStats] component=market_stats provider=EfinanceFetcher "
                "api=ef.stock.get_realtime_quotes action=failed error=%s",
                e,
            )
            return None
        
    def _calc_market_stats(
        self,
        df: pd.DataFrame,
        ) -> Optional[Dict[str, Any]]:
        """Compute advance/decline statistics from a quote DataFrame."""
        import numpy as np

        df = df.copy()
        
        # 1. Extract base comparison data: latest price, previous close
        # Handle column names from different APIs: sina/em efinance tushare xtdata
        code_col = next((c for c in ['代码', '股票代码', 'ts_code','stock_code'] if c in df.columns), None)
        name_col = next((c for c in ['名称', '股票名称','name','name'] if c in df.columns), None)
        close_col = next((c for c in ['最新价', '最新价', 'close','lastPrice'] if c in df.columns), None)
        pre_close_col = next((c for c in ['昨收', '昨日收盘', 'pre_close','lastClose'] if c in df.columns), None)
        amount_col = next((c for c in ['成交额', '成交额', 'amount','amount'] if c in df.columns), None) 
        
        limit_up_count = 0
        limit_down_count = 0
        up_count = 0
        down_count = 0
        flat_count = 0

        for code, name, current_price, pre_close, amount in zip(
            df[code_col], df[name_col], df[close_col], df[pre_close_col], df[amount_col]
        ):
            
            # Suspension filter: efinance suspended rows sometimes lack a price and show '-', em shows None
            if pd.isna(current_price) or pd.isna(pre_close) or current_price in ['-'] or pre_close in ['-'] or amount == 0:
                continue
            
            # em and efinance return str, which must be converted to float
            current_price = float(current_price)
            pre_close = float(pre_close)
            
            # Get the numeric code with the prefix stripped
            pure_code = normalize_stock_code(str(code)) 

            # A. Determine each stock's price limit ratio (based on the numeric code)
            if is_bse_code(pure_code): 
                ratio = 0.30
            elif is_kc_cy_stock(pure_code): #pure_code.startswith(('688', '30')):
                ratio = 0.20
            elif is_st_stock(name): #'ST' in str_name:
                ratio = 0.05
            else:
                ratio = 0.10

            # B. Compute limit-up/limit-down prices strictly per A-share rules: prev close * (1 ± ratio) -> rounded to 2 decimals
            limit_up_price = np.floor(pre_close * (1 + ratio) * 100 + 0.5) / 100.0
            limit_down_price = np.floor(pre_close * (1 - ratio) * 100 + 0.5) / 100.0

            limit_up_price_Tolerance = round(abs(pre_close * (1 + ratio) - limit_up_price), 10)
            limit_down_price_Tolerance = round(abs(pre_close * (1 - ratio) - limit_down_price), 10)

            # C. Exact comparison
            if current_price > 0 :
                is_limit_up = (current_price > 0) and (abs(current_price - limit_up_price) <= limit_up_price_Tolerance)
                is_limit_down = (current_price > 0) and (abs(current_price - limit_down_price) <= limit_down_price_Tolerance)

                if is_limit_up:
                    limit_up_count += 1
                if is_limit_down:
                    limit_down_count += 1

                if current_price > pre_close:
                    up_count += 1
                elif current_price < pre_close:
                    down_count += 1
                else:
                    flat_count += 1
                
        # Count totals
        stats = {
            'up_count': up_count,
            'down_count': down_count,
            'flat_count': flat_count,
            'limit_up_count': limit_up_count,
            'limit_down_count': limit_down_count,
            'total_amount': 0.0,
        }
        
        # Turnover amount statistics
        if amount_col and amount_col in df.columns:
            df[amount_col] = pd.to_numeric(df[amount_col], errors='coerce')
            stats['total_amount'] = (df[amount_col].sum() / 1e8)
            
        return stats

    def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        """
        Fetch sector gainers/losers ranking (efinance)
        """
        import efinance as ef

        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            logger.info("[API调用] ef.stock.get_realtime_quotes(['行业板块']) 获取板块行情...")
            df = _ef_call_with_timeout(ef.stock.get_realtime_quotes, ['行业板块'])
            if df is None or df.empty:
                logger.warning("[efinance] 板块行情数据为空")
                return None

            change_col = '涨跌幅' if '涨跌幅' in df.columns else 'pct_chg'
            name_col = '股票名称' if '股票名称' in df.columns else 'name'
            if change_col not in df.columns or name_col not in df.columns:
                return None

            df[change_col] = pd.to_numeric(df[change_col], errors='coerce')
            df = df.dropna(subset=[change_col])
            top = df.nlargest(n, change_col)
            bottom = df.nsmallest(n, change_col)

            top_sectors = [
                {'name': str(row[name_col]), 'change_pct': float(row[change_col])}
                for _, row in top.iterrows()
            ]
            bottom_sectors = [
                {'name': str(row[name_col]), 'change_pct': float(row[change_col])}
                for _, row in bottom.iterrows()
            ]
            return top_sectors, bottom_sectors
        except Exception as e:
            logger.error(f"[efinance] 获取板块排行失败: {e}")
            return None
    
    def get_base_info(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        Fetch basic stock info

        Data source: ef.stock.get_base_info()
        Includes: P/E, P/B, industry, total market cap, float market cap, ROE, net margin, etc.

        Args:
            stock_code: Stock code

        Returns:
            Dict of basic info, or None on failure
        """
        import efinance as ef
        
        try:
            # Anti-ban strategy
            self._set_random_user_agent()
            self._enforce_rate_limit()
            
            logger.info(f"[API调用] ef.stock.get_base_info(stock_codes={stock_code}) 获取基本信息...")
            import time as _time
            api_start = _time.time()
            
            info = _ef_call_with_timeout(ef.stock.get_base_info, stock_code)
            
            api_elapsed = _time.time() - api_start
            logger.info(f"[API返回] ef.stock.get_base_info 成功, 耗时 {api_elapsed:.2f}s")
            
            if info is None:
                logger.warning(f"[API返回] 未获取到 {stock_code} 的基本信息")
                return None
            
            # Convert to dict
            if isinstance(info, pd.Series):
                return info.to_dict()
            elif isinstance(info, pd.DataFrame):
                if not info.empty:
                    return info.iloc[0].to_dict()
            
            return None
            
        except Exception as e:
            logger.error(f"[API错误] 获取 {stock_code} 基本信息失败: {e}")
            return None
    
    def get_belong_board(self, stock_code: str) -> Optional[pd.DataFrame]:
        """
        Fetch the sectors a stock belongs to

        Data source: ef.stock.get_belong_board()

        Args:
            stock_code: Stock code

        Returns:
            DataFrame of sectors, or None on failure
        """
        import efinance as ef
        
        try:
            # Anti-ban strategy
            self._set_random_user_agent()
            self._enforce_rate_limit()
            
            logger.info(f"[API调用] ef.stock.get_belong_board(stock_code={stock_code}) 获取所属板块...")
            import time as _time
            api_start = _time.time()
            
            df = _ef_call_with_timeout(ef.stock.get_belong_board, stock_code)
            
            api_elapsed = _time.time() - api_start
            
            if df is not None and not df.empty:
                logger.info(f"[API返回] ef.stock.get_belong_board 成功: 返回 {len(df)} 个板块, 耗时 {api_elapsed:.2f}s")
                return df
            else:
                logger.warning(f"[API返回] 未获取到 {stock_code} 的板块信息")
                return None
            
        except FuturesTimeoutError:
            logger.warning(f"[超时] ef.stock.get_belong_board({stock_code}) 超过 {_EF_CALL_TIMEOUT}s，跳过")
            return None
        except Exception as e:
            logger.error(f"[API错误] 获取 {stock_code} 所属板块失败: {e}")
            return None
    
    def get_enhanced_data(self, stock_code: str, days: int = 60) -> Dict[str, Any]:
        """
        Fetch enhanced data (historical K-line + realtime quote + basic info)

        Args:
            stock_code: Stock code
            days: Number of days of historical data

        Returns:
            Dict containing all data
        """
        result = {
            'code': stock_code,
            'daily_data': None,
            'realtime_quote': None,
            'base_info': None,
            'belong_board': None,
        }
        
        # Fetch daily data
        try:
            df = self.get_daily_data(stock_code, days=days)
            result['daily_data'] = df
        except Exception as e:
            logger.error(f"获取 {stock_code} 日线数据失败: {e}")
        
        # Fetch realtime quote
        result['realtime_quote'] = self.get_realtime_quote(stock_code)
        
        # Fetch basic info
        result['base_info'] = self.get_base_info(stock_code)
        
        # Fetch sectors
        result['belong_board'] = self.get_belong_board(stock_code)
        
        return result


if __name__ == "__main__":
    # Test code
    logging.basicConfig(level=logging.DEBUG)
    
    fetcher = EfinanceFetcher()
    
    # Test a regular stock
    print("=" * 50)
    print("测试普通股票数据获取 (efinance)")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('600519')  # Kweichow Moutai
        print(f"[股票] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[股票] 获取失败: {e}")
    
    # Test an ETF fund
    print("\n" + "=" * 50)
    print("测试 ETF 基金数据获取 (efinance)")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('512400')  # Non-ferrous metals leaders ETF
        print(f"[ETF] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[ETF] 获取失败: {e}")
    
    # Test realtime quote
    print("\n" + "=" * 50)
    print("测试实时行情获取 (efinance)")
    print("=" * 50)
    try:
        quote = fetcher.get_realtime_quote('600519')
        if quote:
            print(f"[实时行情] {quote.name}: 价格={quote.price}, 涨跌幅={quote.change_pct}%")
        else:
            print("[实时行情] 未获取到数据")
    except Exception as e:
        print(f"[实时行情] 获取失败: {e}")
    
    # Test basic info
    print("\n" + "=" * 50)
    print("测试基本信息获取 (efinance)")
    print("=" * 50)
    try:
        info = fetcher.get_base_info('600519')
        if info:
            print(f"[基本信息] 市盈率={info.get('市盈率(动)', 'N/A')}, 市净率={info.get('市净率', 'N/A')}")
        else:
            print("[基本信息] 未获取到数据")
    except Exception as e:
        print(f"[基本信息] 获取失败: {e}")

    # Test market statistics
    print("\n" + "=" * 50)
    print("Testing get_market_stats (efinance)")
    print("=" * 50)
    try:
        stats = fetcher.get_market_stats()
        if stats:
            print(f"Market Stats successfully computed:")
            print(f"Up: {stats['up_count']} (Limit Up: {stats['limit_up_count']})")
            print(f"Down: {stats['down_count']} (Limit Down: {stats['limit_down_count']})")
            print(f"Flat: {stats['flat_count']}")
            print(f"Total Amount: {stats['total_amount']:.2f} 亿 (Yi)")
        else:
            print("Failed to compute market stats.")
    except Exception as e:
        print(f"Failed to compute market stats: {e}")
