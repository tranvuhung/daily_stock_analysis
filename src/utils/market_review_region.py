# -*- coding: utf-8 -*-
"""Shared normalization rules for market-review region values.

区域列表不再在本模块硬编码，而是派生自 :mod:`src.core.markets`
注册表，并在**调用时**按 ``SUPPORTED_MARKETS`` 过滤。这样停用一个市场
之后，复盘入口不会再接受它，也不会把它默认回 ``cn``。

模块级常量保留下来（仍是全量已知区域），因为 API schema 描述、UI 文案和
既有测试都直接导入它们；它们表示"系统知道哪些复盘区域"，而
``enabled_market_review_regions()`` 表示"当前启用哪些"。
"""

from typing import Optional, Tuple

from src.core.markets import ALL_MARKETS, market_review_regions

# 全量已知复盘区域（不受 SUPPORTED_MARKETS 影响），按历史顺序 cn,hk,us,jp,kr
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
    """当前启用且支持复盘的区域，按历史顺序排列。

    可能为空元组：例如只启用越南时，越南还没有复盘 profile/prompt，
    这时调用方应当跳过复盘，而不是回退到 ``cn``（那会用不存在的 A 股
    数据源去跑复盘）。
    """

    return market_review_regions()


def market_review_supported() -> bool:
    """当前配置下是否还有任何可执行的复盘区域。"""

    return bool(enabled_market_review_regions())


def _valid_hint() -> str:
    enabled = enabled_market_review_regions()
    if not enabled:
        return "当前 SUPPORTED_MARKETS 下没有任何支持大盘复盘的市场"
    joined = "/".join(enabled)
    return f"{', '.join((*enabled, 'both'))}，或 {joined} 的合法逗号分隔组合"


def normalize_market_review_region_lenient(value: Optional[str]) -> Optional[str]:
    """Normalize persistent config input while preserving legacy filtering.

    ``None`` 和空串沿用历史上的默认行为，但默认值现在是"第一个启用的复盘
    区域"而不是写死的 ``cn``。逗号列表只保留启用区域，``both`` 展开为全部
    启用区域。没有任何合法 token 时返回 ``None``。
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
