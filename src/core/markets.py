# -*- coding: utf-8 -*-
"""
===================================
市场注册表 / Market registry
===================================

本模块是"系统支持哪些市场"的唯一真源。在此之前，市场列表在仓库中被独立
重复声明了至少 8 处（market_review_region、trading_calendar、
data_capability_service、config_registry 等），彼此并不同步，
新增或停用一个市场需要改动分散的几十处分支。

职责：
1. 声明所有已知市场及其时区、货币、交易日历代码
2. 通过 ``SUPPORTED_MARKETS`` 环境变量控制哪些市场处于启用状态
3. 为上述调用方提供派生列表，避免各自再硬编码

设计约束：
- 本模块**不导入** ``src.config``。``src.config`` 通过
  ``src.utils.market_review_region`` 间接依赖本模块，直接反向导入会形成
  循环。因此这里直接读取环境变量，与各 fetcher 读取 ``*_PRIORITY``
  的既有做法保持一致。
- 停用一个市场只是把它移出 allowlist，不删除它的 ``MarketSpec``；
  这样回滚只需要改一个环境变量。
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
    """一个市场的静态元数据。

    Attributes:
        code: 市场代码，小写，全仓库统一使用（``vn`` / ``cn`` / ``us`` …）
        label_vi: 越南语显示名
        label_en: 英语显示名
        timezone: IANA 时区，用于按市场取"今天"
        currency: 报价币种
        xcals_code: exchange-calendars 的交易所代码；``None`` 表示该库没有
            收录这个市场（越南即如此），交易日判断按既有策略 fail-open
        detect_order: 代码归属判定顺序。数字小的先判定。``vn`` 必须排在
            ``us`` 之前：越南裸代码（HPG/FPT）与美股 ticker 正则
            ``^[A-Z]{1,5}$`` 重叠，先判 US 会把越南股误判成美股。
        supports_market_review: 是否有对应的大盘复盘 profile / prompt
        review_order: 大盘复盘中的市场顺序。与 ``detect_order`` 分开，因为
            两者的排序目标不同：``detect_order`` 要让 cn 兜底排最后，而复盘
            的历史顺序是 cn 在最前（``cn,hk,us,jp,kr``）。报告标题、
            ``MARKET_REVIEW_REGION_ALL`` 和既有测试都依赖这个顺序。
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


# 所有已知市场。停用的市场保留在这里，以便通过 SUPPORTED_MARKETS 回滚。
ALL_MARKETS: Dict[str, MarketSpec] = {
    "vn": MarketSpec(
        code="vn",
        label_vi="Việt Nam",
        label_en="Vietnam",
        timezone="Asia/Ho_Chi_Minh",
        currency="VND",
        # exchange-calendars 未收录 HOSE/HNX/UPCOM（已核对：该库 102 个日历
        # 中没有任何越南条目），交易日判断沿用 fail-open 分支。
        xcals_code=None,
        detect_order=10,
        # 越南的大盘复盘 profile/prompt 尚未实现，复盘入口按不支持处理。
        supports_market_review=False,
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
        # 台股此前也没有 market review profile（market_profile.py 只有
        # cn/hk/us/jp/kr），保持原状。
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
    """把 ``SUPPORTED_MARKETS`` 的原始字符串解析成有序市场元组。

    与仓库既有的配置解析风格一致：非法值记 warning 并回退默认值，不抛异常，
    避免一个拼写错误让整个进程起不来。

    - 空值 / 仅空白 → 默认值
    - ``all`` → 所有已知市场
    - 逗号分隔，忽略大小写与空白，去重
    - 全部 token 非法 → 默认值
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
    """返回当前启用的市场代码，按 ``detect_order`` 排序。

    结果按环境变量原始字符串缓存：进程内重复调用很频繁（每次代码归属判定
    都会用到），但测试会改动环境变量，因此以原始值而不是"是否已初始化"
    作为缓存键。
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
    """清空缓存（测试用）。"""

    global _cached_raw, _cached_markets
    with _cache_lock:
        _cached_raw = None
        _cached_markets = ()


def is_market_enabled(market: Optional[str]) -> bool:
    """市场是否启用。未知或空值一律视为未启用。"""

    if not market:
        return False
    return str(market).strip().lower() in enabled_markets()


def enabled_specs() -> Tuple[MarketSpec, ...]:
    """返回启用市场的 ``MarketSpec``，按 ``detect_order`` 排序。"""

    return tuple(ALL_MARKETS[code] for code in enabled_markets())


def get_spec(market: str) -> Optional[MarketSpec]:
    return ALL_MARKETS.get(str(market or "").strip().lower())


def market_label(market: str, language: str = "vi") -> str:
    """市场显示名。未知市场回退为大写代码，避免 UI 出现空白。"""

    spec = get_spec(market)
    if spec is None:
        return str(market or "").upper()
    return spec.label_en if str(language).lower().startswith("en") else spec.label_vi


def enabled_market_exchanges() -> Dict[str, str]:
    """启用市场中，exchange-calendars 支持的 ``market -> MIC`` 映射。

    没有日历的市场（如越南）不出现在结果里；``trading_calendar`` 的既有
    fail-open 分支会接管它们。
    """

    return {
        spec.code: spec.xcals_code
        for spec in enabled_specs()
        if spec.xcals_code
    }


def enabled_market_timezones() -> Dict[str, str]:
    """启用市场的 ``market -> IANA 时区`` 映射。"""

    return {spec.code: spec.timezone for spec in enabled_specs()}


def market_review_regions() -> Tuple[str, ...]:
    """支持大盘复盘的启用市场，按 ``detect_order`` 排序。

    复盘的 prompt/profile 是按市场手写的，没有 profile 的市场（越南、台湾）
    即使启用也不能进复盘链路，否则会生成没有依据的复盘文本。
    """

    regions = [spec for spec in enabled_specs() if spec.supports_market_review]
    regions.sort(key=lambda spec: spec.review_order)
    return tuple(spec.code for spec in regions)
