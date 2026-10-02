# -*- coding: utf-8 -*-
"""
===================================
US index and stock code utilities
===================================

Provides:
1. US index code mapping (e.g. SPX -> ^GSPC)
2. US stock code recognition (AAPL, TSLA, etc.)

US indices need a ^ prefix in Yahoo Finance, unlike stock codes.
"""

import re

# US stock code regex: 1-5 uppercase letters, optional .X suffix (e.g. BRK.B)
_US_STOCK_PATTERN = re.compile(r'^[A-Z]{1,5}(\.[A-Z])?$')


# User input -> (Yahoo Finance symbol, Chinese name)
US_INDEX_MAPPING = {
    # S&P 500
    'SPX': ('^GSPC', '标普500指数'),
    '^GSPC': ('^GSPC', '标普500指数'),
    'GSPC': ('^GSPC', '标普500指数'),
    # Dow Jones Industrial Average
    'DJI': ('^DJI', '道琼斯工业指数'),
    '^DJI': ('^DJI', '道琼斯工业指数'),
    'DJIA': ('^DJI', '道琼斯工业指数'),
    # Nasdaq Composite
    'IXIC': ('^IXIC', '纳斯达克综合指数'),
    '^IXIC': ('^IXIC', '纳斯达克综合指数'),
    'NASDAQ': ('^IXIC', '纳斯达克综合指数'),
    # Nasdaq 100
    'NDX': ('^NDX', '纳斯达克100指数'),
    '^NDX': ('^NDX', '纳斯达克100指数'),
    # VIX volatility index
    'VIX': ('^VIX', 'VIX恐慌指数'),
    '^VIX': ('^VIX', 'VIX恐慌指数'),
    # Russell 2000
    'RUT': ('^RUT', '罗素2000指数'),
    '^RUT': ('^RUT', '罗素2000指数'),
}


def is_us_index_code(code: str) -> bool:
    """
    Determine whether the code is a US index symbol.

    Args:
        code: stock/index code, e.g. 'SPX', 'DJI'

    Returns:
        True if it is a known US index symbol, otherwise False

    Examples:
        >>> is_us_index_code('SPX')
        True
        >>> is_us_index_code('AAPL')
        False
    """
    return (code or '').strip().upper() in US_INDEX_MAPPING


def is_us_stock_code(code: str) -> bool:
    """
    Determine whether the code is a US stock symbol (US indices excluded).

    US stock codes are 1-5 uppercase letters, with an optional .X suffix such as BRK.B.
    US indices (SPX, DJI, etc.) are explicitly excluded.

    Args:
        code: stock code, e.g. 'AAPL', 'TSLA', 'BRK.B'

    Returns:
        True if it is a US stock symbol, otherwise False

    Examples:
        >>> is_us_stock_code('AAPL')
        True
        >>> is_us_stock_code('TSLA')
        True
        >>> is_us_stock_code('BRK.B')
        True
        >>> is_us_stock_code('SPX')
        False
        >>> is_us_stock_code('600519')
        False
    """
    normalized = (code or '').strip().upper()
    # A US index is not a stock
    if normalized in US_INDEX_MAPPING:
        return False
    return bool(_US_STOCK_PATTERN.match(normalized))


def get_us_index_yf_symbol(code: str) -> tuple:
    """
    Get the Yahoo Finance symbol and Chinese name for a US index.

    Args:
        code: user input, e.g. 'SPX', '^GSPC', 'DJI'

    Returns:
        A (yf_symbol, chinese_name) tuple; (None, None) if not found.

    Examples:
        >>> get_us_index_yf_symbol('SPX')[0]  # second item is the Chinese name of the S&P 500
        '^GSPC'
        >>> get_us_index_yf_symbol('AAPL')
        (None, None)
    """
    normalized = (code or '').strip().upper()
    return US_INDEX_MAPPING.get(normalized, (None, None))
