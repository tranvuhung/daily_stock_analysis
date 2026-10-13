# -*- coding: utf-8 -*-
"""
===================================
Tra tên cổ phiếu -> mã (thị trường Việt Nam)
===================================

Tra tên cổ phiếu Việt Nam sang mã dựa trên stock index đóng gói (``stocks.index.json``):
khớp chính xác tên (có dấu hoặc không dấu), rồi khớp mờ (difflib) với ngưỡng chặt.
Không gọi mạng.

Resolve Vietnamese stock names to codes using the bundled stock index (offline).
"""

from __future__ import annotations

import difflib
import logging
import threading
import unicodedata
from typing import Dict, Optional, Set, Tuple

from src.services.stock_code_utils import is_code_like, normalize_code

logger = logging.getLogger(__name__)

_index_lock = threading.Lock()
_name_index: Optional[Tuple[Dict[str, str], Set[str]]] = None


def _fold_name(text: str) -> str:
    """Chuẩn hoá tên để so khớp: bỏ dấu tiếng Việt, chữ thường, bỏ khoảng trắng.

    Fold a name for matching: strip Vietnamese diacritics, lowercase, drop whitespace.
    """
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    stripped = stripped.replace("đ", "d").replace("Đ", "D")
    return "".join(stripped.casefold().split())


def _build_name_index() -> Tuple[Dict[str, str], Set[str]]:
    """Dựng bảng tên-đã-chuẩn-hoá -> mã (bỏ các tên trùng nhiều mã)."""
    from src.data.stock_index_loader import get_stock_index_rows

    name_to_codes: Dict[str, Set[str]] = {}
    for row in get_stock_index_rows():
        code = str(row[0] or "").strip().upper()
        if not code:
            continue
        keys = [row[2]]
        if len(row) > 3 and isinstance(row[3], str):
            keys.append(row[3])
        for key in keys:
            folded = _fold_name(key)
            if folded:
                name_to_codes.setdefault(folded, set()).add(code)

    unique = {name: next(iter(codes)) for name, codes in name_to_codes.items() if len(codes) == 1}
    ambiguous = {name for name, codes in name_to_codes.items() if len(codes) > 1}
    return unique, ambiguous


def _get_name_index() -> Tuple[Dict[str, str], Set[str]]:
    global _name_index
    if _name_index is None:
        with _index_lock:
            if _name_index is None:
                _name_index = _build_name_index()
    return _name_index


def clear_name_index_cache() -> None:
    """Xoá cache bảng tên (dùng cho test). / Clear the name index cache (tests)."""
    global _name_index
    with _index_lock:
        _name_index = None


def resolve_name_to_code(name: str) -> Optional[str]:
    """
    Tra tên (hoặc mã) cổ phiếu Việt Nam sang mã.

    Thứ tự:
    1. Input trông giống mã -> trả mã đã chuẩn hoá.
    2. Khớp chính xác tên (có dấu / không dấu) trong stock index; tên trùng nhiều mã trả None.
    3. Khớp mờ (difflib, cutoff 0.85) cho input dài hơn 3 ký tự.

    Returns:
        Mã cổ phiếu, hoặc None khi không tra được / nhập nhằng.
    """
    if not name or not isinstance(name, str):
        return None
    text = name.strip()
    if not text:
        return None

    if is_code_like(text):
        return normalize_code(text)

    folded = _fold_name(text)
    if not folded:
        return None

    unique, ambiguous = _get_name_index()
    if folded in unique:
        return unique[folded]
    if folded in ambiguous:
        logger.debug("[NameResolver] Tên nhập nhằng, trả None: %s", text)
        return None

    if len(folded) > 3:
        matches = difflib.get_close_matches(folded, list(unique.keys()), n=1, cutoff=0.85)
        if matches:
            logger.debug("[NameResolver] Khớp mờ: input=%s, matched=%s", text, matches[0])
            return unique[matches[0]]

    logger.debug("[NameResolver] Không tra được: %s", text)
    return None
