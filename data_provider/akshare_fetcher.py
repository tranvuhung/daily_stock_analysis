# -*- coding: utf-8 -*-
"""
===================================
AkshareFetcher - primary data source (Priority 1)
===================================

Data sources:
1. Eastmoney scraper (via the akshare library) - default source
2. Sina Finance API - fallback source
3. Tencent Finance API - fallback source

Pros: free, no token required, comprehensive data
Risk: scrapers are easily blocked by anti-scraping measures

Anti-blocking strategy:
1. Random sleep of 2-5 seconds before each request
2. Random User-Agent rotation
3. Exponential backoff retries via tenacity
4. Circuit breaker: automatic cooldown after consecutive failures

Enhanced data:
- Realtime quotes: volume ratio, turnover rate, PE, PB, total market cap, float market cap
- Chip distribution: profit ratio, average cost, chip concentration
"""

import logging
import multiprocessing
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd
import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from src.patches.eastmoney_patch import eastmoney_patch
from src.config import get_config
from src.services.stock_list_parser import ParseStatus, parse_analysis_target
from .base import BaseFetcher, DataFetchError, RateLimitError, STANDARD_COLUMNS, is_bse_code, is_st_stock, is_kc_cy_stock, normalize_stock_code
from .realtime_types import (
    UnifiedRealtimeQuote, ChipDistribution, RealtimeSource,
    get_realtime_circuit_breaker, get_chip_circuit_breaker,
    safe_float, safe_int  # Use the shared type-conversion helpers
)
from .us_index_mapping import is_us_index_code, is_us_stock_code


# Keep the old RealtimeQuote alias for backward compatibility
RealtimeQuote = UnifiedRealtimeQuote


logger = logging.getLogger(__name__)

SINA_REALTIME_ENDPOINT = "hq.sinajs.cn/list"
TENCENT_REALTIME_ENDPOINT = "qt.gtimg.cn/q"
_AKSHARE_HISTORY_CALL_TIMEOUT = 30.0
_AKSHARE_TIMEOUT_PROCESS_JOIN_GRACE = 1.0
_AKSHARE_TIMEOUT_PROCESS_START_METHOD = "spawn"


# User-Agent pool for random rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


# Realtime quote cache (avoids repeated requests)
# TTL set to 20 minutes (1200 seconds):
# - Batch analysis: ~30 stocks usually finish within 5 minutes, so 20 minutes is enough
# - Freshness: stock analysis does not need second-level data; a 20-minute delay is acceptable
# - Anti-blocking: reduces API call frequency
_realtime_cache: Dict[str, Any] = {
    'data': None,
    'timestamp': 0,
    'ttl': 1200  # 20-minute cache TTL
}

# ETF realtime quote cache
_etf_realtime_cache: Dict[str, Any] = {
    'data': None,
    'timestamp': 0,
    'ttl': 1200  # 20-minute cache TTL
}

# HK realtime quote cache. stock_hk_spot_em() returns the whole market, so each call is expensive.
_hk_realtime_cache: Dict[str, Any] = {
    'data': None,
    'timestamp': 0,
    'ttl': 1200,  # 20-minute cache TTL
    'failure_ttl': 30,
    'last_result': None,
}
_hk_realtime_cache_lock = threading.Lock()


def _is_etf_code(stock_code: str) -> bool:
    """
    Check whether a code is an ETF fund.

    ETF code rules:
    - SSE ETFs: 51xxxx, 52xxxx, 56xxxx, 58xxxx
    - SZSE ETFs: 15xxxx, 16xxxx, 18xxxx

    Args:
        stock_code: stock/fund code

    Returns:
        True if it is an ETF code, False if it is a regular stock code
    """
    etf_prefixes = ('51', '52', '56', '58', '15', '16', '18')
    code = stock_code.strip().split('.')[0]
    return code.startswith(etf_prefixes) and len(code) == 6


def _is_hk_code(stock_code: str) -> bool:
    """
    Check whether a code is an HK stock.

    HK stock code rules:
    - 4-5 digit numeric codes, e.g. '00700' (Tencent), '0001' (CK Hutchison), '0941' (China Mobile)
    - Some HK codes may carry a prefix, e.g. 'hk00700', 'hk1810'

    Kept consistent with ``data_provider.base._is_hk_market`` and
    ``YfinanceFetcher._convert_stock_code`` / ``LongbridgeFetcher._is_hk_code``
    on the digit range (4-5) for bare HK codes, so that a code routed as HK by
    ``DataFetcherManager`` is not rejected inside ``AkshareFetcher``, breaking the call chain.

    Args:
        stock_code: stock code

    Returns:
        True if it is an HK stock code, False otherwise
    """
    # Strip a possible 'hk' prefix and check that the rest is numeric
    code = stock_code.strip().lower()
    if code.endswith('.hk'):
        numeric_part = code[:-3]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    if code.startswith('hk'):
        # Anything with an hk prefix is an HK stock; after stripping it should be 1-5 digits
        numeric_part = code[2:]
        return numeric_part.isdigit() and 1 <= len(numeric_part) <= 5
    # Without a prefix, only 4-5 digit numbers count as HK (A-share / BSE codes are all 6 digits, no conflict)
    return code.isdigit() and 4 <= len(code) <= 5


def _normalize_tencent_volume(fields: List[str]) -> Optional[int]:
    """
    Normalize Tencent realtime volume to shares.

    Tencent's public description of field 6 does not fully match what it actually
    returns. Prefer cross-checking with turnover rate, price and float market cap,
    choosing whichever of the raw value or the legacy "lots to shares" result is
    closer. If cross-checking is not possible, keep the legacy "lots to shares"
    fallback so traditional Tencent payloads do not regress to 1/100 of the volume.
    """
    if len(fields) <= 6 or not fields[6]:
        return None

    raw_volume = safe_int(fields[6])
    if raw_volume is None:
        return None

    price = safe_float(fields[3]) if len(fields) > 3 else None
    turnover_rate = safe_float(fields[38]) if len(fields) > 38 else None
    circ_mv_yi = safe_float(fields[44]) if len(fields) > 44 and fields[44] else None
    circ_mv = circ_mv_yi * 100000000 if circ_mv_yi is not None else None

    if price and price > 0 and turnover_rate and turnover_rate > 0 and circ_mv and circ_mv > 0:
        expected_volume = (circ_mv / price) * (turnover_rate / 100)
        if expected_volume > 0:
            raw_delta = abs(raw_volume - expected_volume)
            hand_to_share_volume = raw_volume * 100
            hand_delta = abs(hand_to_share_volume - expected_volume)
            return raw_volume if raw_delta <= hand_delta else hand_to_share_volume

    return raw_volume * 100


def _parse_tencent_amount(fields: List[str]) -> Optional[float]:
    """
    Parse Tencent realtime turnover amount, in CNY.

    In observed payloads, field 35 holds a more precise "price/volume/amount"
    triple. Field 37 is the legacy fallback field in units of 10k CNY.
    """
    if len(fields) > 35 and fields[35]:
        parts = fields[35].split("/")
        if len(parts) >= 3:
            precise_amount = safe_float(parts[2])
            if precise_amount is not None:
                return precise_amount

    amount_wan = safe_float(fields[37]) if len(fields) > 37 and fields[37] else None
    return amount_wan * 10000 if amount_wan is not None else None


def is_hk_stock_code(stock_code: str) -> bool:
    """
    Public API: determine if a stock code is a Hong Kong stock.

    Delegates to _is_hk_code for internal compatibility.

    Args:
        stock_code: Stock code (e.g. '00700', 'hk00700')

    Returns:
        True if HK stock, False otherwise
    """
    return _is_hk_code(stock_code)


def _is_us_code(stock_code: str) -> bool:
    """
    Check whether a code is a US stock (excluding US indices).

    Delegates to is_us_stock_code() in the us_index_mapping module.

    Args:
        stock_code: stock code

    Returns:
        True if it is a US stock code, False otherwise

    Examples:
        >>> _is_us_code('AAPL')
        True
        >>> _is_us_code('TSLA')
        True
        >>> _is_us_code('SPX')
        False
        >>> _is_us_code('600519')
        False
    """
    return is_us_stock_code(stock_code)


def _to_sina_tx_symbol(stock_code: str) -> str:
    """Convert 6-digit A-share code to sh/sz/bj prefixed symbol for Sina/Tencent APIs.

    Explicit sh/sz/bj prefixes are preserved (``sh000016`` -> ``sh000016``) so
    registered index codes keep their index identity instead of degrading into
    the colliding stock symbol (Story 1.5).
    """
    raw = (stock_code or "").strip()
    lower = raw.lower()
    for prefix in ("sh", "sz", "bj"):
        if lower.startswith(prefix) and len(raw) > len(prefix):
            candidate = raw[len(prefix):]
            if candidate.isdigit() and len(candidate) == 6:
                return f"{prefix}{candidate}"
    base = (raw.split(".")[0] if "." in raw else raw).strip()
    if is_bse_code(base):
        return f"bj{base}"
    # Shanghai: 60xxxx, 5xxxx (ETF), 90xxxx (B-shares)
    if base.startswith(("6", "5", "90")):
        return f"sh{base}"
    return f"sz{base}"


