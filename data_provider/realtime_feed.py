# -*- coding: utf-8 -*-
"""
Feed giá realtime dạng push (tick) cho thị trường Việt Nam.

Push-based realtime price feed primitives:

- ``RealtimeTick``: một tick giá đã chuẩn hoá (giá quy về đồng, giống SSI snapshot).
- ``parse_tvsi_stock_tick``: đọc payload JSON của topic ``topic/<MÃ>`` trên broker TVSI.
- ``LatestTickStore``: tick mới nhất theo mã, an toàn đa luồng, bỏ tick đến trễ
  theo ``SequenceNumber``.
- ``get_active_feed`` / ``set_active_feed``: feed đang chạy trong process, để
  ``DataFetcherManager`` và engine cảnh báo đọc tick mà không phụ thuộc vòng import.

Module này không import paho; kết nối MQTT nằm ở ``data_provider/tvsi_mqtt_feed.py``.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Giá trên broker TVSI tính bằng nghìn đồng (20.25 = 20.250 đ). TVSI prices are in thousand VND.
TVSI_PRICE_MULTIPLIER = 1000.0
# FloorCode của TVSI → sàn. UPCOM chưa có mẫu thật, giữ mã thường gặp "04".
TVSI_FLOOR_CODES = {"10": "hose", "02": "hnx", "04": "upcom"}


@dataclass(frozen=True)
class RealtimeTick:
    """Một tick giá đã chuẩn hoá. Normalized realtime tick (prices in VND)."""

    symbol: str
    price: Optional[float]
    change: Optional[float] = None
    change_pct: Optional[float] = None
    ref: Optional[float] = None
    ceiling: Optional[float] = None
    floor: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    average: Optional[float] = None
    volume: Optional[int] = None
    value: Optional[float] = None
    name: str = ""
    exchange: str = ""
    market_status: str = ""
    sequence: Optional[int] = None
    ts: Optional[datetime] = None
    received_at: float = 0.0
    source: str = "tvsi_mqtt"

    def age_seconds(self, now: Optional[float] = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.received_at)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "change": self.change,
            "change_pct": self.change_pct,
            "ref": self.ref,
            "ceiling": self.ceiling,
            "floor": self.floor,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "market_status": self.market_status,
            "ts": self.ts.isoformat() if self.ts else None,
            "source": self.source,
        }


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _price(value: Any) -> Optional[float]:
    number = _num(value)
    if number is None:
        return None
    return round(number * TVSI_PRICE_MULTIPLIER, 4)


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return int(number) if number is not None else None


def _parse_tvsi_time(trade_date: Any, hhmmss: Any) -> Optional[datetime]:
    """``TradeDate`` (``2026-09-28T00:00:00``) + ``Time`` (``143002``) → datetime giờ VN."""

    try:
        day = datetime.fromisoformat(str(trade_date)).date()
        raw = str(hhmmss or "").strip().zfill(6)
        if len(raw) != 6 or not raw.isdigit():
            return None
        return datetime(day.year, day.month, day.day, int(raw[:2]), int(raw[2:4]), int(raw[4:6]), tzinfo=VN_TZ)
    except (TypeError, ValueError):
        return None


def parse_tvsi_stock_tick(payload: Any, *, received_at: Optional[float] = None) -> Optional[RealtimeTick]:
    """Đọc payload ``topic/<MÃ>`` của TVSI. Trả ``None`` nếu không phải tick cổ phiếu hợp lệ."""

    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        data = json.loads(payload) if isinstance(payload, str) else payload
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("StockSymbol") or "").strip().upper()
    if not symbol:
        return None
    price = _price(data.get("MatchPrice"))
    if price is None or price <= 0:
        # Chưa khớp lệnh (ví dụ đầu phiên ATO): không có giá để đánh giá.
        return None
    sequence = _int(data.get("SequenceNumber"))
    return RealtimeTick(
        symbol=symbol,
        price=price,
        change=_price(data.get("PriceChange")),
        change_pct=_num(data.get("PriceChangePercent")),
        ref=_price(data.get("BasicPrice")) or _price(data.get("PriorClosePrice")),
        ceiling=_price(data.get("CeilingPrice")),
        floor=_price(data.get("FloorPrice")),
        open=_price(data.get("OpenPrice")),
        high=_price(data.get("HighestPrice")),
        low=_price(data.get("LowestPrice")),
        average=_price(data.get("AveragePrice")),
        volume=_int(data.get("TotalTradingQtty")),
        value=_num(data.get("TotalTradingValue")),
        name=str(data.get("SecurityName") or "").strip(),
        exchange=TVSI_FLOOR_CODES.get(str(data.get("FloorCode") or "").strip(), ""),
        market_status=str(data.get("MarketStatus") or "").strip(),
        sequence=sequence,
        ts=_parse_tvsi_time(data.get("TradeDate"), data.get("Time")),
        received_at=received_at if received_at is not None else time.time(),
    )


TickListener = Callable[[RealtimeTick], None]


class LatestTickStore:
    """Tick mới nhất theo mã, an toàn đa luồng. Thread-safe latest tick per symbol."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ticks: Dict[str, RealtimeTick] = {}

    def update(self, tick: RealtimeTick) -> bool:
        """Lưu tick; trả ``False`` nếu tick đến trễ (sequence cũ hơn cùng ngày)."""

        with self._lock:
            current = self._ticks.get(tick.symbol)
            if (
                current is not None
                and current.sequence is not None
                and tick.sequence is not None
                and tick.sequence <= current.sequence
                and (current.ts is None or tick.ts is None or current.ts.date() == tick.ts.date())
            ):
                return False
            self._ticks[tick.symbol] = tick
            return True

    def get(self, symbol: str) -> Optional[RealtimeTick]:
        with self._lock:
            return self._ticks.get(str(symbol or "").strip().upper())

    def snapshot(self, symbols: Optional[Iterable[str]] = None) -> Dict[str, RealtimeTick]:
        with self._lock:
            if symbols is None:
                return dict(self._ticks)
            wanted = {str(item or "").strip().upper() for item in symbols}
            return {key: value for key, value in self._ticks.items() if key in wanted}

    def clear(self) -> None:
        with self._lock:
            self._ticks.clear()


