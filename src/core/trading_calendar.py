# -*- coding: utf-8 -*-
"""
===================================
交易日历模块 (Issue #373 / Issue #1386 P0)
===================================

职责：
1. 按市场（A股/港股/美股/日股/韩股/台股）判断当日是否为交易日
2. 按市场时区取“今日”日期，避免服务器 UTC 导致日期错误
3. 支持 per-stock 过滤：只分析当日开市市场的股票
4. 提供 regular-session 市场阶段推断基线，不改变现有分析入口行为

依赖：exchange-calendars（可选，交易日判断不可用时 fail-open，阶段推断不可用时 unknown）
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from src.core.markets import ALL_MARKETS, enabled_markets
from src.services.market_symbol_utils import get_suffix_market

logger = logging.getLogger(__name__)

# Exchange-calendars availability
_XCALS_AVAILABLE = False
try:
    import exchange_calendars as xcals
    _XCALS_AVAILABLE = True
except ImportError:
    logger.warning(
        "exchange-calendars not installed; trading day check disabled. "
        "Run: pip install exchange-calendars"
    )

# Market -> exchange code (exchange-calendars), derived from the market registry.
# 只包含 exchange-calendars 实际收录的市场；越南（HOSE/HNX/UPCOM）不在该库的
# 102 个日历里，因此不会出现在这里，交易日判断沿用本模块既有的 fail-open 分支。
MARKET_EXCHANGE = {
    spec.code: spec.xcals_code
    for spec in sorted(ALL_MARKETS.values(), key=lambda item: item.detect_order)
    if spec.xcals_code
}

# Market -> IANA timezone for "today"（含没有交易日历的市场，如越南）
MARKET_TIMEZONE = {
    spec.code: spec.timezone
    for spec in sorted(ALL_MARKETS.values(), key=lambda item: item.detect_order)
}

# P0 market phase baseline (Issue #1386). This is an intentionally small
# regular-session inference layer; it does not change existing fail-open
# trading-day filtering or effective-date behavior.
# tw: TWSE/TPEx run a 13:25-13:30 closing call auction (5 min). JP/KR use
# regular-session closing auction windows before the 15:30 close (JP 5 min,
# KR 10 min). Without an entry here .get(market, 0) yields a zero-width
# window, so the last regular-session minutes stay INTRADAY until POSTMARKET.
_CLOSING_AUCTION_WINDOW_MINUTES = {
    # 越南 HOSE 收盘集合竞价 ATC 为 14:30-14:45。
    "vn": 15,
    "cn": 3,
    "hk": 10,
    "us": 5,
    "jp": 5,
    "kr": 10,
    "tw": 5,
}
# Thị trường không có trong exchange-calendars nhưng giờ giao dịch cố định (giờ địa phương):
# (mở cửa, (nghỉ trưa bắt đầu, kết thúc), đóng cửa). VN: ATO 9:00, nghỉ 11:30–13:00, ATC kết thúc 14:45.
# Chỉ loại thứ 7/CN; ngày lễ chưa có nguồn lịch nên vẫn coi là ngày giao dịch.
# Fixed regular-session hours for markets without an xcals calendar (weekends only, no holidays).
_STATIC_SESSION_HOURS = {
    "vn": ((9, 0), ((11, 30), (13, 0)), (14, 45)),
}
_SUPPORTED_ANALYSIS_PHASES = {
    "auto",
    "premarket",
    "intraday",
    "postmarket",
}


class MarketPhase(str, Enum):
    """Regular-session market phase labels for Issue #1386 P0."""

    PREMARKET = "premarket"
    INTRADAY = "intraday"
    LUNCH_BREAK = "lunch_break"
    CLOSING_AUCTION = "closing_auction"
    POSTMARKET = "postmarket"
    NON_TRADING = "non_trading"
    UNKNOWN = "unknown"