def _classify_realtime_http_error(exc: Exception) -> Tuple[str, str]:
    """
    Classify Sina/Tencent realtime quote failures into stable categories.
    """
    detail = str(exc).strip() or type(exc).__name__
    lowered = detail.lower()

    remote_disconnect_keywords = (
        "remotedisconnected",
        "remote end closed connection without response",
        "connection aborted",
        "connection broken",
        "protocolerror",
        "chunkedencodingerror",
    )
    timeout_keywords = (
        "timeout",
        "timed out",
        "readtimeout",
        "connecttimeout",
    )
    rate_limit_keywords = (
        "banned",
        "blocked",
        "频率",
        "rate limit",
        "too many requests",
        "429",
        "限制",
        "forbidden",
        "403",
    )

    if any(keyword in lowered for keyword in remote_disconnect_keywords):
        return "remote_disconnect", detail
    if isinstance(exc, (TimeoutError, requests.exceptions.Timeout)) or any(
        keyword in lowered for keyword in timeout_keywords
    ):
        return "timeout", detail
    if any(keyword in lowered for keyword in rate_limit_keywords):
        return "rate_limit_or_anti_bot", detail
    if isinstance(exc, requests.exceptions.RequestException):
        return "request_error", detail
    return "unknown_request_error", detail


def _build_realtime_failure_message(
    source_name: str,
    endpoint: str,
    stock_code: str,
    symbol: str,
    category: str,
    detail: str,
    elapsed: float,
    error_type: str,
) -> str:
    return (
        f"{source_name} 实时行情接口失败: endpoint={endpoint}, stock_code={stock_code}, "
        f"symbol={symbol}, category={category}, error_type={error_type}, "
        f"elapsed={elapsed:.2f}s, detail={detail}"
    )


def _akshare_call_with_timeout(
    func,
    *args,
    timeout: Optional[float] = None,
    call_name: str = "akshare",
    **kwargs,
):
    """Run an akshare call with a bounded wait time."""
    wait_seconds = _AKSHARE_HISTORY_CALL_TIMEOUT if timeout is None else float(timeout)

    multiprocessing.freeze_support()
    ctx = multiprocessing.get_context(_AKSHARE_TIMEOUT_PROCESS_START_METHOD)
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_akshare_timeout_worker,
        args=(child_conn, func, args, kwargs),
        name=f"akshare-{call_name}",
        daemon=True,
    )

    process.start()
    child_conn.close()

    try:
        if not parent_conn.poll(wait_seconds):
            _terminate_akshare_process(process)
            raise TimeoutError(f"{call_name} 调用超过 {wait_seconds:g}s，已放弃等待")

        try:
            ok, value = parent_conn.recv()
        except EOFError as exc:
            raise RuntimeError(f"{call_name} 调用进程未返回结果") from exc
    finally:
        parent_conn.close()
        process.join(_AKSHARE_TIMEOUT_PROCESS_JOIN_GRACE)
        _terminate_akshare_process(process)

    if ok:
        return value
    raise value


def _akshare_timeout_worker(conn, func, args, kwargs) -> None:
    try:
        conn.send((True, func(*args, **kwargs)))
    except BaseException as exc:
        try:
            conn.send((False, exc))
        except BaseException:
            try:
                conn.send((False, RuntimeError(f"{type(exc).__name__}: {exc}")))
            except BaseException:
                pass
    finally:
        conn.close()


