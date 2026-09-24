# -*- coding: utf-8 -*-
"""Lý do lỗi trong Run Flow / Chẩn đoán không được lộ tiếng Trung.

Bản ghi cũ lưu ``error_message_sanitized="过滤后无有效新闻"`` (``error_type=NoUsableNews``);
khi đọc phải quy về câu tiếng Anh chuẩn, và metadata event mang ``error_message``
để Web bản địa hoá theo ``error_type``.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.services.run_diagnostics import (  # noqa: E402
    activate_run_diagnostic_context,
    build_run_diagnostic_summary,
    diagnostic_error_reason,
    record_provider_run,
    reset_run_diagnostic_context,
)
from src.services.run_flow import build_history_run_flow_snapshot  # noqa: E402

_CJK = re.compile(r"[一-鿿]")
_LEGACY_CHINESE = "过滤后无有效新闻"


def _history_record(context_snapshot: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=11,
        query_id="query-reason",
        code="HPG",
        name="HPG",
        report_type="detailed",
        created_at=datetime(2026, 9, 24, 16, 19, 39),
        raw_result=json.dumps({"success": True, "model_used": "gemini"}),
        context_snapshot=json.dumps(context_snapshot, ensure_ascii=False),
    )


def _legacy_news_run(**overrides) -> dict:
    run = {
        "trace_id": "trace-reason",
        "data_type": "news_search",
        "provider": "Tavily",
        "operation": "search_stock_news",
        "success": False,
        "latency_ms": 500,
        "error_type": "NoUsableNews",
        "error_message_sanitized": _LEGACY_CHINESE,
        "created_at": "2026-09-24T16:19:39",
    }
    run.update(overrides)
    return run


class DiagnosticErrorReasonTestCase(unittest.TestCase):
    def test_known_error_type_uses_canonical_english(self):
        self.assertEqual(
            diagnostic_error_reason("NoUsableNews", _LEGACY_CHINESE),
            "No usable news after filtering",
        )

    def test_unknown_error_type_keeps_sanitized_server_text(self):
        self.assertEqual(diagnostic_error_reason("TimeoutError", "Request timed out"), "Request timed out")
        self.assertIsNone(diagnostic_error_reason(None, None))


class HistoryRunFlowReasonTestCase(unittest.TestCase):
    def _snapshot(self, provider_runs):
        context_snapshot = {
            "diagnostics": {
                "trace_id": "trace-reason",
                "task_id": "task-reason",
                "query_id": "query-reason",
                "stock_code": "HPG",
                "trigger_source": "api",
                "provider_runs": provider_runs,
                "llm_runs": [],
                "history_runs": [],
                "notification_runs": [],
            }
        }
        return build_history_run_flow_snapshot(_history_record(context_snapshot))

    def test_legacy_chinese_reason_is_not_rendered(self):
        snapshot = self._snapshot([_legacy_news_run()])

        event = next(item for item in snapshot.events if item.type == "provider_run")
        self.assertEqual(event.message, "News Tavily failed: No usable news after filtering")
        self.assertEqual(event.metadata["error_type"], "NoUsableNews")
        self.assertEqual(event.metadata["error_message"], "No usable news after filtering")

        node = next(item for item in snapshot.nodes if item.id == event.node_id)
        self.assertEqual(node.metadata["error_message"], "No usable news after filtering")

        payload = snapshot.model_dump_json()
        self.assertIsNone(_CJK.search(payload), payload)

    def test_unknown_error_type_keeps_server_reason(self):
        snapshot = self._snapshot(
            [_legacy_news_run(error_type="TimeoutError", error_message_sanitized="Request timed out")]
        )
        event = next(item for item in snapshot.events if item.type == "provider_run")
        self.assertEqual(event.message, "News Tavily failed: Request timed out")
        self.assertEqual(event.metadata["error_message"], "Request timed out")


class LiveProviderFlowEventReasonTestCase(unittest.TestCase):
    def test_sse_event_carries_canonical_reason(self):
        events = []
        token = activate_run_diagnostic_context(
            trace_id="trace-live",
            task_id="task-live",
            query_id="query-live",
            stock_code="HPG",
            event_sink=events.append,
        )
        try:
            record_provider_run(
                data_type="news_search",
                provider="Tavily",
                operation="search_stock_news",
                success=False,
                error_type="NoUsableNews",
                error_message=_LEGACY_CHINESE,
            )
        finally:
            reset_run_diagnostic_context(token)

        event = next(item for item in events if item["type"] == "provider_run")
        self.assertEqual(event["message"], "News Tavily failed: No usable news after filtering")
        self.assertEqual(event["metadata"]["error_message"], "No usable news after filtering")
        self.assertIsNone(_CJK.search(json.dumps(event, ensure_ascii=False)))


class DiagnosticsPanelReasonTestCase(unittest.TestCase):
    def test_provider_component_uses_canonical_reason(self):
        diagnostics = {
            "trace_id": "trace-p",
            "task_id": "task-p",
            "query_id": "query-p",
            "stock_code": "HPG",
            "trigger_source": "api",
            "provider_runs": [
                {
                    "trace_id": "trace-p",
                    "data_type": "daily_data",
                    "provider": "VciFetcher",
                    "operation": "get_daily_data",
                    "success": False,
                    "error_type": "NoUsableNews",
                    "error_message_sanitized": _LEGACY_CHINESE,
                }
            ],
            "llm_runs": [],
            "history_runs": [],
            "notification_runs": [],
        }

        summary = build_run_diagnostic_summary(
            context_snapshot={"diagnostics": diagnostics},
            raw_result={"success": True, "model_used": "gemini"},
            report_saved=True,
        )

        component = summary["components"]["daily_data"]
        self.assertEqual(component["details"]["error"], "No usable news after filtering")
        self.assertIsNone(_CJK.search(component["message"]))


class SearchServiceErrorTextTestCase(unittest.TestCase):
    def test_search_service_error_strings_are_not_chinese(self):
        # Chuỗi lỗi đi vào error_message của provider run nên không được là tiếng Trung.
        source = Path(__file__).resolve().parents[1].joinpath("src", "search_service.py").read_text(encoding="utf-8")
        offending = [
            line.strip()
            for line in source.splitlines()
            if re.search(r"\b(error_message|error_msg)\s*=", line) and _CJK.search(line)
        ]
        self.assertEqual(offending, [])


if __name__ == "__main__":
    unittest.main()
