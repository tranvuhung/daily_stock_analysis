# -*- coding: utf-8 -*-
"""Phân tích giá trị STOCK_LIST và mục tiêu phân tích (chỉ thị trường Việt Nam).

Module cung cấp :func:`parse_analysis_target`, chuyển một token người dùng nhập
(``"HPG"``, ``"hpg"``, ``"HPG.VN"``, ``"vnHPG"``, ``"FUEVN100"``, ``"VNINDEX"`` …)
thành :class:`AnalysisTarget` có ``asset_type`` / ``canonical_id`` /
``display_code`` / ``exchange`` ổn định.

- Mã cổ phiếu 3 ký tự, chứng chỉ quỹ và chỉ số Việt Nam đều trả ``asset_type=stock``:
  chỉ số VN đi qua chuỗi nến ngày thông thường để tới ``VciFetcher``.
- Mọi token khác trả ``asset_type=unsupported`` kèm lý do, không phát request nào.

Parse STOCK_LIST tokens into structured analysis targets (Vietnam only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

__all__ = [
    "AnalysisTarget",
    "ParseStatus",
    "parse_analysis_target",
    "parse_stock_list",
    "serialize_stock_list",
    "split_stock_list",
]


_STOCK_LIST_SEPARATOR_RE = re.compile(r"[\s,;，、；]+")


def split_stock_list(value: str) -> List[str]:
    """Split STOCK_LIST values on common copy/paste separators."""
    return [
        item.strip()
        for item in _STOCK_LIST_SEPARATOR_RE.split(value or "")
        if item.strip()
    ]


def serialize_stock_list(value: str) -> str:
    """Return STOCK_LIST in the canonical comma-separated storage form."""
    return ",".join(split_stock_list(value))


class ParseStatus:
    """Loại mục tiêu phân tích, mã hoá bằng chuỗi để serialize JSON gọn.

    ``INDEX`` được giữ làm hằng số vì có thể còn xuất hiện trong dữ liệu lịch sử /
    payload API cũ; parser hiện không còn trả giá trị này.
    """

    STOCK = "stock"
    INDEX = "index"
    UNSUPPORTED = "unsupported"


# Chỉ số Việt Nam. API của VCI phân biệt hoa/thường nên lưu đúng cách viết provider yêu cầu.
# Vietnamese indices, stored with the exact spelling the provider expects.
_VN_INDEX_CANONICAL = {
    "VNINDEX": "VNINDEX",
    "VN30": "VN30",
    "VN100": "VN100",
    "HNXINDEX": "HNXIndex",
    "HNX30": "HNX30",
    "HNXUPCOMINDEX": "HNXUpcomIndex",
    "UPCOMINDEX": "HNXUpcomIndex",
    "UPCOM": "HNXUpcomIndex",
}

_UNSUPPORTED_REASON = "not a Vietnam stock, fund or index code"


@dataclass(frozen=True)
class AnalysisTarget:
    """Kết quả có cấu trúc của :func:`parse_analysis_target`."""

    raw_input: str
    asset_type: str  # một trong ParseStatus.*
    canonical_id: str
    display_code: str
    exchange: str  # 'VN' / 'UNKNOWN'
    unsupported_reason: Optional[str] = None
    # Dấu vết chẩn đoán, không thuộc hợp đồng công khai.
    normalized_prefix: Optional[str] = None
    normalized_code: Optional[str] = None


def _unsupported(raw_input: str, reason: str) -> AnalysisTarget:
    return AnalysisTarget(
        raw_input=raw_input,
        asset_type=ParseStatus.UNSUPPORTED,
        canonical_id="",
        display_code="",
        exchange="UNKNOWN",
        unsupported_reason=reason,
    )


def _resolve_vn_code(token: str) -> Optional[str]:
    """Quy một token về mã Việt Nam dạng trần; ``None`` nếu không phải mã VN."""

    from data_provider.base import is_vn_fund_code, is_vn_security_code

    upper = token.upper()
    if upper.endswith(".VN") and is_vn_security_code(upper[:-3]):
        return upper[:-3]
    # Tiền tố ``vn`` viết thường (``vnHPG``); ``VN30``/``VNINDEX`` là chỉ số nên không cắt.
    if (
        len(token) > 2
        and token[:2] == "vn"
        and upper not in _VN_INDEX_CANONICAL
        and is_vn_security_code(upper[2:])
    ):
        return upper[2:]
    # ``VN`` + mã quỹ (kể cả viết hoa) luôn là Việt Nam.
    if upper.startswith("VN") and is_vn_fund_code(upper[2:]):
        return upper[2:]
    if upper in _VN_INDEX_CANONICAL:
        return _VN_INDEX_CANONICAL[upper]
    if is_vn_security_code(upper):
        return upper
    return None


def parse_analysis_target(raw_input: str) -> AnalysisTarget:
    """Phân tích một token thành :class:`AnalysisTarget`.

    Args:
        raw_input: một token từ :func:`split_stock_list` (hoặc chuỗi bất kỳ).

    Returns:
        ``asset_type=stock`` cho mã Việt Nam hợp lệ (cổ phiếu, quỹ, chỉ số);
        ``asset_type=unsupported`` kèm ``unsupported_reason`` cho mọi trường hợp khác.
    """
    raw = (raw_input or "").strip()
    if not raw:
        return _unsupported(raw_input or "", "empty input")

    bare = _resolve_vn_code(raw)
    if bare is None:
        return _unsupported(raw_input, _UNSUPPORTED_REASON)

    return AnalysisTarget(
        raw_input=raw_input,
        asset_type=ParseStatus.STOCK,
        canonical_id=bare,
        display_code=bare,
        exchange="VN",
        normalized_prefix="vn",
        normalized_code=bare,
    )


def parse_stock_list(value: str) -> List[AnalysisTarget]:
    """Phân tích toàn bộ STOCK_LIST thành danh sách :class:`AnalysisTarget`."""
    return [parse_analysis_target(token) for token in split_stock_list(value)]
