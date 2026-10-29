# -*- coding: utf-8 -*-
"""Count of news evidence actually consumed by the Agent at runtime.

The empty-news disclosure must reflect the news evidence actually used by this analysis. In Agent
mode the Agent gathers intel by calling search tools itself, so the count can only come from what
those tools really returned. It must not be replaced by an extra `search_stock_news()` call made
after the analysis: that follow-up query is unrelated to the evidence the Agent consumed and
distorts the result in both directions — the Agent used news but gets labelled "no news evidence
included" because the follow-up failed, or the Agent got no news but the warning is wrongly
suppressed because the follow-up found results.

Usage: the pipeline opens the scope before `executor.run()` and reads it afterwards; search tools
record how many items they actually handed to the Agent when returning results. Tools run in a
ThreadPoolExecutor and `src/agent/runner.py` submits tasks via `contextvars.copy_context()`, so the
**mutable** accumulator inside the ContextVar is the same object in worker and parent threads, and
increments made in tool threads are visible to the pipeline. Do not replace it with a ContextVar
holding an immutable value — the parent thread would not see the updates.
"""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar, Token
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


class NewsEvidenceAccumulator:
    """Thread-safe accumulator of news items actually consumed by the Agent in this analysis."""

    # Giới hạn số tin lưu vào snapshot để không phình bản ghi lịch sử.
    # Cap on items kept for the snapshot so history rows stay small.
    MAX_ITEMS = 30

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._calls = 0
        self._items: List[Dict[str, Any]] = []
        self._seen_urls: set = set()

    def record(self, count: int, items: Optional[Iterable[Dict[str, Any]]] = None) -> None:
        """Record the number of items a search tool actually returned; zero hits must be recorded too (as 0).

        ``items`` (title/url/source/...) là chính các tin đã trao cho Agent, lưu vào snapshot để
        số liệu trong báo cáo truy được nguồn. / The items handed to the Agent, kept so report
        figures can be traced back to their source.
        """
        try:
            value = int(count)
        except (TypeError, ValueError):
            value = 0
        if value < 0:
            value = 0
        with self._lock:
            self._total += value
            self._calls += 1
            for item in items or ():
                if len(self._items) >= self.MAX_ITEMS:
                    break
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                if url and url in self._seen_urls:
                    continue
                if url:
                    self._seen_urls.add(url)
                self._items.append(dict(item))

    @property
    def calls(self) -> int:
        """Số lần tool tìm kiếm thực sự chạy (kể cả không trúng). / Search tool invocations."""
        with self._lock:
            return self._calls

    @property
    def items(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def resolve(self, *, search_available: bool) -> Optional[int]:
        """Collapse into the three-state semantics of `news_result_count`.

        - Search channel unavailable: `None`, i.e. no search ran; disclose "no search channel configured".
        - Channel available: start from `0` and count whatever the Agent's tool calls returned.

        The case where the channel is available but the Agent never searched is deliberately mapped
        to `0` rather than `None`: the report really has no news evidence, but "no search channel
        configured" would be a false explanation, while "no usable news data obtained" holds in
        both sub-cases.
        """
        if not search_available:
            return None
        return self.total


_CURRENT_ACCUMULATOR: ContextVar[Optional[NewsEvidenceAccumulator]] = ContextVar(
    "news_evidence_accumulator",
    default=None,
)


def activate_news_evidence_scope() -> Token:
    """Open an evidence scope for one Agent run and return the reset token."""
    return _CURRENT_ACCUMULATOR.set(NewsEvidenceAccumulator())


def get_current_news_evidence() -> Optional[NewsEvidenceAccumulator]:
    return _CURRENT_ACCUMULATOR.get()


def reset_news_evidence_scope(token: Optional[Token]) -> None:
    if token is None:
        return
    try:
        _CURRENT_ACCUMULATOR.reset(token)
    except Exception as exc:  # pragma: no cover - defensive fail-open
        logger.warning("news evidence scope reset failed: %s", exc)


def record_news_evidence(count: int, items: Optional[Iterable[Dict[str, Any]]] = None) -> None:
    """Called by Agent search tools.

    Silently ignored when no scope is active: non-Agent paths maintain the count themselves, and
    tools may also be called outside an Agent analysis (e.g. follow-up news lookups from the report
    page); none of those should affect this analysis's disclosure decision.
    """
    accumulator = _CURRENT_ACCUMULATOR.get()
    if accumulator is None:
        return
    accumulator.record(count, items)
