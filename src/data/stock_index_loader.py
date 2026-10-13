# -*- coding: utf-8 -*-
"""Nạp stock index (``stocks.index.json``) cho tra cứu tên và mã cổ phiếu Việt Nam.

File index ở định dạng tuple nén:
``[canonical, display, name, pinyin_full, pinyin_abbr, aliases, market, asset_type, active, popularity]``.
Repo đóng gói sẵn bản chỉ chứa mã Việt Nam (``apps/dsa-web/public`` và ``static``).

Load the bundled Vietnam stock index for name and code lookup.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import RLock
from typing import Dict, Iterable, List, Optional

from src.data.stock_mapping import is_meaningful_stock_name

logger = logging.getLogger(__name__)

_STOCK_INDEX_FILENAME = "stocks.index.json"
_STOCK_INDEX_CACHE: Dict[str, str] | None = None
_STOCK_CODE_LOOKUP_CACHE: Dict[str, str] | None = None
_STOCK_INDEX_ROWS_CACHE: List[list] | None = None
_STOCK_INDEX_CACHE_LOCK = RLock()


def get_stock_index_candidate_paths() -> tuple[Path, ...]:
    """Các vị trí có thể chứa stock index (bản đóng gói của web trước, rồi ``static``)."""
    repo_root = Path(__file__).resolve().parents[2]
    return (
        repo_root / "apps" / "dsa-web" / "public" / _STOCK_INDEX_FILENAME,
        repo_root / "static" / _STOCK_INDEX_FILENAME,
    )


def find_existing_stock_index_path(
    candidate_paths: Optional[Iterable[Path]] = None,
) -> Path | None:
    """Trả về file index đầu tiên tồn tại trong danh sách ứng viên."""
    paths = tuple(candidate_paths) if candidate_paths is not None else get_stock_index_candidate_paths()
    for candidate_path in paths:
        try:
            if candidate_path.is_file():
                return candidate_path
        except OSError:
            continue
    return None


def _load_stock_index_payload(index_path: Path) -> list:
    with index_path.open("r", encoding="utf-8") as fh:
        raw_items = json.load(fh)

    if not isinstance(raw_items, list):
        raise ValueError(
            f"Unexpected {_STOCK_INDEX_FILENAME} payload type: {type(raw_items).__name__}"
        )
    return raw_items


def get_stock_index_rows() -> List[list]:
    """Nạp (có cache) các dòng hợp lệ đang hoạt động của stock index."""
    global _STOCK_INDEX_ROWS_CACHE

    if _STOCK_INDEX_ROWS_CACHE is not None:
        return _STOCK_INDEX_ROWS_CACHE

    with _STOCK_INDEX_CACHE_LOCK:
        if _STOCK_INDEX_ROWS_CACHE is not None:
            return _STOCK_INDEX_ROWS_CACHE

        rows: List[list] = []
        # Thử lần lượt từng ứng viên; file hỏng (UTF-8 lỗi, JSON sai cấu trúc) thì
        # chuyển sang ứng viên kế tiếp thay vì trả rỗng.
        # Try candidates in order; a corrupt file falls through to the next one.
        for index_path in get_stock_index_candidate_paths():
            try:
                if not index_path.is_file():
                    continue
                payload = _load_stock_index_payload(index_path)
            except (OSError, TypeError, ValueError) as exc:
                logger.debug("[Stock index] Đọc stock index thất bại %s: %s", index_path, exc)
                continue
            for item in payload:
                if not isinstance(item, list) or len(item) < 3:
                    continue
                if len(item) > 8 and item[8] is False:
                    continue
                rows.append(item)
            logger.debug("[Stock index] Đã nạp %s (%d dòng)", index_path, len(rows))
            break

        _STOCK_INDEX_ROWS_CACHE = rows
        return _STOCK_INDEX_ROWS_CACHE


def get_stock_name_index_map() -> Dict[str, str]:
    """Bảng mã (viết hoa) -> tên cổ phiếu, nạp lười và có cache."""
    global _STOCK_INDEX_CACHE

    if _STOCK_INDEX_CACHE is not None:
        return _STOCK_INDEX_CACHE

    with _STOCK_INDEX_CACHE_LOCK:
        if _STOCK_INDEX_CACHE is not None:
            return _STOCK_INDEX_CACHE

        name_map: Dict[str, str] = {}
        for item in get_stock_index_rows():
            canonical_code, display_code, name = item[0], item[1], item[2]
            if not is_meaningful_stock_name(name, str(display_code or canonical_code or "")):
                continue
            for key in (canonical_code, display_code):
                text = str(key or "").strip().upper()
                if text:
                    name_map[text] = str(name).strip()

        _STOCK_INDEX_CACHE = name_map
        return _STOCK_INDEX_CACHE


def _get_stock_code_lookup_map() -> Dict[str, str]:
    """Bảng mã/alias (viết hoa) -> mã canonical trong index."""
    global _STOCK_CODE_LOOKUP_CACHE

    if _STOCK_CODE_LOOKUP_CACHE is not None:
        return _STOCK_CODE_LOOKUP_CACHE

    with _STOCK_INDEX_CACHE_LOCK:
        if _STOCK_CODE_LOOKUP_CACHE is not None:
            return _STOCK_CODE_LOOKUP_CACHE

        lookup: Dict[str, str] = {}
        for item in get_stock_index_rows():
            canonical = str(item[0] or "").strip()
            if not canonical:
                continue
            keys = [item[0], item[1]]
            if len(item) > 5 and isinstance(item[5], list):
                keys.extend(item[5])
            for key in keys:
                text = str(key or "").strip().upper()
                if text:
                    lookup.setdefault(text, canonical)

        _STOCK_CODE_LOOKUP_CACHE = lookup
        return _STOCK_CODE_LOOKUP_CACHE


def get_index_stock_name(stock_code: str) -> str | None:
    """Tra tên cổ phiếu theo mã từ stock index."""
    code = str(stock_code or "").strip()
    if not code:
        return None

    name = get_stock_name_index_map().get(code.upper())
    if is_meaningful_stock_name(name, code):
        return name
    return None


def resolve_index_stock_code(query: str) -> str | None:
    """Trả về mã canonical trong index khi ``query`` khớp chính xác mã/alias; ngược lại ``None``."""
    code = str(query or "").strip().upper()
    if not code:
        return None
    return _get_stock_code_lookup_map().get(code)


def clear_stock_index_cache() -> None:
    """Xoá cache tra cứu stock index trong tiến trình."""
    global _STOCK_INDEX_CACHE, _STOCK_CODE_LOOKUP_CACHE, _STOCK_INDEX_ROWS_CACHE
    with _STOCK_INDEX_CACHE_LOCK:
        _STOCK_INDEX_CACHE = None
        _STOCK_CODE_LOOKUP_CACHE = None
        _STOCK_INDEX_ROWS_CACHE = None


def _clear_stock_index_cache_for_tests() -> None:
    clear_stock_index_cache()