@dataclass
class MarketPhaseContext:
    """Runtime market-phase context for stock analysis plumbing."""

    market: Optional[str]
    phase: MarketPhase
    market_local_time: datetime
    session_date: date
    effective_daily_bar_date: date
    is_trading_day: Optional[bool]
    is_market_open_now: Optional[bool]
    is_partial_bar: Optional[bool]
    minutes_to_open: Optional[int] = None
    minutes_to_close: Optional[int] = None
    trigger_source: str = "system"
    analysis_intent: str = "auto"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation for runtime context passing."""
        return {
            "market": self.market,
            "phase": self.phase.value,
            "market_local_time": self.market_local_time.isoformat(),
            "session_date": self.session_date.isoformat(),
            "effective_daily_bar_date": self.effective_daily_bar_date.isoformat(),
            "is_trading_day": self.is_trading_day,
            "is_market_open_now": self.is_market_open_now,
            "is_partial_bar": self.is_partial_bar,
            "minutes_to_open": self.minutes_to_open,
            "minutes_to_close": self.minutes_to_close,
            "trigger_source": self.trigger_source,
            "analysis_intent": self.analysis_intent,
            "warnings": list(self.warnings),
        }


def get_market_for_stock(code: str) -> Optional[str]:
    """
    Infer market region for a stock code.

    Returns:
        'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw' | None (None = unrecognized, fail-open: treat as open)
    """
    if not code or not isinstance(code, str):
        return None
    code = (code or "").strip().upper()

    from data_provider import is_us_stock_code, is_us_index_code, is_hk_stock_code
    from data_provider.base import _is_vn_market
    from src.core.markets import is_market_enabled

    # 越南必须排在美股之前判定：越南裸代码（HPG/FPT）同时满足美股 ticker 正则，
    # 先判美股会让它们在美股停用时被当成"今日休市"，整轮分析被跳过。
    #
    # 这里刻意**不**调用 ``_market_tag``：后者有一个"其余都算 A 股"的兜底分支，
    # 而本函数的契约是无法识别时返回 ``None``（调用方据此 fail-open）。借用
    # ``_market_tag`` 会让 ``UNKNOWN`` 之类的占位代码被判成 ``cn``。
    if is_market_enabled("vn") and _is_vn_market(code):
        return "vn"

    if is_us_stock_code(code) or is_us_index_code(code):
        return "us"
    if is_hk_stock_code(code):
        return "hk"
    suffix_market = get_suffix_market(code)
    if suffix_market:
        return suffix_market
    # A-share: 6-digit numeric
    if code.isdigit() and len(code) == 6:
        return "cn"
    return None


def is_market_open(market: str, check_date: date) -> bool:
    """
    Check if the given market is open on the given date.

    Fail-open: returns True if exchange-calendars unavailable or date out of range.

    Args:
        market: 'cn' | 'hk' | 'us'
        check_date: Date to check

    Returns:
        True if trading day (or fail-open), False otherwise
    """
    if not _XCALS_AVAILABLE:
        return True
    ex = MARKET_EXCHANGE.get(market)
    if not ex:
        # Thị trường đã đăng ký nhưng exchange-calendars không có lịch (hiện là
        # vn): ít nhất loại thứ 7/CN, để lịch chạy tự động không điểm lại thị
        # trường vào cuối tuần. Ngày lễ (Tết...) vẫn fail-open vì không có nguồn lịch.
        # Known market without an xcals calendar: close on weekends only.
        if market in ALL_MARKETS:
            return check_date.weekday() < 5
        return True
    try:
        cal = xcals.get_calendar(ex)
        session = datetime(check_date.year, check_date.month, check_date.day)
        return cal.is_session(session)
    except Exception as e:
        logger.warning("trading_calendar.is_market_open fail-open: %s", e)
        return True


# Phiên khớp lệnh của Việt Nam (giờ VN): ATO 9:00–9:15, liên tục 9:15–11:30 và 13:00–14:30,
# ATC 14:30–14:45. Cảnh báo realtime chỉ đánh giá trong các khung có giá khớp.
# Vietnam matching sessions (local time); realtime alerts only evaluate inside them.
VN_MATCHING_SESSIONS = (((9, 0), (11, 30)), ((13, 0), (14, 45)))


def is_vn_matching_session(current_time: Optional[datetime] = None, *, grace_minutes: int = 0) -> bool:
    """Có đang trong phiên khớp lệnh VN không (thứ 2–6, chưa xét ngày lễ).

    ``grace_minutes`` kéo dài cuối mỗi khung để nhận giá khớp ATO/ATC công bố ngay sau giờ đóng
    (ví dụ tick khớp ATC lúc 14:45:03). Extends each window's end by a few minutes.
    """

    now = get_market_now("vn", current_time)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    grace = max(0, int(grace_minutes))
    for (start_h, start_m), (end_h, end_m) in VN_MATCHING_SESSIONS:
        if start_h * 60 + start_m <= minutes < end_h * 60 + end_m + grace:
            return True
    return False


def get_market_now(
    market: Optional[str], current_time: Optional[datetime] = None
) -> datetime:
    """
    Return current time in the market's local timezone.

    If current_time is naive, treat it as already expressed in the market timezone.
    Unknown markets fall back to the given datetime (or local system time).
    """
    tz_name = MARKET_TIMEZONE.get(market or "")

    if current_time is None:
        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
        return datetime.now()

    if not tz_name:
        return current_time

    tz = ZoneInfo(tz_name)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=tz)
    return current_time.astimezone(tz)


def get_effective_trading_date(
    market: Optional[str], current_time: Optional[datetime] = None
) -> date:
    """
    Resolve the latest reusable daily-bar date for checkpoint/resume logic.

    Rules:
    - Non-trading day / holiday: previous trading session
    - Trading day before market close: previous completed trading session
    - Trading day after market close: current trading session
    - Calendar lookup failure: fail-open to market-local natural date
    """
    market_now = get_market_now(market, current_time=current_time)
    fallback_date = market_now.date()

    if not _XCALS_AVAILABLE:
        return fallback_date

    ex = MARKET_EXCHANGE.get(market or "")
    tz_name = MARKET_TIMEZONE.get(market or "")
    if not ex or not tz_name:
        return fallback_date

    try:
        cal = xcals.get_calendar(ex)
        local_date = market_now.date()

        if not cal.is_session(local_date):
            return cal.date_to_session(local_date, direction="previous").date()

        session = cal.date_to_session(local_date, direction="previous")
        session_close = cal.session_close(session)
        if hasattr(session_close, "tz_convert"):
            close_local = session_close.tz_convert(tz_name).to_pydatetime()
        elif session_close.tzinfo is not None:
            close_local = session_close.astimezone(ZoneInfo(tz_name))
        else:
            close_local = session_close.replace(tzinfo=ZoneInfo(tz_name))

        if market_now >= close_local:
            return session.date()

        return cal.previous_session(session).date()
    except Exception as e:
        logger.warning("trading_calendar.get_effective_trading_date fail-open: %s", e)
        return fallback_date


def resolve_historical_daily_bar_date(
    market: Optional[str],
    target_date: date,
    phase: Optional[str],
) -> Optional[date]:
    """Resolve the completed daily bar that a historical phase could consume.

    A persisted ``effective_daily_bar_date`` remains the primary authority.
    This fallback is only for older snapshots that still contain a trustworthy
    phase. It fails closed for missing, unknown, or calendar-inconsistent phases.
    """
    if market not in MARKET_EXCHANGE or not _XCALS_AVAILABLE:
        return None

    normalized_phase = str(phase or "").strip().lower()
    if normalized_phase not in {
        "premarket",
        "intraday",
        "lunch_break",
        "closing_auction",
        "postmarket",
        "non_trading",
    }:
        return None

    try:
        cal = xcals.get_calendar(MARKET_EXCHANGE[market])
        is_session = bool(cal.is_session(target_date))

        if normalized_phase in {
            "premarket",
            "intraday",
            "lunch_break",
            "closing_auction",
        }:
            if not is_session:
                return None
            session = cal.date_to_session(target_date, direction="previous")
            return cal.previous_session(session).date()

        if normalized_phase == "postmarket":
            return target_date if is_session else None

        if is_session:
            return None
        return cal.date_to_session(target_date, direction="previous").date()
    except Exception as e:
        logger.warning(
            "trading_calendar.resolve_historical_daily_bar_date fail-closed: %s",
            e,
        )
        return None


def _as_market_datetime(value: Any, tz_name: str) -> Optional[datetime]:
    """
    Convert exchange-calendar timestamps into market-local datetimes.

    Returns None for missing or pandas NaT-like values. Naive datetimes are
    interpreted as already expressed in the target market timezone, matching
    get_market_now()'s current_time contract.
    """
    if value is None:
        return None
    if pd.isna(value):
        return None

    try:
        if isinstance(value, pd.Timestamp):
            if value.tzinfo is None:
                dt = value.to_pydatetime()
            else:
                dt = value.tz_convert(tz_name).to_pydatetime()
        elif isinstance(value, datetime):
            dt = value
        elif hasattr(value, "to_pydatetime"):
            dt = value.to_pydatetime()
        else:
            return None
    except (AttributeError, TypeError, ValueError):
        return None

    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def infer_market_phase(
    market: Optional[str], current_time: Optional[datetime] = None
) -> MarketPhase:
    """
    Infer the regular-session market phase for a market.

    This P0 helper is intentionally fail-closed: unknown markets, unavailable
    exchange calendars, and calendar errors return ``MarketPhase.UNKNOWN``.
    That differs from ``is_market_open()`` and ``get_effective_trading_date()``,
    which keep their existing fail-open behavior for backwards compatibility.

    ``premarket`` and ``postmarket`` mean before/after the regular trading
    session only; they do not imply that extended-hours quote data is available.
    ``closing_auction`` uses a small per-market near-close heuristic window and
    does not model full exchange auction microstructure.
    """
    if market in _STATIC_SESSION_HOURS and market not in MARKET_EXCHANGE:
        return _infer_static_market_phase(market, get_market_now(market, current_time=current_time))
    if market not in MARKET_EXCHANGE or market not in MARKET_TIMEZONE:
        return MarketPhase.UNKNOWN
    if not _XCALS_AVAILABLE:
        return MarketPhase.UNKNOWN

    ex = MARKET_EXCHANGE[market]
    tz_name = MARKET_TIMEZONE[market]
    market_now = get_market_now(market, current_time=current_time)
    local_date = market_now.date()

    try:
        cal = xcals.get_calendar(ex)
        if not cal.is_session(local_date):
            return MarketPhase.NON_TRADING

        session = cal.date_to_session(local_date, direction="previous")
        session_open = _as_market_datetime(cal.session_open(session), tz_name)
        session_close = _as_market_datetime(cal.session_close(session), tz_name)
        if session_open is None or session_close is None:
            return MarketPhase.UNKNOWN

        if market_now < session_open:
            return MarketPhase.PREMARKET
        if market_now >= session_close:
            return MarketPhase.POSTMARKET

        # Calendars without session_has_break may still expose break timestamps.
        has_break = True
        if hasattr(cal, "session_has_break"):
            has_break = bool(cal.session_has_break(session))

        break_start = None
        break_end = None
        if has_break:
            break_start = _as_market_datetime(cal.session_break_start(session), tz_name)
            break_end = _as_market_datetime(cal.session_break_end(session), tz_name)

        return _classify_open_session(market, market_now, session_close, break_start, break_end)
    except Exception as e:
        logger.warning("trading_calendar.infer_market_phase fail-closed: %s", e)
        return MarketPhase.UNKNOWN


def _classify_open_session(
    market: str,
    market_now: datetime,
    session_close: datetime,
    break_start: Optional[datetime],
    break_end: Optional[datetime],
) -> MarketPhase:
    """Phân loại thời điểm nằm giữa giờ mở và đóng cửa của một phiên."""
    window_minutes = _CLOSING_AUCTION_WINDOW_MINUTES.get(market, 0)
    closing_window_start = session_close - timedelta(minutes=window_minutes)

    if break_start is not None and break_end is not None:
        if market_now < break_start:
            return MarketPhase.INTRADAY
        if market_now < break_end:
            return MarketPhase.LUNCH_BREAK
        if market_now < closing_window_start:
            return MarketPhase.INTRADAY
        return MarketPhase.CLOSING_AUCTION

    if market_now < closing_window_start:
        return MarketPhase.INTRADAY
    return MarketPhase.CLOSING_AUCTION


def _static_session_times(
    market: Optional[str], market_now: datetime
) -> Optional[Tuple[datetime, datetime, datetime, datetime]]:
    """(mở, nghỉ trưa bắt đầu, nghỉ trưa kết thúc, đóng) hôm nay cho thị trường giờ cố định."""
    hours = _STATIC_SESSION_HOURS.get(market or "")
    if hours is None or market_now.weekday() >= 5:
        return None
    (open_h, open_m), ((bs_h, bs_m), (be_h, be_m)), (close_h, close_m) = hours

    def _at(hour: int, minute: int) -> datetime:
        return market_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return _at(open_h, open_m), _at(bs_h, bs_m), _at(be_h, be_m), _at(close_h, close_m)


def _infer_static_market_phase(market: str, market_now: datetime) -> MarketPhase:
    times = _static_session_times(market, market_now)
    if times is None:
        return MarketPhase.NON_TRADING
    session_open, break_start, break_end, session_close = times
    if market_now < session_open:
        return MarketPhase.PREMARKET
    if market_now >= session_close:
        return MarketPhase.POSTMARKET
    return _classify_open_session(market, market_now, session_close, break_start, break_end)


def _add_warning_code(warnings: List[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


def _phase_booleans(
    phase: MarketPhase,
) -> Tuple[Optional[bool], Optional[bool], Optional[bool]]:
    if phase == MarketPhase.UNKNOWN:
        return None, None, None

    is_trading_day = phase != MarketPhase.NON_TRADING
    is_market_open_now = phase in {
        MarketPhase.INTRADAY,
        MarketPhase.CLOSING_AUCTION,
    }
    is_partial_bar = phase in {
        MarketPhase.INTRADAY,
        MarketPhase.LUNCH_BREAK,
        MarketPhase.CLOSING_AUCTION,
    }
    return is_trading_day, is_market_open_now, is_partial_bar


def _session_open_close_for_today(
    market: str,
    market_now: datetime,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    ex = MARKET_EXCHANGE.get(market)
    tz_name = MARKET_TIMEZONE.get(market)
    if not ex and market in _STATIC_SESSION_HOURS:
        times = _static_session_times(market, market_now)
        return (times[0], times[3]) if times else (None, None)
    if not ex or not tz_name or not _XCALS_AVAILABLE:
        return None, None

    cal = xcals.get_calendar(ex)
    local_date = market_now.date()
    if not cal.is_session(local_date):
        return None, None

    session = cal.date_to_session(local_date, direction="previous")
    return (
        _as_market_datetime(cal.session_open(session), tz_name),
        _as_market_datetime(cal.session_close(session), tz_name),
    )


def _phase_minutes(
    market: Optional[str],
    market_now: datetime,
    phase: MarketPhase,
) -> Tuple[Optional[int], Optional[int], bool]:
    static_market = market in _STATIC_SESSION_HOURS and market not in MARKET_EXCHANGE
    if (
        (market not in MARKET_EXCHANGE and not static_market)
        or phase in {MarketPhase.UNKNOWN, MarketPhase.NON_TRADING, MarketPhase.POSTMARKET}
    ):
        return None, None, False
    if not _XCALS_AVAILABLE and not static_market:
        return None, None, False

    try:
        session_open, session_close = _session_open_close_for_today(market, market_now)
    except Exception as e:
        logger.warning("trading_calendar.market_phase_context calendar_error: %s", e)
        return None, None, True

    if session_open is None or session_close is None:
        return None, None, False

    if phase == MarketPhase.PREMARKET and market_now < session_open:
        seconds = (session_open - market_now).total_seconds()
        return max(0, int(seconds // 60)), None, False

    if phase in {
        MarketPhase.INTRADAY,
        MarketPhase.LUNCH_BREAK,
        MarketPhase.CLOSING_AUCTION,
    } and market_now < session_close:
        seconds = (session_close - market_now).total_seconds()
        return None, max(0, int(seconds // 60)), False

    return None, None, False


def _normalize_analysis_phase(
    analysis_phase: Optional[str],
    analysis_intent: Optional[str],
) -> str:
    def _coerce(value: Optional[str]) -> str:
        if isinstance(value, MarketPhase):
            return value.value
        return str(value or "").strip().lower()

    requested = _coerce(analysis_phase) or "auto"
    legacy_intent = _coerce(analysis_intent)
    if requested == "auto" and legacy_intent and legacy_intent != "auto":
        requested = legacy_intent
    if requested not in _SUPPORTED_ANALYSIS_PHASES:
        raise ValueError(
            f"invalid analysis_phase: {requested}. "
            f"Must be one of {sorted(_SUPPORTED_ANALYSIS_PHASES)}"
        )
    return requested


def build_market_phase_context(
    *,
    market: Optional[str],
    current_time: Optional[datetime] = None,
    trigger_source: str = "system",
    analysis_intent: str = "auto",
    analysis_phase: str = "auto",
) -> MarketPhaseContext:
    """
    Build a JSON-safe runtime market-phase context for analysis plumbing.

    ``analysis_phase="auto"`` keeps calendar inference. Explicit supported
    phases override only the phase and derived flags/minute fields; they do
    not rewrite market-local time or the effective daily-bar date. The legacy
    ``analysis_intent`` argument remains a compatibility alias when
    ``analysis_phase`` is left as ``auto``.
    """
    requested_phase = _normalize_analysis_phase(analysis_phase, analysis_intent)
    market_now = get_market_now(market, current_time=current_time)
    warnings: List[str] = []

    static_market = market in _STATIC_SESSION_HOURS and market not in MARKET_EXCHANGE
    if static_market:
        # Giờ cố định, không cần exchange-calendars. Fixed hours; no xcals needed.
        phase = infer_market_phase(market, current_time=current_time) if requested_phase == "auto" else MarketPhase(requested_phase)
    elif market not in MARKET_EXCHANGE or market not in MARKET_TIMEZONE:
        phase = MarketPhase.UNKNOWN
        _add_warning_code(warnings, "unknown_market")
    else:
        if not _XCALS_AVAILABLE:
            _add_warning_code(warnings, "calendar_unavailable")
        if requested_phase == "auto":
            phase = infer_market_phase(market, current_time=current_time)
            if phase == MarketPhase.UNKNOWN and _XCALS_AVAILABLE:
                _add_warning_code(warnings, "calendar_error")
        else:
            phase = MarketPhase(requested_phase)

    if requested_phase != "auto" and phase == MarketPhase.UNKNOWN:
        phase = MarketPhase(requested_phase)

    effective_daily_bar_date = get_effective_trading_date(
        market,
        current_time=current_time,
    )
    is_trading_day, is_market_open_now, is_partial_bar = _phase_booleans(phase)
    minutes_to_open, minutes_to_close, minutes_calendar_error = _phase_minutes(
        market,
        market_now,
        phase,
    )
    if minutes_calendar_error:
        _add_warning_code(warnings, "calendar_error")

    return MarketPhaseContext(
        market=market,
        phase=phase,
        market_local_time=market_now,
        session_date=market_now.date(),
        effective_daily_bar_date=effective_daily_bar_date,
        is_trading_day=is_trading_day,
        is_market_open_now=is_market_open_now,
        is_partial_bar=is_partial_bar,
        minutes_to_open=minutes_to_open,
        minutes_to_close=minutes_to_close,
        trigger_source=trigger_source or "system",
        analysis_intent=requested_phase,
        warnings=warnings,
    )


def get_open_markets_today() -> Set[str]:
    """
    Get markets that are open today (by each market's local timezone).

    只考虑 ``SUPPORTED_MARKETS`` 启用的市场：停用市场不应影响"今天是否需要
    执行分析"的判断，否则关掉一个市场之后，它仍然能让整轮任务被认为有开市。

    Returns:
        Set of market keys that are trading today
    """
    candidates = {
        mkt: tz_name
        for mkt, tz_name in MARKET_TIMEZONE.items()
        if mkt in enabled_markets()
    }
    if not _XCALS_AVAILABLE:
        return set(candidates)
    result: Set[str] = set()
    for mkt, tz_name in candidates.items():
        try:
            tz = ZoneInfo(tz_name)
            today = datetime.now(tz).date()
            if is_market_open(mkt, today):
                result.add(mkt)
        except Exception as e:
            logger.warning("get_open_markets_today fail-open for %s: %s", mkt, e)
            result.add(mkt)
    return result


def compute_effective_region(
    config_region: str, open_markets: Set[str]
) -> Optional[str]:
    """
    Compute effective market review region given config and open markets.

    Args:
        config_region: From MARKET_REVIEW_REGION ('cn' | 'hk' | 'us' | 'jp' | 'kr' | 'vn' | 'both' or comma subset)
        open_markets: Markets open today

    Returns:
        None: caller uses config default (check disabled)
        '': all relevant markets closed, skip market review
        'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'vn' | 'both': effective subset for today
    """
    # 与 ``MARKET_REVIEW_REGION`` 的解析口径保持一致：只认当前启用且支持
    # 大盘复盘的市场。此前这里硬编码了一份市场列表，与
    # ``market_review_region.py`` 各自维护，容易漂移。
    from src.utils.market_review_region import enabled_market_review_regions

    markets = enabled_market_review_regions()
    if not markets:
        # 没有任何可复盘市场（例如只启用台湾）：跳过复盘而不是回退到 cn。
        return ""

    default_region = markets[0]
    normalized = (config_region or default_region).strip().lower()
    if not normalized:
        normalized = default_region

    requested = {
        item.strip() for item in normalized.split(",") if item.strip()
    }
    if not requested:
        requested = {default_region}

    if "both" in requested:
        requested = set(markets)
    else:
        # Ignore invalid tokens and only keep known markets.
        requested = {item for item in requested if item in markets}

    if not requested:
        # No valid market token left after filtering; follow parser fallback behavior.
        requested = {default_region}

    # single explicit region: keep single-region return semantics (empty when closed)
    if len(requested) == 1:
        region = next(iter(requested))
        return region if region in open_markets else ""

    # multi-region subset: keep only markets open today, in canonical order
    open_selected = [m for m in markets if m in requested and m in open_markets]
    if not open_selected:
        return ""
    if len(open_selected) == 1:
        return open_selected[0]
    return ",".join(open_selected)
