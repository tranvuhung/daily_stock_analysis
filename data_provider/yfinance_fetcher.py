# -*- coding: utf-8 -*-
"""
===================================
YfinanceFetcher - nguồn dự phòng nến ngày cho Việt Nam (Priority 4)
===================================

Nguồn: Yahoo Finance (qua thư viện yfinance)
Vai trò: tuyến cuối khi VCI / SSI / vnstock đều hỏng. Chỉ phục vụ nến ngày của
cổ phiếu sàn HOSE (``HPG.VN``); HNX/UPCoM và chỉ số Việt Nam không có trên Yahoo.
Nến từ Yahoo không được ghi vào bảng ``stock_daily`` (xem
``_NON_PERSISTABLE_DAILY_SOURCES`` trong ``base.py``) vì lệch cơ sở điều chỉnh giá.

Yahoo Finance daily fallback for Vietnam (HOSE only, never persisted).
"""

import logging
import os

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
    _is_vn_market,
    is_vn_index_code,
    normalize_stock_code,
)

logger = logging.getLogger(__name__)


class YfinanceFetcher(BaseFetcher):
    """
    Nguồn dữ liệu Yahoo Finance (chỉ nến ngày cổ phiếu Việt Nam sàn HOSE).

    Lưu ý:
    - Không phân biệt được HOSE/HNX/UPCoM từ hình dạng mã; mã ngoài HOSE trả
      DataFrame rỗng -> DataFetchError -> manager coi như một nguồn hỏng bình thường.
    - Chỉ số Việt Nam bị từ chối thẳng, không phát request.
    """

    name = "YfinanceFetcher"
    priority = int(os.getenv("YFINANCE_PRIORITY", "4"))

    def __init__(self):
        """Initialize YfinanceFetcher"""
        pass

    def _convert_stock_code(self, stock_code: str) -> str:
        """
        Chuyển mã Việt Nam sang định dạng Yahoo Finance (``HPG`` -> ``HPG.VN``).

        Convert a Vietnam stock code to Yahoo Finance format.

        Raises:
            DataFetchError: mã không phải Việt Nam hoặc là chỉ số Việt Nam.
        """
        code = stock_code.strip().upper()

        if not _is_vn_market(stock_code):
            raise DataFetchError(
                f"[{self.name}] {stock_code}: không phải mã chứng khoán Việt Nam"
            )

        # Chỉ số Việt Nam bị từ chối thẳng, không phát request: ``^VNINDEX.VN``
        # trả về đúng 1 nến rác khi xin cả năm, còn ``^VN30.VN`` /
        # ``^HNXINDEX.VN`` / ``^UPCOM.VN`` thì rỗng hoàn toàn.
        if is_vn_index_code(code):
            raise DataFetchError(
                f"[{self.name}] {stock_code}: Yahoo không có lịch sử chỉ số "
                "Việt Nam, chỉ số phải do VCI phục vụ"
            )

        # normalize_stock_code() rút mọi cách viết (``HPG.VN``/``vnHPG``) về mã
        # trần ``HPG``, tránh nối thành ``HPG.VN.VN``.
        vn_code = normalize_stock_code(stock_code)
        logger.debug(f"Nhận diện mã Việt Nam: {stock_code} -> {vn_code}.VN")
        return f"{vn_code}.VN"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Lấy dữ liệu thô từ Yahoo Finance qua ``yfinance.download()``.

        Fetch raw daily bars from Yahoo Finance.
        """
        import yfinance as yf

        yf_code = self._convert_stock_code(stock_code)

        logger.debug(f"Gọi yfinance.download({yf_code}, {start_date}, {end_date})")

        try:
            df = yf.download(
                tickers=yf_code,
                start=start_date,
                end=end_date,
                progress=False,  # Tắt thanh tiến trình
                auto_adjust=True,  # Tự điều chỉnh giá theo quyền
                multi_level_index=True
            )

            # Chỉ lấy cột của yf_code để tránh trộn dữ liệu nhiều mã
            if isinstance(df.columns, pd.MultiIndex) and len(df.columns) > 1:
                ticker_level = df.columns.get_level_values(1)
                mask = ticker_level == yf_code
                if mask.any():
                    df = df.loc[:, mask].copy()

            if df.empty:
                raise DataFetchError(f"Yahoo Finance không có dữ liệu cho {stock_code}")

            return df

        except Exception as e:
            if isinstance(e, DataFetchError):
                raise
            raise DataFetchError(f"Yahoo Finance lấy dữ liệu thất bại: {e}") from e

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        Chuẩn hoá dữ liệu Yahoo Finance về bộ cột chuẩn.

        yfinance trả Open/High/Low/Close/Volume (chỉ mục là ngày); bản mới trả cột
        MultiIndex dạng ('Close', 'HPG.VN') nên phải làm phẳng trước.
        """
        df = df.copy()

        # Làm phẳng MultiIndex (định dạng của yfinance bản mới)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Đưa chỉ mục ngày thành cột
        df = df.reset_index()

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

        # Tính % thay đổi (yfinance không cung cấp trực tiếp)
        if 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100
            df['pct_chg'] = df['pct_chg'].fillna(0).round(2)

        # Giá trị giao dịch ước lượng ≈ khối lượng * giá đóng cửa
        if 'volume' in df.columns and 'close' in df.columns:
            df['amount'] = df['volume'] * df['close']
        else:
            df['amount'] = 0

        df['code'] = stock_code

        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]

        return df
