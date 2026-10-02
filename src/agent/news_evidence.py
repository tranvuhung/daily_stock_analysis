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
from typing import Optional

logger = logging.getLogger(__name__)


class NewsEvidenceAccumulator:
    """Thread-safe accumulator of news items actually consumed by the Agent in this analysis."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0

    def record(self, count: int) -> None:
        """Record the number of items a search tool actually returned; zero hits must be recorded too (as 0)."""
        try:
            value = int(count)
        except (TypeError, ValueError):
            value = 0
        if value < 0:
            value = 0
        with self._lock:
            self._total += value

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


def record_news_evidence(count: int) -> None:
    """Called by Agent search tools.

    Silently ignored when no scope is active: non-Agent paths maintain the count themselves, and
    tools may also be called outside an Agent analysis (e.g. follow-up news lookups from the report
    page); none of those should affect this analysis's disclosure decision.
    """
    accumulator = _CURRENT_ACCUMULATOR.get()
    if accumulator is None:
        return
    accumulator.record(count)
