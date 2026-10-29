# -*- coding: utf-8 -*-
"""Bộ đếm bằng chứng tin giữ lại chính các tin Agent đã dùng và số lần gọi tìm kiếm.

The evidence accumulator keeps the items the Agent consumed and the search call count
(regression for FPT #159: search_performed=false with 18 results, sources untraceable).
"""

from src.agent.news_evidence import (
    NewsEvidenceAccumulator,
    activate_news_evidence_scope,
    get_current_news_evidence,
    record_news_evidence,
    reset_news_evidence_scope,
)


def test_records_calls_even_without_hits():
    acc = NewsEvidenceAccumulator()
    acc.record(0)
    assert acc.calls == 1
    assert acc.total == 0
    assert acc.items == []


def test_items_are_deduplicated_by_url_and_capped():
    acc = NewsEvidenceAccumulator()
    item = {"title": "FPT KQKD Q3", "url": "https://a/1"}
    acc.record(2, [item, dict(item)])
    acc.record(
        NewsEvidenceAccumulator.MAX_ITEMS + 5,
        [{"title": f"t{i}", "url": f"https://b/{i}"} for i in range(NewsEvidenceAccumulator.MAX_ITEMS + 5)],
    )
    items = acc.items
    assert len(items) == NewsEvidenceAccumulator.MAX_ITEMS
    assert [it["url"] for it in items].count("https://a/1") == 1
    assert acc.calls == 2


def test_scope_collects_items_from_tool_calls():
    token = activate_news_evidence_scope()
    try:
        record_news_evidence(1, [{"title": "x", "url": "https://c/1"}])
        evidence = get_current_news_evidence()
        assert evidence is not None
        assert evidence.calls == 1
        assert evidence.items[0]["url"] == "https://c/1"
    finally:
        reset_news_evidence_scope(token)
