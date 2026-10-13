# -*- coding: utf-8 -*-
"""Quy tắc chuẩn hoá giá trị vùng điểm lại thị trường (chỉ Việt Nam).

Hệ thống chỉ còn một vùng ``vn``. Để client / cấu hình cũ không bị vỡ, các giá trị
vùng cũ (``cn``, ``hk``, ``us``, ``jp``, ``kr``, ``tw``, ``both`` hoặc danh sách phân
cách bằng dấu phẩy) được chuẩn hoá về ``vn``.

Normalization rules for market-review region values (Vietnam only). Legacy region
values are accepted and normalized to ``vn`` for backward compatibility.
"""

import logging
from typing import Optional, Tuple

from src.core.markets import ALL_MARKETS, market_review_regions

logger = logging.getLogger(__name__)

# Toàn bộ vùng điểm lại thị trường mà hệ thống biết (chỉ ``vn``).
MARKET_REVIEW_REGION_ORDER: Tuple[str, ...] = tuple(
    spec.code
    for spec in sorted(
        (spec for spec in ALL_MARKETS.values() if spec.supports_market_review),
        key=lambda spec: spec.review_order,
    )
)
MARKET_REVIEW_REGION_SET = frozenset(MARKET_REVIEW_REGION_ORDER)
MARKET_REVIEW_REGION_ALL = ",".join(MARKET_REVIEW_REGION_ORDER)
MARKET_REVIEW_REGION_VALID_INPUTS = MARKET_REVIEW_REGION_ORDER

# Giá trị vùng cũ của các thị trường đã bị loại bỏ; vẫn chấp nhận và quy về ``vn``.
# Legacy region tokens from removed markets; accepted and normalized to ``vn``.
_LEGACY_REGION_TOKENS = frozenset({"cn", "hk", "us", "jp", "kr", "tw", "both"})


def enabled_market_review_regions() -> Tuple[str, ...]:
    """Các vùng đang bật và hỗ trợ điểm lại thị trường (``("vn",)``)."""

    return market_review_regions()


def market_review_supported() -> bool:
    """Cấu hình hiện tại còn vùng điểm lại thị trường nào chạy được không."""

    return bool(enabled_market_review_regions())


def _tokens(value: str) -> Tuple[str, ...]:
    return tuple(token.strip() for token in value.strip().lower().split(","))


def normalize_market_review_region_lenient(value: Optional[str]) -> Optional[str]:
    """Chuẩn hoá cấu hình lưu trữ: mọi giá trị hợp lệ (kể cả vùng cũ) đều thành ``vn``.

    ``None`` / chuỗi rỗng trả vùng mặc định ``vn``. Trả ``None`` khi không có token
    nào nhận diện được.
    """

    enabled = enabled_market_review_regions()
    if not enabled:
        return None
    default_region = enabled[0]

    normalized = str(value or default_region).strip().lower()
    if not normalized:
        return default_region
    known = set(enabled) | _LEGACY_REGION_TOKENS
    tokens = [token for token in _tokens(normalized) if token]
    if tokens and any(token in known for token in tokens):
        if any(token in _LEGACY_REGION_TOKENS for token in tokens):
            logger.warning(
                "MARKET_REVIEW_REGION=%s chứa vùng không còn được hỗ trợ; đã quy về %s",
                value,
                default_region,
            )
        return default_region
    return None


def normalize_market_review_region_strict(value: str) -> str:
    """Kiểm tra và chuẩn hoá vùng điểm lại thị trường theo từng request.

    Khác với cấu hình lưu trữ, input của request fail-fast: rỗng, token rỗng hoặc token
    không nhận diện được sẽ ném ``ValueError``. Vùng cũ (``cn``/``both``…) vẫn được
    chấp nhận để client cũ không vỡ và được quy về ``vn``.

    Validate a request-scoped region; legacy tokens normalize to ``vn``.
    """

    enabled = enabled_market_review_regions()
    if not enabled:
        raise ValueError("Không có thị trường nào hỗ trợ điểm lại thị trường")

    normalized = (value or "").strip().lower()
    valid_hint = ", ".join(enabled)
    if not normalized:
        raise ValueError(f"region không được để trống; giá trị hợp lệ: {valid_hint}")

    tokens = _tokens(normalized)
    if any(not token for token in tokens):
        raise ValueError(f"region không được chứa mục rỗng; giá trị hợp lệ: {valid_hint}")

    known = set(enabled) | _LEGACY_REGION_TOKENS
    invalid_tokens = sorted({token for token in tokens if token not in known})
    if invalid_tokens:
        raise ValueError(
            f"region chứa giá trị không hợp lệ: {', '.join(invalid_tokens)}; giá trị hợp lệ: {valid_hint}"
        )
    return enabled[0]
