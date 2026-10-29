# -*- coding: utf-8 -*-
"""
===================================
Lịch giao dịch (Issue #373 / Issue #1386 P0) — thị trường Việt Nam
===================================

Trách nhiệm:
1. Xác định hôm nay có phải ngày giao dịch của thị trường Việt Nam không
2. Lấy "hôm nay" theo múi giờ của thị trường để tránh sai ngày khi server chạy UTC
3. Lọc theo từng mã: chỉ phân tích mã có thị trường mở cửa hôm nay
4. Suy luận phiên giao dịch (premarket/intraday/...) theo giờ cố định

Thư viện exchange-calendars không có lịch cho HOSE/HNX/UPCoM, nên module dùng giờ
giao dịch cố định: chỉ loại thứ 7/CN; ngày lễ (Tết…) chưa có nguồn lịch nên vẫn coi là
ngày giao dịch (fail-open).

Vietnam trading calendar using fixed session hours (weekends closed, holidays fail-open).
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from src.core.markets import ALL_MARKETS, enabled_markets

logger = logging.getLogger(__name__)

# Thị trường -> mã sàn exchange-calendars. Việt Nam không có lịch trong thư viện nên rỗng;
# giữ hằng số để tương thích với nơi gọi cũ.
# Market -> exchange-calendars code (empty: Vietnam is not covered).
MARKET_EXCHANGE: Dict[str, str] = {
    spec.code: spec.xcals_code
    for spec in ALL_MARKETS.values()
    if spec.xcals_code
}

# Thị trường -> múi giờ IANA dùng để xác định "hôm nay"
MARKET_TIMEZONE: Dict[str, str] = {
    spec.code: spec.timezone
    for spec in ALL_MARKETS.values()
}

# Phiên khớp lệnh định kỳ đóng cửa (ATC) của HOSE chạy 14:30-14:45.
# Vietnam closing auction (ATC) window in minutes.
_CLOSING_AUCTION_WINDOW_MINUTES = {
    "vn": 15,
}
# Giờ giao dịch cố định (giờ địa phương): (mở cửa, (nghỉ trưa bắt đầu, kết thúc), đóng cửa).
# VN: ATO 9:00, nghỉ 11:30–13:00, ATC kết thúc 14:45. Chỉ loại thứ 7/CN.
# Fixed regular-session hours (weekends only, no holidays).
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
    Suy ra thị trường của một mã.

    Returns:
        ``'vn'`` khi là mã Việt Nam; ``None`` khi không nhận diện được (nơi gọi
        fail-open: coi như thị trường đang mở).
    """
    if not code or not isinstance(code, str):
        return None

    from data_provider.base import _is_vn_market

    if _is_vn_market(code.strip()):
        return "vn"
    return None