class RealtimeFeed(Protocol):
    """Giao diện tối thiểu của một feed push. Minimal push feed interface."""

    store: LatestTickStore

    def is_connected(self) -> bool: ...

    def is_subscribed(self, symbol: str) -> bool: ...

    def set_symbols(self, symbols: Iterable[str]) -> None: ...

    def add_listener(self, listener: TickListener) -> None: ...

    def status(self) -> Dict[str, Any]: ...


_active_lock = threading.Lock()
_active_feed: Optional[RealtimeFeed] = None


def set_active_feed(feed: Optional[RealtimeFeed]) -> None:
    global _active_feed
    with _active_lock:
        _active_feed = feed


def get_active_feed() -> Optional[RealtimeFeed]:
    with _active_lock:
        return _active_feed


def get_fresh_tick(symbol: str, max_age_seconds: float) -> Optional[RealtimeTick]:
    """Tick còn mới (``received_at`` trong ``max_age_seconds``) của feed đang kết nối, nếu có."""

    feed = get_active_feed()
    if feed is None or not feed.is_connected():
        return None
    tick = feed.store.get(symbol)
    if tick is None or tick.age_seconds() > max_age_seconds:
        return None
    return tick


def vn_bare_symbol(code: str) -> str:
    """``HPG.VN`` / ``hpg`` → ``HPG`` (topic TVSI dùng mã trần)."""

    value = str(code or "").strip().upper()
    if value.endswith(".VN"):
        value = value[:-3]
    return value


def tick_to_quote(tick: RealtimeTick) -> Any:
    """Đổi tick sang ``UnifiedRealtimeQuote`` để dùng chung contract giá realtime."""

    from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote

    amplitude = None
    if tick.ref and tick.high is not None and tick.low is not None:
        amplitude = round((tick.high - tick.low) / tick.ref * 100, 2)
    quote = UnifiedRealtimeQuote(
        code=tick.symbol,
        name=tick.name,
        source=RealtimeSource.TVSI_MQTT,
        market="vn",
        currency="VND",
        price=tick.price,
        change_pct=tick.change_pct,
        change_amount=tick.change,
        volume=tick.volume,
        amount=tick.value,
        amplitude=amplitude,
        open_price=tick.open,
        high=tick.high,
        low=tick.low,
        pre_close=tick.ref,
    )
    quote.provider_timestamp = tick.ts.isoformat() if tick.ts else None
    # Cùng thuộc tính bổ sung như SsiFetcher để tầng trên đọc trần/sàn.
    setattr(quote, "vn_ceiling", tick.ceiling)
    setattr(quote, "vn_floor", tick.floor)
    setattr(quote, "vn_exchange", tick.exchange)
    return quote


def feed_status() -> Dict[str, Any]:
    feed = get_active_feed()
    if feed is None:
        return {"enabled": False, "state": "disabled"}
    return {"enabled": True, **feed.status()}


def listeners_safe_call(listeners: List[TickListener], tick: RealtimeTick, logger: Any) -> None:
    for listener in list(listeners):
        try:
            listener(tick)
        except Exception as exc:  # một listener lỗi không được chặn các listener khác
            logger.warning("[RealtimeFeed] listener failed for %s: %s", tick.symbol, exc)
