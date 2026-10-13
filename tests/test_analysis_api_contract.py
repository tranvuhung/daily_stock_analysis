# -*- coding: utf-8 -*-
"""Regression tests for analysis API/report-type contracts."""

import asyncio
from concurrent.futures import Future
from datetime import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

_ORIGINAL_ENVIRON = dict(os.environ)
_MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
_MODULE_ENV_FILE = Path(_MODULE_TEMP_DIR.name) / ".env"
_MODULE_ENV_FILE.write_text("STOCK_LIST=HPG,VCB\n", encoding="utf-8")
os.environ["ENV_FILE"] = str(_MODULE_ENV_FILE)

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    from api.app import create_app
    from api.v1.endpoints import analysis as analysis_endpoint_module
    from api.v1.endpoints.analysis import (
        trigger_analysis,
        trigger_market_review,
        _handle_sync_analysis,
        _build_analysis_report,
        _load_sync_fundamental_sources,
        get_analysis_status,
        get_task_list,
    )
except Exception:  # pragma: no cover - optional dependency environments
    create_app = None
    analysis_endpoint_module = None
    trigger_analysis = None
    trigger_market_review = None
    _handle_sync_analysis = None
    _build_analysis_report = None
    _load_sync_fundamental_sources = None
    get_analysis_status = None
    get_task_list = None

from src.enums import ReportType
from src.config import Config
from src.services.analysis_service import AnalysisService
from src.services.image_stock_extractor import _call_litellm_vision
from src.services.task_queue import AnalysisTaskQueue, DuplicateTaskError, TaskInfo as QueueTaskInfo, TaskStatus


def tearDownModule() -> None:
    current_test = os.environ.get("PYTEST_CURRENT_TEST")
    for key in list(os.environ):
        if key == "PYTEST_CURRENT_TEST":
            continue
        if key not in _ORIGINAL_ENVIRON:
            os.environ.pop(key, None)
    os.environ.update(_ORIGINAL_ENVIRON)
    if current_test is not None:
        os.environ["PYTEST_CURRENT_TEST"] = current_test
    _MODULE_TEMP_DIR.cleanup()


def _analysis_context_pack_overview() -> dict:
    return {
        "pack_version": "1.0",
        "created_at": "2026-04-10T08:30:00+00:00",
        "subject": {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "market": "vn",
        },
        "blocks": [
            {
                "key": "quote",
                "label": "行情",
                "status": "available",
                "source": "mock",
                "warnings": [],
                "missing_reasons": [],
            },
            {
                "key": "news",
                "label": "新闻",
                "status": "missing",
                "source": None,
                "warnings": [],
                "missing_reasons": ["news_context_missing"],
            },
        ],
        "counts": {
            "available": 1,
            "missing": 1,
            "not_supported": 0,
            "fallback": 0,
            "stale": 0,
            "estimated": 0,
            "partial": 0,
            "fetch_failed": 0,
        },
        "data_quality": {
            "overall_score": 88,
            "level": "good",
            "block_scores": {
                "quote": 100,
                "daily_bars": 100,
                "technical": 100,
                "news": 35,
                "fundamentals": 100,
                "chip": 100,
            },
            "limitations": [],
        },
        "warnings": ["news_context_missing"],
        "metadata": {
            "trigger_source": "api",
            "news_result_count": 0,
        },
    }


def _market_phase_summary() -> dict:
    return {
        "market": "vn",
        "phase": "intraday",
        "market_local_time": "2026-03-27T10:00:00+07:00",
        "session_date": "2026-03-27",
        "effective_daily_bar_date": "2026-03-26",
        "is_trading_day": True,
        "is_market_open_now": True,
        "is_partial_bar": True,
        "minutes_to_open": None,
        "minutes_to_close": 300,
        "trigger_source": "api",
        "analysis_intent": "auto",
        "warnings": ["partial_bar"],
    }


