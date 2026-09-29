# -*- coding: utf-8 -*-
"""
Feed giá realtime từ broker MQTT của TVSI (MQTT thuần trên TLS, cổng 443).

TVSI realtime price feed over MQTT (plain MQTT over TLS on port 443; the broker
resets WebSocket handshakes).

- Chỉ subscribe topic giá theo từng mã (mặc định ``topic/{symbol}``). KHÔNG subscribe
  wildcard ``#``: broker còn phát ``order/<id>`` / ``new-order/<account>`` chứa lệnh
  của khách hàng khác. Only per-symbol price topics are subscribed, never ``#``.
- paho chạy ``loop_start()`` trên thread riêng và tự reconnect (1s → 30s).
- Callback tick chạy trên thread MQTT: listener phải xử lý nhanh (engine cảnh báo chỉ
  đẩy tick vào hàng đợi).
- Thông tin đăng nhập chỉ đến từ cấu hình (``TVSI_MQTT_USERNAME`` / ``TVSI_MQTT_PASSWORD``).
"""

from __future__ import annotations

import logging
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from data_provider.realtime_feed import (
    LatestTickStore,
    RealtimeTick,
    TickListener,
    listeners_safe_call,
    parse_tvsi_stock_tick,
)

logger = logging.getLogger(__name__)

DEFAULT_TOPIC_TEMPLATE = "topic/{symbol}"
_FORBIDDEN_TOPIC_CHARS = ("#", "+")


@dataclass(frozen=True)
class TvsiMqttSettings:
    host: str
    port: int = 443
    tls: bool = True
    transport: str = "tcp"
    username: str = ""
    password: str = ""
    topic_template: str = DEFAULT_TOPIC_TEMPLATE
    keepalive: int = 30

    @classmethod
    def from_config(cls, config: Any) -> "TvsiMqttSettings":
        template = str(getattr(config, "tvsi_mqtt_topic_template", "") or DEFAULT_TOPIC_TEMPLATE).strip()
        return cls(
            host=str(getattr(config, "tvsi_mqtt_host", "") or "").strip(),
            port=int(getattr(config, "tvsi_mqtt_port", 443) or 443),
            tls=bool(getattr(config, "tvsi_mqtt_tls", True)),
            transport=str(getattr(config, "tvsi_mqtt_transport", "tcp") or "tcp").strip().lower(),
            username=str(getattr(config, "tvsi_mqtt_username", "") or ""),
            password=str(getattr(config, "tvsi_mqtt_password", "") or ""),
            topic_template=template or DEFAULT_TOPIC_TEMPLATE,
        )

    def topic_for(self, symbol: str) -> str:
        topic = self.topic_template.format(symbol=symbol)
        # Chặn wildcard để không bao giờ nhận nhầm topic lệnh của khách hàng.
        if any(char in topic for char in _FORBIDDEN_TOPIC_CHARS):
            raise ValueError(f"wildcard topics are not allowed: {topic}")
        return topic

    def symbol_from_topic(self, topic: str) -> Optional[str]:
        prefix, _, suffix = self.topic_template.partition("{symbol}")
        if not topic.startswith(prefix) or (suffix and not topic.endswith(suffix)):
            return None
        end = len(topic) - len(suffix) if suffix else len(topic)
        symbol = topic[len(prefix):end].strip().upper()
        return symbol or None