def is_market_open(market: str, check_date: date) -> bool:
    """
    Thị trường có mở cửa vào ``check_date`` không.

    Việt Nam: loại thứ 7/CN; ngày lễ chưa có nguồn lịch nên fail-open (coi là mở).
    Thị trường không rõ: fail-open, trả True.
    """
    if market in ALL_MARKETS:
        return check_date.weekday() < 5
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
    Ngày nến ngày có thể tái sử dụng cho checkpoint/resume.

    Việt Nam không có lịch exchange-calendars nên luôn trả ngày tự nhiên theo giờ địa
    phương của thị trường (fail-open, giữ đúng hành vi trước đây).
    """
    return get_market_now(market, current_time=current_time).date()


def resolve_historical_daily_bar_date(
    market: Optional[str],
    target_date: date,
    phase: Optional[str],
) -> Optional[date]:
    """Resolve the completed daily bar that a historical phase could consume.

    Thị trường giờ cố định (Việt Nam) dùng lịch thứ 2–6 (chưa xét ngày lễ):
    - premarket / intraday / lunch_break / closing_auction trong ngày giao dịch ->
      phiên giao dịch liền trước;
    - postmarket -> chính ngày đó nếu là ngày giao dịch;
    - non_trading -> phiên giao dịch liền trước nếu ngày đó không giao dịch.
    Phase thiếu / không rõ / mâu thuẫn với lịch thì fail-closed (``None``); giá trị
    ``effective_daily_bar_date`` đã lưu vẫn là nguồn chính.

    Fixed-hours markets (Vietnam) use a weekday calendar (holidays not modelled);
    missing, unknown or calendar-inconsistent phases fail closed.
    """
    if market not in _STATIC_SESSION_HOURS:
        return None

    normalized_phase = str(phase or "").strip().lower()
    is_session = target_date.weekday() < 5

    def _previous_session(day: date) -> date:
        candidate = day - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    if normalized_phase in {"premarket", "intraday", "lunch_break", "closing_auction"}:
        return _previous_session(target_date) if is_session else None
    if normalized_phase == "postmarket":
        return target_date if is_session else None
    if normalized_phase == "non_trading":
        return None if is_session else _previous_session(target_date)
    return None


def latest_completed_session_date(
    market: str,
    as_of: date,
    current_time: Optional[datetime] = None,
) -> date:
    """Ngày của phiên giao dịch đã hoàn tất gần nhất tính đến ``as_of``.

    - ``as_of`` là hôm nay (giờ thị trường) và phiên chưa đóng (premarket / intraday /
      lunch_break / closing_auction) -> lùi về phiên trước đó;
    - sau đó bỏ qua các ngày không giao dịch (thứ 7/CN). Ngày lễ chưa được mô hình hoá,
      nên vẫn được coi là ngày giao dịch (fail-open).

    Latest completed trading session on or before ``as_of`` (weekends skipped,
    holidays not modelled).
    """
    candidate = as_of
    if as_of == get_market_now(market, current_time).date():
        phase = infer_market_phase(market, current_time)
        if phase in {
            MarketPhase.PREMARKET,
            MarketPhase.INTRADAY,
            MarketPhase.LUNCH_BREAK,
            MarketPhase.CLOSING_AUCTION,
        }:
            candidate -= timedelta(days=1)
    while not is_market_open(market, candidate):
        candidate -= timedelta(days=1)
    return candidate


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


def session_elapsed_fraction(
    market: Optional[str], current_time: Optional[datetime] = None
) -> Optional[float]:
    """Tỷ lệ thời gian giao dịch đã trôi qua trong ngày (0..1), không tính nghỉ trưa.

    Fraction of today's trading time elapsed, excluding the lunch break. ``None``
    outside the session (premarket, postmarket, non-trading day, unknown market).
    """
    if market not in _STATIC_SESSION_HOURS:
        return None
    market_now = get_market_now(market, current_time=current_time)
    times = _static_session_times(market, market_now)
    if times is None:
        return None
    session_open, break_start, break_end, session_close = times
    if market_now < session_open or market_now >= session_close:
        return None
    morning = break_start - session_open
    total = morning + (session_close - break_end)
    if market_now < break_start:
        elapsed = market_now - session_open
    elif market_now < break_end:
        elapsed = morning
    else:
        elapsed = morning + (market_now - break_end)
    return max(0.0, min(1.0, elapsed / total))


def partial_bar_session_fraction(
    bar_date: Any,
    market: Optional[str],
    current_time: Optional[datetime] = None,
) -> Optional[float]:
    """Phần phiên đã qua nếu ``bar_date`` là nến hôm nay chưa đóng; ngược lại ``None``.

    Session fraction for a still-forming bar dated today; ``None`` for closed bars.
    """
    if bar_date is None:
        return None
    if isinstance(bar_date, datetime):
        bar_day = bar_date.date()
    elif isinstance(bar_date, date):
        bar_day = bar_date
    else:
        try:
            bar_day = date.fromisoformat(str(bar_date)[:10])
        except ValueError:
            return None
    market_now = get_market_now(market, current_time=current_time)
    if bar_day != market_now.date():
        return None
    return session_elapsed_fraction(market, market_now)


def infer_market_phase(
    market: Optional[str], current_time: Optional[datetime] = None
) -> MarketPhase:
    """
    Suy luận phiên giao dịch theo giờ cố định của thị trường.

    Thị trường không rõ trả ``MarketPhase.UNKNOWN`` (fail-closed). ``premarket`` /
    ``postmarket`` chỉ là trước/sau phiên chính, không ngụ ý có dữ liệu ngoài giờ.
    """
    if market not in _STATIC_SESSION_HOURS:
        return MarketPhase.UNKNOWN
    market_now = get_market_now(market, current_time=current_time)
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


def _phase_minutes(
    market: Optional[str],
    market_now: datetime,
    phase: MarketPhase,
) -> Tuple[Optional[int], Optional[int]]:
    if market not in _STATIC_SESSION_HOURS or phase in {
        MarketPhase.UNKNOWN,
        MarketPhase.NON_TRADING,
        MarketPhase.POSTMARKET,
    }:
        return None, None

    times = _static_session_times(market, market_now)
    if times is None:
        return None, None
    session_open, _, _, session_close = times

    if phase == MarketPhase.PREMARKET and market_now < session_open:
        seconds = (session_open - market_now).total_seconds()
        return max(0, int(seconds // 60)), None

    if phase in {
        MarketPhase.INTRADAY,
        MarketPhase.LUNCH_BREAK,
        MarketPhase.CLOSING_AUCTION,
    } and market_now < session_close:
        seconds = (session_close - market_now).total_seconds()
        return None, max(0, int(seconds // 60))

    return None, None


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

    ``analysis_phase="auto"`` keeps fixed-hours inference. Explicit supported
    phases override only the phase and derived flags/minute fields; they do
    not rewrite market-local time or the effective daily-bar date. The legacy
    ``analysis_intent`` argument remains a compatibility alias when
    ``analysis_phase`` is left as ``auto``.
    """
    requested_phase = _normalize_analysis_phase(analysis_phase, analysis_intent)
    market_now = get_market_now(market, current_time=current_time)
    warnings: List[str] = []

    if market in _STATIC_SESSION_HOURS:
        phase = (
            infer_market_phase(market, current_time=current_time)
            if requested_phase == "auto"
            else MarketPhase(requested_phase)
        )
    else:
        phase = MarketPhase.UNKNOWN
        _add_warning_code(warnings, "unknown_market")

    if requested_phase != "auto" and phase == MarketPhase.UNKNOWN:
        phase = MarketPhase(requested_phase)

    effective_daily_bar_date = get_effective_trading_date(
        market,
        current_time=current_time,
    )
    is_trading_day, is_market_open_now, is_partial_bar = _phase_booleans(phase)
    minutes_to_open, minutes_to_close = _phase_minutes(market, market_now, phase)

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
    Các thị trường mở cửa hôm nay (theo múi giờ địa phương của từng thị trường).

    Returns:
        Set of market keys that are trading today
    """
    result: Set[str] = set()
    for mkt, tz_name in MARKET_TIMEZONE.items():
        if mkt not in enabled_markets():
            continue
        try:
            today = datetime.now(ZoneInfo(tz_name)).date()
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
    Vùng điểm lại thị trường có hiệu lực hôm nay.

    Args:
        config_region: giá trị ``MARKET_REVIEW_REGION`` (chỉ ``vn`` còn ý nghĩa)
        open_markets: các thị trường mở cửa hôm nay

    Returns:
        ``'vn'`` khi thị trường mở cửa; ``''`` khi đóng cửa (bỏ qua điểm lại thị trường)
    """
    from src.utils.market_review_region import enabled_market_review_regions

    markets = enabled_market_review_regions()
    if not markets:
        return ""
    region = markets[0]
    return region if region in open_markets else ""
