# -*- coding: utf-8 -*-
"""
Tiện ích mã cổ phiếu dùng chung (thị trường Việt Nam).

Shared stock code utilities (Vietnam market only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from data_provider.base import (
    canonical_stock_code,
    is_vn_fund_code,
    is_vn_index_code,
    is_vn_security_code,
    normalize_stock_code,
)

# Nhãn thị trường duy nhất của hệ thống. / The only supported market tag.
VN_MARKET = "vn"


@dataclass(frozen=True)
class DailyStockIdentity:
    """One parsed identity shared by daily-bar lookup, calendar, and refill."""

    normalized_code: str
    market: str
    refill_code: str
    code_candidates: tuple[str, ...]


def _normalize_vn_code(raw: str) -> Optional[str]:
    """Quy chuẩn một mã Việt Nam về dạng trần viết hoa; ``None`` nếu không phải mã VN."""
    text = (raw or "").strip()
    if not text:
        return None
    normalized = normalize_stock_code(text).strip().upper()
    if is_vn_security_code(normalized) or is_vn_index_code(normalized):
        return normalized
    return None


def is_code_like(value: str) -> bool:
    """Chuỗi có trông giống mã chứng khoán Việt Nam không.

    Nhận: mã cổ phiếu 3 ký tự (``HPG``), chứng chỉ quỹ (``FUEVN100``), chỉ số
    (``VNINDEX``/``VN30``), hậu tố ``.VN`` và tiền tố ``vn``.

    Check whether a string looks like a Vietnam stock / fund / index code.
    """
    return _normalize_vn_code(value) is not None


def normalize_code(raw: str) -> Optional[str]:
    """Quy chuẩn và kiểm tra một mã Việt Nam.

    Hỗ trợ: ``HPG``, ``hpg``, ``HPG.VN``, ``vnHPG``, ``FUEVN100``, ``VNINDEX``.
    Trả ``None`` khi không phải mã Việt Nam hợp lệ.
    """
    return _normalize_vn_code(raw)


def resolve_daily_stock_identity(
    code: Optional[str],
    *,
    market_hint: Optional[str] = None,
) -> Optional[DailyStockIdentity]:
    """Parse one stock identity for every local daily-bar consumer.

    Trả ``None`` khi mã không phải Việt Nam, hoặc khi ``market_hint`` là một thị
    trường khác ``vn`` (dữ liệu cũ của thị trường đã bị loại bỏ).
    """
    raw_code = str(code or "").strip().upper()
    if not raw_code:
        return None

    trusted_market = str(market_hint or "").strip().lower()
    if trusted_market and trusted_market != VN_MARKET:
        return None

    normalized_code = _normalize_vn_code(str(code or ""))
    if normalized_code is None:
        return None

    candidates = list(dict.fromkeys([raw_code, normalized_code, f"{normalized_code}.VN"]))
    return DailyStockIdentity(
        normalized_code=normalized_code,
        market=VN_MARKET,
        refill_code=normalized_code,
        code_candidates=tuple(candidates),
    )


def build_daily_code_candidates(code: Optional[str]) -> List[str]:
    """Build ordered code variants used to locate locally stored daily bars."""
    identity = resolve_daily_stock_identity(code)
    return list(identity.code_candidates) if identity is not None else []


def resolve_index_stock_code_for_analysis(raw: str) -> str:
    """Chuẩn hoá mã nhập vào cho luồng phân tích.

    Với input trông giống mã, ưu tiên mã có trong stock index (``stocks.index.json``);
    còn lại chỉ chuẩn hoá chữ hoa để nơi gọi tự áp dụng chính sách kiểm tra của mình.
    """
    text = (raw or "").strip()
    if not text:
        return ""

    if is_code_like(text) or is_vn_fund_code(text):
        from src.data.stock_index_loader import resolve_index_stock_code

        resolved = resolve_index_stock_code(text)
        if resolved:
            return canonical_stock_code(resolved)

    return canonical_stock_code(text)