class TvsiMqttFeed:
    """Kết nối broker TVSI, subscribe theo tập mã, lưu tick mới nhất và báo listener."""

    def __init__(
        self,
        settings: TvsiMqttSettings,
        *,
        store: Optional[LatestTickStore] = None,
        client_factory: Optional[Any] = None,
    ) -> None:
        if not settings.host:
            raise ValueError("TVSI_MQTT_HOST is required for the realtime feed")
        self.settings = settings
        self.store = store or LatestTickStore()
        self._client_factory = client_factory
        self._client: Any = None
        self._lock = threading.RLock()
        self._symbols: Set[str] = set()
        self._subscribed: Set[str] = set()
        self._listeners: List[TickListener] = []
        self._connected = False
        self._state = "stopped"
        self._last_message_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._message_count = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        with self._lock:
            if self._client is not None:
                return
            self._client = self._build_client()
            self._state = "connecting"
        try:
            self._client.connect_async(self.settings.host, self.settings.port, keepalive=self.settings.keepalive)
            self._client.loop_start()
            logger.info(
                "[TvsiMqttFeed] connecting to %s:%s (transport=%s, tls=%s)",
                self.settings.host,
                self.settings.port,
                self.settings.transport,
                self.settings.tls,
            )
        except Exception as exc:
            with self._lock:
                self._state = "error"
                self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("[TvsiMqttFeed] start failed: %s", self._last_error)

    def stop(self) -> None:
        with self._lock:
            client, self._client = self._client, None
            self._connected = False
            self._subscribed.clear()
            self._state = "stopped"
        if client is None:
            return
        try:
            client.disconnect()
            client.loop_stop()
        except Exception as exc:
            logger.debug("[TvsiMqttFeed] stop error: %s", exc)

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            client = self._client_factory()
        else:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"dsa-alerts-{uuid.uuid4().hex[:12]}",
                transport=self.settings.transport,
            )
        if self.settings.tls and hasattr(client, "tls_set"):
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        if self.settings.username:
            client.username_pw_set(self.settings.username, self.settings.password)
        if hasattr(client, "reconnect_delay_set"):
            client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    # ------------------------------------------------------------------ subscriptions
    def set_symbols(self, symbols: Iterable[str]) -> None:
        wanted = {str(item or "").strip().upper() for item in symbols if str(item or "").strip()}
        with self._lock:
            self._symbols = wanted
            client = self._client if self._connected else None
            to_add = wanted - self._subscribed
            to_remove = self._subscribed - wanted
        if client is None:
            return
        for symbol in sorted(to_remove):
            self._unsubscribe(client, symbol)
        for symbol in sorted(to_add):
            self._subscribe(client, symbol)

    def _subscribe(self, client: Any, symbol: str) -> None:
        try:
            client.subscribe(self.settings.topic_for(symbol), qos=0)
            with self._lock:
                self._subscribed.add(symbol)
        except Exception as exc:
            logger.warning("[TvsiMqttFeed] subscribe %s failed: %s", symbol, exc)

    def _unsubscribe(self, client: Any, symbol: str) -> None:
        try:
            client.unsubscribe(self.settings.topic_for(symbol))
        except Exception as exc:
            logger.debug("[TvsiMqttFeed] unsubscribe %s failed: %s", symbol, exc)
        with self._lock:
            self._subscribed.discard(symbol)

    def is_subscribed(self, symbol: str) -> bool:
        with self._lock:
            return self._connected and str(symbol or "").strip().upper() in self._subscribed

    # ------------------------------------------------------------------ callbacks (thread MQTT)
    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        failed = bool(getattr(reason_code, "is_failure", False))
        with self._lock:
            if failed:
                self._connected = False
                self._state = "error"
                self._last_error = f"connect refused: {reason_code}"
                symbols: Set[str] = set()
            else:
                self._connected = True
                self._state = "connected"
                self._last_error = None
                self._subscribed.clear()
                symbols = set(self._symbols)
        if failed:
            logger.warning("[TvsiMqttFeed] %s", self._last_error)
            return
        logger.info("[TvsiMqttFeed] connected; subscribing %d symbols", len(symbols))
        for symbol in sorted(symbols):
            self._subscribe(client, symbol)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any) -> None:
        reason = args[1] if len(args) >= 2 else (args[0] if args else None)
        with self._lock:
            was_running = self._client is not None
            self._connected = False
            self._subscribed.clear()
            if was_running:
                self._state = "reconnecting"
                self._last_error = f"disconnected: {reason}"
        if was_running:
            logger.warning("[TvsiMqttFeed] disconnected (%s); paho will reconnect", reason)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        symbol = self.settings.symbol_from_topic(str(getattr(message, "topic", "")))
        if symbol is None:
            return
        tick = parse_tvsi_stock_tick(getattr(message, "payload", b""))
        if tick is None or tick.symbol != symbol:
            return
        with self._lock:
            self._last_message_at = tick.received_at
            self._message_count += 1
            listeners = list(self._listeners)
        if self.store.update(tick):
            listeners_safe_call(listeners, tick, logger)

    # ------------------------------------------------------------------ status
    def add_listener(self, listener: TickListener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: TickListener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def status(self) -> Dict[str, Any]:
        with self._lock:
            last = self._last_message_at
            return {
                "state": self._state,
                "connected": self._connected,
                "source": "tvsi_mqtt",
                "subscribed": len(self._subscribed),
                "last_message_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last)) if last else None,
                "last_message_age_seconds": round(time.time() - last, 1) if last else None,
                "message_count": self._message_count,
                "last_error": self._last_error,
            }

    def latest(self, symbol: str) -> Optional[RealtimeTick]:
        return self.store.get(symbol)
