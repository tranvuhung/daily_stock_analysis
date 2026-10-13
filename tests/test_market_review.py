# -*- coding: utf-8 -*-
"""Kiểm thử luồng điểm lại thị trường Việt Nam (vi/en).

Tests for the localized Vietnam market review wrappers.
"""

import importlib
import json
import os
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

def _build_optional_module_stubs() -> dict[str, ModuleType]:
    stubs: dict[str, ModuleType] = {}
    google_module: ModuleType | None = None

    for module_name in ("google.generativeai", "google.genai", "anthropic"):
        try:
            importlib.import_module(module_name)
            continue
        except ImportError:
            stub = ModuleType(module_name)
            stubs[module_name] = stub
            if not module_name.startswith("google."):
                continue
            if google_module is None:
                try:
                    google_module = importlib.import_module("google")
                except ImportError:
                    google_module = ModuleType("google")
                    stubs["google"] = google_module
            setattr(google_module, module_name.split(".", 1)[1], stub)

    return stubs


sys.modules.update(_build_optional_module_stubs())
import src.core.market_review as market_review_module
from src.config import Config
from src.llm.generation_backend import GenerationError, GenerationErrorCode
from src.services.run_diagnostics import activate_run_diagnostic_context, reset_run_diagnostic_context
from src.storage import AnalysisHistory, DatabaseManager

run_market_review = market_review_module.run_market_review


