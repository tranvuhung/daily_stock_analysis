# -*- coding: utf-8 -*-
"""
===================================
Nguồn dữ liệu vnstock - thị trường Việt Nam (tuỳ chọn, phải bật tường minh)
===================================

``vnstock`` là thư viện Python có độ phủ thị trường Việt Nam rộng nhất, nhưng nó
**không phải giấy phép mã nguồn mở**:

- License: ``Custom (Personal Use)``, xem https://vnstocks.com/onboard/giay-phep-su-dung
- Phụ thuộc ``vnai``, có gửi dữ liệu sử dụng về phía tác giả

Repo này dùng giấy phép MIT và được triển khai dưới dạng dịch vụ, nên ``vnstock``
**không nằm trong ``requirements.txt``**, chỉ đặt ở ``requirements-vn-optional.txt``.
Nguồn này chỉ được bật khi thoả **đồng thời** hai điều kiện:

1. Biến môi trường ``VNSTOCK_ENABLED=true``
2. Môi trường chạy đã cài sẵn ``vnstock``

Chuỗi nguồn mặc định ``VciFetcher`` → ``SsiFetcher`` không phụ thuộc module này;
đây chỉ là nguồn bổ sung ở vị trí thứ ba.
"""

import logging
import os
from typing import Optional

import pandas as pd

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS

logger = logging.getLogger(__name__)

# Import ở cấp module để _init_default_fetchers có thể dùng ImportError làm tín
# hiệu nhận biết nguồn này có khả dụng hay không
from vnstock import Quote  # noqa: E402

_DEFAULT_SOURCE = os.getenv("VNSTOCK_SOURCE", "VCI").strip() or "VCI"

# vnstock trả giá theo đơn vị nghìn đồng, còn repo này thống nhất dùng đồng.
_PRICE_SCALE_TO_VND = 1000


class VnstockFetcher(BaseFetcher):
    """Nguồn nến ngày Việt Nam dựa trên ``vnstock`` (nguồn bổ sung, tuỳ chọn)."""

    name = "VnstockFetcher"
    priority = int(os.getenv("VNSTOCK_PRIORITY", "2"))

    def __init__(self, source: Optional[str] = None) -> None:
        self.source = (source or _DEFAULT_SOURCE).strip() or "VCI"

    @staticmethod
    def to_provider_symbol(stock_code: str) -> str:
        normalized = (stock_code or "").strip().upper()
        if normalized.endswith(".VN"):
            normalized = normalized[:-3]
        return normalized

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        symbol = self.to_provider_symbol(stock_code)
        try:
            quote = Quote(symbol=symbol, source=self.source)
            df = quote.history(start=start_date, end=end_date, interval="1D")
        except Exception as exc:
            raise DataFetchError(f"[{self.name}] {symbol}: {exc}") from exc

        if df is None:
            return pd.DataFrame()
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        frame = df.copy()
        frame.columns = [str(col).strip().lower() for col in frame.columns]

        # Mỗi source của vnstock trả về tên cột ngày khác nhau
        # (``time`` / ``date`` / hoặc nằm ở index)
        date_column = next(
            (name for name in ("time", "date", "tradingdate") if name in frame.columns),
            None,
        )
        out = pd.DataFrame()
        if date_column is not None:
            out["date"] = pd.to_datetime(frame[date_column], errors="coerce")
        else:
            out["date"] = pd.to_datetime(frame.index, errors="coerce")

        # ⚠️ vnstock trả giá theo **nghìn đồng** (HPG báo 21.20), lệch đúng 1000 lần
        # so với đơn vị "đồng" của VciFetcher và snapshot SSI. Đã kiểm chứng thực tế
        # (vnstock 4.0.8 + source VCI). Không quy đổi thì đường trung bình, giá cắt
        # lỗ và % thay đổi đều lệch ba bậc độ lớn một cách âm thầm, không báo lỗi.
        for source_name in ("open", "high", "low", "close"):
            out[source_name] = (
                pd.to_numeric(frame.get(source_name), errors="coerce")
                * _PRICE_SCALE_TO_VND
            )
        out["volume"] = pd.to_numeric(frame.get("volume"), errors="coerce")

        # Nến ngày của vnstock không có giá trị giao dịch, lấy giá trung bình ×
        # khối lượng để xấp xỉ, thống nhất cách làm với SsiFetcher
        out["amount"] = ((out["high"] + out["low"]) / 2) * out["volume"]
        out["pct_chg"] = (out["close"].pct_change() * 100).round(2)
        return out[STANDARD_COLUMNS]
