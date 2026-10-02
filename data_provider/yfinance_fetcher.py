# -*- coding: utf-8 -*-
"""
===================================
YfinanceFetcher - fallback data source (Priority 4)
===================================

Data source: Yahoo Finance (via the yfinance library)
Features: international data source, may be delayed or incomplete
Role: last line of defence when all domestic data sources fail

Key strategies:
1. Automatically convert A-share codes to yfinance format (.SS / .SZ)
2. Handle Yahoo Finance data format differences
3. Exponential backoff retry on failure
"""

import csv
import logging
from datetime import datetime
from io import StringIO
from typing import Optional, List, Dict, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    _is_hk_market,
    _is_vn_market,
    is_bse_code,
    is_vn_index_code,
    normalize_stock_code,
)
from src.core.markets import is_market_enabled
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource
from .us_index_mapping import get_us_index_yf_symbol, is_us_stock_code
from .yfinance_fundamental_adapter import _safe_float
from src.services.market_symbol_utils import get_suffix_market, is_suffix_market_symbol

# Optionally import the local stock-name mapping patch; fall back to an empty dict if missing
try:
    from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name
except (ImportError, ModuleNotFoundError):
    STOCK_NAME_MAP = {}

    def is_meaningful_stock_name(name: str | None, stock_code: str) -> bool:
        """Simple fallback for name validity checking"""
        if not name:
            return False
        n = str(name).strip()
        return bool(n and n.upper() != str(stock_code).strip().upper())

import os

logger = logging.getLogger(__name__)