class AnalysisApiContractTestCase(unittest.TestCase):
    def test_market_review_region_is_authoritative_across_active_task_lifecycle(self) -> None:
        if (
            trigger_market_review is None
            or get_analysis_status is None
            or get_task_list is None
            or analysis_endpoint_module is None
        ):
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        class CapturingExecutor:
            def __init__(self) -> None:
                self.calls = []

            def submit(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))
                return Future()

        # Chỉ còn vùng "vn": cấu hình/request vùng cũ đều được chuẩn hoá về "vn".
        # Only the "vn" region remains: legacy configured/request regions normalize to "vn".
        cases = (
            ("vn", None, "vn"),
            ("us", None, "vn"),
            ("vn", "vn", "vn"),
        )
        original_queue_instance = AnalysisTaskQueue._instance
        try:
            for configured_region, request_region, expected_region in cases:
                with self.subTest(
                    configured_region=configured_region,
                    request_region=request_region,
                ):
                    AnalysisTaskQueue._instance = None
                    queue = AnalysisTaskQueue(max_workers=1)
                    executor = CapturingExecutor()
                    queue._executor = executor
                    events = []
                    queue._broadcast_event = (
                        lambda event_type, data: events.append((event_type, data))
                    )
                    request = analysis_endpoint_module.MarketReviewRequest(
                        send_notification=False,
                        region=request_region,
                    )
                    config = Config(market_review_region=configured_region)
                    report = SimpleNamespace(
                        report=f"{expected_region} report",
                        market_review_payload={
                            "kind": "market_review",
                            "region": expected_region,
                            "sections": [],
                        },
                    )

                    with patch.object(
                        analysis_endpoint_module,
                        "_try_acquire_market_review_lock",
                        return_value=object(),
                    ), patch.object(
                        analysis_endpoint_module,
                        "_build_market_review_runtime",
                        return_value=(MagicMock(), MagicMock(), MagicMock()),
                    ), patch(
                        "src.core.market_review.run_market_review",
                        return_value=report,
                    ) as run_market_review, patch(
                        "api.v1.endpoints.analysis.get_task_queue",
                        return_value=queue,
                    ), patch.object(
                        analysis_endpoint_module,
                        "_release_market_review_lock",
                    ):
                        accepted = trigger_market_review(request=request, config=config)
                        pending = get_analysis_status(accepted.task_id)
                        task_list = get_task_list(status=None, limit=20)
                        queue.update_task_progress(
                            accepted.task_id,
                            progress=5,
                            message="queued",
                        )

                        worker, args, kwargs = executor.calls[0]
                        worker(*args, **kwargs)
                        completed = get_analysis_status(accepted.task_id)

                    self.assertEqual(accepted.region, expected_region)
                    self.assertEqual(pending.status, "pending")
                    self.assertEqual(pending.region, expected_region)
                    self.assertEqual(task_list.tasks[0].region, expected_region)
                    self.assertEqual(completed.status, "completed")
                    self.assertEqual(completed.region, expected_region)
                    self.assertEqual(
                        completed.market_review_payload["region"],
                        expected_region,
                    )
                    self.assertEqual(
                        run_market_review.call_args.kwargs["override_region"],
                        expected_region,
                    )
                    for event_type in (
                        "task_created",
                        "task_progress",
                        "task_started",
                        "task_completed",
                    ):
                        payload = next(
                            payload
                            for current_type, payload in events
                            if current_type == event_type
                        )
                        self.assertEqual(payload["region"], expected_region)
        finally:
            AnalysisTaskQueue._instance = original_queue_instance

    def test_trigger_market_review_accepts_background_task(self) -> None:
        if trigger_market_review is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")
        task_queue = MagicMock()
        task_queue.submit_background_task.return_value = SimpleNamespace(task_id="market-task-1")
        request = analysis_endpoint_module.MarketReviewRequest(send_notification=False)
        config = SimpleNamespace(
            trading_day_check_enabled=False,
            report_language="en",
            market_review_region="vn",
        )
        lock_token = object()

        with patch.object(
            analysis_endpoint_module,
            "_try_acquire_market_review_lock",
            return_value=lock_token,
        ), patch("api.v1.endpoints.analysis.get_task_queue", return_value=task_queue):
            response = trigger_market_review(
                request=request,
                config=config,
            )

        self.assertEqual(response.status, "accepted")
        self.assertFalse(response.send_notification)
        self.assertEqual(response.region, "vn")
        self.assertEqual(response.task_id, "market-task-1")
        task_queue.submit_background_task.assert_called_once()
        args, kwargs = task_queue.submit_background_task.call_args
        self.assertTrue(callable(args[0]))
        self.assertEqual(kwargs["stock_code"], "market_review")
        self.assertEqual(kwargs["stock_name"], "Market review")
        self.assertEqual(kwargs["message"], "Market review task submitted")
        self.assertEqual(kwargs["region"], "vn")

    def test_trigger_market_review_accepts_request_level_report_language(self) -> None:
        if trigger_market_review is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        request = analysis_endpoint_module.MarketReviewRequest(
            send_notification=True,
            report_language="vi",
        )
        config = SimpleNamespace(trading_day_check_enabled=False, report_language="en", market_review_region="vn")
        lock_token = object()
        task_payload: dict[str, object] = {}

        runtime_notifier = MagicMock()
        runtime_search = MagicMock()
        runtime_analyzer = MagicMock()

        task_queue = MagicMock()

        def _capture_background_task(task_fn, **kwargs):
            task_payload["background_task"] = task_fn
            return SimpleNamespace(task_id="market-task-1")

        task_queue.submit_background_task.side_effect = _capture_background_task

        with patch.object(
            analysis_endpoint_module,
            "_try_acquire_market_review_lock",
            return_value=lock_token,
        ), patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            return_value=(runtime_notifier, runtime_analyzer, runtime_search),
        ), patch("src.core.market_review.run_market_review") as run_market_review, patch(
            "api.v1.endpoints.analysis.get_task_queue",
            return_value=task_queue,
        ), patch.object(
            analysis_endpoint_module,
            "_release_market_review_lock",
            return_value=None,
        ):
            trigger_market_review(request=request, config=config)
            self.assertIn("background_task", task_payload)
            task_payload["background_task"]()

        call_kwargs = run_market_review.call_args.kwargs
        self.assertEqual(call_kwargs["send_notification"], True)
        self.assertEqual(call_kwargs["override_region"], "vn")
        self.assertEqual(call_kwargs["trigger_source"], "api")
        runtime_config = call_kwargs.get("config")
        self.assertIsNotNone(runtime_config)
        self.assertEqual(getattr(runtime_config, "report_language", None), "vi")
        self.assertIsNot(runtime_config, config)

    def test_trigger_market_review_accepts_camel_case_report_language_alias(self) -> None:
        if trigger_market_review is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        request = analysis_endpoint_module.MarketReviewRequest.model_validate({
            "send_notification": True,
            "reportLanguage": "vi",
        })
        config = SimpleNamespace(trading_day_check_enabled=False, report_language="en", market_review_region="vn")
        task_payload: dict[str, object] = {}

        runtime_notifier = MagicMock()
        runtime_search = MagicMock()
        runtime_analyzer = MagicMock()

        task_queue = MagicMock()

        def _capture_background_task(task_fn, **kwargs):
            task_payload["background_task"] = task_fn
            return SimpleNamespace(task_id="market-task-1")

        task_queue.submit_background_task.side_effect = _capture_background_task

        with patch.object(
            analysis_endpoint_module,
            "_try_acquire_market_review_lock",
            return_value=object(),
        ), patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            return_value=(runtime_notifier, runtime_analyzer, runtime_search),
        ), patch("src.core.market_review.run_market_review") as run_market_review, patch(
            "api.v1.endpoints.analysis.get_task_queue",
            return_value=task_queue,
        ), patch.object(
            analysis_endpoint_module,
            "_release_market_review_lock",
            return_value=None,
        ):
            response = trigger_market_review(request=request, config=config)
            self.assertEqual(response.status, "accepted")
            self.assertIn("background_task", task_payload)
            task_payload["background_task"]()

        call_kwargs = run_market_review.call_args.kwargs
        runtime_config = call_kwargs.get("config")
        self.assertEqual(getattr(runtime_config, "report_language", None), "vi")
        self.assertEqual(call_kwargs["trigger_source"], "api")

    def test_trigger_market_review_passes_canonical_request_region_without_mutating_config(self) -> None:
        if trigger_market_review is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        request = analysis_endpoint_module.MarketReviewRequest.model_validate({
            "send_notification": False,
            "region": " KR, jp, KR ",
        })
        # Cấu hình vùng cũ "us" phải giữ nguyên (không bị ghi đè), còn vùng hiệu lực là "vn".
        # Legacy configured region must stay untouched while the effective region is "vn".
        config = SimpleNamespace(report_language="en", market_review_region="us")
        task_payload: dict[str, object] = {}
        task_queue = MagicMock()

        def _capture_background_task(task_fn, **kwargs):
            task_payload["background_task"] = task_fn
            return SimpleNamespace(task_id="market-task-region")

        task_queue.submit_background_task.side_effect = _capture_background_task

        with patch.object(
            analysis_endpoint_module,
            "_try_acquire_market_review_lock",
            return_value=object(),
        ), patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            return_value=(MagicMock(), MagicMock(), MagicMock()),
        ), patch(
            "src.core.market_review.run_market_review",
            return_value="report",
        ) as run_market_review, patch(
            "api.v1.endpoints.analysis.get_task_queue",
            return_value=task_queue,
        ), patch.object(
            analysis_endpoint_module,
            "_release_market_review_lock",
            return_value=None,
        ):
            response = trigger_market_review(request=request, config=config)
            task_payload["background_task"]()

        self.assertEqual(response.task_id, "market-task-region")
        self.assertEqual(response.region, "vn")
        self.assertEqual(task_queue.submit_background_task.call_args.kwargs["region"], "vn")
        self.assertEqual(run_market_review.call_args.kwargs["override_region"], "vn")
        self.assertEqual(config.market_review_region, "us")

    def test_market_review_background_uses_runtime_region_when_request_omits_override(self) -> None:
        if analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        runtime_config = SimpleNamespace(market_review_region="us")
        with patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            return_value=(MagicMock(), MagicMock(), MagicMock()),
        ), patch(
            "src.core.market_review.run_market_review",
            return_value="report",
        ) as run_market_review, patch.object(
            analysis_endpoint_module,
            "_release_market_review_lock",
        ):
            analysis_endpoint_module._run_market_review_background(
                send_notification=False,
                effective_region="us",
                config=runtime_config,
            )

        call_kwargs = run_market_review.call_args.kwargs
        self.assertIs(call_kwargs["config"], runtime_config)
        self.assertEqual(call_kwargs["override_region"], "us")
        self.assertEqual(call_kwargs["config"].market_review_region, "us")

    def test_trigger_market_review_rejects_duplicate_submission(self) -> None:
        if trigger_market_review is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        task_queue = MagicMock()
        request = analysis_endpoint_module.MarketReviewRequest(send_notification=True)
        config = SimpleNamespace(
            trading_day_check_enabled=False,
            report_language="en",
            market_review_region="vn",
        )

        with patch.object(
            analysis_endpoint_module,
            "_try_acquire_market_review_lock",
            return_value=None,
        ), patch("api.v1.endpoints.analysis.get_task_queue", return_value=task_queue):
            with self.assertRaises(Exception) as ctx:
                trigger_market_review(
                    request=request,
                    config=config,
                )

        self.assertEqual(getattr(ctx.exception, "status_code", None), 409)
        task_queue.submit_background_task.assert_not_called()

    def test_trigger_market_review_rejects_when_shared_lock_is_held(self) -> None:
        if trigger_market_review is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        from src.core.market_review_lock import (
            release_market_review_lock,
            try_acquire_market_review_lock,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            config = SimpleNamespace(
                trading_day_check_enabled=False,
                database_path=str(Path(temp_dir) / "stock_analysis.db"),
                report_language="vi",
                market_review_region="cn",
            )
            lock_token = try_acquire_market_review_lock(config)
            self.assertIsNotNone(lock_token)

            task_queue = MagicMock()
            try:
                with patch("api.v1.endpoints.analysis.get_task_queue", return_value=task_queue):
                    with self.assertRaises(Exception) as ctx:
                        trigger_market_review(
                            request=analysis_endpoint_module.MarketReviewRequest(
                                send_notification=True
                            ),
                            config=config,
                        )
            finally:
                release_market_review_lock(lock_token)

        self.assertEqual(getattr(ctx.exception, "status_code", None), 409)
        task_queue.submit_background_task.assert_not_called()

    def test_trigger_market_review_submits_even_when_configured_markets_closed(self) -> None:
        if trigger_market_review is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        task_queue = MagicMock()
        task_queue.submit_background_task.return_value = SimpleNamespace(task_id="market-task-manual")
        request = analysis_endpoint_module.MarketReviewRequest(send_notification=True)
        config = SimpleNamespace(trading_day_check_enabled=True, market_review_region="cn")
        lock_token = object()

        with patch(
            "src.core.trading_calendar.get_open_markets_today",
            return_value=set(),
        ) as get_open_markets_today, patch(
            "src.core.trading_calendar.compute_effective_region",
            return_value="",
        ) as compute_effective_region, patch.object(
            analysis_endpoint_module,
            "_try_acquire_market_review_lock",
            return_value=lock_token,
        ) as acquire, patch("api.v1.endpoints.analysis.get_task_queue", return_value=task_queue):
            response = trigger_market_review(
                request=request,
                config=config,
            )

        self.assertEqual(response.status, "accepted")
        self.assertEqual(response.task_id, "market-task-manual")
        get_open_markets_today.assert_not_called()
        compute_effective_region.assert_not_called()
        acquire.assert_called_once_with(config)
        task_queue.submit_background_task.assert_called_once()

    def test_run_market_review_background_uses_configured_pipeline(self) -> None:
        if analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        config = SimpleNamespace(
            has_search_capability_enabled=lambda: True,
            bocha_api_keys=["bocha"],
            tavily_api_keys=["tavily"],
            anspire_api_keys=["anspire"],
            brave_api_keys=["brave"],
            serpapi_keys=["serpapi"],
            minimax_api_keys=["minimax"],
            searxng_base_urls=["http://searxng.local"],
            searxng_public_instances_enabled=False,
            news_max_age_days=5,
            news_strategy_profile="balanced",
            gemini_api_key="gemini-key",
            openai_api_key=None,
        )

        runtime_notifier = MagicMock()
        runtime_search = MagicMock()
        runtime_analyzer = MagicMock()
        with patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            return_value=(runtime_notifier, runtime_analyzer, runtime_search),
        ), patch("src.core.market_review.run_market_review") as run_market_review:
            analysis_endpoint_module._run_market_review_background(
                send_notification=False,
                effective_region="cn,us",
                lock_token=None,
                config=config,
            )

        run_market_review.assert_called_once_with(
            notifier=runtime_notifier,
            analyzer=runtime_analyzer,
            search_service=runtime_search,
            config=config,
            send_notification=False,
            override_region="cn,us",
            return_structured=True,
            trigger_source="api",
        )

    def test_market_review_runtime_initializes_analyzer_for_litellm_provider(self) -> None:
        if analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        config = SimpleNamespace(
            has_search_capability_enabled=lambda: False,
            gemini_api_key=None,
            openai_api_key=None,
            litellm_model="anthropic/claude-sonnet-4-6",
            llm_model_list=[],
            anthropic_api_keys=["sk-ant-test-value"],
        )

        with patch("src.notification.NotificationService"), \
             patch("src.analyzer.GeminiAnalyzer") as analyzer_cls:
            analyzer_cls.return_value.is_available.return_value = True

            _, analyzer, search_service = analysis_endpoint_module._build_market_review_runtime(config)

        analyzer_cls.assert_called_once_with(config=config)
        self.assertIs(analyzer, analyzer_cls.return_value)
        self.assertIsNone(search_service)

    def test_run_market_review_background_returns_non_empty_result_payload(self) -> None:
        if analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        runtime_notifier = MagicMock()
        runtime_search = MagicMock()
        runtime_analyzer = MagicMock()
        market_review_config = SimpleNamespace()
        with patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            return_value=(runtime_notifier, runtime_analyzer, runtime_search),
        ), patch("src.core.market_review.run_market_review", return_value="report") as run_market_review:
            result = analysis_endpoint_module._run_market_review_background(
                send_notification=False,
                effective_region="cn",
                lock_token=None,
                config=market_review_config,
            )

        self.assertEqual(result, {"result": "report", "region": "cn"})
        run_market_review.assert_called_once_with(
            notifier=runtime_notifier,
            analyzer=runtime_analyzer,
            search_service=runtime_search,
            config=market_review_config,
            send_notification=False,
            override_region="cn",
            return_structured=True,
            trigger_source="api",
        )

    def test_run_market_review_uses_request_scoped_config_language(self) -> None:
        from src.core.market_review import run_market_review

        global_config = SimpleNamespace(report_language="vi", market_review_region="cn")
        scoped_config = SimpleNamespace(report_language="en", market_review_region="cn")
        notifier = MagicMock()
        notifier.save_report_to_file.return_value = "market_review.md"
        notifier.is_available.return_value = False
        review_result = SimpleNamespace(
            report="Market review body",
            market_light_snapshot={},
            structured_payload={
                "kind": "market_review",
                "language": "en",
                "sections": [{"key": "summary", "title": "Summary", "markdown": "Market review body"}],
            },
        )
        market_analyzer = MagicMock()
        market_analyzer.run_daily_review_with_snapshot.return_value = review_result

        with patch("src.core.market_review.get_config", return_value=global_config) as get_config_mock, \
             patch("src.core.market_review.MarketAnalyzer", return_value=market_analyzer) as market_analyzer_cls, \
             patch("src.core.market_review._persist_market_review_history") as persist:
            result = run_market_review(
                notifier=notifier,
                search_service=MagicMock(),
                send_notification=False,
                return_structured=True,
                config=scoped_config,
            )

        get_config_mock.assert_not_called()
        market_analyzer_cls.assert_called_once()
        self.assertIs(market_analyzer_cls.call_args.kwargs["config"], scoped_config)
        self.assertEqual(result.market_review_payload["language"], "en")
        self.assertEqual(persist.call_args.kwargs["config"].report_language, "en")

    def test_get_analysis_status_returns_market_review_report_from_queue(self) -> None:
        if get_analysis_status is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        queue = MagicMock()
        queue.get_task.return_value = SimpleNamespace(
            task_id="market-task-1",
            region="cn,us",
            stock_code="market_review",
            stock_name="Market review",
            status=analysis_endpoint_module.TaskStatusEnum.COMPLETED,
            progress=100,
            result={
                "result": "市场复盘报告示例文本",
                "market_review_payload": {
                    "kind": "market_review",
                    "region": "cn,us",
                    "sections": [],
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
        )

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            status = get_analysis_status("market-task-1")

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.market_review_report, "市场复盘报告示例文本")
        self.assertEqual(status.market_review_payload["kind"], "market_review")
        self.assertEqual(status.market_review_payload["region"], "cn,us")
        self.assertEqual(status.region, "cn,us")
        self.assertIsNone(status.result)

    def test_get_analysis_status_accepts_cancel_states_from_queue(self) -> None:
        if get_analysis_status is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        for task_status in (
            analysis_endpoint_module.TaskStatusEnum.CANCEL_REQUESTED,
            analysis_endpoint_module.TaskStatusEnum.CANCELLED,
        ):
            with self.subTest(task_status=task_status.value):
                queue = MagicMock()
                queue.get_task.return_value = SimpleNamespace(
                    task_id=f"task-{task_status.value}",
                    region=None,
                    trace_id=f"trace-{task_status.value}",
                    stock_code="HPG",
                    stock_name="Hòa Phát",
                    status=task_status,
                    progress=42,
                    result=None,
                    error=None,
                    original_query=None,
                    selection_source=None,
                    analysis_phase="auto",
                    skills=[],
                )

                with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
                    status = get_analysis_status(f"task-{task_status.value}")

                self.assertEqual(status.status, task_status.value)
                self.assertEqual(status.progress, 42)
                self.assertIsNone(status.result)

    def test_get_analysis_status_normalizes_completed_queue_result_contract(self) -> None:
        if get_analysis_status is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        created_at = datetime(2026, 5, 21, 17, 40, 0)
        queue = MagicMock()
        queue.get_task.return_value = SimpleNamespace(
            task_id="task-queue-1",
            region=None,
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=analysis_endpoint_module.TaskStatusEnum.COMPLETED,
            progress=100,
            result={
                "stock_code": "HPG",
                "stock_name": "Hòa Phát",
                "report": {
                    "meta": {"query_id": "task-queue-1", "stock_code": "HPG"},
                    "summary": {"analysis_summary": "summary", "operation_advice": "Không nên mua"},
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
            created_at=created_at,
            completed_at=datetime(2026, 5, 21, 17, 45, 0),
        )

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            status = get_analysis_status("task-queue-1")

        self.assertEqual(status.status, "completed")
        self.assertIsNotNone(status.result)
        self.assertEqual(status.result.query_id, "task-queue-1")
        self.assertEqual(status.result.stock_code, "HPG")
        self.assertEqual(status.result.stock_name, "Hòa Phát")
        self.assertEqual(status.result.created_at, created_at.isoformat())
        self.assertEqual(
            status.result.report["summary"]["analysis_summary"],
            "summary",
        )
        self.assertEqual(status.result.report["summary"]["operation_advice"], "Không nên mua")
        self.assertEqual(status.result.report["summary"]["action"], "avoid")
        self.assertEqual(status.result.report["summary"]["action_label"], "Tránh")

    def test_get_analysis_status_prefers_raw_result_action_over_summary_action(self) -> None:
        if get_analysis_status is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        created_at = datetime(2026, 5, 21, 17, 40, 0)
        queue = MagicMock()
        queue.get_task.return_value = SimpleNamespace(
            task_id="task-queue-action-conflict",
            region=None,
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=analysis_endpoint_module.TaskStatusEnum.COMPLETED,
            progress=100,
            result={
                "stock_code": "HPG",
                "stock_name": "Hòa Phát",
                "report": {
                    "meta": {
                        "query_id": "task-queue-action-conflict",
                        "stock_code": "HPG",
                        "report_type": "detailed",
                        "report_language": "vi",
                    },
                    "summary": {
                        "analysis_summary": "summary",
                        "operation_advice": "持有观察",
                        "action": "buy",
                    },
                    "details": {
                        "raw_result": {
                            "operation_advice": "持有观察",
                            "action": "watch",
                            "report_language": "vi",
                        },
                    },
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
            created_at=created_at,
            completed_at=datetime(2026, 5, 21, 17, 45, 0),
        )

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            status = get_analysis_status("task-queue-action-conflict")

        self.assertEqual(status.status, "completed")
        self.assertIsNotNone(status.result)
        self.assertEqual(status.result.report["summary"]["action"], "watch")
        self.assertEqual(status.result.report["summary"]["action_label"], "Quan sát")

    def test_get_analysis_status_preserves_zero_sentiment_score_when_aligning_action(self) -> None:
        if get_analysis_status is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        created_at = datetime(2026, 5, 21, 17, 40, 0)
        queue = MagicMock()
        queue.get_task.return_value = SimpleNamespace(
            task_id="task-queue-zero-score",
            region=None,
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=analysis_endpoint_module.TaskStatusEnum.COMPLETED,
            progress=100,
            result={
                "stock_code": "HPG",
                "stock_name": "Hòa Phát",
                "report": {
                    "meta": {
                        "query_id": "task-queue-zero-score",
                        "stock_code": "HPG",
                        "report_type": "detailed",
                        "report_language": "vi",
                    },
                    "summary": {
                        "analysis_summary": "Xu hướng xấu đi rõ rệt",
                        "operation_advice": "Nắm giữ",
                        "sentiment_score": 0,
                    },
                    "details": {
                        "raw_result": {
                            "operation_advice": "Nắm giữ",
                            "report_language": "vi",
                        },
                    },
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
            created_at=created_at,
            completed_at=datetime(2026, 5, 21, 17, 45, 0),
        )

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            status = get_analysis_status("task-queue-zero-score")

        self.assertEqual(status.status, "completed")
        self.assertIsNotNone(status.result)
        self.assertEqual(status.result.report["summary"]["sentiment_score"], 0)
        self.assertEqual(status.result.report["summary"]["action"], "sell")
        self.assertEqual(status.result.report["summary"]["action_label"], "Bán")

    def test_get_analysis_status_preserves_zero_sentiment_score_when_enriching_report(self) -> None:
        if get_analysis_status is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        created_at = datetime(2026, 5, 21, 17, 40, 0)
        queue = MagicMock()
        queue.get_task.return_value = SimpleNamespace(
            task_id="task-queue-zero-score-enriched",
            region=None,
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=analysis_endpoint_module.TaskStatusEnum.COMPLETED,
            progress=100,
            result={
                "stock_code": "HPG",
                "stock_name": "Hòa Phát",
                "report": {
                    "meta": {
                        "query_id": "task-queue-zero-score-enriched",
                        "stock_code": "HPG",
                        "report_type": "detailed",
                        "report_language": "vi",
                    },
                    "summary": {
                        "analysis_summary": "Xu hướng xấu đi rõ rệt",
                        "operation_advice": "Nắm giữ",
                        "sentiment_score": 0,
                    },
                    "details": {
                        "raw_result": {
                            "operation_advice": "Nắm giữ",
                            "report_language": "vi",
                        },
                    },
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
            created_at=created_at,
            completed_at=datetime(2026, 5, 21, 17, 45, 0),
        )

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue), \
             patch(
                 "api.v1.endpoints.analysis._load_sync_fundamental_sources",
                 return_value=({}, None, None),
             ):
            status = get_analysis_status("task-queue-zero-score-enriched")

        self.assertEqual(status.status, "completed")
        self.assertIsNotNone(status.result)
        self.assertEqual(status.result.report["summary"]["sentiment_score"], 0)
        self.assertEqual(status.result.report["summary"]["action"], "sell")
        self.assertEqual(status.result.report["summary"]["action_label"], "Bán")

    def test_get_analysis_status_preserves_queue_report_created_at_when_enriching(self) -> None:
        if get_analysis_status is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        created_at = datetime(2026, 5, 21, 17, 40, 0)
        queue = MagicMock()
        queue.get_task.return_value = SimpleNamespace(
            task_id="task-queue-2",
            region=None,
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=analysis_endpoint_module.TaskStatusEnum.COMPLETED,
            progress=100,
            result={
                "stock_code": "HPG",
                "stock_name": "Hòa Phát",
                "report": {
                    "meta": {"query_id": "task-queue-2", "stock_code": "HPG"},
                    "summary": {"analysis_summary": "summary"},
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
            created_at=created_at,
            completed_at=datetime(2026, 5, 21, 17, 45, 0),
        )

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue), \
             patch(
                 "api.v1.endpoints.analysis._load_sync_fundamental_sources",
                 return_value=({}, None, None),
             ):
            status = get_analysis_status("task-queue-2")

        self.assertEqual(status.status, "completed")
        self.assertIsNotNone(status.result)
        self.assertEqual(status.result.created_at, created_at.isoformat())
        self.assertEqual(
            status.result.report["meta"]["created_at"],
            created_at.isoformat(),
        )

    def test_run_market_review_background_raises_when_report_is_empty(self) -> None:
        if analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        runtime_notifier = MagicMock()
        runtime_search = MagicMock()
        runtime_analyzer = MagicMock()
        with patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            return_value=(runtime_notifier, runtime_analyzer, runtime_search),
        ), patch("src.core.market_review.run_market_review", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "The market review did not return a persistable report"):
                analysis_endpoint_module._run_market_review_background(
                    send_notification=False,
                    effective_region="cn",
                    lock_token=None,
                    config=SimpleNamespace(),
                )

    def test_run_market_review_background_releases_lock_on_runtime_build_failure(self) -> None:
        if analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        lock_token = object()
        with patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            side_effect=RuntimeError("runtime init failed"),
        ), patch.object(
            analysis_endpoint_module,
            "_release_market_review_lock",
        ) as release_market_review_lock:
            with self.assertRaises(RuntimeError):
                analysis_endpoint_module._run_market_review_background(
                    send_notification=False,
                    effective_region="cn",
                    lock_token=lock_token,
                    config=SimpleNamespace(),
                )

        release_market_review_lock.assert_called_once_with(lock_token)

    def test_run_market_review_background_runtime_build_failure_marks_task_failed(self) -> None:
        if analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        class _SyncExecutor:
            def submit(self, fn, *args, **kwargs):
                future = Future()
                try:
                    future.set_result(fn(*args, **kwargs))
                except Exception as exc:  # pragma: no cover - exercised via assert below
                    future.set_exception(exc)
                return future

        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = _SyncExecutor()
        with patch.object(
            analysis_endpoint_module,
            "_build_market_review_runtime",
            side_effect=RuntimeError("runtime init failed"),
        ), patch.object(analysis_endpoint_module, "_release_market_review_lock") as release_market_review_lock:
            task = queue.submit_background_task(
                lambda: analysis_endpoint_module._run_market_review_background(
                    send_notification=False,
                    effective_region="cn",
                    lock_token=object(),
                    config=SimpleNamespace(),
                ),
                stock_code="market_review",
                stock_name="Market review",
                message="Market review task submitted",
            )

        task_info = queue.get_task(task.task_id)
        self.assertIsNotNone(task_info)
        self.assertEqual(task_info.status, TaskStatus.FAILED)
        self.assertEqual(task_info.error, "runtime init failed")
        self.assertEqual(task_info.message, "Task failed: runtime init failed")
        release_market_review_lock.assert_called_once()

    def test_get_analysis_status_completed_db_snapshot_preserves_zero_change_pct(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        mock_queue = MagicMock()
        mock_queue.get_task.return_value = None
        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [
            SimpleNamespace(
                id=1,
                code="HPG",
                name="Hòa Phát",
                report_type="detailed",
                raw_result={"report_language": "vi", "model_used": "test-model"},
                context_snapshot={
                    "enhanced_context": {
                        "realtime": {
                            "price": 1234.5,
                            "change_pct": 0.0,
                            "change_60d": 12.3,
                        }
                    },
                    "realtime_quote_raw": {"price": 1234.5, "change_pct": 9.9},
                },
                sentiment_score=80,
                operation_advice="持有",
                trend_prediction="看多",
                analysis_summary="summary",
                ideal_buy=None,
                secondary_buy=None,
                stop_loss=None,
                take_profit=None,
                created_at=None,
            )
        ]

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=mock_queue), \
             patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            result = get_analysis_status("task-1")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result.report["meta"]["current_price"], 1234.5)
        self.assertEqual(result.result.report["meta"]["change_pct"], 0.0)

    def test_get_analysis_status_returns_market_review_report_from_db(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        mock_queue = MagicMock()
        mock_queue.get_task.return_value = None
        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [
            SimpleNamespace(
                id=10,
                code="MARKET",
                name="Market review",
                report_type="market_review",
                raw_result={"raw_response": "# 🎯 大盘复盘\n\n复盘正文"},
                context_snapshot={
                    "market_review_region": "jp,kr",
                    "market_review_payload": {
                        "kind": "market_review",
                        "region": "jp,kr",
                        "sections": [],
                    },
                },
                news_content="复盘正文",
                created_at=None,
            )
        ]

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=mock_queue), \
             patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            result = get_analysis_status("market-task-1")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.market_review_report, "# 🎯 大盘复盘\n\n复盘正文")
        self.assertEqual(result.region, "jp,kr")
        self.assertEqual(result.market_review_payload["region"], "jp,kr")
        self.assertIsNone(result.result)

    def test_get_analysis_status_completed_db_snapshot_reads_change_pct_from_raw_when_price_present(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        mock_queue = MagicMock()
        mock_queue.get_task.return_value = None
        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [
            SimpleNamespace(
                id=2,
                code="AAPL",
                name="Apple",
                report_type="detailed",
                raw_result={"report_language": "vi", "model_used": "test-model"},
                context_snapshot={
                    "enhanced_context": {
                        "realtime": {
                            "price": 180.35,
                            "change_pct": None,
                            "change_60d": None,
                        }
                    },
                    "realtime_quote_raw": {"price": 180.35, "pct_chg": -1.25},
                },
                sentiment_score=72,
                operation_advice="Hold",
                trend_prediction="Neutral",
                analysis_summary="summary",
                ideal_buy=None,
                secondary_buy=None,
                stop_loss=None,
                take_profit=None,
                created_at=None,
            )
        ]

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=mock_queue), \
             patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            result = get_analysis_status("task-2")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result.report["meta"]["current_price"], 180.35)
        self.assertEqual(result.result.report["meta"]["change_pct"], -1.25)

    def test_get_analysis_status_completed_db_snapshot_does_not_use_change_60d_as_intraday_change(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        mock_queue = MagicMock()
        mock_queue.get_task.return_value = None
        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [
            SimpleNamespace(
                id=3,
                code="MSFT",
                name="Microsoft",
                report_type="detailed",
                raw_result={"report_language": "vi", "model_used": "test-model"},
                context_snapshot={
                    "enhanced_context": {
                        "realtime": {
                            "price": 412.6,
                            "change_pct": None,
                            "change_60d": 14.8,
                        }
                    },
                    "realtime_quote_raw": {"price": 412.6},
                },
                sentiment_score=70,
                operation_advice="Hold",
                trend_prediction="Neutral",
                analysis_summary="summary",
                ideal_buy=None,
                secondary_buy=None,
                stop_loss=None,
                take_profit=None,
                created_at=None,
            )
        ]

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=mock_queue), \
             patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            result = get_analysis_status("task-3")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.result.report["meta"]["current_price"], 412.6)
        self.assertIsNone(result.result.report["meta"]["change_pct"])

    def test_report_type_full_maps_to_full_pipeline_mode(self) -> None:
        service = object.__new__(AnalysisService)
        pipeline_instance = MagicMock()
        pipeline_instance.process_single_stock.return_value = object()

        with patch("src.config.get_config", return_value=SimpleNamespace()), \
             patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline_instance), \
             patch.object(AnalysisService, "_build_analysis_response", return_value={"stock_code": "HPG"}):
            result = AnalysisService.analyze_stock(service, "HPG", report_type="full", query_id="q1")

        self.assertEqual(result, {"stock_code": "HPG"})
        self.assertEqual(
            pipeline_instance.process_single_stock.call_args.kwargs["report_type"],
            ReportType.FULL,

        )

    def test_analysis_service_passes_request_skills_to_pipeline(self) -> None:
        service = object.__new__(AnalysisService)
        pipeline_instance = MagicMock()
        pipeline_instance.process_single_stock.return_value = object()
        request_skills = ["growth_quality"]

        with patch("src.config.get_config", return_value=SimpleNamespace()), \
             patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline_instance) as pipeline_cls, \
             patch.object(AnalysisService, "_build_analysis_response", return_value={"stock_code": "HPG"}):
            result = AnalysisService.analyze_stock(
                service,
                "HPG",
                report_type="full",
                query_id="q1",
                skills=request_skills,
            )

        self.assertEqual(result, {"stock_code": "HPG"})
        self.assertEqual(pipeline_cls.call_args.kwargs["analysis_skills"], request_skills)

    def test_report_type_full_is_preserved_in_response_metadata(self) -> None:
        service = AnalysisService()
        pipeline_instance = MagicMock()
        pipeline_instance.process_single_stock.return_value = SimpleNamespace(
            code="HPG",
            name="Hòa Phát",
            current_price=1234.56,
            change_pct=1.23,
            model_used="test-model",
            analysis_summary="summary",
            operation_advice="hold",
            trend_prediction="up",
            sentiment_score=80,
            news_summary="news",
            technical_analysis="tech",
            fundamental_analysis="fundamental",
            risk_warning="risk",
            get_sniper_points=lambda: {},
        )

        with patch("src.config.get_config", return_value=SimpleNamespace()), \
             patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline_instance):
            result = service.analyze_stock("HPG", report_type="full", query_id="q1", send_notification=False)

        self.assertEqual(result["report"]["meta"]["report_type"], "full")

    def test_analysis_service_returns_none_and_records_last_error_for_unsuccessful_pipeline_result(self) -> None:
        service = AnalysisService()
        pipeline_instance = MagicMock()
        pipeline_instance.process_single_stock.return_value = SimpleNamespace(
            success=False,
            error_message="LLM stream interrupted",
        )

        with patch("src.config.get_config", return_value=SimpleNamespace()), \
             patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline_instance):
            result = service.analyze_stock("HPG", report_type="detailed", query_id="q1", send_notification=False)

        self.assertIsNone(result)
        self.assertEqual(service.last_error, "LLM stream interrupted")

    def test_handle_sync_analysis_uses_service_last_error_for_failed_pipeline_result(self) -> None:
        if _handle_sync_analysis is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        service_instance = MagicMock()
        service_instance.analyze_stock.return_value = None
        service_instance.last_error = "LLM stream interrupted"

        with patch("src.services.analysis_service.AnalysisService", return_value=service_instance):
            with self.assertRaises(Exception) as ctx:
                _handle_sync_analysis(
                    "HPG",
                    SimpleNamespace(
                        report_type="detailed",
                        force_refresh=False,
                        notify=True,
                        analysis_phase="auto",
                    ),
                )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(
            ctx.exception.detail,
            {
                "error": "analysis_failed",
                "message": "LLM stream interrupted",
            },
        )

    def test_handle_sync_analysis_response_exposes_overview(self) -> None:
        if _handle_sync_analysis is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        overview = _analysis_context_pack_overview()
        phase_summary = _market_phase_summary()
        service_instance = MagicMock()
        service_instance.analyze_stock.return_value = {
            "stock_code": "HPG",
            "stock_name": "Hòa Phát",
            "report": {
                "meta": {"stock_code": "HPG", "report_language": "vi"},
                "summary": {"analysis_summary": "summary"},
                "strategy": {},
                "details": {"news_summary": "news"},
            },
        }

        with patch("uuid.uuid4", return_value=SimpleNamespace(hex="q-sync-overview")), \
             patch("src.services.analysis_service.AnalysisService", return_value=service_instance), \
             patch(
                 "api.v1.endpoints.analysis._load_sync_fundamental_sources",
                 return_value=(
                     {
                         "enhanced_context": {"code": "HPG"},
                         "analysis_context_pack_overview": overview,
                         "market_phase_summary": phase_summary,
                     },
                     None,
                     None,
                 ),
             ):
            result = _handle_sync_analysis(
                "HPG",
                SimpleNamespace(
                    report_type="detailed",
                    force_refresh=False,
                    notify=True,
                    skills=None,
                    analysis_phase="intraday",
                ),
            )

        self.assertEqual(
            service_instance.analyze_stock.call_args.kwargs["analysis_phase"],
            "intraday",
        )
        details = result.report["details"]
        self.assertEqual(result.report["meta"]["market_phase_summary"]["phase"], "intraday")
        self.assertEqual(
            details["analysis_context_pack_overview"]["metadata"]["trigger_source"],
            "api",
        )
        self.assertEqual(
            details["analysis_context_pack_overview"]["data_quality"]["overall_score"],
            88,
        )
        self.assertNotIn("analysis_context_pack_overview", details["context_snapshot"])
        self.assertNotIn("market_phase_summary", details["context_snapshot"])

    def test_build_analysis_response_localizes_placeholder_stock_name_for_english(self) -> None:
        service = AnalysisService()
        result = service._build_analysis_response(
            SimpleNamespace(
                code="AAPL",
                name="股票AAPL",
                current_price=180.35,
                change_pct=1.04,
                model_used="test-model",
                analysis_summary="Momentum remains constructive.",
                operation_advice="Buy",
                trend_prediction="Bullish",
                sentiment_score=78,
                news_summary="news",
                technical_analysis="tech",
                fundamental_analysis="fundamental",
                risk_warning="risk",
                report_language="en",
                get_sniper_points=lambda: {},
            ),
            "q1",
            report_type="full",
        )

        self.assertEqual(result["stock_name"], "Unnamed Stock")
        self.assertEqual(result["report"]["meta"]["stock_name"], "Unnamed Stock")

    def test_build_analysis_response_does_not_use_model_news_summary_as_retrieval_evidence(self) -> None:
        service = AnalysisService()
        result = service._build_analysis_response(
            SimpleNamespace(
                code="HPG",
                name="Hòa Phát",
                current_price=1234.56,
                change_pct=1.23,
                model_used="test-model",
                analysis_summary="summary",
                operation_advice="hold",
                trend_prediction="up",
                sentiment_score=80,
                news_summary="model generated news summary",
                technical_analysis="tech",
                fundamental_analysis="fundamental",
                risk_warning="risk",
                get_sniper_points=lambda: {},
            ),
            "q1",
            report_type="full",
        )

        news_component = result["diagnostic_summary"]["components"]["news"]
        self.assertEqual(news_component["status"], "unknown")
        self.assertEqual(
            result["report"]["details"]["empty_news_disclosure"],
            "⚠️ Chưa cấu hình kênh tìm kiếm tin tức; phân tích này không sử dụng bằng chứng từ tin tức.",
        )

    def test_build_analysis_response_includes_market_phase_summary_from_result_snapshot(self) -> None:
        service = AnalysisService()
        phase_summary = _market_phase_summary()

        result = service._build_analysis_response(
            SimpleNamespace(
                code="HPG",
                name="Hòa Phát",
                current_price=1234.56,
                change_pct=1.23,
                model_used="test-model",
                analysis_summary="summary",
                operation_advice="hold",
                trend_prediction="up",
                sentiment_score=80,
                news_summary="news",
                technical_analysis="tech",
                fundamental_analysis="fundamental",
                risk_warning="risk",
                diagnostic_context_snapshot={"market_phase_summary": phase_summary},
                get_sniper_points=lambda: {},
            ),
            "q1",
            report_type="full",
        )

        self.assertEqual(
            result["report"]["meta"]["market_phase_summary"]["phase"],
            "intraday",
        )

    def test_analysis_service_passes_analysis_phase_to_pipeline(self) -> None:
        service = AnalysisService()
        pipeline_instance = MagicMock()
        pipeline_instance.process_single_stock.return_value = SimpleNamespace(
            success=True,
            code="HPG",
            name="Hòa Phát",
            current_price=1234.56,
            change_pct=1.23,
            model_used="test-model",
            analysis_summary="summary",
            operation_advice="hold",
            trend_prediction="up",
            sentiment_score=80,
            news_summary="news",
            technical_analysis="tech",
            fundamental_analysis="fundamental",
            risk_warning="risk",
            get_sniper_points=lambda: {},
        )

        with patch("src.config.get_config", return_value=SimpleNamespace()), patch(
            "src.core.pipeline.StockAnalysisPipeline",
            return_value=pipeline_instance,
        ) as pipeline_cls:
            result = service.analyze_stock(
                "HPG",
                report_type="detailed",
                send_notification=False,
                analysis_phase="postmarket",
            )

        self.assertIsNotNone(result)
        self.assertEqual(pipeline_cls.call_args.kwargs["analysis_phase"], "postmarket")

    def test_build_analysis_report_extracts_fundamental_fields_from_snapshot(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {},
                "summary": {},
                "strategy": {},
                "details": {"news_summary": "news"},
            },
            query_id="q1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot={
                "enhanced_context": {
                    "fundamental_context": {
                        "earnings": {
                            "data": {
                                "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                                "dividend": {"ttm_dividend_yield_pct": 2.5},
                            }
                        }
                    }
                }
            },
            fallback_fundamental_payload=None,
        )

        self.assertEqual(report.details.financial_report["report_date"], "2025-12-31")
        self.assertEqual(report.details.dividend_metrics["ttm_dividend_yield_pct"], 2.5)

    def test_build_analysis_report_derives_decision_action_fields(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {"report_type": "detailed", "report_language": "vi"},
                "summary": {
                    "analysis_summary": "Chờ xác nhận",
                    "operation_advice": "Không nên mua",
                    "trend_prediction": "震荡",
                    "sentiment_score": 45,
                },
                "strategy": {},
                "details": {"decision_type": "buy"},
            },
            query_id="q1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot=None,
            fallback_fundamental_payload=None,
        )

        self.assertEqual(report.summary.operation_advice, "Không nên mua")
        self.assertEqual(report.summary.action, "avoid")
        self.assertEqual(report.summary.action_label, "Tránh")

    def test_build_analysis_report_aligns_score_and_legacy_advice(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {"report_type": "detailed", "report_language": "vi"},
                "summary": {
                    "analysis_summary": "Chờ xác nhận",
                    "operation_advice": "Nắm giữ",
                    "sentiment_score": 72,
                },
                "strategy": {},
                "details": {},
            },
            query_id="q2",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot=None,
            fallback_fundamental_payload=None,
        )

        self.assertEqual(report.summary.action, "buy")
        self.assertEqual(report.summary.action_label, "Mua")

    def test_build_analysis_report_reads_decision_action_from_raw_result(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {"report_type": "detailed", "report_language": "vi"},
                "summary": {
                    "analysis_summary": "等待确认",
                    "operation_advice": "持有观察",
                    "action": "buy",
                    "trend_prediction": "震荡",
                    "sentiment_score": 45,
                },
                "strategy": {},
                "details": {
                    "action": "sell",
                    "raw_result": {
                        "operation_advice": "持有观察",
                        "action": "watch",
                        "report_language": "vi",
                    },
                },
            },
            query_id="q1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot=None,
            fallback_fundamental_payload=None,
        )

        self.assertEqual(report.summary.action, "watch")
        self.assertEqual(report.summary.action_label, "Quan sát")

    def test_build_analysis_report_stringifies_strategy_price_fields(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {},
                "summary": {},
                "strategy": {
                    "ideal_buy": 10.0,
                    "secondary_buy": None,
                    "stop_loss": 9.5,
                    "take_profit": 11.6,
                },
                "details": {},
            },
            query_id="q1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot=None,
            fallback_fundamental_payload=None,
        )

        self.assertEqual(report.strategy.ideal_buy, "10.0")
        self.assertIsNone(report.strategy.secondary_buy)
        self.assertEqual(report.strategy.stop_loss, "9.5")
        self.assertEqual(report.strategy.take_profit, "11.6")

    def test_build_analysis_report_exposes_overview_but_sanitizes_snapshot(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        overview = _analysis_context_pack_overview()
        phase_summary = _market_phase_summary()
        report = _build_analysis_report(
            report_data={
                "meta": {},
                "summary": {},
                "strategy": {},
                "details": {"news_summary": "news"},
            },
            query_id="q1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot={
                "enhanced_context": {
                    "code": "HPG",
                    "portfolio_context": {
                        "quantity": 100,
                        "avg_cost": 1800,
                        "unrealized_pnl_base": 5000,
                    },
                },
                "portfolio_context": {"total_cost": 180000},
                "analysis_context_pack_overview": overview,
                "market_phase_summary": {
                    **phase_summary,
                    "market_phase_context": {"raw": True},
                },
            },
            fallback_fundamental_payload=None,
        )

        self.assertIsNotNone(report.meta.market_phase_summary)
        self.assertEqual(report.meta.market_phase_summary.phase, "intraday")
        self.assertEqual(
            report.details.analysis_context_pack_overview.metadata.trigger_source,
            "api",
        )
        self.assertEqual(
            report.details.analysis_context_pack_overview.data_quality.overall_score,
            88,
        )
        self.assertEqual(
            report.details.analysis_context_pack_overview.blocks[1].missing_reasons,
            ["news_context_missing"],
        )
        self.assertNotIn(
            "analysis_context_pack_overview",
            report.details.context_snapshot,
        )
        self.assertNotIn(
            "market_phase_summary",
            report.details.context_snapshot,
        )
        self.assertNotIn(
            "portfolio_context",
            report.details.context_snapshot,
        )
        self.assertNotIn(
            "portfolio_context",
            report.details.context_snapshot["enhanced_context"],
        )
        self.assertNotIn("avg_cost", str(report.details.context_snapshot))

    def test_build_analysis_report_falls_back_to_sanitized_report_meta_phase_summary(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        phase_summary = {
            **_market_phase_summary(),
            "warnings": ["api_key=secret"],
            "market_phase_context": {"raw": True},
        }

        report = _build_analysis_report(
            report_data={
                "meta": {"market_phase_summary": phase_summary},
                "summary": {},
                "strategy": {},
                "details": {},
            },
            query_id="q-meta-phase",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot=None,
            fallback_fundamental_payload=None,
        )

        self.assertIsNotNone(report.meta.market_phase_summary)
        self.assertEqual(report.meta.market_phase_summary.phase, "intraday")
        self.assertEqual(report.meta.market_phase_summary.warnings, ["[REDACTED]"])

    def test_build_analysis_report_prefers_snapshot_phase_summary_over_report_meta(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        snapshot_summary = _market_phase_summary()
        report = _build_analysis_report(
            report_data={
                "meta": {
                    "market_phase_summary": {
                        **snapshot_summary,
                        "phase": "postmarket",
                    },
                },
                "summary": {},
                "strategy": {},
                "details": {},
            },
            query_id="q-snapshot-phase",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot={"market_phase_summary": snapshot_summary},
            fallback_fundamental_payload=None,
        )

        self.assertIsNotNone(report.meta.market_phase_summary)
        self.assertEqual(report.meta.market_phase_summary.phase, "intraday")

    def test_build_analysis_report_merges_partial_top_level_context_with_fallback(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {},
                "summary": {},
                "strategy": {},
                "details": {},
            },
            query_id="q1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot={
                "fundamental_context": {
                    "valuation": {"data": {"pe_ttm": 12.5}},
                }
            },
            fallback_fundamental_payload={
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                        "dividend": {"ttm_dividend_yield_pct": 2.6},
                    }
                }
            },
        )

        self.assertEqual(report.details.financial_report["report_date"], "2025-12-31")
        self.assertEqual(report.details.dividend_metrics["ttm_dividend_yield_pct"], 2.6)

    def test_build_analysis_report_keeps_fallback_when_snapshot_has_empty_placeholders(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {},
                "summary": {},
                "strategy": {},
                "details": {},
            },
            query_id="q1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            context_snapshot={
                "fundamental_context": {
                    "earnings": {},
                },
                "enhanced_context": {
                    "fundamental_context": {
                        "earnings": {"data": {}},
                    }
                },
            },
            fallback_fundamental_payload={
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                        "dividend": {"ttm_dividend_yield_pct": 2.6},
                    }
                },
            },
        )

        self.assertEqual(report.details.financial_report["report_date"], "2025-12-31")
        self.assertEqual(report.details.dividend_metrics["ttm_dividend_yield_pct"], 2.6)

    def test_build_analysis_report_preserves_report_language(self) -> None:
        if _build_analysis_report is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        report = _build_analysis_report(
            report_data={
                "meta": {"report_language": "vi"},
                "summary": {"analysis_summary": "English output"},
                "strategy": {},
                "details": {},
            },
            query_id="q1",
            stock_code="AAPL",
            stock_name="Apple",
            context_snapshot={"report_language": "vi"},
            fallback_fundamental_payload=None,
        )

        self.assertEqual(report.meta.report_language, "vi")

    def test_load_sync_fundamental_sources_uses_query_and_code_for_fallback(self) -> None:
        if _load_sync_fundamental_sources is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        mock_db = MagicMock()
        raw_result_payload = {
            "model_used": "test-model",
            "report_language": "vi",
        }
        mock_db.get_analysis_history.return_value = [
            SimpleNamespace(
                context_snapshot=None,
                raw_result=json.dumps(raw_result_payload, ensure_ascii=False),
            )
        ]
        fallback_payload = {
            "earnings": {
                "data": {
                    "financial_report": {"report_date": "2025-12-31"},
                    "dividend": {"ttm_dividend_yield_pct": 2.1},
                }
            }
        }
        mock_db.get_latest_fundamental_snapshot.return_value = fallback_payload

        with patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            context_snapshot, fundamental_snapshot, raw_result_snapshot = _load_sync_fundamental_sources(
                query_id="q_sync_001",
                stock_code="HPG",
            )

        self.assertIsNone(context_snapshot)
        self.assertEqual(fundamental_snapshot, fallback_payload)
        self.assertEqual(raw_result_snapshot, raw_result_payload)
        mock_db.get_analysis_history.assert_called_once_with(
            query_id="q_sync_001",
            code="HPG",
            limit=1,
        )
        mock_db.get_latest_fundamental_snapshot.assert_called_once_with(
            query_id="q_sync_001",
            code="HPG",
        )

    def test_get_analysis_status_reads_price_fields_from_context_snapshot_preserving_zero_change_pct(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        record = SimpleNamespace(
            id=1,
            code="HPG",
            name="Hòa Phát",
            report_type="detailed",
            created_at=datetime(2026, 4, 10, 12, 0, 0),
            raw_result=json.dumps({"model_used": "test-model", "report_language": "vi"}),
            context_snapshot=json.dumps(
                {
                    "enhanced_context": {
                        "realtime": {
                            "price": 1234.5,
                            "change_pct": 0.0,
                            "change_60d": 9.99,
                        }
                    },
                    "realtime_quote_raw": {
                        "price": 999.9,
                        "change_pct": 8.88,
                        "pct_chg": 7.77,
                    },
                }
            ),
            sentiment_score=80,
            operation_advice="持有",
            trend_prediction="震荡上行",
            analysis_summary="summary",
            ideal_buy=None,
            secondary_buy=None,
            stop_loss=None,
            take_profit=None,
        )
        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [record]

        with patch("api.v1.endpoints.analysis.get_task_queue") as queue_mock, \
             patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            queue_mock.return_value.get_task.return_value = None
            status = get_analysis_status("task_123")

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.result.report["meta"]["current_price"], 1234.5)
        self.assertEqual(status.result.report["meta"]["change_pct"], 0.0)
        self.assertEqual(status.result.report["meta"]["model_used"], "test-model")

    def test_get_analysis_status_completed_db_snapshot_includes_agent_snapshot_details(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        overview = _analysis_context_pack_overview()
        phase_summary = _market_phase_summary()
        record = SimpleNamespace(
            id=1,
            code="HPG",
            name="Hòa Phát",
            report_type="detailed",
            created_at=datetime(2026, 4, 10, 12, 0, 0),
            raw_result=json.dumps({"model_used": "test-model", "report_language": "vi"}),
            context_snapshot=json.dumps(
                {
                    "realtime_quote": {
                        "price": 1888.0,
                        "change_pct": 1.56,
                    },
                    "analysis_context_pack_overview": overview,
                    "market_phase_summary": {
                        **phase_summary,
                        "quote_timestamp": "not-public",
                    },
                }
            ),
            news_content="news",
            sentiment_score=80,
            operation_advice="持有",
            trend_prediction="震荡上行",
            analysis_summary="summary",
            ideal_buy=None,
            secondary_buy=None,
            stop_loss=None,
            take_profit=None,
        )
        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [record]
        mock_db.get_latest_fundamental_snapshot.return_value = None

        with patch("api.v1.endpoints.analysis.get_task_queue") as queue_mock, \
             patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            queue_mock.return_value.get_task.return_value = None
            status = get_analysis_status("task_agent_snapshot_1")

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.result.report["meta"]["current_price"], 1888.0)
        self.assertEqual(status.result.report["meta"]["change_pct"], 1.56)
        self.assertIsNone(status.analysis_phase)
        self.assertEqual(
            status.result.report["meta"]["market_phase_summary"]["phase"],
            "intraday",
        )
        self.assertEqual(
            status.result.report["details"]["analysis_context_pack_overview"]["metadata"]["trigger_source"],
            "api",
        )
        self.assertEqual(
            status.result.report["details"]["analysis_context_pack_overview"]["data_quality"]["overall_score"],
            88,
        )
        self.assertNotIn(
            "analysis_context_pack_overview",
            status.result.report["details"]["context_snapshot"],
        )
        self.assertNotIn(
            "market_phase_summary",
            status.result.report["details"]["context_snapshot"],
        )

    def test_get_analysis_status_in_memory_task_enriches_agent_snapshot_details(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        overview = _analysis_context_pack_overview()
        phase_summary = _market_phase_summary()
        context_snapshot = {
            "realtime_quote": {
                "price": 1888.0,
                "change_pct": 1.56,
            },
            "analysis_context_pack_overview": overview,
            "market_phase_summary": phase_summary,
        }
        task = QueueTaskInfo(
            task_id="task_agent_snapshot_in_memory_1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=TaskStatus.COMPLETED,
            progress=100,
            result={
                "stock_code": "HPG",
                "stock_name": "Hòa Phát",
                "report": {
                    "meta": {
                        "query_id": "task_agent_snapshot_in_memory_1",
                        "stock_code": "HPG",
                        "stock_name": "Hòa Phát",
                        "report_type": "detailed",
                        "report_language": "vi",
                        "created_at": "2026-04-10T12:00:00",
                        "model_used": "test-model",
                    },
                    "summary": {"analysis_summary": "summary"},
                    "details": {"news_summary": "news"},
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
            skills=None,
            created_at=datetime(2026, 4, 10, 12, 0, 0),
            completed_at=datetime(2026, 4, 10, 12, 1, 0),
        )
        record = SimpleNamespace(context_snapshot=json.dumps(context_snapshot))
        mock_db = MagicMock()
        mock_db.get_analysis_history.return_value = [record]
        mock_db.get_latest_fundamental_snapshot.return_value = None

        with patch("api.v1.endpoints.analysis.get_task_queue") as queue_mock, \
             patch("src.storage.DatabaseManager.get_instance", return_value=mock_db):
            queue_mock.return_value.get_task.return_value = task
            status = get_analysis_status("task_agent_snapshot_in_memory_1")

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.result.report["meta"]["current_price"], 1888.0)
        self.assertEqual(status.result.report["meta"]["change_pct"], 1.56)
        self.assertEqual(
            status.result.report["meta"]["market_phase_summary"]["phase"],
            "intraday",
        )
        self.assertEqual(
            status.result.report["details"]["analysis_context_pack_overview"]["metadata"]["trigger_source"],
            "api",
        )
        self.assertEqual(
            status.result.report["details"]["analysis_context_pack_overview"]["data_quality"]["overall_score"],
            88,
        )
        self.assertNotIn(
            "analysis_context_pack_overview",
            status.result.report["details"]["context_snapshot"],
        )
        self.assertNotIn(
            "market_phase_summary",
            status.result.report["details"]["context_snapshot"],
        )
        mock_db.get_analysis_history.assert_called_once_with(
            query_id="task_agent_snapshot_in_memory_1",
            code="HPG",
            limit=1,
        )

    def test_get_analysis_status_in_memory_task_without_db_snapshot_preserves_service_phase_summary(self) -> None:
        if get_analysis_status is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        phase_summary = _market_phase_summary()
        task = QueueTaskInfo(
            task_id="task_no_snapshot_in_memory_1",
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=TaskStatus.COMPLETED,
            progress=100,
            analysis_phase="intraday",
            result={
                "stock_code": "HPG",
                "stock_name": "Hòa Phát",
                "report": {
                    "meta": {
                        "query_id": "task_no_snapshot_in_memory_1",
                        "stock_code": "HPG",
                        "stock_name": "Hòa Phát",
                        "market_phase_summary": phase_summary,
                    },
                    "summary": {"analysis_summary": "summary"},
                },
            },
            error=None,
            original_query=None,
            selection_source=None,
            skills=None,
            created_at=datetime(2026, 4, 10, 12, 0, 0),
            completed_at=datetime(2026, 4, 10, 12, 1, 0),
        )

        with patch("api.v1.endpoints.analysis.get_task_queue") as queue_mock, \
             patch(
                 "api.v1.endpoints.analysis._load_sync_fundamental_sources",
                 return_value=(None, None, None),
             ) as load_sources:
            queue_mock.return_value.get_task.return_value = task
            status = get_analysis_status("task_no_snapshot_in_memory_1")

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.analysis_phase, "intraday")
        self.assertIsNotNone(status.result)
        self.assertEqual(
            status.result.report["meta"]["market_phase_summary"]["phase"],
            "intraday",
        )
        load_sources.assert_called_once_with(
            query_id="task_no_snapshot_in_memory_1",
            stock_code="HPG",
        )

    def test_openapi_declares_single_and_batch_async_202_payloads(self) -> None:
        if create_app is None:
            self.skipTest("fastapi is not installed in this test environment")

        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(static_dir=Path(temp_dir))
            schema = app.openapi()["paths"]["/api/v1/analysis/analyze"]["post"]["responses"]["202"][
                "content"
            ]["application/json"]["schema"]

        refs = {item["$ref"] for item in schema["anyOf"]}
        self.assertEqual(
            refs,
            {
                "#/components/schemas/TaskAccepted",
                "#/components/schemas/BatchTaskAcceptedResponse",
            },
        )

    def test_openapi_declares_backtest_phase_filter_enum_and_400(self) -> None:
        if create_app is None:
            self.skipTest("fastapi is not installed in this test environment")

        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(static_dir=Path(temp_dir))
            paths = app.openapi()["paths"]

        for path in (
            "/api/v1/backtest/results",
            "/api/v1/backtest/performance",
            "/api/v1/backtest/performance/{code}",
        ):
            operation = paths[path]["get"]
            self.assertIn("400", operation["responses"])
            params = {param["name"]: param for param in operation["parameters"]}
            schema = params["analysis_phase"]["schema"]
            enum_values = set()
            stack = [schema]
            while stack:
                current = stack.pop()
                if not isinstance(current, dict):
                    continue
                enum_values.update(current.get("enum") or [])
                stack.extend(current.get("anyOf") or [])
                stack.extend(current.get("oneOf") or [])

            self.assertEqual(enum_values, {"premarket", "intraday", "postmarket", "unknown"})

    def test_market_review_endpoint_accepts_omitted_body(self) -> None:
        if create_app is None or analysis_endpoint_module is None:
            self.skipTest("fastapi is not installed in this test environment")

        config = SimpleNamespace(trading_day_check_enabled=True, market_review_region="cn")

        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(static_dir=Path(temp_dir))
            request_body = app.openapi()["paths"]["/api/v1/analysis/market-review"]["post"][
                "requestBody"
            ]

        self.assertNotIn("required", request_body)

        task_queue = MagicMock()
        task_queue.submit_background_task.return_value = SimpleNamespace(task_id="market-task-omitted")

        with patch.object(
            analysis_endpoint_module,
            "_try_acquire_market_review_lock",
            return_value=object(),
        ), patch("api.v1.endpoints.analysis.get_task_queue", return_value=task_queue):
            response = trigger_market_review(
                request=None,
                config=config,
            )

        self.assertTrue(response.send_notification)
        self.assertEqual(response.task_id, "market-task-omitted")
        task_queue.submit_background_task.assert_called_once()

    def test_trigger_analysis_rejects_blank_only_stock_inputs(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        with self.assertRaises(Exception) as ctx:
            trigger_analysis(
                request=SimpleNamespace(
                    stock_code="   ",
                    stock_codes=None,
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=False,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(
            ctx.exception.detail["message"],
            "Stock code must not be empty or whitespace only",
        )

    def test_trigger_analysis_rejects_obviously_invalid_mixed_input_before_resolution(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        with patch("api.v1.endpoints.analysis.resolve_name_to_code") as resolve_mock:
            with self.assertRaises(Exception) as ctx:
                trigger_analysis(
                    request=SimpleNamespace(
                        stock_code="00AAAAA",
                        stock_codes=None,
                        report_type="detailed",
                        force_refresh=False,
                        async_mode=True,
                        analysis_phase="auto",
                    ),
                    config=SimpleNamespace(),
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail["message"], "Enter a valid stock code or stock name")
        resolve_mock.assert_not_called()

    def test_trigger_analysis_rejects_unresolvable_alpha_garbage(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        with patch("api.v1.endpoints.analysis.resolve_name_to_code", return_value=None), \
             patch("api.v1.endpoints.analysis.get_task_queue") as queue_mock:
            with self.assertRaises(Exception) as ctx:
                trigger_analysis(
                    request=SimpleNamespace(
                        stock_code="aaaaaaa",
                        stock_codes=None,
                        report_type="detailed",
                        force_refresh=False,
                        async_mode=True,
                        analysis_phase="auto",
                    ),
                    config=SimpleNamespace(),
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail["message"], "Enter a valid stock code or stock name")
        queue_mock.assert_not_called()

    def test_trigger_analysis_accepts_vn_suffix_code(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [])

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue), \
             patch("api.v1.endpoints.analysis.resolve_name_to_code") as resolve_mock:
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code="HPG.VN",
                    stock_codes=None,
                    stock_name=None,
                    original_query="HPG.VN",
                    selection_source="manual",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 202)
        resolve_mock.assert_not_called()
        queue.submit_tasks_batch.assert_called_once_with(
            stock_codes=["HPG.VN"],
            stock_name=None,
            original_query="HPG.VN",
            selection_source="manual",
            report_type="detailed",
            analysis_phase="auto",
            force_refresh=False,
            notify=True,
        )

    def test_trigger_analysis_raw_token_limit_counts_rejected_and_duplicate_tokens(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [])

        # 51 token thô không rỗng: 1 mã hợp lệ + 50 token trùng lặp.
        # Giới hạn trước khi phân giải phải chặn, nếu không bước kiểm tra sau khử trùng
        # (chỉ thấy một mã) sẽ cho request đi qua.
        # 51 non-blank raw tokens: 1 valid + 50 duplicates; the pre-resolution cap must reject.
        codes = ["HPG"] + ["hpg"] * 50
        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            with self.assertRaises(Exception) as ctx:
                trigger_analysis(
                    request=SimpleNamespace(
                        stock_code=None,
                        stock_codes=codes,
                        stock_name=None,
                        original_query=None,
                        selection_source="manual",
                        report_type="detailed",
                        force_refresh=False,
                        async_mode=True,
                        notify=True,
                        analysis_phase="auto",
                    ),
                    config=SimpleNamespace(),
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("supports at most 50", ctx.exception.detail["message"])
        queue.submit_tasks_batch.assert_not_called()

    def test_trigger_analysis_exactly_fifty_raw_tokens_is_accepted(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [])

        # 50 mã Việt Nam 3 ký tự khác nhau / 50 distinct 3-letter Vietnam codes.
        codes = [f"A{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(50)]
        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue), \
             patch("api.v1.endpoints.analysis.resolve_name_to_code", return_value=None):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code=None,
                    stock_codes=codes,
                    stock_name=None,
                    original_query=None,
                    selection_source="manual",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(queue.submit_tasks_batch.call_args.kwargs["stock_codes"]), 50)

    def test_trigger_analysis_single_duplicate_still_returns_409(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        dup = DuplicateTaskError("HPG", "existing-1")
        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [dup])

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code="HPG",
                    stock_codes=None,
                    stock_name=None,
                    original_query=None,
                    selection_source="manual",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 409)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "duplicate_task")
        self.assertEqual(payload["stock_code"], "HPG")

    def test_trigger_analysis_name_input_stays_on_name_resolution(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [])

        with patch("api.v1.endpoints.analysis.resolve_name_to_code", return_value="HPG") as resolve_mock, \
             patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code="Hòa Phát",
                    stock_codes=None,
                    stock_name="Hòa Phát",
                    original_query="Hòa Phát",
                    selection_source="manual",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 202)
        resolve_mock.assert_called_once_with("Hòa Phát")
        call_kwargs = queue.submit_tasks_batch.call_args.kwargs
        self.assertEqual(call_kwargs["stock_codes"], ["HPG"])
        self.assertNotIn("analysis_targets", call_kwargs)

    def test_trigger_analysis_accepts_camel_case_report_language_alias(self) -> None:
        if trigger_analysis is None or analysis_endpoint_module is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        task = SimpleNamespace(
            task_id="task-report-language-1",
            trace_id="trace-report-language-1",
            stock_code="HPG",
            analysis_phase="auto",
        )
        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([task], [])

        request = analysis_endpoint_module.AnalyzeRequest.model_validate({
            "stock_code": "HPG",
            "async_mode": True,
            "reportLanguage": "vi",
        })

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(request=request, config=SimpleNamespace())

        self.assertEqual(response.status_code, 202)
        queue.submit_tasks_batch.assert_called_once_with(
            stock_codes=["HPG"],
            stock_name=None,
            original_query=None,
            selection_source=None,
            report_type="detailed",
            analysis_phase="auto",
            force_refresh=False,
            notify=True,
            report_language="vi",
        )

    def test_trigger_analysis_async_passes_and_returns_analysis_phase(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        task = SimpleNamespace(
            task_id="task-phase-1",
            trace_id="trace-phase-1",
            stock_code="HPG",
            analysis_phase="intraday",
        )
        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([task], [])

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code="HPG",
                    stock_codes=None,
                    stock_name=None,
                    original_query=None,
                    selection_source=None,
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="intraday",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(json.loads(response.body)["analysis_phase"], "intraday")
        queue.submit_tasks_batch.assert_called_once_with(
            stock_codes=["HPG"],
            stock_name=None,
            original_query=None,
            selection_source=None,
            report_type="detailed",
            analysis_phase="intraday",
            force_refresh=False,
            notify=True,
        )

    def test_trigger_analysis_rejects_non_bse_code_with_bj_exchange_hint(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        for bad_code in ("600519.BJ", "BJ600519"):
            with self.subTest(bad_code=bad_code):
                queue = MagicMock()

                with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue), \
                     patch("api.v1.endpoints.analysis.resolve_name_to_code") as resolve_mock:
                    with self.assertRaises(Exception) as exc:
                        trigger_analysis(
                            request=SimpleNamespace(
                                stock_code=bad_code,
                                stock_codes=None,
                                stock_name=None,
                                original_query=bad_code,
                                selection_source="manual",
                                report_type="detailed",
                                force_refresh=False,
                                async_mode=True,
                                notify=True,
                                analysis_phase="auto",
                            ),
                            config=SimpleNamespace(),
                        )

                self.assertEqual(exc.exception.status_code, 400)
                self.assertEqual(exc.exception.detail["error"], "validation_error")
                resolve_mock.assert_not_called()
                queue.submit_tasks_batch.assert_not_called()

    def test_trigger_analysis_accepts_resolvable_free_text_input(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [])

        with patch("api.v1.endpoints.analysis.resolve_name_to_code", return_value="VNM"), \
             patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code="Vinamilk",
                    stock_codes=None,
                    stock_name=None,
                    original_query="Vinamilk",
                    selection_source="manual",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 202)
        queue.submit_tasks_batch.assert_called_once_with(
            stock_codes=["VNM"],
            stock_name=None,
            original_query="Vinamilk",
            selection_source="manual",
            report_type="detailed",
            analysis_phase="auto",
            force_refresh=False,
            notify=True,
        )

    def test_trigger_analysis_preserves_batch_metadata(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [])

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code=None,
                    stock_codes=["HPG", "VCB"],
                    stock_name=None,
                    original_query="uploaded.csv",
                    selection_source="import",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 202)
        queue.submit_tasks_batch.assert_called_once_with(
            stock_codes=["HPG", "VCB"],
            stock_name=None,
            original_query="uploaded.csv",
            selection_source="import",
            report_type="detailed",
            analysis_phase="auto",
            force_refresh=False,
            notify=True,
        )

    def test_trigger_analysis_rejects_cross_request_duplicate_for_equivalent_code_shapes(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        original_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None
        try:
            queue = AnalysisTaskQueue(max_workers=1)
            queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()

            with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
                first = trigger_analysis(
                    request=SimpleNamespace(
                        stock_code="HPG",
                        stock_codes=None,
                        stock_name=None,
                        original_query=None,
                        selection_source=None,
                        report_type="detailed",
                        force_refresh=False,
                        async_mode=True,
                        notify=True,
                        analysis_phase="auto",
                    ),
                    config=SimpleNamespace(),
                )
                second = trigger_analysis(
                    request=SimpleNamespace(
                        stock_code="HPG.VN",
                        stock_codes=None,
                        stock_name=None,
                        original_query=None,
                        selection_source=None,
                        report_type="detailed",
                        force_refresh=False,
                        async_mode=True,
                        notify=True,
                        analysis_phase="auto",
                    ),
                    config=SimpleNamespace(),
                )

            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 409)
            self.assertEqual(json.loads(second.body)["error"], "duplicate_task")
            self.assertEqual(json.loads(second.body)["stock_code"], "HPG.VN")
            self.assertEqual(
                json.loads(second.body)["existing_task_id"],
                json.loads(first.body)["task_id"],
            )
        finally:
            queue = AnalysisTaskQueue._instance
            if queue is not None and queue is not original_instance:
                executor = getattr(queue, "_executor", None)
                if executor is not None and hasattr(executor, "shutdown"):
                    executor.shutdown(wait=False, cancel_futures=True)
            AnalysisTaskQueue._instance = original_instance

    def test_trigger_analysis_batch_does_not_apply_single_stock_name_to_all_tasks(self) -> None:
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([], [])

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code=None,
                    stock_codes=["HPG", "VCB"],
                    stock_name="Hòa Phát",
                    original_query="Hòa Phát,Vietcombank",
                    selection_source="import",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )

        self.assertEqual(response.status_code, 202)
        queue.submit_tasks_batch.assert_called_once_with(
            stock_codes=["HPG", "VCB"],
            stock_name=None,
            original_query="Hòa Phát,Vietcombank",
            selection_source="import",
            report_type="detailed",
            analysis_phase="auto",
            force_refresh=False,
            notify=True,
        )

    def test_spa_fallback_returns_json_404_for_bare_api_path(self) -> None:
        if create_app is None:
            self.skipTest("fastapi is not installed in this test environment")

        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir)
            (static_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
            app = create_app(static_dir=static_dir)

            serve_spa = next(
                route.endpoint for route in app.routes
                if getattr(route, "path", None) == "/{full_path:path}"
            )

            response = asyncio.run(serve_spa(None, "api"))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            json.loads(response.body),
            {"error": "not_found", "message": "API endpoint /api not found"},
        )

    def test_spa_fallback_blocks_path_traversal(self) -> None:
        """SPA fallback must not serve files outside static_dir.

        Starlette's :path converter does not normalize `..` segments, so
        without an explicit containment check `static_dir / full_path` can
        resolve to arbitrary files on disk (CVE-class path traversal).
        """
        if create_app is None:
            self.skipTest("fastapi is not installed in this test environment")

        from fastapi.responses import FileResponse

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static_dir = root / "static"
            static_dir.mkdir()
            (static_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
            secret = root / "secret.txt"
            secret.write_text("TOPSECRET", encoding="utf-8")

            app = create_app(static_dir=static_dir)
            serve_spa = next(
                route.endpoint for route in app.routes
                if getattr(route, "path", None) == "/{full_path:path}"
            )

            for traversal in ("../secret.txt", "../../secret.txt", "foo/../../secret.txt"):
                with self.subTest(traversal=traversal):
                    response = asyncio.run(serve_spa(None, traversal))
                    self.assertIsInstance(response, FileResponse)
                    self.assertEqual(Path(response.path).resolve(), (static_dir / "index.html").resolve())

    def test_sse_generator_reraises_cancelled_error(self) -> None:
        """CancelledError must propagate (not be swallowed) from the SSE event generator."""
        try:
            from api.v1.endpoints.analysis import task_stream
        except Exception:  # pragma: no cover - optional dependency environments
            self.skipTest("api.v1.endpoints.analysis not importable")

        class _NeverQueue:
            """Queue that never returns from get(), used to exercise cancellation."""
            async def get(self):
                await asyncio.sleep(3600)

        never_queue = _NeverQueue()
        mock_task_queue = MagicMock()
        mock_task_queue.list_pending_tasks.return_value = []

        async def run():
            with patch("api.v1.endpoints.analysis.get_task_queue", return_value=mock_task_queue), \
                 patch("asyncio.Queue", return_value=never_queue):
                response = await task_stream()
                gen = response.body_iterator

                async def consume():
                    async for _ in gen:
                        pass

                task = asyncio.create_task(consume())
                await asyncio.sleep(0)  # let generator start and reach wait_for
                task.cancel()
                await task  # should re-raise CancelledError

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(run())

        mock_task_queue.unsubscribe.assert_called_once_with(never_queue)

    def test_get_task_list_includes_analysis_phase_and_skills(self) -> None:
        if get_task_list is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        task = SimpleNamespace(
            task_id="task-list-phase",
            trace_id="trace-list-phase",
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=TaskStatus.PROCESSING,
            progress=42,
            message="running",
            report_type="detailed",
            created_at=datetime(2026, 4, 10, 12, 0, 0),
            started_at=datetime(2026, 4, 10, 12, 0, 1),
            completed_at=None,
            error=None,
            original_query="Hòa Phát",
            selection_source="manual",
            analysis_phase="postmarket",
            skills=["growth_quality"],
            region=None,
        )
        queue = MagicMock()
        queue.list_all_tasks.return_value = [task]
        queue.get_task_stats.return_value = {
            "total": 1,
            "pending": 0,
            "processing": 1,
            "completed": 0,
            "failed": 0,
        }

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = get_task_list(status=None, limit=20)

        self.assertEqual(response.tasks[0].analysis_phase, "postmarket")
        self.assertEqual(response.tasks[0].skills, ["growth_quality"])

    def test_task_list_exposes_parser_asset_type(self) -> None:
        """Valid asset_type literals pass through verbatim on the task list."""
        if get_task_list is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        for expected_asset_type, stock_code, stock_name in (
            # "index" chỉ còn là literal cũ được giữ để tương thích / legacy literal kept for compatibility.
            ("index", "VNINDEX", "VN-Index"),
            ("stock", "HPG", "Hòa Phát"),
        ):
            with self.subTest(asset_type=expected_asset_type):
                task = SimpleNamespace(
                    task_id="task-list-asset-type",
                    trace_id="trace-list-asset-type",
                    stock_code=stock_code,
                    stock_name=stock_name,
                    status=TaskStatus.PROCESSING,
                    progress=42,
                    message="running",
                    report_type="detailed",
                    created_at=datetime(2026, 4, 10, 12, 0, 0),
                    started_at=datetime(2026, 4, 10, 12, 0, 1),
                    completed_at=None,
                    error=None,
                    original_query=stock_code,
                    selection_source="manual",
                    analysis_phase="intraday",
                    skills=None,
                    region=None,
                    asset_type=expected_asset_type,
                )
                queue = MagicMock()
                queue.list_all_tasks.return_value = [task]
                queue.get_task_stats.return_value = {
                    "total": 1,
                    "pending": 0,
                    "processing": 1,
                    "completed": 0,
                    "failed": 0,
                }

                with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
                    response = get_task_list(status=None, limit=20)

                self.assertEqual(response.tasks[0].stock_code, stock_code)
                self.assertEqual(response.tasks[0].asset_type, expected_asset_type)

    def test_task_list_omits_asset_type_for_legacy_tasks(self) -> None:
        if get_task_list is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        task = SimpleNamespace(
            task_id="task-list-legacy",
            trace_id="trace-list-legacy",
            stock_code="HPG",
            stock_name="Hòa Phát",
            status=TaskStatus.PENDING,
            progress=0,
            message="waiting",
            report_type="detailed",
            created_at=datetime(2026, 4, 10, 12, 0, 0),
            started_at=None,
            completed_at=None,
            error=None,
            original_query=None,
            selection_source=None,
            analysis_phase="auto",
            skills=None,
            region=None,
        )
        queue = MagicMock()
        queue.list_all_tasks.return_value = [task]
        queue.get_task_stats.return_value = {
            "total": 1,
            "pending": 1,
            "processing": 0,
            "completed": 0,
            "failed": 0,
        }

        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = get_task_list(status=None, limit=20)

        self.assertIsNone(response.tasks[0].asset_type)

    def test_batch_accepted_item_exposes_parser_asset_type(self) -> None:
        """Valid asset_type literals pass through verbatim on batch accepted items."""
        if trigger_analysis is None:
            self.skipTest("fastapi is not installed in this test environment")

        for expected_asset_type, stock_code, stock_name, second_code in (
            ("index", "VNINDEX", "VN-Index", "VCB"),
            ("stock", "HPG", "Hòa Phát", "VCB"),
        ):
            request_codes = [stock_code, second_code]
            with self.subTest(asset_type=expected_asset_type):
                task = SimpleNamespace(
                    task_id="task-batch-asset-type",
                    trace_id="trace-batch-asset-type",
                    stock_code=stock_code,
                    stock_name=stock_name,
                    analysis_phase="auto",
                    asset_type=expected_asset_type,
                )
                queue = MagicMock()
                queue.submit_tasks_batch.return_value = ([task], [])

                with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
                    response = trigger_analysis(
                        request=SimpleNamespace(
                            stock_code=None,
                            stock_codes=request_codes,
                            stock_name=None,
                            original_query=",".join(request_codes),
                            selection_source="manual",
                            report_type="detailed",
                            force_refresh=False,
                            async_mode=True,
                            notify=True,
                            analysis_phase="auto",
                        ),
                        config=SimpleNamespace(),
                    )

                self.assertEqual(response.status_code, 202)
                payload = json.loads(response.body)
                accepted = payload["accepted"]
                self.assertEqual(len(accepted), 1)
                self.assertEqual(accepted[0]["stock_code"], stock_code)
                self.assertEqual(accepted[0]["asset_type"], expected_asset_type)

    def _legacy_task_mock(
        self,
        *,
        task_id: str,
        asset_type: str | None = None,
        with_task_list_fields: bool = False,
    ) -> MagicMock:
        """Build a legacy MagicMock proxy task; ``asset_type`` stays implicit
        unless explicitly requested so ``getattr`` yields a MagicMock child."""
        kwargs: dict = {
            "task_id": task_id,
            "trace_id": f"trace-{task_id}",
            "stock_code": "HPG",
            "stock_name": "Hòa Phát",
            "analysis_phase": "auto",
        }
        if with_task_list_fields:
            kwargs.update(
                status=TaskStatus.PENDING,
                progress=0,
                message="waiting",
                report_type="detailed",
                created_at=datetime(2026, 4, 10, 12, 0, 0),
                started_at=None,
                completed_at=None,
                error=None,
                original_query=None,
                selection_source=None,
                skills=None,
                region=None,
            )
        if asset_type is not None:
            kwargs["asset_type"] = asset_type
        return MagicMock(**kwargs)

    def _task_list_asset_type_value(self, task) -> object:
        """Run get_task_list against a mocked queue and return the asset_type."""
        queue = MagicMock()
        queue.list_all_tasks.return_value = [task]
        queue.get_task_stats.return_value = {
            "total": 1,
            "pending": 1,
            "processing": 0,
            "completed": 0,
            "failed": 0,
        }
        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = get_task_list(status=None, limit=20)
        # 走真实 JSON 序列化再读取，证明 GET /tasks 响应层面降级为 null，
        # 而非仅依赖 Pydantic 对象属性。
        payload = json.loads(response.model_dump_json())
        return payload["tasks"][0]["asset_type"]

    def _batch_accepted_asset_type_value(self, task) -> object:
        """Run trigger_analysis in batch mode against a mocked queue and return
        the first accepted item's asset_type."""
        queue = MagicMock()
        queue.submit_tasks_batch.return_value = ([task], [])
        with patch("api.v1.endpoints.analysis.get_task_queue", return_value=queue):
            response = trigger_analysis(
                request=SimpleNamespace(
                    stock_code=None,
                    stock_codes=["HPG", "VCB"],
                    stock_name=None,
                    original_query="HPG,VCB",
                    selection_source="manual",
                    report_type="detailed",
                    force_refresh=False,
                    async_mode=True,
                    notify=True,
                    analysis_phase="auto",
                ),
                config=SimpleNamespace(),
            )
        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.body)
        accepted = payload["accepted"]
        self.assertEqual(len(accepted), 1)
        return accepted[0]["asset_type"]

    def test_asset_type_degrades_magicmock_proxy_without_warning(self) -> None:
        """Legacy MagicMock proxy (implicit asset_type) degrades to None on both
        response entries and never emits a warning."""
        if get_task_list is None or trigger_analysis is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        with self.subTest(entry="task_list"):
            with self.assertNoLogs("api.v1.endpoints.analysis", level="WARNING"):
                value = self._task_list_asset_type_value(
                    self._legacy_task_mock(
                        task_id="task-list-magic-asset-type",
                        with_task_list_fields=True,
                    )
                )
            self.assertIsNone(value)

        with self.subTest(entry="batch_accepted"):
            with self.assertNoLogs("api.v1.endpoints.analysis", level="WARNING"):
                value = self._batch_accepted_asset_type_value(
                    self._legacy_task_mock(task_id="task-batch-magic-asset-type")
                )
            self.assertIsNone(value)

    def test_asset_type_degrades_out_of_domain_string_with_warning(self) -> None:
        """A real string outside the literal domain (including blank and
        whitespace-only forms) degrades to None on both response entries and
        logs a warning carrying the task id and value on every entry."""
        if get_task_list is None or trigger_analysis is None:
            self.skipTest("analysis endpoint helpers unavailable in this environment")

        invalid_values = ("etf", "   ")
        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                with self.subTest(entry="task_list"):
                    with self.assertLogs("api.v1.endpoints.analysis", level="WARNING") as logs:
                        value = self._task_list_asset_type_value(
                            self._legacy_task_mock(
                                task_id="task-list-out-of-domain-asset-type",
                                asset_type=invalid_value,
                                with_task_list_fields=True,
                            )
                        )
                    self.assertIsNone(value)
                    self.assertTrue(
                        any(
                            "task_id=task-list-out-of-domain-asset-type" in line
                            and f"asset_type={invalid_value!r}" in line
                            for line in logs.output
                        ),
                        "expected warning carrying task id and "
                        f"{invalid_value!r} value, got: {logs.output}",
                    )

                with self.subTest(entry="batch_accepted"):
                    with self.assertLogs("api.v1.endpoints.analysis", level="WARNING") as logs:
                        value = self._batch_accepted_asset_type_value(
                            self._legacy_task_mock(
                                task_id="task-batch-out-of-domain-asset-type",
                                asset_type=invalid_value,
                            )
                        )
                    self.assertIsNone(value)
                    self.assertTrue(
                        any(
                            "task_id=task-batch-out-of-domain-asset-type" in line
                            and f"asset_type={invalid_value!r}" in line
                            for line in logs.output
                        ),
                        "expected warning carrying task id and "
                        f"{invalid_value!r} value, got: {logs.output}",
                    )


class BatchTaskQueueContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None

    def tearDown(self) -> None:
        queue = AnalysisTaskQueue._instance
        if queue is not None and queue is not self._original_instance:
            executor = getattr(queue, "_executor", None)
            if executor is not None and hasattr(executor, "shutdown"):
                executor.shutdown(wait=False, cancel_futures=True)
        AnalysisTaskQueue._instance = self._original_instance

    def test_batch_submit_rolls_back_when_executor_submit_fails(self) -> None:
        class FailingExecutor:
            def __init__(self) -> None:
                self.submit_count = 0

            def submit(self, *args, **kwargs):
                self.submit_count += 1
                if self.submit_count == 2:
                    raise RuntimeError("executor down")
                return Future()

        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = FailingExecutor()

        with self.assertRaisesRegex(RuntimeError, "executor down"):
            queue.submit_tasks_batch(["HPG", "VNM"], report_type="detailed")

        self.assertEqual(queue._tasks, {})
        self.assertEqual(queue._analyzing_stocks, {})
        self.assertEqual(queue._futures, {})

    def test_batch_submit_ignores_blank_stock_codes(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()

        accepted, duplicates = queue.submit_tasks_batch(["HPG", "   "], report_type="detailed")

        self.assertEqual([task.stock_code for task in accepted], ["HPG"])
        self.assertEqual(duplicates, [])
        self.assertEqual(sorted(task.stock_code for task in queue._tasks.values()), ["HPG"])

    def test_batch_submit_and_worker_use_copied_request_skills(self) -> None:
        class CapturingExecutor:
            def __init__(self) -> None:
                self.calls = []

            def submit(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))
                return Future()

        queue = AnalysisTaskQueue(max_workers=1)
        executor = CapturingExecutor()
        queue._executor = executor
        broadcast_events = []
        queue._broadcast_event = lambda event_type, data: broadcast_events.append((event_type, data))
        request_skills = ["growth_quality"]
        portfolio_context = {
            "account_id": 7,
            "account_name": "Main",
            "symbol": "HPG",
            "quantity": 100,
        }

        accepted, duplicates = queue.submit_tasks_batch(
            ["HPG"],
            report_type="detailed",
            analysis_phase="intraday",
            query_source="portfolio",
            portfolio_context=portfolio_context,
            skills=request_skills,
        )
        request_skills.append("mutated_after_submit")
        portfolio_context["quantity"] = 999

        self.assertEqual(duplicates, [])
        self.assertEqual(accepted[0].analysis_phase, "intraday")
        self.assertEqual(accepted[0].to_dict()["analysis_phase"], "intraday")
        self.assertNotIn("portfolio_context", accepted[0].to_dict())
        self.assertNotIn("query_source", accepted[0].to_dict())
        self.assertNotIn("portfolio_context", broadcast_events[0][1])
        self.assertNotIn("query_source", broadcast_events[0][1])
        self.assertEqual(accepted[0].copy().analysis_phase, "intraday")
        self.assertEqual(accepted[0].query_source, "portfolio")
        self.assertEqual(accepted[0].portfolio_context["quantity"], 100)
        self.assertEqual(accepted[0].copy().portfolio_context["quantity"], 100)
        self.assertEqual(accepted[0].skills, ["growth_quality"])
        self.assertIs(executor.calls[0][1][5], accepted[0].skills)
        self.assertIsNone(executor.calls[0][1][6])

        service_instance = MagicMock()
        service_instance.analyze_stock.return_value = {"stock_name": "Hòa Phát"}
        with patch("src.services.analysis_service.AnalysisService", return_value=service_instance):
            executor.calls[0][0](*executor.calls[0][1])

        self.assertIs(
            service_instance.analyze_stock.call_args.kwargs["skills"],
            accepted[0].skills,
        )
        self.assertEqual(service_instance.analyze_stock.call_args.kwargs["skills"], ["growth_quality"])
        self.assertEqual(service_instance.analyze_stock.call_args.kwargs["analysis_phase"], "intraday")
        self.assertEqual(service_instance.analyze_stock.call_args.kwargs["query_source"], "portfolio")
        self.assertEqual(
            service_instance.analyze_stock.call_args.kwargs["portfolio_context"]["quantity"],
            100,
        )

    def test_batch_submit_deduplicates_equivalent_stock_code_shapes(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()

        accepted, duplicates = queue.submit_tasks_batch(["HPG"], report_type="detailed")

        self.assertEqual(len(accepted), 1)
        self.assertEqual(duplicates, [])
        self.assertTrue(queue.is_analyzing("HPG.VN"))
        self.assertEqual(queue.get_analyzing_task_id("HPG.VN"), accepted[0].task_id)

        accepted_again, duplicates_again = queue.submit_tasks_batch(
            ["HPG.VN"],
            report_type="detailed",
            analysis_phase="intraday",
        )

        self.assertEqual(accepted_again, [])
        self.assertEqual(len(duplicates_again), 1)
        self.assertEqual(duplicates_again[0].stock_code, "HPG.VN")
        self.assertEqual(duplicates_again[0].existing_task_id, accepted[0].task_id)

    def test_submit_task_rejects_blank_stock_code(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()

        with self.assertRaisesRegex(ValueError, "Stock code must not be empty or whitespace only"):
            queue.submit_task("   ", report_type="detailed")

        self.assertEqual(queue._tasks, {})
        self.assertEqual(queue._analyzing_stocks, {})
        self.assertEqual(queue._futures, {})

    def test_batch_submit_broadcasts_task_created_while_queue_lock_is_held(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()
        lock_states = []

        def record_broadcast(event_type, data):
            if event_type == "task_created":
                lock_states.append(queue._data_lock._is_owned())

        queue._broadcast_event = record_broadcast

        accepted, duplicates = queue.submit_tasks_batch(["HPG", "VNM"], report_type="detailed")

        self.assertEqual(len(accepted), 2)
        self.assertEqual(duplicates, [])
        self.assertEqual(lock_states, [True, True])

    def test_update_task_progress_broadcasts_task_progress_event(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()
        accepted, _ = queue.submit_tasks_batch(["HPG"], report_type="detailed")

        events = []
        queue._broadcast_event = lambda event_type, data: events.append((event_type, data))

        updated = queue.update_task_progress(
            accepted[0].task_id,
            62,
            "LLM 正在生成分析结果",
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.progress, 62)
        self.assertEqual(updated.message, "LLM 正在生成分析结果")
        self.assertEqual(events, [("task_progress", updated.to_dict())])

    def _executor_stub_queue(self) -> AnalysisTaskQueue:
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()
        return queue

    def test_stock_dedup_semantics_unchanged_with_analysis_targets_none(self) -> None:
        queue = self._executor_stub_queue()
        accepted, duplicates = queue.submit_tasks_batch(["HPG"], report_type="detailed")

        self.assertEqual(len(accepted), 1)
        self.assertEqual(duplicates, [])
        self.assertTrue(queue.is_analyzing("HPG.VN"))
        self.assertEqual(queue.get_analyzing_task_id("HPG.VN"), accepted[0].task_id)
        self.assertIsNone(accepted[0].analysis_target)
        self.assertIsNotNone(accepted[0].dedupe_key)

class ImageStockExtractorContractTestCase(unittest.TestCase):
    def test_litellm_completion_patch_target_remains_available(self) -> None:
        cfg = SimpleNamespace(
            vision_model="",
            openai_vision_model=None,
            litellm_model="",
            gemini_api_keys=["sk-gemini-testkey-1234"],
            gemini_model="gemini-2.0-flash",
            anthropic_api_keys=[],
            anthropic_model="claude-3-5-sonnet-20241022",
            openai_api_keys=[],
            openai_model="gpt-4o-mini",
            openai_base_url=None,
        )
        msg = MagicMock()
        msg.content = '["HPG"]'
        choice = MagicMock()
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]

        with patch("src.services.image_stock_extractor.get_config", return_value=cfg), \
             patch("src.services.image_stock_extractor.litellm.completion", return_value=response) as mock_completion:
            result = _call_litellm_vision("base64data", "image/jpeg")

        self.assertEqual(result, '["HPG"]')
        mock_completion.assert_called_once()


if __name__ == "__main__":
    unittest.main()
