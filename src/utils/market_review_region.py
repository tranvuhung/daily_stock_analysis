# -*- coding: utf-8 -*-
"""Shared normalization rules for market-review region values.

The region list is no longer hardcoded in this module; it is derived from the :mod:`src.core.markets`
registry and filtered by ``SUPPORTED_MARKETS`` **at call time**. That way, once a market is
disabled, the review entry points no longer accept it nor default it back to ``cn``.

The module-level constants are kept (still the full set of known regions) because API schema descriptions, UI text and
existing tests import them directly; they mean "which review regions the system knows about", while
``enabled_market_review_regions()`` means "which ones are currently enabled".
"""

from typing import Optional, Tuple

from src.core.markets import ALL_MARKETS, market_review_regions

# All known review regions (unaffected by SUPPORTED_MARKETS), in historical order cn,hk,us,jp,kr,vn
MARKET_REVIEW_REGION_ORDER: Tuple[str, ...] = tuple(
    spec.code
    for spec in sorted(
        (spec for spec in ALL_MARKETS.values() if spec.supports_market_review),
        key=lambda spec: spec.review_order,
    )
)
MARKET_REVIEW_REGION_SET = frozenset(MARKET_REVIEW_REGION_ORDER)
MARKET_REVIEW_REGION_ALL = ",".join(MARKET_REVIEW_REGION_ORDER)
MARKET_REVIEW_REGION_VALID_INPUTS = (*MARKET_REVIEW_REGION_ORDER, "both")


def enabled_market_review_regions() -> Tuple[str, ...]:
    """Regions currently enabled and supporting market review, in historical order.

    May be an empty tuple: e.g. when only Taiwan is enabled, Taiwan has no review profile/prompt yet,
    so callers should skip the review rather than fall back to ``cn`` (which would run the review
    against an A-share data source that does not exist).
    """

    return market_review_regions()


def market_review_supported() -> bool:
    """Whether any runnable review region remains under the current configuration."""

    return bool(enabled_market_review_regions())


def _valid_hint() -> str:
    enabled = enabled_market_review_regions()
    if not enabled:
        return "当前 SUPPORTED_MARKETS 下没有任何支持大盘复盘的市场"
    joined = "/".join(enabled)
    return f"{', '.join((*enabled, 'both'))}，或 {joined} 的合法逗号分隔组合"


def normalize_market_review_region_lenient(value: Optional[str]) -> Optional[str]:
    """Normalize persistent config input while preserving legacy filtering.

    ``None`` and empty strings keep the historical default behavior, but the default is now "the first enabled review
    region" rather than a hardcoded ``cn``. Comma lists keep only enabled regions; ``both`` expands to all
    enabled regions. Returns ``None`` when there is no valid token.
    """

    enabled = enabled_market_review_regions()
    if not enabled:
        return None

    enabled_set = frozenset(enabled)
    all_enabled = ",".join(enabled)
    default_region = enabled[0]

    normalized = str(value or default_region).strip().lower()
    if normalized in enabled_set:
        return normalized
    if normalized == "both":
        return all_enabled

    if "," in normalized:
        requested = {token.strip() for token in normalized.split(",") if token.strip()}
        if "both" in requested:
            return all_enabled
        regions = [region for region in enabled if region in requested]
        if regions:
            return ",".join(regions)

    return None


def normalize_market_review_region_strict(value: str) -> str:
    """Validate and canonicalize a request-scoped market-review region.

    Unlike persistent configuration parsing, request input is fail-fast: empty
    values, empty tokens, unknown tokens, and mixing ``both`` with other tokens
    raise ``ValueError`` instead of being filtered or defaulted.
    """

    enabled = enabled_market_review_regions()
    normalized = value.strip().lower()
    valid_hint = _valid_hint()

    if not enabled:
        raise ValueError(
            f"当前部署未启用任何支持大盘复盘的市场；{valid_hint}"
        )

    enabled_set = frozenset(enabled)
    if not normalized:
        raise ValueError(f"region 不能为空；合法值：{valid_hint}")

    tokens = [token.strip() for token in normalized.split(",")]
    if any(not token for token in tokens):
        raise ValueError(f"region 不能包含空项；合法值：{valid_hint}")

    invalid_tokens = sorted(
        {token for token in tokens if token not in enabled_set and token != "both"}
    )
    if invalid_tokens:
        raise ValueError(
            f"region 包含非法值：{', '.join(invalid_tokens)}；合法值：{valid_hint}"
        )

    if "both" in tokens:
        if len(tokens) != 1:
            raise ValueError("region 中 both 必须单独使用，不能与其他市场混合")
        return ",".join(enabled)

    requested = set(tokens)
    return ",".join(region for region in enabled if region in requested)
