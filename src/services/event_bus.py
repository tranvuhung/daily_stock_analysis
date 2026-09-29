# -*- coding: utf-8 -*-
"""
Event bus trong process để đẩy sự kiện cảnh báo / giá live lên Web qua SSE.

In-process event bus used to push alert and live-price events to SSE clients.
Theo mẫu ``AnalysisTaskQueue._broadcast_event``: mỗi subscriber là một
``asyncio.Queue`` gắn với event loop của FastAPI; publisher (thread worker, thread
MQTT) chuyển sự kiện vào loop bằng ``call_soon_threadsafe``.

Chỉ đúng khi chạy một process (uvicorn không multi-worker), giống task stream hiện có.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 500


class EventBus:
    """Pub/sub đơn giản, an toàn giữa thread và asyncio."""

    def __init__(self, name: str = "events") -> None:
        self.name = name
        self._lock = threading.Lock()
        self._subscribers: List[Tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = []

    def subscribe(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> asyncio.Queue:
        """Tạo queue cho một kết nối SSE. Phải gọi trong event loop đang chạy."""

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append((queue, loop))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [item for item in self._subscribers if item[0] is not queue]

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Phát sự kiện tới mọi subscriber; queue đầy thì bỏ sự kiện của subscriber đó."""

        event = {"type": event_type, "data": data or {}}
        with self._lock:
            subscribers = list(self._subscribers)
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(_put_nowait_dropping, queue, event)
            except RuntimeError:
                # Event loop đã đóng (client ngắt / app shutdown).
                self.unsubscribe(queue)
            except Exception as exc:
                logger.debug("[EventBus:%s] publish failed: %s", self.name, exc)


def _put_nowait_dropping(queue: asyncio.Queue, event: Dict[str, Any]) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # Client quá chậm: bỏ sự kiện thay vì chặn publisher.
        pass


alert_event_bus = EventBus("alerts")


def publish_event(event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
    alert_event_bus.publish(event_type, data)
