# -*- coding: utf-8 -*-
"""
Engine cảnh báo realtime: đánh giá rule giá mã Việt Nam ngay khi nhận tick từ feed push.

Realtime alert engine: evaluates price rules for Vietnamese symbols on every tick
from the push feed (TVSI MQTT), and hands triggered results to the shared
``AlertWorker.process_result`` pipeline (trigger history, DB cooldown, notification
channels from Settings, ``alert_triggered`` SSE event).

Luồng / Flow:
- Thread MQTT gọi ``_on_tick`` → chỉ ghi tick mới nhất vào ``_pending`` (gộp theo mã).
- Thread ``realtime-alert-engine`` lấy tick ra, đánh giá trong bộ nhớ, gọi ``process_result``.
- Mỗi ``RULE_REFRESH_SECONDS`` (hoặc khi CRUD rule gọi ``request_reload``) nạp lại rule và
  cập nhật tập mã subscribe của feed.
- Ngoài phiên khớp lệnh VN thì không đánh giá.
- Khi feed mất kết nối, ``claims`` trả ``False`` nên engine định kỳ tự đánh giá lại qua SSI.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from data_provider.realtime_feed import RealtimeTick, tick_to_quote, vn_bare_symbol
from src.services.event_bus import publish_event
from src.services.price_alert_rules import is_vn_price_alert_window, is_vn_price_rule

logger = logging.getLogger(__name__)

RULE_REFRESH_SECONDS = 30.0
PRICE_EVENT_MIN_INTERVAL_SECONDS = 1.0
FEED_STATUS_EVENT_SECONDS = 10.0
# Kích hoạt nhưng không tạo được cooldown (vd. mọi kênh gửi lỗi): chờ bấy nhiêu giây mới thử lại,
# tránh gửi/ghi lịch sử theo từng tick. Retry backoff when a trigger left no DB cooldown.
TRIGGER_RETRY_SECONDS = 60.0


class RealtimeAlertEngine:
    """Đánh giá rule giá theo tick. Tick-driven price rule evaluation."""

    def __init__(
        self,
        feed: Any,
        *,
        worker: Any,
        config_provider: Callable[[], Any],
        session_checker: Callable[[], bool] = is_vn_price_alert_window,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.feed = feed
        self.worker = worker
        self.config_provider = config_provider
        self.session_checker = session_checker
        self.now = now
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending: Dict[str, RealtimeTick] = {}
        self._rules_by_symbol: Dict[str, List[Any]] = {}
        self._claimed_keys: Set[str] = set()
        self._last_reload = 0.0
        self._reload_requested = True
        self._last_price_event: Dict[str, float] = {}
        self._last_status_event = 0.0
        # rule key -> epoch giây đến khi được xử lý kích hoạt tiếp (cooldown DB hoặc retry backoff).
        self._suppressed_until: Dict[str, float] = {}
        self.stats: Dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self.feed.add_listener(self._on_tick)
        self._thread = threading.Thread(target=self._run, name="realtime-alert-engine", daemon=True)
        self._thread.start()
        logger.info("[RealtimeAlertEngine] started")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        remove = getattr(self.feed, "remove_listener", None)
        if callable(remove):
            remove(self._on_tick)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def request_reload(self) -> None:
        """Yêu cầu nạp lại rule ngay (gọi sau khi tạo/sửa/xoá/bật/tắt rule)."""

        with self._lock:
            self._reload_requested = True
        self._wake.set()

    # ------------------------------------------------------------------ claims
    def claims(self, runtime_rule: Any) -> bool:
        """Rule này đang do engine realtime xử lý (feed kết nối và đã subscribe mã)?"""

        if not self.feed.is_connected():
            return False
        with self._lock:
            if runtime_rule.key not in self._claimed_keys:
                return False
        symbol = vn_bare_symbol(getattr(runtime_rule.rule, "stock_code", ""))
        return self.feed.is_subscribed(symbol)

    # ------------------------------------------------------------------ feed callback (thread MQTT)
    def _on_tick(self, tick: RealtimeTick) -> None:
        with self._lock:
            watched = tick.symbol in self._rules_by_symbol
            if watched:
                self._pending[tick.symbol] = tick
        if watched:
            self._wake.set()

    # ------------------------------------------------------------------ worker thread
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            try:
                self._maybe_reload()
                self._drain()
                self._maybe_publish_status()
            except Exception as exc:  # engine không được chết vì một lỗi lẻ
                logger.warning("[RealtimeAlertEngine] loop error: %s", exc, exc_info=True)

    def _maybe_reload(self) -> None:
        with self._lock:
            due = self._reload_requested or (self.now() - self._last_reload) >= RULE_REFRESH_SECONDS
        if due:
            self.reload_rules()

    def reload_rules(self) -> None:
        config = self.config_provider()
        rules_by_symbol: Dict[str, List[Any]] = defaultdict(list)
        claimed: Set[str] = set()
        for runtime_rule in self.worker._load_runtime_rules(config):
            if runtime_rule.source != "db":
                continue
            rule = runtime_rule.rule
            if not is_vn_price_rule(rule):
                continue
            symbol = vn_bare_symbol(str(getattr(rule, "stock_code", "") or ""))
            rules_by_symbol[symbol].append(runtime_rule)
            claimed.add(runtime_rule.key)
        with self._lock:
            self._rules_by_symbol = dict(rules_by_symbol)
            self._claimed_keys = claimed
            self._last_reload = self.now()
            self._reload_requested = False
            self._pending = {key: value for key, value in self._pending.items() if key in self._rules_by_symbol}
            # Rule có thể vừa được sửa / reset cooldown: đọc lại cooldown từ DB ở lần kích hoạt sau.
            self._suppressed_until = {}
        self.feed.set_symbols(rules_by_symbol.keys())
        logger.debug("[RealtimeAlertEngine] %d rules on %d symbols", len(claimed), len(rules_by_symbol))

    def _drain(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
            rules_by_symbol = self._rules_by_symbol
        if not pending:
            return
        in_session = self.session_checker()
        for symbol, tick in pending.items():
            self._maybe_publish_price(tick)
            if not in_session:
                self.stats["out_of_session"] += 1
                continue
            quote = tick_to_quote(tick)
            for runtime_rule in rules_by_symbol.get(symbol, []):
                self.evaluate_tick(runtime_rule, quote)

    def evaluate_tick(self, runtime_rule: Any, quote: Any) -> Dict[str, Any]:
        self.stats["evaluated"] += 1
        try:
            result = self.worker.service.evaluate_price_quote(runtime_rule.rule, quote, data_source="tvsi_mqtt")
        except Exception as exc:
            logger.warning("[RealtimeAlertEngine] evaluate failed for %s: %s", runtime_rule.key, exc)
            self.stats["failed"] += 1
            return {}
        # Chỉ kết quả kích hoạt mới đi qua pipeline ghi lịch sử / thông báo; tick không thoả
        # điều kiện hoặc thiếu dữ liệu không ghi lịch sử để tránh hàng nghìn dòng mỗi phút.
        # Rule đang cooldown vẫn được đánh giá (để chế độ ``cross`` cập nhật giá quan sát), nhưng
        # không ghi trigger: engine định kỳ ghi mỗi chu kỳ, còn tick thì tới vài lần mỗi giây.
        if result.get("record_status") == "triggered":
            if self._is_suppressed(runtime_rule):
                self.stats["cooldown_skipped"] += 1
                return result
            self.stats["triggered"] += 1
            self.worker.process_result(runtime_rule, result, self.stats)
            self._remember_suppression(runtime_rule)
        return result

    def _is_suppressed(self, runtime_rule: Any) -> bool:
        key = runtime_rule.key
        until = self._suppressed_until.get(key)
        if until is None:
            until = self._db_cooldown_epoch(runtime_rule)
            if until is None:
                return False
            self._suppressed_until[key] = until
        if self.now() < until:
            return True
        self._suppressed_until.pop(key, None)
        return False

    def _remember_suppression(self, runtime_rule: Any) -> None:
        until = self._db_cooldown_epoch(runtime_rule)
        self._suppressed_until[runtime_rule.key] = until if until is not None else self.now() + TRIGGER_RETRY_SECONDS

    def _db_cooldown_epoch(self, runtime_rule: Any) -> Optional[float]:
        lookup = getattr(self.worker, "active_db_cooldown_until", None)
        if not callable(lookup):
            return None
        until = lookup(runtime_rule)
        if not isinstance(until, datetime):
            return None
        return until.timestamp()

    def _maybe_publish_price(self, tick: RealtimeTick) -> None:
        now = self.now()
        last = self._last_price_event.get(tick.symbol, 0.0)
        if now - last < PRICE_EVENT_MIN_INTERVAL_SECONDS:
            return
        self._last_price_event[tick.symbol] = now
        publish_event("price_update", tick.to_public_dict())

    def _maybe_publish_status(self) -> None:
        now = self.now()
        if now - self._last_status_event < FEED_STATUS_EVENT_SECONDS:
            return
        self._last_status_event = now
        publish_event("feed_status", self.status())

    def watched_symbols(self) -> List[str]:
        with self._lock:
            return sorted(self._rules_by_symbol)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            rule_count = sum(len(items) for items in self._rules_by_symbol.values())
            symbols = len(self._rules_by_symbol)
        return {
            **self.feed.status(),
            "enabled": True,
            "rules": rule_count,
            "symbols": symbols,
            "in_session": bool(self.session_checker()),
        }


_active_engine_lock = threading.Lock()
_active_engine: Optional[RealtimeAlertEngine] = None


def get_active_realtime_engine() -> Optional[RealtimeAlertEngine]:
    with _active_engine_lock:
        return _active_engine


def set_active_realtime_engine(engine: Optional[RealtimeAlertEngine]) -> None:
    global _active_engine
    with _active_engine_lock:
        _active_engine = engine


def request_realtime_rules_reload() -> None:
    engine = get_active_realtime_engine()
    if engine is not None:
        engine.request_reload()