class MarketReviewLocalizationTestCase(unittest.TestCase):
    def _make_notifier(self) -> MagicMock:
        notifier = MagicMock()
        notifier.save_report_to_file.return_value = "/tmp/market_review.md"
        notifier.is_available.return_value = True
        notifier.send.return_value = True
        return notifier

    def _make_analyzer(self, report: str = "VN body", **extra) -> MagicMock:
        market_analyzer = MagicMock()
        market_analyzer.run_daily_review_with_snapshot.return_value = SimpleNamespace(
            report=report,
            market_light_snapshot={"region": "vn", "trade_date": "2026-04-10", "score": 60},
            **extra,
        )
        return market_analyzer

    def test_resolve_market_review_regions_always_vn(self) -> None:
        # Mọi giá trị hợp lệ / cũ đều quy về vn. / Every valid or legacy value maps to vn.
        for raw_region in (None, "", "vn", "VN", "both", " CN,US,cn ", "jp", "invalid", ",,"):
            with self.subTest(raw_region=raw_region):
                self.assertEqual(market_review_module._resolve_market_review_regions(raw_region), ["vn"])

    def test_wrapper_titles_are_localized(self) -> None:
        en = market_review_module._get_market_review_text("en")
        self.assertEqual(en["root_title"], "# 🎯 Market Review")
        self.assertEqual(en["vn_title"], "# Vietnam Market Recap")
        vi = market_review_module._get_market_review_text("vi")
        self.assertEqual(vi["root_title"], "# 🎯 Điểm lại thị trường")
        # zh/ko đã bị loại bỏ và quy về vi. / Removed zh/ko fall back to vi.
        self.assertEqual(market_review_module._get_market_review_text("zh"), vi)

    def test_run_market_review_uses_english_notification_title(self) -> None:
        notifier = self._make_notifier()
        market_analyzer = self._make_analyzer("## 2026-04-10 Vietnam Market Recap\n\nBody")

        with patch.object(
            market_review_module,
            "get_config",
            return_value=SimpleNamespace(report_language="en", market_review_region="vn"),
        ), patch.object(
            market_review_module,
            "MarketAnalyzer",
            return_value=market_analyzer,
        ) as analyzer_cls, patch.object(market_review_module, "_persist_market_review_history") as persist_history:
            result = run_market_review(notifier, send_notification=True)

        self.assertEqual(result, "## 2026-04-10 Vietnam Market Recap\n\nBody")
        self.assertEqual(analyzer_cls.call_args.kwargs["region"], "vn")
        saved_content = notifier.save_report_to_file.call_args.args[0]
        self.assertTrue(saved_content.startswith("[dsa-market-region]: # (vn)\n\n# 🎯 Market Review\n\n"))
        sent_content = notifier.send.call_args.args[0]
        self.assertTrue(sent_content.startswith("[dsa-market-region]: # (vn)\n\n🎯 Market Review\n\n"))
        self.assertTrue(notifier.send.call_args.kwargs["email_send_to_all"])
        self.assertEqual(notifier.send.call_args.kwargs["route_type"], "report")
        persist_history.assert_called_once()
        self.assertTrue(persist_history.call_args.kwargs["query_id"].startswith("market_review_"))

    def test_run_market_review_uses_vietnamese_title_by_default(self) -> None:
        notifier = self._make_notifier()
        with patch.object(
            market_review_module,
            "get_config",
            return_value=SimpleNamespace(report_language="vi", market_review_region="vn"),
        ), patch.object(
            market_review_module, "MarketAnalyzer", return_value=self._make_analyzer()
        ), patch.object(market_review_module, "_persist_market_review_history"):
            run_market_review(notifier, send_notification=True)

        sent_content = notifier.send.call_args.args[0]
        self.assertTrue(sent_content.startswith("[dsa-market-region]: # (vn)\n\n🎯 Điểm lại thị trường\n\n"))

    def test_run_market_review_legacy_region_override_runs_vn(self) -> None:
        notifier = self._make_notifier()
        with patch.object(
            market_review_module,
            "get_config",
            return_value=SimpleNamespace(report_language="vi", market_review_region="cn"),
        ), patch.object(
            market_review_module, "MarketAnalyzer", return_value=self._make_analyzer()
        ) as analyzer_cls, patch.object(
            market_review_module, "_persist_market_review_history"
        ) as persist_history:
            result = run_market_review(notifier, send_notification=False, override_region="cn,us")

        self.assertEqual(result, "VN body")
        self.assertEqual(analyzer_cls.call_count, 1)
        self.assertEqual(analyzer_cls.call_args.kwargs["region"], "vn")
        self.assertEqual(persist_history.call_args.kwargs["region"], "vn")
        snapshots = persist_history.call_args.kwargs["market_light_snapshots"]
        self.assertEqual(set(snapshots), {"vn"})
        self.assertEqual(snapshots["vn"]["trade_date"], "2026-04-10")

    def test_run_market_review_can_skip_report_file_for_context_generation(self) -> None:
        notifier = self._make_notifier()

        with patch.object(
            market_review_module,
            "get_config",
            return_value=SimpleNamespace(report_language="vi", market_review_region="vn"),
        ), patch.object(
            market_review_module,
            "MarketAnalyzer",
            return_value=self._make_analyzer(),
        ), patch.object(market_review_module, "_persist_market_review_history") as persist_history:
            result = run_market_review(
                notifier,
                send_notification=False,
                return_structured=True,
                save_report_file=False,
            )

        self.assertIsInstance(result, market_review_module.MarketReviewRunResult)
        self.assertEqual(result.report, "VN body")
        notifier.save_report_to_file.assert_not_called()
        persist_history.assert_called_once()

    def test_run_market_review_passes_request_config_to_generation(self) -> None:
        notifier = self._make_notifier()
        request_config = SimpleNamespace(report_language="en", market_review_region="vn")
        global_config = SimpleNamespace(report_language="vi", market_review_region="vn")

        with patch.object(
            market_review_module,
            "get_config",
            return_value=global_config,
        ), patch.object(
            market_review_module,
            "MarketAnalyzer",
            return_value=self._make_analyzer("English market review body"),
        ) as analyzer_cls, patch.object(market_review_module, "_persist_market_review_history") as persist_history:
            result = run_market_review(
                notifier,
                config=request_config,
                send_notification=False,
                return_structured=True,
            )

        self.assertEqual(analyzer_cls.call_args.kwargs["config"], request_config)
        self.assertEqual(persist_history.call_args.kwargs["config"], request_config)
        self.assertTrue(
            notifier.save_report_to_file.call_args.args[0].startswith(
                "[dsa-market-region]: # (vn)\n\n# 🎯 Market Review\n\n"
            )
        )
        self.assertEqual(result.market_review_payload["language"], "en")
        self.assertEqual(result.report, "English market review body")

    def test_run_market_review_merge_mode_skips_standalone_notification(self) -> None:
        notifier = self._make_notifier()
        with patch.object(
            market_review_module,
            "get_config",
            return_value=SimpleNamespace(report_language="vi", market_review_region="vn"),
        ), patch.object(
            market_review_module,
            "MarketAnalyzer",
            return_value=self._make_analyzer("## Thị trường hôm nay\n\nNội dung."),
        ), patch.object(market_review_module, "_persist_market_review_history"):
            result = run_market_review(notifier, send_notification=True, merge_notification=True)

        self.assertEqual(result, "## Thị trường hôm nay\n\nNội dung.")
        notifier.send.assert_not_called()

    def test_run_market_review_reraises_generation_backend_config_error(self) -> None:
        notifier = self._make_notifier()
        backend_error = GenerationError(
            error_code=GenerationErrorCode.BACKEND_NOT_CONFIGURED,
            stage="generation",
            retryable=False,
            fallbackable=False,
            backend="codex",
            details={
                "field": "GENERATION_BACKEND",
                "requested_backend": "codex",
            },
        )
        market_analyzer = MagicMock()
        market_analyzer.run_daily_review_with_snapshot.side_effect = backend_error

        with patch.object(
            market_review_module,
            "MarketAnalyzer",
            return_value=market_analyzer,
        ):
            with self.assertRaises(GenerationError) as exc_info:
                run_market_review(
                    notifier,
                    config=SimpleNamespace(report_language="vi", market_review_region="vn"),
                    send_notification=False,
                    save_report_file=False,
                    persist_history=False,
                )

        self.assertIs(exc_info.exception, backend_error)
        notifier.save_report_to_file.assert_not_called()
        notifier.send.assert_not_called()

    def test_render_market_review_payload_markdown_does_not_repeat_title(self) -> None:
        markdown = market_review_module._render_market_review_payload_markdown(
            {
                "title": "Điểm lại thị trường 2026-06-03",
                "sections": [
                    {
                        "key": "daily_review",
                        "title": "Điểm lại thị trường 2026-06-03",
                        "markdown": "> Chỉ số phân hóa.\n\n### 1. Tổng quan\nNội dung",
                    }
                ],
            },
            wrapper_title="🎯 Điểm lại thị trường",
        )

        self.assertEqual(markdown.count("Điểm lại thị trường 2026-06-03"), 1)
        self.assertTrue(markdown.startswith("🎯 Điểm lại thị trường\n\n## Điểm lại thị trường 2026-06-03"))

    def test_render_market_review_payload_markdown_prefixes_vn_region_metadata(self) -> None:
        payload = {
            "region": "vn",
            "title": "Điểm lại thị trường",
            "sections": [{"key": "overview", "title": "Tổng quan", "markdown": "> Theo dõi VN30."}],
        }
        markdown = market_review_module._render_market_review_payload_markdown(
            payload, wrapper_title="🎯 Điểm lại thị trường"
        )

        self.assertTrue(markdown.startswith("[dsa-market-region]: # (vn)\n\n🎯 Điểm lại thị trường"))
        self.assertEqual(markdown.count("[dsa-market-region]: # (vn)"), 1)
        # Section overview không lặp tiêu đề. / The overview section title is not repeated.
        self.assertNotIn("### Tổng quan", markdown)

    def test_render_market_review_payload_markdown_skips_unknown_region_metadata(self) -> None:
        markdown = market_review_module._render_market_review_payload_markdown(
            {"region": "us", "markdown_report": "Body"}
        )
        self.assertEqual(markdown, "Body")

    def test_persist_market_review_history_saves_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_db_path = os.environ.get("DATABASE_PATH")
            os.environ["DATABASE_PATH"] = os.path.join(temp_dir, "market_review_history.db")
            Config._instance = None
            DatabaseManager.reset_instance()
            try:
                saved = market_review_module._persist_market_review_history(
                    review_report="## Thị trường hôm nay\n\nNội dung điểm lại",
                    markdown_report="# 🎯 Điểm lại thị trường\n\n## Thị trường hôm nay\n\nNội dung điểm lại",
                    region="vn",
                    config=SimpleNamespace(report_language="vi"),
                    query_id="market-task-001",
                    market_light_snapshots={
                        "vn": {
                            "region": "vn",
                            "trade_date": "2026-03-06",
                            "status": "red",
                            "score": 30,
                            "label": "Phòng thủ",
                            "temperature_label": "Yếu",
                            "reasons": ["test"],
                            "guidance": "test",
                            "dimensions": {
                                "breadth": {"score": 20, "available": True},
                                "index": {"score": 30, "available": True},
                                "limit": {"score": 10, "available": True},
                            },
                            "data_quality": "ok",
                        }
                    },
                    market_review_payload={
                        "version": 1,
                        "kind": "market_review",
                        "region": "vn",
                        "sections": [{"title": "Thị trường hôm nay", "markdown": "Nội dung điểm lại"}],
                    },
                )

                self.assertGreater(saved, 0)
                db = DatabaseManager.get_instance()
                with db.get_session() as session:
                    row = session.query(AnalysisHistory).filter(
                        AnalysisHistory.query_id == "market-task-001"
                    ).first()
                    self.assertIsNotNone(row)
                    self.assertEqual(row.id, saved)
                    self.assertEqual(row.code, market_review_module.MARKET_REVIEW_HISTORY_CODE)
                    self.assertEqual(row.name, "Market Review")
                    self.assertEqual(row.report_type, market_review_module.MARKET_REVIEW_REPORT_TYPE)
                    self.assertEqual(row.news_content, "## Thị trường hôm nay\n\nNội dung điểm lại")
                    self.assertIn("# 🎯 Điểm lại thị trường", row.raw_result)
                    self.assertIn('"market_light_snapshots"', row.context_snapshot)
                    self.assertIn('"market_review_payload"', row.context_snapshot)
                    self.assertIn('"trade_date": "2026-03-06"', row.context_snapshot)
                    snapshot = json.loads(row.context_snapshot or "{}")
                    self.assertEqual(snapshot["market_review_region"], "vn")
                    self.assertEqual(snapshot["market_review_payload"]["region"], "vn")
                    self.assertIn("analysis_context_pack_overview", snapshot)
            finally:
                DatabaseManager.reset_instance()
                Config._instance = None
                if old_db_path is None:
                    os.environ.pop("DATABASE_PATH", None)
                else:
                    os.environ["DATABASE_PATH"] = old_db_path

    def test_run_market_review_persists_notification_diagnostics_after_history_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_db_path = os.environ.get("DATABASE_PATH")
            os.environ["DATABASE_PATH"] = os.path.join(temp_dir, "market_review_notification.db")
            Config._instance = None
            DatabaseManager.reset_instance()
            query_id = "market-task-notification"
            notifier = self._make_notifier()
            market_analyzer = MagicMock()
            market_analyzer.run_daily_review_with_snapshot.return_value = SimpleNamespace(
                report="## Thị trường hôm nay\n\nNội dung điểm lại",
                market_light_snapshot={"region": "vn", "trade_date": "2026-03-06", "score": 60},
            )
            token = activate_run_diagnostic_context(
                trace_id="trace-market-notification",
                task_id=query_id,
                query_id=query_id,
                stock_code=market_review_module.MARKET_REVIEW_HISTORY_CODE,
                trigger_source="api",
            )
            try:
                with patch.object(market_review_module, "MarketAnalyzer", return_value=market_analyzer):
                    result = run_market_review(
                        notifier,
                        config=SimpleNamespace(report_language="vi", market_review_region="vn"),
                        send_notification=True,
                        query_id=query_id,
                        trigger_source="api",
                    )

                self.assertEqual(result, "## Thị trường hôm nay\n\nNội dung điểm lại")
                db = DatabaseManager.get_instance()
                with db.get_session() as session:
                    row = session.query(AnalysisHistory).filter(
                        AnalysisHistory.query_id == query_id
                    ).first()
                    self.assertIsNotNone(row)
                    context_snapshot = json.loads(row.context_snapshot)
                    notification_runs = context_snapshot["diagnostics"]["notification_runs"]
                    self.assertEqual(notification_runs[-1]["status"], "success")
                    self.assertTrue(notification_runs[-1]["success"])
            finally:
                reset_run_diagnostic_context(token)
                DatabaseManager.reset_instance()
                Config._instance = None
                if old_db_path is None:
                    os.environ.pop("DATABASE_PATH", None)
                else:
                    os.environ["DATABASE_PATH"] = old_db_path

    def test_run_market_review_reuses_generated_query_id_for_notification_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_db_path = os.environ.get("DATABASE_PATH")
            os.environ["DATABASE_PATH"] = os.path.join(temp_dir, "market_review_generated_query.db")
            Config._instance = None
            DatabaseManager.reset_instance()
            notifier = self._make_notifier()
            market_analyzer = MagicMock()
            market_analyzer.run_daily_review_with_snapshot.return_value = SimpleNamespace(
                report="## Thị trường hôm nay\n\nNội dung điểm lại",
                market_light_snapshot={"region": "vn", "trade_date": "2026-03-06", "score": 60},
            )
            token = activate_run_diagnostic_context(
                trace_id="trace-market-generated",
                task_id="task-market-generated",
                stock_code=market_review_module.MARKET_REVIEW_HISTORY_CODE,
                trigger_source="cli",
            )
            try:
                with patch.object(market_review_module, "MarketAnalyzer", return_value=market_analyzer):
                    result = run_market_review(
                        notifier,
                        config=SimpleNamespace(report_language="vi", market_review_region="vn"),
                        send_notification=True,
                        trigger_source="cli",
                    )

                self.assertEqual(result, "## Thị trường hôm nay\n\nNội dung điểm lại")
                db = DatabaseManager.get_instance()
                with db.get_session() as session:
                    rows = session.query(AnalysisHistory).all()
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertTrue(row.query_id.startswith("market_review_"))
                    context_snapshot = json.loads(row.context_snapshot)
                    notification_runs = context_snapshot["diagnostics"]["notification_runs"]
                    self.assertEqual(notification_runs[-1]["status"], "success")
                    self.assertTrue(notification_runs[-1]["success"])
            finally:
                reset_run_diagnostic_context(token)
                DatabaseManager.reset_instance()
                Config._instance = None
                if old_db_path is None:
                    os.environ.pop("DATABASE_PATH", None)
                else:
                    os.environ["DATABASE_PATH"] = old_db_path



if __name__ == "__main__":
    unittest.main()