class YfinanceFetcher(BaseFetcher):
    """
    Yahoo Finance data source implementation

    Priority: 4 (lowest, used as fallback)
    Data source: Yahoo Finance

    Key strategies:
    - Automatically convert stock code format
    - Handle timezone and data format differences
    - Exponential backoff retry on failure

    Notes:
    - A-share data may be delayed
    - Some stocks may have no data
    - Data precision may differ slightly from domestic sources
    """

    name = "YfinanceFetcher"
    priority = int(os.getenv("YFINANCE_PRIORITY", "4"))

    def __init__(self):
        """Initialize YfinanceFetcher"""
        pass

    @staticmethod
    def _is_jp_kr_suffix_stock(stock_code: str) -> bool:
        """Return True for supported JP/KR suffix-only Yahoo symbols."""
        return is_suffix_market_symbol(stock_code, "jp") or is_suffix_market_symbol(stock_code, "kr")

    @staticmethod
    def _is_tw_suffix_stock(stock_code: str) -> bool:
        """Return True for supported Taiwan suffix-only Yahoo symbols (TWSE `.TW` / TPEx `.TWO`).

        Taiwan base codes are 4-6 digits (common stocks 4, ETFs/others up to 6,
        e.g. 00878 / 006208), wider than the JP `.T` range.
        """
        return is_suffix_market_symbol(stock_code, "tw")

    def _convert_stock_code(self, stock_code: str) -> str:
        """
        Convert a stock code to Yahoo Finance format

        Yahoo Finance code formats:
        - A-share Shanghai: 600519.SS (Shanghai Stock Exchange)
        - A-share Shenzhen: 000001.SZ (Shenzhen Stock Exchange)
        - HK stocks: 0700.HK (Hong Kong Stock Exchange)
        - US stocks: AAPL, TSLA, GOOGL (no suffix needed)
        - Vietnam stocks (HOSE): HPG.VN, FPT.VN

        Args:
            stock_code: raw code, e.g. '600519', 'hk00700', 'AAPL'

        Returns:
            Code in Yahoo Finance format

        Examples:
            >>> fetcher._convert_stock_code('600519')
            '600519.SS'
            >>> fetcher._convert_stock_code('hk00700')
            '0700.HK'
            >>> fetcher._convert_stock_code('AAPL')
            'AAPL'
            >>> fetcher._convert_stock_code('HPG')   # khi thị trường us đang tắt
            'HPG.VN'
        """
        code = stock_code.strip().upper()

        # Việt Nam: PHẢI đặt trước nhánh mã Mỹ. Manager gọi normalize_stock_code()
        # trước nên ``HPG.VN`` đã bị rút gọn thành ``HPG``, mà is_us_stock_code()
        # khớp [A-Z]{1,5} nên sẽ nhận nhầm là mã Mỹ. Truyền mã gốc chứ không phải
        # bản đã upper(): _is_vn_market() nhận dạng tiền tố ``vn`` trên chuỗi
        # thường (``vnHPG``), và nó cũng mang sẵn quy tắc va chạm VN/US — mã trần
        # 3 ký tự chỉ được coi là VN khi thị trường ``us`` đang tắt.
        #
        # Điều kiện ``is_market_enabled("vn")`` là để khớp với cách manager định
        # tuyến: ``_market_tag`` chỉ hỏi ``_is_vn_market`` khi thị trường VN đang
        # bật. Thiếu nó thì các mã trong ``VN_INDEX_SYMBOLS`` (``UPCOM`` là 5 ký
        # tự chữ hoa, hình dạng hợp lệ của một ticker Mỹ) sẽ bị nhánh này chiếm
        # mất ngay cả khi triển khai không hề bật thị trường Việt Nam.
        if is_market_enabled("vn") and _is_vn_market(stock_code):
            # Chỉ số Việt Nam bị từ chối thẳng, không phát request: ``^VNINDEX.VN``
            # trả về đúng 1 nến rác khi xin cả năm, còn ``^VN30.VN`` /
            # ``^HNXINDEX.VN`` / ``^UPCOM.VN`` thì rỗng hoàn toàn. Để lọt sẽ thành
            # dữ liệu sai thay vì một lỗi sạch mà manager xử lý được.
            if is_vn_index_code(code):
                raise DataFetchError(
                    f"[{self.name}] {stock_code}: Yahoo không có lịch sử chỉ số "
                    "Việt Nam, chỉ số phải do VCI phục vụ"
                )

            # normalize_stock_code() rút mọi cách viết (``HPG.VN``/``vnHPG``) về mã
            # trần ``HPG``, tránh nối thành ``HPG.VN.VN``.
            #
            # Không thể phân biệt HOSE/HNX/UPCOM từ hình dạng mã, và không được
            # phụ thuộc bảng mã của VCI vì VCI hỏng chính là lý do rơi vào đây.
            # Thực đo: HOSE phủ 20/20 mã mẫu, HNX 0/20, UPCOM 0/20. Mã ngoài HOSE
            # sẽ cho DataFrame rỗng -> BaseFetcher.get_daily_data raise
            # DataFetchError -> manager coi như một nguồn hỏng bình thường.
            vn_code = normalize_stock_code(stock_code)
            logger.debug(f"识别为越南股票代码: {stock_code} -> {vn_code}.VN")
            return f"{vn_code}.VN"

        # US indices: map to the Yahoo Finance symbol (e.g. SPX -> ^GSPC)
        yf_symbol, _ = get_us_index_yf_symbol(code)
        if yf_symbol:
            logger.debug(f"识别为美股指数: {code} -> {yf_symbol}")
            return yf_symbol

        # US stocks: 1-5 uppercase letters (optional .X suffix), returned as-is
        if is_us_stock_code(code):
            logger.debug(f"识别为美股代码: {code}")
            return code

        # JP/KR/TW stocks MVP: explicit Yahoo Finance suffix-only codes, passed to Yahoo as-is.
        if self._is_jp_kr_suffix_stock(code) or self._is_tw_suffix_stock(code):
            logger.debug(f"识别为日韩台 Yahoo suffix 代码: {code}")
            return code

        # HK stocks: hk prefix -> .HK suffix
        if code.startswith('HK'):
            hk_code = code[2:].lstrip('0') or '0'  # Strip leading zeros but keep at least one 0
            hk_code = hk_code.zfill(4)  # Pad to 4 digits
            logger.debug(f"转换港股代码: {stock_code} -> {hk_code}.HK")
            return f"{hk_code}.HK"

        # Bare HK codes: 4-5 pure digits (e.g. 00700, 02513, 0001) are treated as HK stocks.
        # A-share codes are all 6 digits, so bare 4-5 digit codes cannot be A-shares or BSE (BSE is
        # 6-digit 4/8/920xxx); they can therefore be routed to .HK before the .SZ fallback, preventing yfinance
        # from treating new HK listings like 02513 as Shenzhen and getting a Yahoo 404. See issue #2091.
        if code.isdigit() and 4 <= len(code) <= 5:
            hk_code = code.lstrip('0') or '0'
            hk_code = hk_code.zfill(4)
            logger.debug(f"识别裸港股代码: {stock_code} -> {hk_code}.HK")
            return f"{hk_code}.HK"

        # Code already has a suffix
        if '.SS' in code or '.SZ' in code or '.HK' in code or '.BJ' in code:
            return code

        # Strip a possible .SH suffix
        code = code.replace('.SH', '')

        # ETF: Shanghai ETF (51xx, 52xx, 56xx, 58xx) -> .SS; Shenzhen ETF (15xx, 16xx, 18xx) -> .SZ
        if len(code) == 6:
            if code.startswith(('51', '52', '56', '58')):
                return f"{code}.SS"
            if code.startswith(('15', '16', '18')):
                return f"{code}.SZ"

        # BSE (Beijing Stock Exchange): 8xxxxx, 4xxxxx, 920xxx
        if is_bse_code(code):
            base = code.split('.')[0] if '.' in code else code
            return f"{base}.BJ"

        # A-shares: determine the market by code prefix
        if code.startswith(('600', '601', '603', '688')):
            return f"{code}.SS"
        elif code.startswith(('000', '002', '300')):
            return f"{code}.SZ"
        else:
            logger.warning(f"无法确定股票 {code} 的市场，默认使用深市")
            return f"{code}.SZ"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch raw data from Yahoo Finance

        Uses yfinance.download() to fetch historical data

        Flow:
        1. Convert the stock code format
        2. Call the yfinance API
        3. Process the returned data
        """
        import yfinance as yf

        # Convert the code format
        yf_code = self._convert_stock_code(stock_code)

        logger.debug(f"调用 yfinance.download({yf_code}, {start_date}, {end_date})")

        try:
            # Download data with yfinance
            df = yf.download(
                tickers=yf_code,
                start=start_date,
                end=end_date,
                progress=False,  # Disable the progress bar
                auto_adjust=True,  # Auto-adjust prices (price adjustment)
                multi_level_index=True
            )

            # Select only the yf_code columns to avoid mixing data from multiple stocks
            if isinstance(df.columns, pd.MultiIndex) and len(df.columns) > 1:
                ticker_level = df.columns.get_level_values(1)
                mask = ticker_level == yf_code
                if mask.any():
                    df = df.loc[:, mask].copy()

            if df.empty:
                raise DataFetchError(f"Yahoo Finance 未查询到 {stock_code} 的数据")

            return df

        except Exception as e:
            if isinstance(e, DataFetchError):
                raise
            raise DataFetchError(f"Yahoo Finance 获取数据失败: {e}") from e

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        Normalize Yahoo Finance data

        Column names returned by yfinance:
        Open, High, Low, Close, Volume (index is the date)

        Note: newer yfinance returns MultiIndex column names, e.g. ('Close', 'AMD'),
        which must be flattened before processing

        Must be mapped to the standard column names:
        date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()

        # Handle MultiIndex column names (newer yfinance return format)
        # e.g. ('Close', 'AMD') -> 'Close'
        if isinstance(df.columns, pd.MultiIndex):
            logger.debug("检测到 MultiIndex 列名，进行扁平化处理")
            # Take the first-level column name (Price level: Close, High, Low, etc.)
            df.columns = df.columns.get_level_values(0)

        # Reset the index, turning the date index into a column
        df = df.reset_index()

        # Column name mapping (yfinance uses capitalized names)
        column_mapping = {
            'Date': 'date',
            'Datetime': 'date',
            'datetime': 'date',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume',
        }

        df = df.rename(columns=column_mapping)
        if 'date' not in df.columns:
            index_col = df.columns[0] if len(df.columns) else None
            if index_col is not None:
                candidate = df[index_col]
                if pd.api.types.is_datetime64_any_dtype(candidate):
                    df = df.rename(columns={index_col: 'date'})
                elif not pd.api.types.is_numeric_dtype(candidate):
                    parsed_dates = pd.to_datetime(candidate, errors='coerce')
                    if parsed_dates.notna().any():
                        df = df.rename(columns={index_col: 'date'})
                        df['date'] = parsed_dates

        # Compute change percent (yfinance does not provide it directly)
        if 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100
            df['pct_chg'] = df['pct_chg'].fillna(0).round(2)

        # Compute turnover amount (not provided by yfinance; use an estimate)
        # turnover amount ≈ volume * average price
        if 'volume' in df.columns and 'close' in df.columns:
            df['amount'] = df['volume'] * df['close']
        else:
            df['amount'] = 0

        # Add the stock code column
        df['code'] = stock_code

        # Keep only the required columns
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]

        return df

    def _fetch_yf_ticker_data(self, yf, yf_code: str, name: str, return_code: str) -> Optional[Dict[str, Any]]:
        """
        Fetch quote data for a single index/stock via yfinance.

        Args:
            yf: reference to the yfinance module
            yf_code: code used by yfinance (e.g. '000001.SS', '^GSPC')
            name: index display name
            return_code: value written to the code field of the result dict (e.g. 'sh000001', 'SPX')

        Returns:
            Quote dict, or None on failure
        """
        ticker = yf.Ticker(yf_code)
        # Fetch the last two days of data to compute the change percent
        hist = ticker.history(period='2d')
        if hist.empty:
            return None
        today_row = hist.iloc[-1]
        prev_row = hist.iloc[-2] if len(hist) > 1 else today_row
        price = float(today_row['Close'])
        prev_close = float(prev_row['Close'])
        change = price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0
        high = float(today_row['High'])
        low = float(today_row['Low'])
        # amplitude = (high - low) / previous close * 100
        amplitude = ((high - low) / prev_close * 100) if prev_close else 0
        return {
            'code': return_code,
            'name': name,
            'current': price,
            'change': change,
            'change_pct': change_pct,
            'open': float(today_row['Open']),
            'high': high,
            'low': low,
            'prev_close': prev_close,
            'volume': float(today_row['Volume']),
            'amount': 0.0,  # Yahoo Finance does not provide accurate turnover amount
            'amplitude': amplitude,
        }

    def get_main_indices(self, region: str = "cn") -> Optional[List[Dict[str, Any]]]:
        """
        Get main index quotes (Yahoo Finance) for A-share, US, HK, JP, KR and TW markets.
        region=us delegates to _get_us_main_indices.
        region=hk delegates to _get_hk_main_indices.
        region=jp/kr/tw delegate to the corresponding market index methods.
        Other non-cn regions (e.g. vn) return None and are left to the next data source.
        """
        import yfinance as yf

        if region == "us":
            return self._get_us_main_indices(yf)
        if region == "hk":
            return self._get_hk_main_indices(yf)
        if region == "jp":
            return self._get_jp_main_indices(yf)
        if region == "kr":
            return self._get_kr_main_indices(yf)
        if region == "tw":
            return self._get_tw_main_indices(yf)
        if region != "cn":
            # Vùng chưa hỗ trợ (vd. vn) trả None để DataFetcherManager chuyển nguồn,
            # tránh rơi xuống danh sách chỉ số A-share bên dưới.
            # Unsupported regions (e.g. vn) return None instead of A-share indices.
            return None

        # A-share indices: akshare code -> (yfinance code, display name)
        yf_mapping = {
            'sh000001': ('000001.SS', '上证指数'),
            'sz399001': ('399001.SZ', '深证成指'),
            'sz399006': ('399006.SZ', '创业板指'),
            'sh000688': ('000688.SS', '科创50'),
            'sh000016': ('000016.SS', '上证50'),
            'sh000300': ('000300.SS', '沪深300'),
        }

        results = []
        try:
            for ak_code, (yf_code, name) in yf_mapping.items():
                try:
                    item = self._fetch_yf_ticker_data(yf, yf_code, name, ak_code)
                    if item:
                        results.append(item)
                        logger.debug(f"[Yfinance] 获取指数 {name} 成功")
                except Exception as e:
                    logger.warning(f"[Yfinance] 获取指数 {name} 失败: {e}")

            if results:
                logger.info(f"[Yfinance] 成功获取 {len(results)} 个 A 股指数行情")
                return results

        except Exception as e:
            logger.error(f"[Yfinance] 获取 A 股指数行情失败: {e}")

        return None

    def _get_us_main_indices(self, yf) -> Optional[List[Dict[str, Any]]]:
        """Get main US index quotes (SPX, IXIC, DJI, VIX), reusing _fetch_yf_ticker_data"""
        # Core US indices needed for the market review
        us_indices = ['SPX', 'IXIC', 'DJI', 'VIX']
        results = []
        try:
            for code in us_indices:
                yf_symbol, name = get_us_index_yf_symbol(code)
                if not yf_symbol:
                    continue
                try:
                    item = self._fetch_yf_ticker_data(yf, yf_symbol, name, code)
                    if item:
                        results.append(item)
                        logger.debug(f"[Yfinance] 获取美股指数 {name} 成功")
                except Exception as e:
                    logger.warning(f"[Yfinance] 获取美股指数 {name} 失败: {e}")

            if results:
                logger.info(f"[Yfinance] 成功获取 {len(results)} 个美股指数行情")
                return results

        except Exception as e:
            logger.error(f"[Yfinance] 获取美股指数行情失败: {e}")

        return None

    def _get_hk_main_indices(self, yf) -> Optional[List[Dict[str, Any]]]:
        """Get main HK index quotes (HSI, HSTECH, HSCEI), reusing _fetch_yf_ticker_data"""
        # Yahoo Finance HK index symbol mapping:
        # - HSI -> ^HSI
        # - HSTECH -> HSTECH.HK (not ^HSTECH)
        # - HSCEI -> ^HSCE (not ^HSCEI)
        # This mapping is pinned by the offline unit test tests/test_yfinance_hk_indices.py to avoid nondeterministic failures from online dependencies.
        hk_indices = {
            'HSI': ('^HSI', '恒生指数'),
            'HSTECH': ('HSTECH.HK', '恒生科技指数'),
            'HSCEI': ('^HSCE', '国企指数'),
        }
        results = []
        try:
            for code, (yf_symbol, name) in hk_indices.items():
                try:
                    item = self._fetch_yf_ticker_data(yf, yf_symbol, name, code)
                    if item:
                        results.append(item)
                        logger.debug(f"[Yfinance] 获取港股指数 {name} 成功")
                except Exception as e:
                    logger.warning(f"[Yfinance] 获取港股指数 {name} 失败: {e}")

            if results:
                logger.info(f"[Yfinance] 成功获取 {len(results)} 个港股指数行情")
                return results

        except Exception as e:
            logger.error(f"[Yfinance] 获取港股指数行情失败: {e}")

        return None

    def _get_jp_main_indices(self, yf) -> Optional[List[Dict[str, Any]]]:
        """Get main Japanese index quotes (Nikkei 225, TOPIX), reusing _fetch_yf_ticker_data."""
        jp_indices = {
            'N225': ('^N225', '日经225'),
            'TOPX': ('^TOPX', '东证指数'),
        }
        results = []
        try:
            for code, (yf_symbol, name) in jp_indices.items():
                try:
                    item = self._fetch_yf_ticker_data(yf, yf_symbol, name, code)
                    if item:
                        results.append(item)
                        logger.debug(f"[Yfinance] 获取日本指数 {name} 成功")
                except Exception as e:
                    logger.warning(f"[Yfinance] 获取日本指数 {name} 失败: {e}")
            if results:
                logger.info(f"[Yfinance] 成功获取 {len(results)} 个日本指数行情")
                return results
        except Exception as e:
            logger.error(f"[Yfinance] 获取日本指数行情失败: {e}")
        return None

    def _get_kr_main_indices(self, yf) -> Optional[List[Dict[str, Any]]]:
        """Get main Korean index quotes (KOSPI, KOSDAQ), reusing _fetch_yf_ticker_data."""
        kr_indices = {
            'KS11': ('^KS11', 'KOSPI'),
            'KQ11': ('^KQ11', 'KOSDAQ'),
        }
        results = []
        try:
            for code, (yf_symbol, name) in kr_indices.items():
                try:
                    item = self._fetch_yf_ticker_data(yf, yf_symbol, name, code)
                    if item:
                        results.append(item)
                        logger.debug(f"[Yfinance] 获取韩国指数 {name} 成功")
                except Exception as e:
                    logger.warning(f"[Yfinance] 获取韩国指数 {name} 失败: {e}")
            if results:
                logger.info(f"[Yfinance] 成功获取 {len(results)} 个韩国指数行情")
                return results
        except Exception as e:
            logger.error(f"[Yfinance] 获取韩国指数行情失败: {e}")
        return None

    def _get_tw_main_indices(self, yf) -> Optional[List[Dict[str, Any]]]:
        """Get main Taiwan index quotes (TAIEX ^TWII, TPEx index ^TWOII), reusing _fetch_yf_ticker_data."""
        tw_indices = {
            'TWII': ('^TWII', '台湾加权指数'),
            'TWOII': ('^TWOII', '台湾柜买指数'),
        }
        results = []
        try:
            for code, (yf_symbol, name) in tw_indices.items():
                try:
                    item = self._fetch_yf_ticker_data(yf, yf_symbol, name, code)
                    if item:
                        results.append(item)
                        logger.debug(f"[Yfinance] 获取台湾指数 {name} 成功")
                except Exception as e:
                    logger.warning(f"[Yfinance] 获取台湾指数 {name} 失败: {e}")
            if results:
                logger.info(f"[Yfinance] 成功获取 {len(results)} 个台湾指数行情")
                return results
        except Exception as e:
            logger.error(f"[Yfinance] 获取台湾指数行情失败: {e}")
        return None

    def _is_us_stock(self, stock_code: str) -> bool:
        """
        Determine whether the code is a US stock (US indices excluded).

        Delegates to is_us_stock_code() in the us_index_mapping module.
        """
        return is_us_stock_code(stock_code)

    def _get_us_stock_quote_from_stooq(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Use Stooq as a key-free fallback for US real-time quotes.

        Stooq provides latest-trading-day quotes, less precise than intraday real-time APIs, but when Yahoo / yfinance
        is rate-limited it at least gives the Web UI a usable price; if the previous close is available, derived metrics such as change percent are also provided.
        """
        symbol = stock_code.strip().upper()
        stooq_symbol = f"{symbol.lower()}.us"
        url = f"https://stooq.com/q/l/?s={stooq_symbol}"
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; DSA/1.0; +https://github.com/ZhuLinsen/daily_stock_analysis)",
                "Accept": "text/plain,text/csv,*/*",
            },
        )

        try:
            with urlopen(request, timeout=15) as response:
                payload = response.read().decode("utf-8", "ignore").strip()
        except (HTTPError, URLError, TimeoutError) as exc:
            logger.warning(f"[Stooq] 获取美股 {symbol} 实时行情失败: {exc}")
            return None

        if not payload or payload.upper().startswith("NO DATA"):
            logger.warning(f"[Stooq] 无法获取 {symbol} 的行情数据")
            return None

        def _fetch_prev_close() -> Optional[float]:
            history_url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
            history_request = Request(
                history_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; DSA/1.0; +https://github.com/ZhuLinsen/daily_stock_analysis)",
                    "Accept": "text/plain,text/csv,*/*",
                },
            )
            try:
                with urlopen(history_request, timeout=15) as response:
                    history_payload = response.read().decode("utf-8", "ignore").strip()
            except (HTTPError, URLError, TimeoutError) as exc:
                logger.debug(f"[Stooq] 获取美股 {symbol} 日线历史失败: {exc}")
                return None

            if not history_payload or history_payload.upper().startswith("NO DATA"):
                return None

            try:
                reader = csv.reader(StringIO(history_payload))
                header = next(reader, None)
                if not header:
                    return None

                header_tokens = [cell.strip().lower() for cell in header]
                has_header = "close" in header_tokens and "date" in header_tokens
                if not has_header:
                    return None

                date_index = header_tokens.index("date")
                close_index = header_tokens.index("close")

                daily_rows: list[tuple[datetime, float]] = []
                for row in reader:
                    if not row:
                        continue
                    date_text = row[date_index].strip() if len(row) > date_index else ""
                    close_text = row[close_index].strip() if len(row) > close_index else ""
                    if not date_text or not close_text:
                        continue
                    try:
                        dt = datetime.strptime(date_text, "%Y-%m-%d")
                        close_val = float(close_text)
                    except Exception:
                        continue
                    daily_rows.append((dt, close_val))

                if len(daily_rows) < 2:
                    return None

                daily_rows.sort(key=lambda item: item[0])
                return daily_rows[-2][1]
            except Exception:
                return None

        try:
            reader = csv.reader(StringIO(payload))
            first_row = next(reader, None)
            if first_row is None:
                raise ValueError(f"unexpected Stooq payload: {payload}")

            normalized_first_row = [cell.strip() for cell in first_row]
            header_tokens = {cell.lower() for cell in normalized_first_row if cell}
            has_header = 'open' in header_tokens and 'close' in header_tokens
            row = next(reader, None) if has_header else first_row
            if row is None:
                raise ValueError(f"unexpected Stooq payload: {payload}")

            normalized_row = [cell.strip() for cell in row]
            while normalized_row and normalized_row[-1] == '':
                normalized_row.pop()

            if len(normalized_row) >= 8:
                open_index, high_index, low_index, price_index, volume_index = 3, 4, 5, 6, 7
            elif len(normalized_row) >= 7:
                open_index, high_index, low_index, price_index, volume_index = 2, 3, 4, 5, 6
            else:
                raise ValueError(f"unexpected Stooq payload: {payload}")

            open_price = float(normalized_row[open_index])
            high = float(normalized_row[high_index])
            low = float(normalized_row[low_index])
            price = float(normalized_row[price_index])
            volume = int(float(normalized_row[volume_index]))

            prev_close = _fetch_prev_close()
            change_amount = None
            change_pct = None
            amplitude = None
            if prev_close is not None and prev_close > 0:
                change_amount = price - prev_close
                change_pct = (change_amount / prev_close) * 100
                amplitude = ((high - low) / prev_close) * 100

            quote = UnifiedRealtimeQuote(
                code=symbol,
                name=STOCK_NAME_MAP.get(symbol, ''),
                source=RealtimeSource.STOOQ,
                price=price,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                change_amount=round(change_amount, 4) if change_amount is not None else None,
                volume=volume,
                amount=None,
                volume_ratio=None,
                turnover_rate=None,
                amplitude=round(amplitude, 2) if amplitude is not None else None,
                open_price=open_price,
                high=high,
                low=low,
                pre_close=prev_close,
                pe_ratio=None,
                pb_ratio=None,
                total_mv=None,
                circ_mv=None,
            )
            logger.info(f"[Stooq] 获取美股 {symbol} 兜底行情成功: 价格={price}")
            return quote
        except Exception as exc:
            logger.warning(f"[Stooq] 解析美股 {symbol} 行情失败: {exc}")
            return None

    def _get_us_index_realtime_quote(
        self,
        user_code: str,
        yf_symbol: str,
        index_name: str,
    ) -> Optional[UnifiedRealtimeQuote]:
        """
        Get realtime quote for US index (e.g. SPX -> ^GSPC).

        Args:
            user_code: User input code (e.g. SPX)
            yf_symbol: Yahoo Finance symbol (e.g. ^GSPC)
            index_name: Chinese name for the index

        Returns:
            UnifiedRealtimeQuote or None
        """
        import yfinance as yf

        try:
            logger.debug(f"[Yfinance] 获取美股指数 {user_code} ({yf_symbol}) 实时行情")
            ticker = yf.Ticker(yf_symbol)

            try:
                info = ticker.fast_info
                if info is None:
                    raise ValueError("fast_info is None")
                price = getattr(info, 'lastPrice', None) or getattr(info, 'last_price', None)
                prev_close = getattr(info, 'previousClose', None) or getattr(info, 'previous_close', None)
                open_price = getattr(info, 'open', None)
                high = getattr(info, 'dayHigh', None) or getattr(info, 'day_high', None)
                low = getattr(info, 'dayLow', None) or getattr(info, 'day_low', None)
                volume = getattr(info, 'lastVolume', None) or getattr(info, 'last_volume', None)
            except Exception:
                logger.debug("[Yfinance] fast_info 失败，尝试 history 方法")
                hist = ticker.history(period='2d')
                if hist.empty:
                    logger.warning(f"[Yfinance] 无法获取 {yf_symbol} 的数据")
                    return None
                today = hist.iloc[-1]
                prev = hist.iloc[-2] if len(hist) > 1 else today
                price = float(today['Close'])
                prev_close = float(prev['Close'])
                open_price = float(today['Open'])
                high = float(today['High'])
                low = float(today['Low'])
                volume = int(today['Volume'])

            change_amount = None
            change_pct = None
            if price is not None and prev_close is not None and prev_close > 0:
                change_amount = price - prev_close
                change_pct = (change_amount / prev_close) * 100

            amplitude = None
            if high is not None and low is not None and prev_close is not None and prev_close > 0:
                amplitude = ((high - low) / prev_close) * 100

            try:
                ticker_info = ticker.info or {}
            except Exception:
                ticker_info = {}
            missing_fields = [
                field
                for field, value in {
                    "price": price,
                    "prev_close": prev_close,
                    "volume": volume,
                    "amount": None,
                    "pe_ratio": None,
                    "pb_ratio": None,
                }.items()
                if value is None
            ]

            quote = UnifiedRealtimeQuote(
                code=user_code,
                name=index_name or user_code,
                source=RealtimeSource.FALLBACK,
                market="us",
                currency=str(ticker_info.get("currency") or "").upper() or None,
                data_quality="partial" if missing_fields else "ok",
                missing_fields=missing_fields or None,
                price=price,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                change_amount=round(change_amount, 4) if change_amount is not None else None,
                volume=volume,
                amount=None,
                volume_ratio=None,
                turnover_rate=None,
                amplitude=round(amplitude, 2) if amplitude is not None else None,
                open_price=open_price,
                high=high,
                low=low,
                pre_close=prev_close,
                pe_ratio=None,
                pb_ratio=None,
                total_mv=None,
                circ_mv=None,
            )
            logger.info(f"[Yfinance] 获取美股指数 {user_code} 实时行情成功: 价格={price}")
            return quote
        except Exception as e:
            logger.warning(f"[Yfinance] 获取美股指数 {user_code} 实时行情失败: {e}")
            return None

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        Get real-time quotes for US, HK, JP/KR/TW stocks or US indices

        Supports US stocks (AAPL, TSLA) and US indices (SPX, DJI, etc.).
        Data source: yfinance Ticker.info

        Args:
            stock_code: US stock or index code, e.g. 'AMD', 'AAPL', 'SPX', 'DJI'

        Returns:
            UnifiedRealtimeQuote object, or None on failure
        """
        import yfinance as yf

        # US indices: use the mapping (SPX -> ^GSPC)
        yf_symbol, index_name = get_us_index_yf_symbol(stock_code)
        if yf_symbol:
            return self._get_us_index_realtime_quote(
                user_code=stock_code.strip().upper(),
                yf_symbol=yf_symbol,
                index_name=index_name,
            )

        # Only handle US, HK or JP/KR/TW suffix-only stocks
        if not (
            self._is_us_stock(stock_code)
            or _is_hk_market(stock_code)
            or self._is_jp_kr_suffix_stock(stock_code)
            or self._is_tw_suffix_stock(stock_code)
        ):
            logger.debug(f"[Yfinance] {stock_code} 不是支持的美股、港股或日韩台代码，跳过")
            return None

        try:
            symbol = self._convert_stock_code(stock_code)
            is_us_symbol = self._is_us_stock(symbol)
            suffix_market = get_suffix_market(symbol)
            logger.debug(f"[Yfinance] 获取 {symbol} 实时行情")

            ticker = yf.Ticker(symbol)

            # Try fast_info first (faster, but fewer fields)
            try:
                info = ticker.fast_info
                if info is None:
                    raise ValueError("fast_info is None")

                price = getattr(info, 'lastPrice', None) or getattr(info, 'last_price', None)
                prev_close = getattr(info, 'previousClose', None) or getattr(info, 'previous_close', None)
                open_price = getattr(info, 'open', None)
                high = getattr(info, 'dayHigh', None) or getattr(info, 'day_high', None)
                low = getattr(info, 'dayLow', None) or getattr(info, 'day_low', None)
                volume = getattr(info, 'lastVolume', None) or getattr(info, 'last_volume', None)
                market_cap = getattr(info, 'marketCap', None) or getattr(info, 'market_cap', None)

            except Exception:
                # Fall back to the history method for the latest data
                logger.debug("[Yfinance] fast_info 失败，尝试 history 方法")
                hist = ticker.history(period='2d')
                if hist.empty:
                    if is_us_symbol:
                        logger.warning(f"[Yfinance] 无法获取 {symbol} 的数据，尝试 Stooq 兜底")
                        return self._get_us_stock_quote_from_stooq(symbol)
                    logger.warning(f"[Yfinance] 无法获取 {symbol} 的数据")
                    return None

                today = hist.iloc[-1]
                prev = hist.iloc[-2] if len(hist) > 1 else today

                price = float(today['Close'])
                prev_close = float(prev['Close'])
                open_price = float(today['Open'])
                high = float(today['High'])
                low = float(today['Low'])
                volume = int(today['Volume'])
                market_cap = None

            # Compute change percent
            change_amount = None
            change_pct = None
            if price is not None and prev_close is not None and prev_close > 0:
                change_amount = price - prev_close
                change_pct = (change_amount / prev_close) * 100

            # Compute amplitude
            amplitude = None
            if high is not None and low is not None and prev_close is not None and prev_close > 0:
                amplitude = ((high - low) / prev_close) * 100

            # Get the stock name and provider metadata
            try:
                ticker_info = ticker.info or {}
            except Exception:
                ticker_info = {}
            try:
                info_name = ticker_info.get('shortName', '') or ticker_info.get('longName', '') or ''
                name = info_name if is_meaningful_stock_name(info_name, symbol) else STOCK_NAME_MAP.get(symbol, '')
            except Exception:
                name = STOCK_NAME_MAP.get(symbol, '')

            # Reuse the ticker_info fetched above, no extra request
            pe_ratio = _safe_float(ticker_info.get('trailingPE'))
            pb_ratio = _safe_float(ticker_info.get('priceToBook'))

            missing_fields = [
                field
                for field, value in {
                    "price": price,
                    "prev_close": prev_close,
                    "volume": volume,
                    "amount": None,
                    "pe_ratio": pe_ratio,
                    "pb_ratio": pb_ratio,
                }.items()
                if value is None
            ]
            quote = UnifiedRealtimeQuote(
                code=symbol,
                name=name,
                source=RealtimeSource.FALLBACK,
                market=suffix_market or ("hk" if _is_hk_market(stock_code) else "us" if is_us_symbol else None),
                currency=str(ticker_info.get("currency") or "").upper() or None,
                data_quality="partial" if missing_fields else "ok",
                missing_fields=missing_fields or None,
                price=price,
                change_pct=round(change_pct, 2) if change_pct is not None else None,
                change_amount=round(change_amount, 4) if change_amount is not None else None,
                volume=volume,
                amount=None,  # yfinance does not provide turnover amount directly
                volume_ratio=None,
                turnover_rate=None,
                amplitude=round(amplitude, 2) if amplitude is not None else None,
                open_price=open_price,
                high=high,
                low=low,
                pre_close=prev_close,
                pe_ratio=pe_ratio,
                pb_ratio=pb_ratio,
                total_mv=market_cap,
                circ_mv=None,
            )

            logger.info(f"[Yfinance] 获取 {symbol} 实时行情成功: 价格={price}")
            return quote

        except Exception as e:
            if self._is_us_stock(stock_code):
                logger.warning(f"[Yfinance] 获取美股 {stock_code} 实时行情失败: {e}，尝试 Stooq 兜底")
                return self._get_us_stock_quote_from_stooq(stock_code)
            logger.warning(f"[Yfinance] 获取 {stock_code} 实时行情失败: {e}")
            return None


if __name__ == "__main__":
    # Test code
    logging.basicConfig(level=logging.DEBUG)

    fetcher = YfinanceFetcher()

    try:
        df = fetcher.get_daily_data('600519')  # Kweichow Moutai
        print(f"获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"获取失败: {e}")
