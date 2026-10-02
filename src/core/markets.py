# -*- coding: utf-8 -*-
"""
===================================
Market registry
===================================

This module is the single source of truth for "which markets the system supports". Before it, the market list
was declared independently in at least 8 places across the repo (market_review_region, trading_calendar,
data_capability_service, config_registry, etc.), which drifted out of sync,
and adding or disabling a market meant touching dozens of scattered branches.

Responsibilities:
1. Declare every known market with its timezone, currency and trading-calendar code
2. Control which markets are enabled via the ``SUPPORTED_MARKETS`` environment variable
3. Provide derived lists to the callers above so they no longer hardcode their own

Design constraints:
- This module does **not import** ``src.config``. ``src.config`` depends on this module
  indirectly through ``src.utils.market_review_region``, so a direct reverse import would create
  a cycle. Hence the environment variable is read directly here, consistent with how the fetchers
  already read ``*_PRIORITY``.
- Disabling a market only removes it from the allowlist; its ``MarketSpec`` is not deleted,
  so rolling back only requires changing one environment variable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from threading import RLock
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_MARKETS_ENV = "SUPPORTED_MARKETS"
DEFAULT_SUPPORTED_MARKETS = "vn"


@dataclass(frozen=True)
class MarketSpec:
    """Static metadata for one market.

    Attributes:
        code: market code, lowercase, used uniformly across the repo (``vn`` / ``cn`` / ``us`` ...)
        label_vi: Vietnamese display name
        label_en: English display name
        timezone: IANA timezone, used to determine "today" per market
        currency: quote currency
        xcals_code: exchange-calendars exchange code; ``None`` means the library does not
            cover this market (as with Vietnam), and trading-day checks fail open per the existing policy
        detect_order: order for deciding which market a code belongs to. Lower numbers are checked first. ``vn`` must come
            before ``us``: bare Vietnamese codes (HPG/FPT) overlap the US ticker regex
            ``^[A-Z]{1,5}$``, so checking US first would misclassify Vietnamese stocks as US stocks.
        supports_market_review: whether a matching market review profile / prompt exists
        review_order: market order in the market review. Kept separate from ``detect_order`` because
            the two orderings serve different goals: ``detect_order`` puts cn last as the fallback, while the review's
            historical order puts cn first (``cn,hk,us,jp,kr``). Report titles,
            ``MARKET_REVIEW_REGION_ALL`` and existing tests all depend on this order.
    """

    code: str
    label_vi: str
    label_en: str
    timezone: str
    currency: str
    xcals_code: Optional[str]
    detect_order: int
    supports_market_review: bool = True
    review_order: int = 99


# All known markets. Disabled markets stay here so they can be restored via SUPPORTED_MARKETS.
ALL_MARKETS: Dict[str, MarketSpec] = {
    "vn": MarketSpec(
        code="vn",
        label_vi="Việt Nam",
        label_en="Vietnam",
        timezone="Asia/Ho_Chi_Minh",
        currency="VND",
        # exchange-calendars does not cover HOSE/HNX/UPCOM (verified: none of the library's 102 calendars
        # is Vietnamese), so trading-day checks keep using the fail-open branch.
        xcals_code=None,
        detect_order=10,
        # Việt Nam có profile/prompt điểm lại thị trường riêng (chỉ số từ VCI,
        # độ rộng thị trường từ snapshot SSI). Xếp sau cn/hk/us/jp/kr để giữ
        # nguyên thứ tự lịch sử của báo cáo đa thị trường và giá trị mặc định
        # khi bật cùng lúc nhiều thị trường.
        review_order=5,
    ),
    "cn": MarketSpec(
        code="cn",
        label_vi="Trung Quốc (A)",
        label_en="China A-share",
        timezone="Asia/Shanghai",
        currency="CNY",
        xcals_code="XSHG",
        detect_order=90,
        review_order=0,
    ),
    "hk": MarketSpec(
        code="hk",
        label_vi="Hồng Kông",
        label_en="Hong Kong",
        timezone="Asia/Hong_Kong",
        currency="HKD",
        xcals_code="XHKG",
        detect_order=30,
        review_order=1,
    ),
    "us": MarketSpec(
        code="us",
        label_vi="Mỹ",
        label_en="United States",
        timezone="America/New_York",
        currency="USD",
        xcals_code="XNYS",
        detect_order=40,
        review_order=2,
    ),
    "jp": MarketSpec(
        code="jp",
        label_vi="Nhật Bản",
        label_en="Japan",
        timezone="Asia/Tokyo",
        currency="JPY",
        xcals_code="XTKS",
        detect_order=50,
        review_order=3,
    ),
    "kr": MarketSpec(
        code="kr",
        label_vi="Hàn Quốc",
        label_en="South Korea",
        timezone="Asia/Seoul",
        currency="KRW",
        xcals_code="XKRX",
        detect_order=60,
        review_order=4,
    ),
    "tw": MarketSpec(
        code="tw",
        label_vi="Đài Loan",
        label_en="Taiwan",
        timezone="Asia/Taipei",
        currency="TWD",
        xcals_code="XTAI",
        detect_order=70,
        # Taiwan had no market review profile before either (market_profile.py only has
        # cn/hk/us/jp/kr); keep it as is.
        supports_market_review=False,
    ),
}

ALL_MARKET_CODES: Tuple[str, ...] = tuple(
    spec.code for spec in sorted(ALL_MARKETS.values(), key=lambda s: s.detect_order)
)

_cache_lock = RLock()
_cached_raw: Optional[str] = None
_cached_markets: Tuple[str, ...] = ()


def parse_supported_markets(raw: Optional[str]) -> Tuple[str, ...]:
    """Parse the raw ``SUPPORTED_MARKETS`` string into an ordered tuple of markets.

    Consistent with the repo's existing config parsing style: invalid values log a warning and fall back to the default instead of raising,
    so a single typo cannot stop the whole process from starting.

    - Empty / whitespace only -> default
    - ``all`` -> all known markets
    - Comma-separated, case- and whitespace-insensitive, deduplicated
    - All tokens invalid -> default
    """

    normalized = str(raw or "").strip().lower()
    if not normalized:
        return _default_markets()

    if normalized == "all":
        return ALL_MARKET_CODES

    tokens = [token.strip() for token in normalized.split(",")]
    requested = {token for token in tokens if token}
    unknown = sorted(token for token in requested if token not in ALL_MARKETS)
    if unknown:
        logger.warning(
            "%s 包含未知市场：%s；已忽略。合法值：%s 或 all",
            SUPPORTED_MARKETS_ENV,
            ", ".join(unknown),
            ", ".join(ALL_MARKET_CODES),
        )

    selected = tuple(code for code in ALL_MARKET_CODES if code in requested)
    if not selected:
        logger.warning(
            "%s=%r 没有任何合法市场，已回退为默认值 %r",
            SUPPORTED_MARKETS_ENV,
            raw,
            DEFAULT_SUPPORTED_MARKETS,
        )
        return _default_markets()

    return selected


def _default_markets() -> Tuple[str, ...]:
    return tuple(
        code
        for code in ALL_MARKET_CODES
        if code in {token.strip() for token in DEFAULT_SUPPORTED_MARKETS.split(",")}
    )


def enabled_markets() -> Tuple[str, ...]:
    """Return the enabled market codes, sorted by ``detect_order``.

    The result is cached by the raw environment variable string: calls are very frequent within a process (every
    market-ownership check uses it), but tests change the environment variable, so the raw value rather than "already initialised"
    is used as the cache key.
    """

    raw = os.getenv(SUPPORTED_MARKETS_ENV)
    global _cached_raw, _cached_markets
    with _cache_lock:
        if _cached_markets and _cached_raw == raw:
            return _cached_markets
        markets = parse_supported_markets(raw)
        _cached_raw = raw
        _cached_markets = markets
        return markets


def reset_cache() -> None:
    """Clear the cache (for tests)."""

    global _cached_raw, _cached_markets
    with _cache_lock:
        _cached_raw = None
        _cached_markets = ()


def is_market_enabled(market: Optional[str]) -> bool:
    """Whether a market is enabled. Unknown or empty values are always treated as disabled."""

    if not market:
        return False
    return str(market).strip().lower() in enabled_markets()


def enabled_specs() -> Tuple[MarketSpec, ...]:
    """Return the ``MarketSpec`` of enabled markets, sorted by ``detect_order``."""

    return tuple(ALL_MARKETS[code] for code in enabled_markets())


def get_spec(market: str) -> Optional[MarketSpec]:
    return ALL_MARKETS.get(str(market or "").strip().lower())


def market_label(market: str, language: str = "vi") -> str:
    """Market display name. Unknown markets fall back to the uppercase code so the UI never shows a blank."""

    spec = get_spec(market)
    if spec is None:
        return str(market or "").upper()
    return spec.label_en if str(language).lower().startswith("en") else spec.label_vi


def enabled_market_exchanges() -> Dict[str, str]:
    """``market -> MIC`` mapping for enabled markets supported by exchange-calendars.

    Markets without a calendar (such as Vietnam) are not included; the existing fail-open
    branch in ``trading_calendar`` handles them.
    """

    return {
        spec.code: spec.xcals_code
        for spec in enabled_specs()
        if spec.xcals_code
    }


def enabled_market_timezones() -> Dict[str, str]:
    """``market -> IANA timezone`` mapping for enabled markets."""

    return {spec.code: spec.timezone for spec in enabled_specs()}


def market_review_regions() -> Tuple[str, ...]:
    """Enabled markets that support market review, sorted by ``detect_order``.

    Review prompts/profiles are hand-written per market; a market without a profile (Taiwan)
    cannot enter the review pipeline even when enabled, otherwise it would produce an unfounded review text.
    """

    regions = [spec for spec in enabled_specs() if spec.supports_market_review]
    regions.sort(key=lambda spec: spec.review_order)
    return tuple(spec.code for spec in regions)
