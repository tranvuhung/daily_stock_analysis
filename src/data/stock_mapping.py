# -*- coding: utf-8 -*-
"""
===================================
Bảng ánh xạ mã cổ phiếu -> tên
===================================

Tên cổ phiếu Việt Nam lấy từ stock index đóng gói (``stocks.index.json``) và các nguồn
dữ liệu; bảng tĩnh ``STOCK_NAME_MAP`` chỉ dùng cho các ghi đè thủ công (mặc định rỗng).

Shared stock code -> name mapping (Vietnam). Names come from the bundled stock index
and data providers; ``STOCK_NAME_MAP`` holds optional manual overrides only.
"""

from __future__ import annotations

from typing import Dict

# Ghi đè tên thủ công (mã viết hoa -> tên). / Manual name overrides (uppercase code -> name).
STOCK_NAME_MAP: Dict[str, str] = {}

# Tiền tố tên giả được sinh từ mã khi chưa tra được tên thật.
# Placeholder name prefixes generated from the code when the real name is unknown.
PLACEHOLDER_NAME_PREFIXES = ("股票", "Cổ phiếu ")


def is_meaningful_stock_name(name: str | None, stock_code: str) -> bool:
    """Tên cổ phiếu có hữu ích để hiển thị / cache hay không."""
    if not name:
        return False

    normalized_name = str(name).strip()
    if not normalized_name:
        return False

    normalized_code = (stock_code or "").strip().upper()
    if normalized_name.upper() == normalized_code:
        return False

    if normalized_name.startswith(PLACEHOLDER_NAME_PREFIXES):
        return False

    placeholder_values = {
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "--",
        "-",
        "UNKNOWN",
        "TICKER",
    }
    if normalized_name.upper() in placeholder_values:
        return False

    return True
