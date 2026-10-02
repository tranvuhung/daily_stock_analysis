# -*- coding: utf-8 -*-
"""
===================================
BaostockFetcher - backup data source 2 (Priority 3)
===================================

Data source: Baostock
Traits: free, no token required, needs login session management
Pros: stable, no quota limits

Key strategies:
1. Manage the bs.login() / bs.logout() lifecycle
2. Use a context manager to prevent connection leaks
3. Exponential backoff retry on failure
"""

import logging
import re
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Generator

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
    is_bse_code,
    normalize_stock_code,
    _is_hk_market,
)
import os

logger = logging.getLogger(__name__)


def _is_us_code(stock_code: str) -> bool:
    """
    Check whether the code is a US stock.

    US stock code rules:
    - 1-5 uppercase letters, e.g. 'AAPL', 'TSLA'
    - May contain '.', e.g. 'BRK.B'
    """
    code = stock_code.strip().upper()
    return bool(re.match(r'^[A-Z]{1,5}(\.[A-Z])?$', code))


class BaostockFetcher(BaseFetcher):
    """
    Baostock data source implementation.

    Priority: 3
    Data source: Baostock API

    Key strategies:
    - Use a context manager to manage the connection lifecycle
    - Log in/out on every request to prevent connection leaks
    - Exponential backoff retry on failure

    Baostock traits:
    - Free, no registration required
    - Requires explicit login/logout
    - Data updates are slightly delayed (T+1)
    """
    
    name = "BaostockFetcher"
    priority = int(os.getenv("BAOSTOCK_PRIORITY", "3"))
    
    def __init__(self):
        """Initialize BaostockFetcher."""
        self._bs_module = None
    
    def _get_baostock(self):
        """
        Lazily load the baostock module.

        Imported only on first use, to avoid errors when it is not installed.
        """
        if self._bs_module is None:
            import baostock as bs
            self._bs_module = bs
        return self._bs_module
    
    @contextmanager
    def _baostock_session(self) -> Generator:
        """
        Baostock connection context manager.

        Guarantees:
        1. Automatic login on entering the context
        2. Automatic logout on exiting the context
        3. Proper logout even when an exception occurs

        Usage example:
            with self._baostock_session():
                # run data queries here
        """
        bs = self._get_baostock()
        login_result = None
        
        try:
            # Log in to Baostock
            login_result = bs.login()
            
            if login_result.error_code != '0':
                raise DataFetchError(f"Baostock 登录失败: {login_result.error_msg}")
            
            logger.debug("Baostock 登录成功")
            
            yield bs
            
        finally:
            # Always log out to prevent connection leaks
            try:
                logout_result = bs.logout()
                if logout_result.error_code == '0':
                    logger.debug("Baostock 登出成功")
                else:
                    logger.warning(f"Baostock 登出异常: {logout_result.error_msg}")
            except Exception as e:
                logger.warning(f"Baostock 登出时发生错误: {e}")
    
    def _convert_stock_code(self, stock_code: str) -> str:
        """
        Convert a stock code to Baostock format.

        Format required by Baostock:
        - Shanghai: sh.600519
        - Shenzhen: sz.000001

        Args:
            stock_code: Raw code, e.g. '600519', '000001'

        Returns:
            Baostock-format code, e.g. 'sh.600519', 'sz.000001'
        """
        raw_code = stock_code.strip()
        upper = raw_code.upper()

        # HK stocks are not supported by Baostock
        if _is_hk_market(raw_code):
            raise DataFetchError(f"BaostockFetcher 不支持港股 {raw_code}，请使用 AkshareFetcher")

        # Keep internal tolerance for existing lowercase baostock-format input, but 6-digit bare codes are still recommended in user config.
        if raw_code.startswith(('sh.', 'sz.')):
            return raw_code.lower()

        exchange_hint = None
        if upper.startswith(('SH', 'SS')) or upper.endswith(('.SH', '.SS')):
            exchange_hint = 'sh'
        elif upper.startswith('SZ') or upper.endswith('.SZ'):
            exchange_hint = 'sz'

        code = normalize_stock_code(raw_code)

        if exchange_hint in ('sh', 'sz') and code.isdigit() and len(code) == 6:
            return f"{exchange_hint}.{code}"
        
        # ETF: Shanghai ETF (51xx, 52xx, 56xx, 58xx) -> sh; Shenzhen ETF (15xx, 16xx, 18xx) -> sz
        if len(code) == 6:
            if code.startswith(('51', '52', '56', '58')):
                return f"sh.{code}"
            if code.startswith(('15', '16', '18')):
                return f"sz.{code}"

        # Determine the market from the code prefix
        if code.startswith(('600', '601', '603', '605', '688')):
            return f"sh.{code}"
        elif code.startswith(('000', '001', '002', '003', '300', '301')):
            return f"sz.{code}"
        else:
            logger.warning(f"无法确定股票 {code} 的市场，默认使用深市")
            return f"sz.{code}"
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch raw data from Baostock.

        Uses query_history_k_data_plus() to fetch daily bars.

        Flow:
        1. Check whether it is a US stock (unsupported)
        2. Manage the connection with a context manager
        3. Convert the stock code format
        4. Call the API to query data
        5. Convert the result into a DataFrame
        """
        # US stocks unsupported; raise so DataFetcherManager switches to another source
        if _is_us_code(stock_code):
            raise DataFetchError(f"BaostockFetcher 不支持美股 {stock_code}，请使用 AkshareFetcher 或 YfinanceFetcher")

        # HK stocks unsupported; raise so DataFetcherManager switches to another source
        if _is_hk_market(stock_code):
            raise DataFetchError(f"BaostockFetcher 不支持港股 {stock_code}，请使用 AkshareFetcher")

        # Beijing Stock Exchange unsupported; raise so DataFetcherManager switches to another source
        if is_bse_code(stock_code):
            raise DataFetchError(
                f"BaostockFetcher 不支持北交所 {stock_code}，将自动切换其他数据源"
            )
        
        # Convert code format
        bs_code = self._convert_stock_code(stock_code)
        
        logger.debug(f"调用 Baostock query_history_k_data_plus({bs_code}, {start_date}, {end_date})")
        
        with self._baostock_session() as bs:
            try:
                # Query daily bars
                # adjustflag: 1 = backward-adjusted, 2 = forward-adjusted, 3 = unadjusted
                rs = bs.query_history_k_data_plus(
                    code=bs_code,
                    fields="date,open,high,low,close,volume,amount,pctChg",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",  # daily
                    adjustflag="2"  # forward-adjusted
                )
                
                if rs.error_code != '0':
                    raise DataFetchError(f"Baostock 查询失败: {rs.error_msg}")
                
                # Convert to DataFrame
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())
                
                if not data_list:
                    raise DataFetchError(f"Baostock 未查询到 {stock_code} 的数据")
                
                df = pd.DataFrame(data_list, columns=rs.fields)
                
                return df
                
            except Exception as e:
                if isinstance(e, DataFetchError):
                    raise
                raise DataFetchError(f"Baostock 获取数据失败: {e}") from e
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        Normalize Baostock data.

        Columns returned by Baostock:
        date, open, high, low, close, volume, amount, pctChg

        Must be mapped to the standard columns:
        date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()
        
        # Column mapping (only pctChg needs handling)
        column_mapping = {
            'pctChg': 'pct_chg',
        }
        
        df = df.rename(columns=column_mapping)
        
        # Numeric type conversion (Baostock returns everything as strings)
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Add stock code column
        df['code'] = stock_code
        
        # Keep only the required columns
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]
        
        return df

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """
        Get the stock name.

        Uses Baostock's query_stock_basic API to fetch basic stock info.

        Args:
            stock_code: Stock code

        Returns:
            Stock name, or None on failure
        """
        # Check cache
        if hasattr(self, '_stock_name_cache') and stock_code in self._stock_name_cache:
            return self._stock_name_cache[stock_code]
        
        # Initialize cache
        if not hasattr(self, '_stock_name_cache'):
            self._stock_name_cache = {}
        
        try:
            bs_code = self._convert_stock_code(stock_code)
            
            with self._baostock_session() as bs:
                # Query basic stock info
                rs = bs.query_stock_basic(code=bs_code)
                
                if rs.error_code == '0':
                    data_list = []
                    while rs.next():
                        data_list.append(rs.get_row_data())
                    
                    if data_list:
                        # Fields returned by Baostock: code, code_name, ipoDate, outDate, type, status
                        fields = rs.fields
                        name_idx = fields.index('code_name') if 'code_name' in fields else None
                        if name_idx is not None and len(data_list[0]) > name_idx:
                            name = data_list[0][name_idx]
                            self._stock_name_cache[stock_code] = name
                            logger.debug(f"Baostock 获取股票名称成功: {stock_code} -> {name}")
                            return name
                
        except Exception as e:
            logger.warning(f"Baostock 获取股票名称失败 {stock_code}: {e}")
        
        return None
    
    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """
        Get the stock list.

        Uses Baostock's query_stock_basic API to fetch the full stock list.

        Returns:
            DataFrame with code and name columns, or None on failure
        """
        try:
            with self._baostock_session() as bs:
                # Query basic info for all stocks
                rs = bs.query_stock_basic()
                
                if rs.error_code == '0':
                    data_list = []
                    while rs.next():
                        data_list.append(rs.get_row_data())
                    
                    if data_list:
                        df = pd.DataFrame(data_list, columns=rs.fields)
                        
                        # Convert code format (strip the sh. or sz. prefix)
                        df['code'] = df['code'].apply(lambda x: x.split('.')[1] if '.' in x else x)
                        df = df.rename(columns={'code_name': 'name'})
                        
                        # Update cache
                        if not hasattr(self, '_stock_name_cache'):
                            self._stock_name_cache = {}
                        for _, row in df.iterrows():
                            self._stock_name_cache[row['code']] = row['name']
                        
                        logger.info(f"Baostock 获取股票列表成功: {len(df)} 条")
                        return df[['code', 'name']]
                
        except Exception as e:
            logger.warning(f"Baostock 获取股票列表失败: {e}")
        
        return None


if __name__ == "__main__":
    # Test code
    logging.basicConfig(level=logging.DEBUG)
    
    fetcher = BaostockFetcher()
    
    try:
        # Test historical data
        df = fetcher.get_daily_data('600519')  # Kweichow Moutai
        print(f"获取成功，共 {len(df)} 条数据")
        print(df.tail())
        
        # Test stock name
        name = fetcher.get_stock_name('600519')
        print(f"股票名称: {name}")
        
    except Exception as e:
        print(f"获取失败: {e}")