def _terminate_akshare_process(process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(_AKSHARE_TIMEOUT_PROCESS_JOIN_GRACE)
    if process.is_alive():
        process.kill()
        process.join(_AKSHARE_TIMEOUT_PROCESS_JOIN_GRACE)


class AkshareFetcher(BaseFetcher):
    """
    Akshare data source implementation.

    Priority: 1 (highest)
    Data source: Eastmoney scraper

    Key strategies:
    - Random sleep of 2.0-5.0 seconds before each request
    - Random User-Agent rotation
    - Exponential backoff retries on failure (up to 3 attempts)
    """
    
    name = "AkshareFetcher"
    priority = int(os.getenv("AKSHARE_PRIORITY", "1"))
    
    def __init__(self, sleep_min: float = 2.0, sleep_max: float = 5.0):
        """
        Initialize AkshareFetcher.

        Args:
            sleep_min: minimum sleep time (seconds)
            sleep_max: maximum sleep time (seconds)
        """
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self._last_request_time: Optional[float] = None
        self._history_call_timeout = _AKSHARE_HISTORY_CALL_TIMEOUT
        # Only apply the Eastmoney patch when it is enabled
        if get_config().enable_eastmoney_patch:
            eastmoney_patch()
    
    def _set_random_user_agent(self) -> None:
        """
        Set a random User-Agent.

        Implemented by modifying the requests Session headers.
        This is one of the key anti-scraping strategies.
        """
        try:
            import akshare as ak
            # akshare uses requests internally; we influence it via env vars or direct settings
            # akshare may not expose its session directly, so fake_useragent is used as a supplement
            random_ua = random.choice(USER_AGENTS)
            logger.debug(f"设置 User-Agent: {random_ua[:50]}...")
        except Exception as e:
            logger.debug(f"设置 User-Agent 失败: {e}")
    
    def _enforce_rate_limit(self) -> None:
        """
        Enforce rate limiting.

        Strategy:
        1. Check the interval since the last request
        2. If the interval is too short, sleep to make up the difference
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
        stop=stop_after_attempt(3),  # Retry up to 3 times
        wait=wait_exponential(multiplier=1, min=2, max=30),  # Exponential backoff: 2, 4, 8... max 30 seconds
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch raw data from Akshare.

        Automatically selects the API by code type:
        - Registered A-share indices: ak.stock_zh_index_daily_em()
        - US stocks: unsupported; raise so YfinanceFetcher handles them (Issue #311)
        - HK stocks: ak.stock_hk_hist()
        - ETF funds: ak.fund_etf_hist_em()
        - Regular A-shares: ak.stock_zh_a_hist()

        Flow:
        1. Determine the code type (US/HK/ETF/A-share)
        2. Set a random User-Agent
        3. Apply rate limiting (random sleep)
        4. Call the matching akshare API
        5. Process the returned data
        """
        target = parse_analysis_target(stock_code)
        if target.asset_type == ParseStatus.INDEX:
            return self._fetch_index_data(target.canonical_id, start_date, end_date)

        # Choose the fetch method by code type
        if _is_us_code(stock_code):
            # US stocks: akshare's stock_us_daily has known price-adjustment issues (see Issue #311)
            # Delegate to YfinanceFetcher to keep adjusted prices consistent
            raise DataFetchError(
                f"AkshareFetcher 不支持美股 {stock_code}，请使用 YfinanceFetcher 获取正确的复权价格"
            )
        elif _is_hk_code(stock_code):
            return self._fetch_hk_data(stock_code, start_date, end_date)
        elif _is_etf_code(stock_code):
            return self._fetch_etf_data(stock_code, start_date, end_date)
        else:
            return self._fetch_stock_data(stock_code, start_date, end_date)

    def _fetch_index_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch a registry-recognized A-share index from Eastmoney."""
        import akshare as ak

        self._set_random_user_agent()
        self._enforce_rate_limit()
        logger.info(
            "[API调用] ak.stock_zh_index_daily_em(symbol=%s, start_date=%s, end_date=%s)",
            stock_code,
            start_date.replace("-", ""),
            end_date.replace("-", ""),
        )
        try:
            df = _akshare_call_with_timeout(
                ak.stock_zh_index_daily_em,
                timeout=self._history_call_timeout,
                call_name="ak.stock_zh_index_daily_em",
                symbol=stock_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
        except (ConnectionError, TimeoutError):
            raise
        except RateLimitError:
            raise
        except Exception as exc:
            error_msg = str(exc).lower()
            if any(
                keyword in error_msg
                for keyword in ("banned", "blocked", "频率", "rate", "限制")
            ):
                raise RateLimitError(f"Akshare 指数接口可能被限流: {exc}") from exc
            raise DataFetchError(f"Akshare 获取指数数据失败: {exc}") from exc

        if df is None:
            return pd.DataFrame()
        if not isinstance(df, pd.DataFrame):
            raise DataFetchError(
                f"Akshare 指数接口返回无效类型: {type(df).__name__}"
            )
        if df.empty:
            return df.copy()

        required_columns = {"date", "open", "high", "low", "close", "volume"}
        missing_columns = sorted(required_columns - set(df.columns))
        if missing_columns:
            raise DataFetchError(
                "Akshare 指数数据缺少必需列: " + ", ".join(missing_columns)
            )

        result = df.copy()
        parsed_dates = pd.to_datetime(result["date"], errors="coerce", format="mixed")
        if parsed_dates.isna().any():
            invalid_dates = result.loc[parsed_dates.isna(), "date"].astype(str).tolist()
            raise DataFetchError(
                "Akshare 指数数据包含无法解析的 date: "
                + ", ".join(invalid_dates[:3])
            )
        result["_index_sort_date"] = parsed_dates
        result = (
            result.sort_values("_index_sort_date", kind="stable")
            .drop(columns="_index_sort_date")
            .reset_index(drop=True)
        )
        if "pct_chg" not in result.columns:
            close = pd.to_numeric(result["close"], errors="coerce")
            pct_chg = close.pct_change(fill_method=None) * 100
            pct_chg = pct_chg.replace(
                [float("inf"), float("-inf")], float("nan")
            )
            if not pct_chg.empty and pd.isna(pct_chg.iloc[0]):
                pct_chg.iloc[0] = 0.0
            result["pct_chg"] = pct_chg
        if "amount" not in result.columns:
            result["amount"] = pd.NA
        return result
    
    def _fetch_stock_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch regular A-share historical data.

        Strategy:
        1. Try the Eastmoney API first (ak.stock_zh_a_hist)
        2. On failure, try the Sina Finance API (ak.stock_zh_a_daily)
        3. Finally, try the Tencent Finance API (ak.stock_zh_a_hist_tx)
        """
        # List of methods to try
        methods = [
            (self._fetch_stock_data_em, "东方财富"),
            (self._fetch_stock_data_sina, "新浪财经"),
            (self._fetch_stock_data_tx, "腾讯财经"),
        ]

        last_error = None

        for fetch_method, source_name in methods:
            try:
                logger.info(f"[数据源] 尝试使用 {source_name} 获取 {stock_code}...")
                df = fetch_method(stock_code, start_date, end_date)

                if df is not None and not df.empty:
                    logger.info(f"[数据源] {source_name} 获取成功")
                    return df
            except Exception as e:
                last_error = e
                logger.warning(f"[数据源] {source_name} 获取失败: {e}")
                # Continue with the next one

        # All failed
        raise DataFetchError(f"Akshare 所有渠道获取失败: {last_error}")

    def _fetch_stock_data_em(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch regular A-share historical data (Eastmoney).
        Data source: ak.stock_zh_a_hist()
        """
        import akshare as ak

        # Anti-blocking strategy 1: random User-Agent
        self._set_random_user_agent()

        # Anti-blocking strategy 2: enforced sleep
        self._enforce_rate_limit()

        logger.info(f"[API调用] ak.stock_zh_a_hist(symbol={stock_code}, ...)")

        try:
            import time as _time
            api_start = _time.time()

            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"
            )

            api_elapsed = _time.time() - api_start

            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.stock_zh_a_hist 成功: {len(df)} 行, 耗时 {api_elapsed:.2f}s")
                return df
            else:
                logger.warning(f"[API返回] ak.stock_zh_a_hist 返回空数据")
                return pd.DataFrame()

        except Exception as e:
            error_msg = str(e).lower()
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                raise RateLimitError(f"Akshare(EM) 可能被限流: {e}") from e
            raise e

    def _fetch_stock_data_sina(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch regular A-share historical data (Sina Finance).
        Data source: ak.stock_zh_a_daily()
        """
        import akshare as ak

        # Convert code format: sh600000, sz000001, bj920748
        symbol = _to_sina_tx_symbol(stock_code)

        self._enforce_rate_limit()

        try:
            df = _akshare_call_with_timeout(
                ak.stock_zh_a_daily,
                symbol=symbol,
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq",
                timeout=self._history_call_timeout,
                call_name="ak.stock_zh_a_daily",
            )

            # Normalize Sina column names
            # Sina returns: date, open, high, low, close, volume, amount, outstanding_share, turnover
            if df is not None and not df.empty:
                # Make sure the date column exists
                if 'date' in df.columns:
                    df = df.rename(columns={'date': '日期'})

                # Map the other columns to match what _normalize_data expects
                # _normalize_data expects the Chinese columns for date, open, close, high, low, volume, amount
                rename_map = {
                    'open': '开盘', 'high': '最高', 'low': '最低',
                    'close': '收盘', 'volume': '成交量', 'amount': '成交额'
                }
                df = df.rename(columns=rename_map)

                # Compute percent change (the Sina API may not return it)
                if '收盘' in df.columns:
                    df['涨跌幅'] = df['收盘'].pct_change() * 100
                    df['涨跌幅'] = df['涨跌幅'].fillna(0)

                return df
            return pd.DataFrame()

        except Exception as e:
            raise e

    def _fetch_stock_data_tx(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch regular A-share historical data (Tencent Finance).
        Data source: ak.stock_zh_a_hist_tx()
        """
        import akshare as ak

        # Convert code format: sh600000, sz000001, bj920748
        symbol = _to_sina_tx_symbol(stock_code)

        self._enforce_rate_limit()

        try:
            df = _akshare_call_with_timeout(
                ak.stock_zh_a_hist_tx,
                symbol=symbol,
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq",
                timeout=self._history_call_timeout,
                call_name="ak.stock_zh_a_hist_tx",
            )

            # Normalize Tencent column names
            # Tencent returns: date, open, close, high, low, volume, amount
            if df is not None and not df.empty:
                rename_map = {
                    'date': '日期', 'open': '开盘', 'high': '最高',
                    'low': '最低', 'close': '收盘', 'volume': '成交量',
                    'amount': '成交额'
                }
                df = df.rename(columns=rename_map)

                # Tencent data usually includes the percent-change column; compute it if missing
                if 'pct_chg' in df.columns:
                    df = df.rename(columns={'pct_chg': '涨跌幅'})
                elif '收盘' in df.columns:
                    df['涨跌幅'] = df['收盘'].pct_change() * 100
                    df['涨跌幅'] = df['涨跌幅'].fillna(0)

                return df
            return pd.DataFrame()

        except Exception as e:
            raise e
    
    def _fetch_etf_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch ETF fund historical data.

        Data source: ak.fund_etf_hist_em()

        Args:
            stock_code: ETF code, e.g. '512400', '159883'
            start_date: start date, format 'YYYY-MM-DD'
            end_date: end date, format 'YYYY-MM-DD'

        Returns:
            ETF historical data DataFrame
        """
        import akshare as ak
        
        # Anti-blocking strategy 1: random User-Agent
        self._set_random_user_agent()
        
        # Anti-blocking strategy 2: enforced sleep
        self._enforce_rate_limit()
        
        logger.info(f"[API调用] ak.fund_etf_hist_em(symbol={stock_code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            import time as _time
            api_start = _time.time()
            
            # Call akshare to fetch ETF daily data
            df = ak.fund_etf_hist_em(
                symbol=stock_code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # Forward-adjusted
            )
            
            api_elapsed = _time.time() - api_start
            
            # Log a summary of the returned data
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.fund_etf_hist_em 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.fund_etf_hist_em 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # Detect anti-scraping blocks
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取 ETF 数据失败: {e}") from e
    
    def _fetch_us_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch US stock historical data.

        Data source: ak.stock_us_daily() (Sina Finance API)

        Args:
            stock_code: US stock code, e.g. 'AMD', 'AAPL', 'TSLA'
            start_date: start date, format 'YYYY-MM-DD'
            end_date: end date, format 'YYYY-MM-DD'

        Returns:
            US stock historical data DataFrame
        """
        import akshare as ak
        
        # Anti-blocking strategy 1: random User-Agent
        self._set_random_user_agent()
        
        # Anti-blocking strategy 2: enforced sleep
        self._enforce_rate_limit()
        
        # Use US stock codes in uppercase as-is
        symbol = stock_code.strip().upper()
        
        logger.info(f"[API调用] ak.stock_us_daily(symbol={symbol}, adjust=qfq)")
        
        try:
            import time as _time
            api_start = _time.time()
            
            # Call akshare to fetch US stock daily data
            # stock_us_daily returns the full history; filter by date afterwards
            df = ak.stock_us_daily(
                symbol=symbol,
                adjust="qfq"  # Forward-adjusted
            )
            
            api_elapsed = _time.time() - api_start
            
            # Log a summary of the returned data
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.stock_us_daily 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                
                # Filter by date
                df['date'] = pd.to_datetime(df['date'])
                start_dt = pd.to_datetime(start_date)
                end_dt = pd.to_datetime(end_date)
                df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]
                
                if not df.empty:
                    logger.info(f"[API返回] 过滤后日期范围: {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}")
                    logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
                else:
                    logger.warning(f"[API返回] 过滤后数据为空，日期范围 {start_date} ~ {end_date} 无数据")
                
                # Rename columns to the Chinese format expected by _normalize_data
                # stock_us_daily returns: date, open, high, low, close, volume
                rename_map = {
                    'date': '日期',
                    'open': '开盘',
                    'high': '最高',
                    'low': '最低',
                    'close': '收盘',
                    'volume': '成交量',
                }
                df = df.rename(columns=rename_map)
                
                # Compute percent change (the US API does not return it)
                if '收盘' in df.columns:
                    df['涨跌幅'] = df['收盘'].pct_change() * 100
                    df['涨跌幅'] = df['涨跌幅'].fillna(0)
                
                # Estimate turnover amount (the US API does not return it)
                if '成交量' in df.columns and '收盘' in df.columns:
                    df['成交额'] = df['成交量'] * df['收盘']
                else:
                    df['成交额'] = 0
                
                return df
            else:
                logger.warning(f"[API返回] ak.stock_us_daily 返回空数据, 耗时 {api_elapsed:.2f}s")
                return pd.DataFrame()
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # Detect anti-scraping blocks
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取美股数据失败: {e}") from e

    def _fetch_hk_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch HK stock historical data.

        Data source: ak.stock_hk_hist()

        Args:
            stock_code: HK stock code, e.g. '00700', '01810'
            start_date: start date, format 'YYYY-MM-DD'
            end_date: end date, format 'YYYY-MM-DD'

        Returns:
            HK stock historical data DataFrame
        """
        import akshare as ak
        
        # Anti-blocking strategy 1: random User-Agent
        self._set_random_user_agent()
        
        # Anti-blocking strategy 2: enforced sleep
        self._enforce_rate_limit()
        
        # Ensure the code format is correct (5 digits)
        code = stock_code.lower().replace('hk', '').zfill(5)
        
        logger.info(f"[API调用] ak.stock_hk_hist(symbol={code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            import time as _time
            api_start = _time.time()
            
            # Call akshare to fetch HK stock daily data
            df = ak.stock_hk_hist(
                symbol=code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # Forward-adjusted
            )
            
            api_elapsed = _time.time() - api_start
            
            # Log a summary of the returned data
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.stock_hk_hist 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.stock_hk_hist 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # Detect anti-scraping blocks
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取港股数据失败: {e}") from e
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        Normalize Akshare data.

        Akshare returns Chinese column names for:
        date, open, close, high, low, volume, amount, amplitude, pct change, change amount, turnover rate

        These are mapped to the standard column names:
        date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()
        
        # Column mapping (Akshare Chinese column names -> standard English column names)
        column_mapping = {
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
        }
        
        # Rename columns
        df = df.rename(columns=column_mapping)

        # Add the stock code column
        df['code'] = stock_code
        
        # Keep only the required columns
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]
        
        return df

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        target = parse_analysis_target(stock_code)
        if target.asset_type != ParseStatus.INDEX or target.matched_index is None:
            return None

        import akshare as ak

        table_name = (
            "上证系列指数"
            if target.matched_index.exchange.upper() == "SH"
            else "深证系列指数"
        )
        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()
            df = _akshare_call_with_timeout(
                ak.stock_zh_index_spot_em,
                timeout=self._history_call_timeout,
                call_name="ak.stock_zh_index_spot_em",
                symbol=table_name,
            )
            if df is None or df.empty or not {"代码", "名称"}.issubset(df.columns):
                return None

            bare_code = target.matched_index.bare_code

            def normalize_index_code(value: Any) -> str:
                text = str(value).strip()
                if text.endswith(".0"):
                    text = text[:-2]
                return text.zfill(6) if text.isdigit() else text

            matches = df[df["代码"].map(normalize_index_code) == bare_code]
            if matches.empty:
                return None
            for name_value in matches["名称"]:
                if pd.isna(name_value):
                    continue
                name = str(name_value).strip()
                if name:
                    return name
            return None
        except Exception:
            logger.debug(
                "Akshare index name lookup failed for %s",
                target.canonical_id,
                exc_info=True,
            )
            return None
    
    def get_realtime_quote(self, stock_code: str, source: str = "em") -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch realtime quote data (multiple sources supported).

        Source priority (configurable):
        1. em: Eastmoney (akshare ak.stock_zh_a_spot_em) - most complete, incl. volume ratio/PE/PB/market cap
        2. sina: Sina Finance (akshare ak.stock_zh_a_spot) - lightweight, basic quotes
        3. tencent: Tencent direct API - single-stock query, low load

        Args:
            stock_code: stock/ETF code
            source: source type, one of "em", "sina", "tencent"

        Returns:
            UnifiedRealtimeQuote object, or None on failure
        """
        circuit_breaker = get_realtime_circuit_breaker()

        # Choose the fetch method by code type
        if _is_us_code(stock_code):
            # US stocks do not use Akshare; YfinanceFetcher handles them
            logger.debug(f"[API跳过] {stock_code} 是美股，Akshare 不支持美股实时行情")
            return None
        elif _is_hk_code(stock_code):
            return self._get_hk_realtime_quote(stock_code)
        elif _is_etf_code(stock_code):
            source_key = "akshare_etf"
            if not circuit_breaker.is_available(source_key):
                logger.info(f"[熔断] 数据源 {source_key} 处于熔断状态，跳过")
                return None
            return self._get_etf_realtime_quote(stock_code)
        else:
            source_key = f"akshare_{source}"
            if not circuit_breaker.is_available(source_key):
                logger.info(f"[熔断] 数据源 {source_key} 处于熔断状态，跳过")
                return None
            # Regular A-shares: pick the data source by `source`
            if source == "sina":
                return self._get_stock_realtime_quote_sina(stock_code)
            elif source == "tencent":
                return self._get_stock_realtime_quote_tencent(stock_code)
            else:
                return self._get_stock_realtime_quote_em(stock_code)
    
    def _get_stock_realtime_quote_em(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch regular A-share realtime quotes (Eastmoney source).

        Data source: ak.stock_zh_a_spot_em()
        Pros: most complete data, incl. volume ratio, turnover rate, PE, PB, total and float market cap
        Cons: full-market pull, large payload, prone to timeouts/rate limiting
        """
        import akshare as ak
        circuit_breaker = get_realtime_circuit_breaker()
        source_key = "akshare_em"
        
        try:
            # Check the cache
            current_time = time.time()
            if (_realtime_cache['data'] is not None and 
                current_time - _realtime_cache['timestamp'] < _realtime_cache['ttl']):
                df = _realtime_cache['data']
                cache_age = int(current_time - _realtime_cache['timestamp'])
                logger.debug(f"[缓存命中] A股实时行情(东财) - 缓存年龄 {cache_age}s/{_realtime_cache['ttl']}s")
            else:
                # Trigger a full refresh
                logger.info(f"[缓存未命中] 触发全量刷新 A股实时行情(东财)")
                last_error: Optional[Exception] = None
                df = None
                for attempt in range(1, 3):
                    try:
                        # Anti-blocking strategy
                        self._set_random_user_agent()
                        self._enforce_rate_limit()

                        logger.info(f"[API调用] ak.stock_zh_a_spot_em() 获取A股实时行情... (attempt {attempt}/2)")
                        import time as _time
                        api_start = _time.time()

                        df = ak.stock_zh_a_spot_em()

                        api_elapsed = _time.time() - api_start
                        logger.info(f"[API返回] ak.stock_zh_a_spot_em 成功: 返回 {len(df)} 只股票, 耗时 {api_elapsed:.2f}s")
                        circuit_breaker.record_success(source_key)
                        break
                    except Exception as e:
                        last_error = e
                        logger.info(f"[API错误] ak.stock_zh_a_spot_em 获取失败 (attempt {attempt}/2): {e}")
                        time.sleep(min(2 ** attempt, 5))

                # Update cache: cache data on success; cache empty data on failure too, so one run does not hammer the same API
                if df is None:
                    logger.info(f"[API错误] ak.stock_zh_a_spot_em 最终失败: {last_error}")
                    circuit_breaker.record_failure(source_key, str(last_error))
                    df = pd.DataFrame()
                _realtime_cache['data'] = df
                _realtime_cache['timestamp'] = current_time
                logger.info(f"[缓存更新] A股实时行情(东财) 缓存已刷新，TTL={_realtime_cache['ttl']}s")

            if df is None or df.empty:
                logger.info(f"[实时行情] A股实时行情数据为空，跳过 {stock_code}")
                return None
            
            # Find the requested stock
            row = df[df['代码'] == stock_code]
            if row.empty:
                logger.info(f"[API返回] 未找到股票 {stock_code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # Use the shared conversion helpers from realtime_types.py
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                source=RealtimeSource.AKSHARE_EM,
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume=safe_int(row.get('成交量')),
                amount=safe_float(row.get('成交额')),
                volume_ratio=safe_float(row.get('量比')),
                turnover_rate=safe_float(row.get('换手率')),
                amplitude=safe_float(row.get('振幅')),
                open_price=safe_float(row.get('今开')),
                high=safe_float(row.get('最高')),
                low=safe_float(row.get('最低')),
                pe_ratio=safe_float(row.get('市盈率-动态')),
                pb_ratio=safe_float(row.get('市净率')),
                total_mv=safe_float(row.get('总市值')),
                circ_mv=safe_float(row.get('流通市值')),
                change_60d=safe_float(row.get('60日涨跌幅')),
                high_52w=safe_float(row.get('52周最高')),
                low_52w=safe_float(row.get('52周最低')),
            )
            
            logger.info(f"[实时行情-东财] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"量比={quote.volume_ratio}, 换手率={quote.turnover_rate}%")
            return quote
            
        except Exception as e:
            logger.info(f"[API错误] 获取 {stock_code} 实时行情(东财)失败: {e}")
            circuit_breaker.record_failure(source_key, str(e))
            return None
    
    def _get_stock_realtime_quote_sina(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch regular A-share realtime quotes (Sina Finance source).

        Data source: Sina Finance API (direct, single-stock query)
        Pros: single-stock query, low load, fast
        Cons: fewer fields, no volume ratio/PE/PB etc.

        Endpoint format: http://hq.sinajs.cn/list=sh600519,sz000001
        """
        circuit_breaker = get_realtime_circuit_breaker()
        source_key = "akshare_sina"
        symbol = _to_sina_tx_symbol(stock_code)
        url = f"http://{SINA_REALTIME_ENDPOINT}={symbol}"
        api_start = time.time()
        
        try:
            headers = {
                'Referer': 'http://finance.sina.com.cn',
                'User-Agent': random.choice(USER_AGENTS)
            }
            
            logger.info(
                f"[API调用] 新浪财经接口获取 {stock_code} 实时行情: endpoint={SINA_REALTIME_ENDPOINT}, symbol={symbol}"
            )
            
            self._enforce_rate_limit()
            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'gbk'
            api_elapsed = time.time() - api_start
            
            if response.status_code != 200:
                failure_message = _build_realtime_failure_message(
                    source_name="新浪",
                    endpoint=SINA_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="http_status",
                    detail=f"HTTP {response.status_code}",
                    elapsed=api_elapsed,
                    error_type="HTTPStatus",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            # Parse data: var hq_str_sh600519="<name>,1866.000,1870.000,..."
            content = response.text.strip()
            if '=""' in content or not content:
                failure_message = _build_realtime_failure_message(
                    source_name="新浪",
                    endpoint=SINA_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="empty_response",
                    detail="empty quote payload",
                    elapsed=api_elapsed,
                    error_type="EmptyResponse",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            # Extract the data inside the quotes
            data_start = content.find('"')
            data_end = content.rfind('"')
            if data_start == -1 or data_end == -1:
                failure_message = _build_realtime_failure_message(
                    source_name="新浪",
                    endpoint=SINA_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="malformed_payload",
                    detail="quote payload missing quotes",
                    elapsed=api_elapsed,
                    error_type="MalformedPayload",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            data_str = content[data_start+1:data_end]
            fields = data_str.split(',')
            
            if len(fields) < 32:
                failure_message = _build_realtime_failure_message(
                    source_name="新浪",
                    endpoint=SINA_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="insufficient_fields",
                    detail=f"field_count={len(fields)}",
                    elapsed=api_elapsed,
                    error_type="InsufficientFields",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            circuit_breaker.record_success(source_key)
            
            # Sina field order:
            # 0:name 1:open 2:prev close 3:last price 4:high 5:low 6:bid1 7:ask1
            # 8:volume (shares) 9:amount (CNY) ... 30:date 31:time
            # Use the shared conversion helpers from realtime_types.py
            price = safe_float(fields[3])
            pre_close = safe_float(fields[2])
            change_pct = None
            change_amount = None
            if price and pre_close and pre_close > 0:
                change_amount = price - pre_close
                change_pct = (change_amount / pre_close) * 100
            
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=fields[0],
                source=RealtimeSource.AKSHARE_SINA,
                price=price,
                change_pct=change_pct,
                change_amount=change_amount,
                volume=safe_int(fields[8]),  # Volume (shares)
                amount=safe_float(fields[9]),  # Amount (CNY)
                open_price=safe_float(fields[1]),
                high=safe_float(fields[4]),
                low=safe_float(fields[5]),
                pre_close=pre_close,
            )
            
            logger.info(
                f"[实时行情-新浪] {stock_code} {quote.name}: endpoint={SINA_REALTIME_ENDPOINT}, "
                f"价格={quote.price}, 涨跌={quote.change_pct}, 成交量={quote.volume}, elapsed={api_elapsed:.2f}s"
            )
            return quote
            
        except Exception as e:
            api_elapsed = time.time() - api_start
            category, detail = _classify_realtime_http_error(e)
            failure_message = _build_realtime_failure_message(
                source_name="新浪",
                endpoint=SINA_REALTIME_ENDPOINT,
                stock_code=stock_code,
                symbol=symbol,
                category=category,
                detail=detail,
                elapsed=api_elapsed,
                error_type=type(e).__name__,
            )
            logger.info(failure_message)
            circuit_breaker.record_failure(source_key, failure_message)
            return None
    
    def _get_stock_realtime_quote_tencent(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch regular A-share realtime quotes (Tencent Finance source).

        Data source: Tencent Finance API (direct, single-stock query)
        Pros: single-stock query, low load, includes turnover rate
        Cons: no valuation data such as volume ratio/PE/PB

        Endpoint format: http://qt.gtimg.cn/q=sh600519,sz000001
        """
        circuit_breaker = get_realtime_circuit_breaker()
        source_key = "akshare_tencent"
        symbol = _to_sina_tx_symbol(stock_code)
        url = f"http://{TENCENT_REALTIME_ENDPOINT}={symbol}"
        api_start = time.time()
        
        try:
            headers = {
                'Referer': 'http://finance.qq.com',
                'User-Agent': random.choice(USER_AGENTS)
            }
            
            logger.info(
                f"[API调用] 腾讯财经接口获取 {stock_code} 实时行情: endpoint={TENCENT_REALTIME_ENDPOINT}, symbol={symbol}"
            )
            
            self._enforce_rate_limit()
            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'gbk'
            api_elapsed = time.time() - api_start
            
            if response.status_code != 200:
                failure_message = _build_realtime_failure_message(
                    source_name="腾讯",
                    endpoint=TENCENT_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="http_status",
                    detail=f"HTTP {response.status_code}",
                    elapsed=api_elapsed,
                    error_type="HTTPStatus",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            content = response.text.strip()
            if '=""' in content or not content:
                failure_message = _build_realtime_failure_message(
                    source_name="腾讯",
                    endpoint=TENCENT_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="empty_response",
                    detail="empty quote payload",
                    elapsed=api_elapsed,
                    error_type="EmptyResponse",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            # Extract data
            data_start = content.find('"')
            data_end = content.rfind('"')
            if data_start == -1 or data_end == -1:
                failure_message = _build_realtime_failure_message(
                    source_name="腾讯",
                    endpoint=TENCENT_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="malformed_payload",
                    detail="quote payload missing quotes",
                    elapsed=api_elapsed,
                    error_type="MalformedPayload",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            data_str = content[data_start+1:data_end]
            fields = data_str.split('~')

            if len(fields) < 45:
                failure_message = _build_realtime_failure_message(
                    source_name="腾讯",
                    endpoint=TENCENT_REALTIME_ENDPOINT,
                    stock_code=stock_code,
                    symbol=symbol,
                    category="insufficient_fields",
                    detail=f"field_count={len(fields)}",
                    elapsed=api_elapsed,
                    error_type="InsufficientFields",
                )
                logger.info(failure_message)
                circuit_breaker.record_failure(source_key, failure_message)
                return None
            
            circuit_breaker.record_success(source_key)
            
            # Tencent field order (full):
            # 1:name 2:code 3:last price 4:prev close 5:open 6:volume 7:outer volume 8:inner volume
            # 9-28:5-level bid/ask 30:timestamp 31:change 32:pct change(%) 33:high 34:low 35:close/volume/amount
            # 36:volume (unit varies by payload) 37:amount (10k CNY) 38:turnover rate(%) 39:PE 43:amplitude(%)
            # 44:float market cap (100M CNY) 45:total market cap (100M CNY) 46:PB 47:limit-up price 48:limit-down price 49:volume ratio
            # Use the shared conversion helpers from realtime_types.py
            amount = _parse_tencent_amount(fields)
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=fields[1] if len(fields) > 1 else "",
                source=RealtimeSource.TENCENT,
                price=safe_float(fields[3]),
                change_pct=safe_float(fields[32]),
                change_amount=safe_float(fields[31]) if len(fields) > 31 else None,
                volume=_normalize_tencent_volume(fields),
                amount=amount,
                open_price=safe_float(fields[5]),
                high=safe_float(fields[33]) if len(fields) > 33 else None,  # Fix: field 33 is the high price
                low=safe_float(fields[34]) if len(fields) > 34 else None,  # Fix: field 34 is the low price
                pre_close=safe_float(fields[4]),
                turnover_rate=safe_float(fields[38]) if len(fields) > 38 else None,
                amplitude=safe_float(fields[43]) if len(fields) > 43 else None,
                volume_ratio=safe_float(fields[49]) if len(fields) > 49 else None,  # Volume ratio
                pe_ratio=safe_float(fields[39]) if len(fields) > 39 else None,  # PE ratio
                pb_ratio=safe_float(fields[46]) if len(fields) > 46 else None,  # PB ratio
                circ_mv=safe_float(fields[44]) * 100000000 if len(fields) > 44 and fields[44] else None,  # Float market cap (100M CNY -> CNY)
                total_mv=safe_float(fields[45]) * 100000000 if len(fields) > 45 and fields[45] else None,  # Total market cap (100M CNY -> CNY)
            )
            
            logger.info(
                f"[实时行情-腾讯] {stock_code} {quote.name}: endpoint={TENCENT_REALTIME_ENDPOINT}, "
                f"价格={quote.price}, 涨跌={quote.change_pct}%, 量比={quote.volume_ratio}, "
                f"换手率={quote.turnover_rate}%, elapsed={api_elapsed:.2f}s"
            )
            return quote
            
        except Exception as e:
            api_elapsed = time.time() - api_start
            category, detail = _classify_realtime_http_error(e)
            failure_message = _build_realtime_failure_message(
                source_name="腾讯",
                endpoint=TENCENT_REALTIME_ENDPOINT,
                stock_code=stock_code,
                symbol=symbol,
                category=category,
                detail=detail,
                elapsed=api_elapsed,
                error_type=type(e).__name__,
            )
            logger.info(failure_message)
            circuit_breaker.record_failure(source_key, failure_message)
            return None
    
    def _get_etf_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch ETF fund realtime quotes.

        Data source: ak.fund_etf_spot_em()
        Includes: last price, pct change, volume, amount, turnover rate, etc.

        Args:
            stock_code: ETF code

        Returns:
            UnifiedRealtimeQuote object, or None on failure
        """
        import akshare as ak
        circuit_breaker = get_realtime_circuit_breaker()
        source_key = "akshare_etf"
        
        try:
            # Check the cache
            current_time = time.time()
            if (_etf_realtime_cache['data'] is not None and 
                current_time - _etf_realtime_cache['timestamp'] < _etf_realtime_cache['ttl']):
                df = _etf_realtime_cache['data']
                logger.debug(f"[缓存命中] 使用缓存的ETF实时行情数据")
            else:
                last_error: Optional[Exception] = None
                df = None
                for attempt in range(1, 3):
                    try:
                        # Anti-blocking strategy
                        self._set_random_user_agent()
                        self._enforce_rate_limit()

                        logger.info(f"[API调用] ak.fund_etf_spot_em() 获取ETF实时行情... (attempt {attempt}/2)")
                        import time as _time
                        api_start = _time.time()

                        df = ak.fund_etf_spot_em()

                        api_elapsed = _time.time() - api_start
                        logger.info(f"[API返回] ak.fund_etf_spot_em 成功: 返回 {len(df)} 只ETF, 耗时 {api_elapsed:.2f}s")
                        circuit_breaker.record_success(source_key)
                        break
                    except Exception as e:
                        last_error = e
                        logger.info(f"[API错误] ak.fund_etf_spot_em 获取失败 (attempt {attempt}/2): {e}")
                        time.sleep(min(2 ** attempt, 5))

                if df is None:
                    logger.info(f"[API错误] ak.fund_etf_spot_em 最终失败: {last_error}")
                    circuit_breaker.record_failure(source_key, str(last_error))
                    df = pd.DataFrame()
                _etf_realtime_cache['data'] = df
                _etf_realtime_cache['timestamp'] = current_time

            if df is None or df.empty:
                logger.info(f"[实时行情] ETF实时行情数据为空，跳过 {stock_code}")
                return None
            
            # Find the requested ETF
            row = df[df['代码'] == stock_code]
            if row.empty:
                logger.info(f"[API返回] 未找到 ETF {stock_code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # Use the shared conversion helpers from realtime_types.py
            # Build the ETF quote
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                source=RealtimeSource.AKSHARE_EM,
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume=safe_int(row.get('成交量')),
                amount=safe_float(row.get('成交额')),
                volume_ratio=safe_float(row.get('量比')),
                turnover_rate=safe_float(row.get('换手率')),
                amplitude=safe_float(row.get('振幅')),
                open_price=safe_float(row.get('开盘价')),
                high=safe_float(row.get('最高价')),
                low=safe_float(row.get('最低价')),
                total_mv=safe_float(row.get('总市值')),
                circ_mv=safe_float(row.get('流通市值')),
                high_52w=safe_float(row.get('52周最高')),
                low_52w=safe_float(row.get('52周最低')),
            )
            
            logger.info(f"[ETF实时行情] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"换手率={quote.turnover_rate}%")
            return quote
            
        except Exception as e:
            logger.info(f"[API错误] 获取 ETF {stock_code} 实时行情失败: {e}")
            circuit_breaker.record_failure(source_key, str(e))
            return None
    
    def _get_hk_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Fetch HK stock realtime quotes.

        Primary source: ak.stock_hk_spot_em() (Eastmoney)
        Fallback source: ak.stock_hk_spot() (Sina)
        Includes: last price, pct change, volume, amount, etc.

        Args:
            stock_code: HK stock code

        Returns:
            UnifiedRealtimeQuote object, or None on failure
        """
        import akshare as ak
        circuit_breaker = get_realtime_circuit_breaker()
        em_key = "akshare_hk_em"
        sina_key = "akshare_hk_sina"

        # Ensure the code format is correct (5 digits)
        raw_code = stock_code.strip().lower()
        if raw_code.endswith('.hk'):
            raw_code = raw_code[:-3]
        if raw_code.startswith('hk'):
            raw_code = raw_code[2:]
        code = raw_code.zfill(5)

        def build_em_quote(df: pd.DataFrame) -> Optional[UnifiedRealtimeQuote]:
            if df.empty:
                logger.info(f"[API返回] 港股实时行情数据为空 (stock_hk_spot_em)")
                return None

            row = df[df['代码'] == code]
            if row.empty:
                logger.info(f"[API返回] 未找到港股 {code} 的实时行情 (stock_hk_spot_em)")
                return None

            row = row.iloc[0]
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                source=RealtimeSource.AKSHARE_EM,
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume=safe_int(row.get('成交量')),
                amount=safe_float(row.get('成交额')),
                volume_ratio=safe_float(row.get('量比')),
                turnover_rate=safe_float(row.get('换手率')),
                amplitude=safe_float(row.get('振幅')),
                pe_ratio=safe_float(row.get('市盈率')),
                pb_ratio=safe_float(row.get('市净率')),
                total_mv=safe_float(row.get('总市值')),
                circ_mv=safe_float(row.get('流通市值')),
                high_52w=safe_float(row.get('52周最高')),
                low_52w=safe_float(row.get('52周最低')),
            )
            logger.info(
                f"[港股实时行情] {stock_code} {quote.name}: 价格={quote.price}, "
                f"涨跌={quote.change_pct}%, 换手率={quote.turnover_rate}%"
            )
            return quote

        def read_cached_em_quote() -> Tuple[bool, Optional[UnifiedRealtimeQuote]]:
            current_time = time.time()
            cache_data = _hk_realtime_cache['data']
            cache_age = current_time - _hk_realtime_cache['timestamp']
            if (
                _hk_realtime_cache.get('last_result') == 'failure'
                and cache_age < _hk_realtime_cache.get('failure_ttl', 0)
            ):
                logger.debug(
                    f"[缓存命中] 港股实时行情(东财) - 复用最近失败结果 "
                    f"{int(cache_age)}s/{_hk_realtime_cache.get('failure_ttl', 0)}s"
                )
                return True, None

            if cache_data is None or cache_age >= _hk_realtime_cache['ttl']:
                return False, None

            logger.debug(
                f"[缓存命中] 港股实时行情(东财) - 缓存年龄 "
                f"{int(cache_age)}s/{_hk_realtime_cache['ttl']}s"
            )
            try:
                return True, build_em_quote(cache_data)
            except Exception as e:
                logger.warning(
                    f"[缓存错误] 港股实时行情缓存无法解析: {e}，"
                    "尝试 stock_hk_spot 备用接口"
                )
                return True, None

        cache_hit, quote = read_cached_em_quote()
        if quote is not None:
            return quote

        # --- Primary source: Eastmoney ---
        if not cache_hit:
            # Portfolio snapshots query several symbols concurrently; the double check inside the lock ensures one cold fetch per process.
            with _hk_realtime_cache_lock:
                cache_hit, quote = read_cached_em_quote()
                if quote is not None:
                    return quote

                if circuit_breaker.is_available(em_key) and not cache_hit:
                    try:
                        # Apply anti-blocking only right before a real network request; hot cache hits do not wait.
                        self._set_random_user_agent()
                        self._enforce_rate_limit()

                        logger.info(f"[API调用] ak.stock_hk_spot_em() 获取港股实时行情...")
                        import time as _time
                        api_start = _time.time()

                        df = ak.stock_hk_spot_em()

                        api_elapsed = _time.time() - api_start
                        logger.info(
                            f"[API返回] ak.stock_hk_spot_em 成功: 返回 {len(df)} 只港股, "
                            f"耗时 {api_elapsed:.2f}s"
                        )

                        if not isinstance(df, pd.DataFrame):
                            raise TypeError("stock_hk_spot_em 未返回 DataFrame")
                        if '代码' not in df.columns:
                            raise KeyError("stock_hk_spot_em 返回结果缺少 代码 列")
                        if df.empty:
                            raise ValueError("stock_hk_spot_em 返回空市场快照")

                        _hk_realtime_cache['data'] = df
                        _hk_realtime_cache['timestamp'] = time.time()
                        _hk_realtime_cache['last_result'] = 'success'
                        logger.info(
                            f"[缓存更新] 港股实时行情(东财) 缓存已刷新，"
                            f"TTL={_hk_realtime_cache['ttl']}s"
                        )

                        quote = build_em_quote(df)
                        circuit_breaker.record_success(em_key)
                        if quote is not None:
                            return quote

                    except Exception as e:
                        _hk_realtime_cache['data'] = None
                        _hk_realtime_cache['timestamp'] = time.time()
                        _hk_realtime_cache['last_result'] = 'failure'
                        logger.warning(
                            f"[API错误] ak.stock_hk_spot_em 获取港股 {stock_code} "
                            f"失败: {e}，尝试 stock_hk_spot 备用接口"
                        )
                        circuit_breaker.record_failure(em_key, str(e))
                elif not cache_hit:
                    logger.info(
                        f"[熔断] 数据源 {em_key} 处于熔断状态，尝试使用备用链路"
                    )

        # --- Fallback source: Sina ---
        if not circuit_breaker.is_available(sina_key):
            logger.info(f"[熔断] 数据源 {sina_key} 处于熔断状态，跳过备用链路")
            return None

        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            logger.info(f"[API调用] ak.stock_hk_spot() 获取港股实时行情（备用）...")
            import time as _time
            api_start = _time.time()

            df_spot = ak.stock_hk_spot()

            api_elapsed = _time.time() - api_start
            logger.info(f"[API返回] ak.stock_hk_spot 成功: 返回 {len(df_spot)} 只港股, 耗时 {api_elapsed:.2f}s")

            row = df_spot[df_spot['代码'] == code]
            if row.empty:
                logger.info(f"[API返回] 未找到港股 {code} 的实时行情 (stock_hk_spot)")
                return None

            row = row.iloc[0]
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                source=RealtimeSource.AKSHARE_EM,
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume=safe_int(row.get('成交量')),
                amount=safe_float(row.get('成交额')),
            )
            circuit_breaker.record_success(sina_key)
            logger.info(f"[港股实时行情-备用] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%")
            return quote

        except Exception as e:
            logger.info(f"[API错误] ak.stock_hk_spot 备用接口也失败: {e}")
            circuit_breaker.record_failure(sina_key, str(e))
            return None
    
    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """
        Fetch chip distribution data.

        Data source: ak.stock_cyq_em()
        Includes: profit ratio, average cost, chip concentration

        Note: ETFs/indices have no chip distribution data; None is returned directly

        Args:
            stock_code: stock code

        Returns:
            ChipDistribution object (latest day), or None on failure
        """
        import akshare as ak

        # US stocks have no chip distribution data (not supported by Akshare)
        if _is_us_code(stock_code):
            logger.debug(f"[API跳过] {stock_code} 是美股，无筹码分布数据")
            return None

        # HK stocks have no chip distribution data (stock_cyq_em is A-share only)
        if _is_hk_code(stock_code):
            logger.debug(f"[API跳过] {stock_code} 是港股，无筹码分布数据")
            return None

        # ETFs/indices have no chip distribution data
        if _is_etf_code(stock_code):
            logger.debug(f"[API跳过] {stock_code} 是 ETF/指数，无筹码分布数据")
            return None
        
        try:
            # Anti-blocking strategy
            self._set_random_user_agent()
            self._enforce_rate_limit()
            
            logger.info(f"[API调用] ak.stock_cyq_em(symbol={stock_code}) 获取筹码分布...")
            import time as _time
            api_start = _time.time()
            
            df = ak.stock_cyq_em(symbol=stock_code)
            
            api_elapsed = _time.time() - api_start
            
            if df.empty:
                logger.warning(f"[API返回] ak.stock_cyq_em 返回空数据, 耗时 {api_elapsed:.2f}s")
                return None
            
            logger.info(f"[API返回] ak.stock_cyq_em 成功: 返回 {len(df)} 天数据, 耗时 {api_elapsed:.2f}s")
            logger.debug(f"[API返回] 筹码数据列名: {list(df.columns)}")
            
            # Take the latest day's data
            latest = df.iloc[-1]
            
            # Use the shared conversion helpers from realtime_types.py
            chip = ChipDistribution(
                code=stock_code,
                date=str(latest.get('日期', '')),
                profit_ratio=safe_float(latest.get('获利比例')),
                avg_cost=safe_float(latest.get('平均成本')),
                cost_90_low=safe_float(latest.get('90成本-低')),
                cost_90_high=safe_float(latest.get('90成本-高')),
                concentration_90=safe_float(latest.get('90集中度')),
                cost_70_low=safe_float(latest.get('70成本-低')),
                cost_70_high=safe_float(latest.get('70成本-高')),
                concentration_70=safe_float(latest.get('70集中度')),
            )
            
            logger.info(f"[筹码分布] {stock_code} 日期={chip.date}: 获利比例={chip.profit_ratio:.1%}, "
                       f"平均成本={chip.avg_cost}, 90%集中度={chip.concentration_90:.2%}, "
                       f"70%集中度={chip.concentration_70:.2%}")
            return chip
            
        except Exception as e:
            logger.error(f"[API错误] 获取 {stock_code} 筹码分布失败: {e}")
            return None
    
    def get_enhanced_data(self, stock_code: str, days: int = 60) -> Dict[str, Any]:
        """
        Fetch enhanced data (historical K-lines + realtime quote + chip distribution).

        Args:
            stock_code: stock code
            days: number of days of history

        Returns:
            Dict containing all data
        """
        result = {
            'code': stock_code,
            'daily_data': None,
            'realtime_quote': None,
            'chip_distribution': None,
        }
        
        # Fetch daily data
        try:
            df = self.get_daily_data(stock_code, days=days)
            result['daily_data'] = df
        except Exception as e:
            logger.error(f"获取 {stock_code} 日线数据失败: {e}")
        
        # Fetch realtime quote
        result['realtime_quote'] = self.get_realtime_quote(stock_code)
        
        # Fetch chip distribution
        result['chip_distribution'] = self.get_chip_distribution(stock_code)
        
        return result

    def get_main_indices(self, region: str = "cn") -> Optional[List[Dict[str, Any]]]:
        """
        Fetch realtime quotes of major indices (Sina API); A-shares only.
        """
        if region != "cn":
            return None
        import akshare as ak

        # Major index code mapping
        indices_map = {
            'sh000001': '上证指数',
            'sz399001': '深证成指',
            'sz399006': '创业板指',
            'sh000688': '科创50',
            'sh000016': '上证50',
            'sh000300': '沪深300',
        }

        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            # Use akshare to fetch index quotes (Sina Finance API)
            df = ak.stock_zh_index_spot_sina()

            results = []
            if df is not None and not df.empty:
                for code, name in indices_map.items():
                    # Find the matching index
                    row = df[df['代码'] == code]
                    if row.empty:
                        # Try looking up with a prefix
                        row = df[df['代码'].str.contains(code)]

                    if not row.empty:
                        row = row.iloc[0]
                        current = safe_float(row.get('最新价', 0))
                        prev_close = safe_float(row.get('昨收', 0))
                        high = safe_float(row.get('最高', 0))
                        low = safe_float(row.get('最低', 0))

                        # Compute amplitude
                        amplitude = 0.0
                        if prev_close > 0:
                            amplitude = (high - low) / prev_close * 100

                        results.append({
                            'code': code,
                            'name': name,
                            'current': current,
                            'change': safe_float(row.get('涨跌额', 0)),
                            'change_pct': safe_float(row.get('涨跌幅', 0)),
                            'open': safe_float(row.get('今开', 0)),
                            'high': high,
                            'low': low,
                            'prev_close': prev_close,
                            'volume': safe_float(row.get('成交量', 0)),
                            'amount': safe_float(row.get('成交额', 0)),
                            'amplitude': amplitude,
                        })
            return results

        except Exception as e:
            logger.error(f"[Akshare] 获取指数行情失败: {e}")
            return None

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        """
        Fetch market advance/decline statistics.

        Source priority:
        1. Eastmoney API (ak.stock_zh_a_spot_em)
        2. Sina API (ak.stock_zh_a_spot)
        """
        import akshare as ak

        # Prefer the Eastmoney API
        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            started_at = time.monotonic()
            logger.info(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot_em action=request_start"
            )
            df = ak.stock_zh_a_spot_em()
            elapsed = time.monotonic() - started_at
            logger.info(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot_em action=request_complete elapsed=%.2fs",
                elapsed,
            )
            if df is not None and not df.empty:
                return self._calc_market_stats(df)
            logger.warning(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot_em action=parse status=empty"
            )
        except Exception as e:
            logger.warning(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot_em action=failed error=%s fallback=ak.stock_zh_a_spot",
                e,
            )

        # If Eastmoney fails, try the Sina API
        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            started_at = time.monotonic()
            logger.info(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot action=request_start"
            )
            df = ak.stock_zh_a_spot()
            elapsed = time.monotonic() - started_at
            logger.info(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot action=request_complete elapsed=%.2fs",
                elapsed,
            )
            if df is not None and not df.empty:
                return self._calc_market_stats(df)
            logger.warning(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot action=parse status=empty"
            )
        except Exception as e:
            logger.error(
                "[MarketStats] component=market_stats provider=AkshareFetcher "
                "api=ak.stock_zh_a_spot action=failed error=%s",
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
        
        # 1. Extract base comparison data: last price, previous close
        # Handle column names returned by different APIs: sina/em efinance tushare xtdata
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
            
            # Suspension filter: efinance sometimes shows missing prices for suspended stocks as '-', em shows None
            if pd.isna(current_price) or pd.isna(pre_close) or current_price in ['-'] or pre_close in ['-'] or amount == 0:
                continue
            
            # em and efinance return str; convert to float
            current_price = float(current_price)
            pre_close = float(pre_close)
            
            # Get the numeric code without prefix
            pure_code = normalize_stock_code(str(code)) 

            # A. Determine each stock's price-limit ratio (based on the numeric code)
            if is_bse_code(pure_code): 
                ratio = 0.30
            elif is_kc_cy_stock(pure_code): #pure_code.startswith(('688', '30')):
                ratio = 0.20
            elif is_st_stock(name): #'ST' in str_name:
                ratio = 0.05
            else:
                ratio = 0.10

            # B. Compute limit-up/limit-down prices strictly per A-share rules: prev close * (1 ± ratio), rounded to 2 decimals
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
                
        # Count
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
        Fetch the industry sector gainers/losers ranking.

        Source priority:
        1. Eastmoney API (ak.stock_board_industry_name_em)
        2. Sina API (ak.stock_sector_spot)
        """
        import akshare as ak

        def _get_rank_top_n(df: pd.DataFrame, change_col: str, industry_name: str, n: int) -> Tuple[list, list]:
            df[change_col] = pd.to_numeric(df[change_col], errors='coerce')
            df = df.dropna(subset=[change_col])

            # Top n gainers
            top = df.nlargest(n, change_col)
            top_sectors = [
                {'name': row[industry_name], 'change_pct': row[change_col]}
                for _, row in top.iterrows()
            ]

            bottom = df.nsmallest(n, change_col)
            bottom_sectors = [
                {'name': row[industry_name], 'change_pct': row[change_col]}
                for _, row in bottom.iterrows()
            ]
            return top_sectors, bottom_sectors
        
        # Prefer the Eastmoney API
        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            logger.info("[API调用] ak.stock_board_industry_name_em() 获取板块排行...")
            df = ak.stock_board_industry_name_em()
            if df is not None and not df.empty:
                change_col = '涨跌幅'
                name = '板块名称'
                return _get_rank_top_n(df, change_col, name, n)
            
        except Exception as e:
            logger.warning(f"[Akshare] 东财接口获取行业板块排行失败: {e}，尝试新浪接口")

        # If Eastmoney fails, try the Sina API
        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            logger.info("[API调用] ak.stock_sector_spot() 获取行业板块排行(新浪)...")
            df = ak.stock_sector_spot(indicator='行业')
            if df is None or df.empty:
                return None
            change_col = '涨跌幅'
            name = '板块'
            return _get_rank_top_n(df, change_col, name, n)
        
        except Exception as e:
            logger.error(f"[Akshare] 新浪接口获取板块排行也失败: {e}")
            return None

    def get_concept_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        """Fetch the concept/theme gainers/losers ranking."""
        import akshare as ak

        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            logger.info("[API调用] ak.stock_board_concept_name_em() 获取概念排行...")
            df = ak.stock_board_concept_name_em()
            if df is None or df.empty:
                return None

            change_col = '涨跌幅'
            name_col = '板块名称'
            if change_col not in df.columns or name_col not in df.columns:
                return None

            df = df.copy()
            df[change_col] = pd.to_numeric(df[change_col], errors='coerce')
            df = df.dropna(subset=[change_col])
            top = df.nlargest(n, change_col)
            bottom = df.nsmallest(n, change_col)
            return (
                [
                    {'name': str(row[name_col]), 'change_pct': float(row[change_col])}
                    for _, row in top.iterrows()
                ],
                [
                    {'name': str(row[name_col]), 'change_pct': float(row[change_col])}
                    for _, row in bottom.iterrows()
                ],
            )
        except Exception as e:
            logger.warning(f"[Akshare] 获取概念排行失败: {e}")
            return None

    def get_hot_stocks(self, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        """Fetch the popular-stock ranking, degrading across config-free hot-list sources."""
        import akshare as ak

        fetch_attempts = (
            ("东方财富人气榜", lambda top_n: self._get_eastmoney_hot_stocks(ak, top_n)),
            ("东方财富飙升榜", lambda top_n: self._get_eastmoney_hot_up_stocks(ak, top_n)),
            ("雪球关注榜", lambda top_n: self._get_xueqiu_hot_stocks(ak, top_n)),
        )
        last_error = ""
        for source, fetch in fetch_attempts:
            try:
                rows = fetch(n)
                if rows:
                    return rows[:n]
            except Exception as e:
                last_error = f"{source}: {e}"
                logger.debug("[Akshare] 人气股候选源失败 source=%s: %s", source, e)
        if last_error:
            logger.warning("[Akshare] 获取人气股全部候选源失败: %s", last_error)
        return None

    def _get_eastmoney_hot_stocks(self, ak: Any, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        """Fetch the Eastmoney popularity ranking."""
        self._set_random_user_agent()
        self._enforce_rate_limit()

        logger.info("[API调用] ak.stock_hot_rank_em() 获取东方财富人气股...")
        df = ak.stock_hot_rank_em()
        if df is None or df.empty:
            return None

        rows: List[Dict[str, Any]] = []
        for _, row in df.head(n).iterrows():
            rows.append({
                'rank': self._safe_int(row.get('当前排名')),
                'code': str(row.get('代码', '')).strip(),
                'name': str(row.get('股票名称', '')).strip(),
                'price': self._safe_float(row.get('最新价')),
                'change_pct': self._safe_float(row.get('涨跌幅')),
                'source': '东方财富人气榜',
            })
        return rows

    def _get_eastmoney_hot_up_stocks(self, ak: Any, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        """Fetch the Eastmoney surge ranking."""
        self._set_random_user_agent()
        self._enforce_rate_limit()

        logger.info("[API调用] ak.stock_hot_up_em() 获取东方财富飙升榜...")
        df = ak.stock_hot_up_em()
        if df is None or df.empty:
            return None

        code_col = self._find_first_column(df, ("代码", "股票代码"))
        name_col = self._find_first_column(df, ("股票名称", "名称", "股票简称"))
        rank_col = self._find_first_column(df, ("当前排名", "排名", "序号"))
        price_col = self._find_first_column(df, ("最新价", "现价"))
        change_col = self._find_column_containing(df, ("涨跌幅",))
        if not code_col or not name_col:
            return None

        rows: List[Dict[str, Any]] = []
        for _, row in df.head(n).iterrows():
            rows.append({
                'rank': self._safe_int(row.get(rank_col)) if rank_col else len(rows) + 1,
                'code': str(row.get(code_col, '')).strip(),
                'name': str(row.get(name_col, '')).strip(),
                'price': self._safe_float(row.get(price_col)) if price_col else None,
                'change_pct': self._safe_float(row.get(change_col)) if change_col else None,
                'source': '东方财富飙升榜',
            })
        return rows

    def _get_xueqiu_hot_stocks(self, ak: Any, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        """Fallback to the Xueqiu most-followed ranking. This API is slow; only tried after the popularity ranking fails."""
        self._set_random_user_agent()
        self._enforce_rate_limit()

        logger.info("[API调用] ak.stock_hot_follow_xq() 获取雪球关注榜...")
        df = ak.stock_hot_follow_xq(symbol='最热门')
        if df is None or df.empty:
            return None

        rows: List[Dict[str, Any]] = []
        for idx, (_, row) in enumerate(df.head(n).iterrows(), 1):
            rows.append({
                'rank': idx,
                'code': str(row.get('股票代码', '')).strip(),
                'name': str(row.get('股票简称', '')).strip(),
                'price': self._safe_float(row.get('最新价')),
                'change_pct': None,
                'source': '雪球关注榜',
            })
        return rows

    def get_limit_up_pool(
        self,
        date: Optional[str] = None,
        n: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch the limit-up pool, ordered by consecutive limit-up count and seal time."""
        import akshare as ak

        query_date = date or datetime.now().strftime('%Y%m%d')
        try:
            self._set_random_user_agent()
            self._enforce_rate_limit()

            logger.info("[API调用] ak.stock_zt_pool_em(date=%s) 获取涨停池...", query_date)
            df = ak.stock_zt_pool_em(date=query_date)
            if df is None or df.empty:
                return None

            df = df.copy()
            for col in ('连板数', '封板资金', '成交额', '换手率', '涨跌幅'):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            if '首次封板时间' in df.columns:
                df['首次封板时间'] = df['首次封板时间'].map(self._normalize_limit_time_value)
                df['_首次封板时间排序'] = df['首次封板时间'].where(df['首次封板时间'] != '', '999999')
            sort_cols = [col for col in ('连板数', '_首次封板时间排序') if col in df.columns]
            if sort_cols:
                ascending = [False if col == '连板数' else True for col in sort_cols]
                df = df.sort_values(sort_cols, ascending=ascending)

            rows: List[Dict[str, Any]] = []
            for _, row in df.head(n).iterrows():
                rows.append({
                    'code': str(row.get('代码', '')).strip(),
                    'name': str(row.get('名称', '')).strip(),
                    'change_pct': self._safe_float(row.get('涨跌幅')),
                    'price': self._safe_float(row.get('最新价')),
                    'amount': self._safe_float(row.get('成交额')),
                    'turnover_rate': self._safe_float(row.get('换手率')),
                    'seal_amount': self._safe_float(row.get('封板资金')),
                    'first_limit_time': str(row.get('首次封板时间', '')).strip(),
                    'last_limit_time': self._normalize_limit_time_value(row.get('最后封板时间')),
                    'break_count': self._safe_int(row.get('炸板次数')),
                    'limit_stat': str(row.get('涨停统计', '')).strip(),
                    'consecutive_boards': self._safe_int(row.get('连板数')),
                    'industry': str(row.get('所属行业', '')).strip(),
                })
            return rows
        except Exception as e:
            logger.warning(f"[Akshare] 获取涨停池失败: {e}")
            return None

    @staticmethod
    def _normalize_limit_time_value(value: Any) -> str:
        """Normalize AkShare HHMMSS-like seal time values to zero-padded HHMMSS."""
        try:
            if pd.isna(value):
                return ""
        except TypeError:
            pass

        text = str(value).strip()
        if not text or text.lower() in {"nan", "nat", "none", "null", "-", "--"}:
            return ""

        if ":" in text:
            parts = text.split(":")
            try:
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                second = int(parts[2]) if len(parts) > 2 else 0
                return f"{hour:02d}{minute:02d}{second:02d}"
            except (TypeError, ValueError):
                return text

        try:
            return f"{int(float(text)):06d}"
        except (TypeError, ValueError):
            digits = "".join(ch for ch in text if ch.isdigit())
            return digits.zfill(6) if digits else text

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if pd.isna(value):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            if pd.isna(value):
                return 0
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _find_first_column(df: pd.DataFrame, candidates: Tuple[str, ...]) -> Optional[str]:
        columns = [str(col) for col in df.columns]
        for candidate in candidates:
            if candidate in columns:
                return candidate
        return None

    @staticmethod
    def _find_column_containing(df: pd.DataFrame, keywords: Tuple[str, ...]) -> Optional[str]:
        for col in df.columns:
            col_text = str(col)
            if all(keyword in col_text for keyword in keywords):
                return col
        return None


if __name__ == "__main__":
    # Test code
    logging.basicConfig(level=logging.DEBUG)
    
    fetcher = AkshareFetcher()
    
    # Test a regular stock
    print("=" * 50)
    print("测试普通股票数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('600519')  # Kweichow Moutai
        print(f"[股票] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[股票] 获取失败: {e}")
    
    # Test an ETF fund
    print("\n" + "=" * 50)
    print("测试 ETF 基金数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('512400')  # Non-ferrous metals leaders ETF
        print(f"[ETF] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[ETF] 获取失败: {e}")
    
    # Test ETF realtime quotes
    print("\n" + "=" * 50)
    print("测试 ETF 实时行情获取")
    print("=" * 50)
    try:
        quote = fetcher.get_realtime_quote('512880')  # Securities ETF
        if quote:
            print(f"[ETF实时] {quote.name}: 价格={quote.price}, 涨跌幅={quote.change_pct}%")
        else:
            print("[ETF实时] 未获取到数据")
    except Exception as e:
        print(f"[ETF实时] 获取失败: {e}")
    
    # Test HK historical data
    print("\n" + "=" * 50)
    print("测试港股历史数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('00700')  # Tencent Holdings
        print(f"[港股] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[港股] 获取失败: {e}")
    
    # Test HK realtime quotes
    print("\n" + "=" * 50)
    print("测试港股实时行情获取")
    print("=" * 50)
    try:
        quote = fetcher.get_realtime_quote('00700')  # Tencent Holdings
        if quote:
            print(f"[港股实时] {quote.name}: 价格={quote.price}, 涨跌幅={quote.change_pct}%")
        else:
            print("[港股实时] 未获取到数据")
    except Exception as e:
        print(f"[港股实时] 获取失败: {e}")

    # Test market statistics
    print("\n" + "=" * 50)
    print("Testing get_market_stats (akshare)")
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

    # Test chip distribution data
    print("\n" + "=" * 50)
    print("测试筹码分布数据获取")
    print("=" * 50)
    try:
        chip = fetcher.get_chip_distribution('600519')  # Kweichow Moutai
    except Exception as e:
        print(f"[筹码分布] 获取失败: {e}")

    # Test industry sector ranking
    print("\n" + "=" * 50)
    print("测试行业板块排名获取")
    print("=" * 50)
    try:
        rankings = fetcher.get_sector_rankings(n=5)
        if rankings:
            top, bottom = rankings
            print("涨幅榜 Top 5:")
            for sector in top:
                print(f"{sector['name']}: {sector['change_pct']}%")
            print("\n跌幅榜 Top 5:")
            for sector in bottom:
                print(f"{sector['name']}: {sector['change_pct']}%")
        else:
            print("未获取到行业板块排名数据")
    except Exception as e:
        print(f"[行业板块排名] 获取失败: {e}")
