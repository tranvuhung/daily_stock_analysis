# -*- coding: utf-8 -*-
"""
Khởi động / dừng feed giá realtime và engine cảnh báo realtime trong process.

Start/stop the realtime price feed (TVSI MQTT) and the realtime alert engine.
Idempotent: FastAPI lifespan và ``main.py --schedule`` cùng gọi được, chỉ khởi tạo một lần.

Điều kiện chạy: ``REALTIME_FEED_ENABLED=true``, ``AGENT_EVENT_MONITOR_ENABLED=true`` và có
``TVSI_MQTT_HOST``. Thiếu điều kiện thì không làm gì (hành vi cũ giữ nguyên).
Đổi các cấu hình này cần khởi động lại dịch vụ.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_started = False


def _default_config_provider() -> Any:
    from src.config import get_config

    return get_config()


def realtime_alerts_requested(config: Any) -> bool:
    return bool(
        getattr(config, "realtime_feed_enabled", False)
        and getattr(config, "agent_event_monitor_enabled", False)
    )


def start_realtime_alerts(
    config: Optional[Any] = None,
    *,
    config_provider: Optional[Callable[[], Any]] = None,
) -> bool:
    """Khởi động feed + engine nếu được bật. Trả ``True`` nếu đang chạy sau lời gọi."""

    global _started
    provider = config_provider or _default_config_provider
    config = config if config is not None else provider()
    if not realtime_alerts_requested(config):
        return False

    with _lock:
        if _started:
            return True
        try:
            from data_provider.realtime_feed import set_active_feed
            from data_provider.tvsi_mqtt_feed import TvsiMqttFeed, TvsiMqttSettings
            from src.services.alert_worker import AlertWorker
            from src.services.realtime_alert_engine import RealtimeAlertEngine, set_active_realtime_engine

            settings = TvsiMqttSettings.from_config(config)
            feed = TvsiMqttFeed(settings)
            engine = RealtimeAlertEngine(
                feed,
                worker=AlertWorker(config_provider=provider),
                config_provider=provider,
            )
            set_active_feed(feed)
            set_active_realtime_engine(engine)
            engine.start()
            feed.start()
            _started = True
            logger.info("[RealtimeAlerts] realtime feed and alert engine started")
            return True
        except Exception as exc:
            # Một nguồn realtime lỗi không được làm hỏng khởi động dịch vụ.
            logger.warning("[RealtimeAlerts] failed to start: %s", exc)
            _reset_active()
            return False


def stop_realtime_alerts() -> None:
    global _started
    with _lock:
        if not _started:
            return
        from data_provider.realtime_feed import get_active_feed
        from src.services.realtime_alert_engine import get_active_realtime_engine

        engine = get_active_realtime_engine()
        feed = get_active_feed()
        try:
            if engine is not None:
                engine.stop()
            if feed is not None and hasattr(feed, "stop"):
                feed.stop()
        except Exception as exc:
            logger.debug("[RealtimeAlerts] stop error: %s", exc)
        _reset_active()
        _started = False
        logger.info("[RealtimeAlerts] stopped")


def _reset_active() -> None:
    from data_provider.realtime_feed import set_active_feed
    from src.services.realtime_alert_engine import set_active_realtime_engine

    set_active_realtime_engine(None)
    set_active_feed(None)
