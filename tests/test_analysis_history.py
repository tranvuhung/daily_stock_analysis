# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 分析历史存储单元测试
===================================

职责：
1. 验证分析历史保存逻辑
2. 验证上下文快照保存开关
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

try:
    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.v1.endpoints.history import delete_history_by_code, get_history_detail, get_history_list, get_stock_bar
except ModuleNotFoundError:
    TestClient = None
    create_app = None
    delete_history_by_code = None
    get_history_detail = None
    get_history_list = None
    get_stock_bar = None

from src.config import Config
from src.storage import (
    DatabaseManager,
    AnalysisHistory,
    BacktestResult,
    DecisionSignalFeedbackRecord,
    DecisionSignalOutcomeRecord,
    DecisionSignalRecord,
)
from src.analyzer import AnalysisResult
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail
from src.services.history_service import HistoryService
import src.auth as auth


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
                "label": "Quote",
                "status": "available",
                "source": "mock",
                "warnings": [],
                "missing_reasons": [],
            }
        ],
        "counts": {
            "available": 1,
            "missing": 0,
            "not_supported": 0,
            "fallback": 0,
            "stale": 0,
            "estimated": 0,
            "partial": 0,
            "fetch_failed": 0,
        },
        "data_quality": {
            "overall_score": 100,
            "level": "good",
            "block_scores": {
                "quote": 100,
                "daily_bars": 100,
                "technical": 100,
                "news": 100,
                "fundamentals": 100,
            },
            "limitations": [],
        },
        "warnings": [],
        "metadata": {
            "trigger_source": "api",
            "news_result_count": 2,
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


class AnalysisHistoryTestCase(unittest.TestCase):
    """分析历史存储测试"""

    def setUp(self) -> None:
        """为每个用例初始化独立数据库"""
        auth._auth_enabled = False
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_analysis_history.db")
        self._original_env = {
            key: os.environ.get(key)
            for key in (
                "ENV_FILE",
                "DATABASE_PATH",
            )
        }
        self._env_path = os.path.join(self._temp_dir.name, ".env")
        with open(self._env_path, "w", encoding="utf-8") as env_file:
            env_file.write("STOCK_LIST=HPG,FPT\n")

        os.environ["ENV_FILE"] = self._env_path
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        """清理资源"""
        Config._instance = None
        DatabaseManager.reset_instance()
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._temp_dir.cleanup()

    def test_history_timestamps_include_server_timezone_offset(self) -> None:
        serialized = HistoryService._serialize_created_at(datetime(2026, 7, 11, 0, 30))

        self.assertIsNotNone(serialized)
        self.assertRegex(serialized or "", r"[+-]\d{2}:\d{2}$")

    def test_history_query_failure_is_not_returned_as_an_empty_success(self) -> None:
        db = MagicMock()
        db.get_analysis_history_paginated.side_effect = RuntimeError("database unavailable")

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            HistoryService(db).get_history_list(page=1, limit=20)

        if get_history_list is None:
            self.skipTest("fastapi is not installed in this test environment")

        with self.assertRaises(Exception) as raised:
            get_history_list(
                stock_code=None,
                report_type=None,
                start_date=None,
                end_date=None,
                page=1,
                limit=20,
                db_manager=db,
            )

        self.assertEqual(getattr(raised.exception, "status_code", None), 500)

    def test_delete_history_by_code_deletes_more_than_one_lookup_batch(self) -> None:
        if delete_history_by_code is None:
            self.skipTest("fastapi is not installed in this test environment")

        remaining = {record_id: SimpleNamespace(id=record_id) for record_id in range(1, 10_002)}
        db = MagicMock()

        def get_records(*, code, limit=20, offset=0, **_kwargs):
            records = list(remaining.values())[offset:offset + limit]
            return records, len(remaining)

        def delete_records(record_ids):
            deleted = 0
            for record_id in record_ids:
                if remaining.pop(record_id, None) is not None:
                    deleted += 1
            return deleted

        db.get_analysis_history_paginated.side_effect = get_records
        db.delete_analysis_history_records.side_effect = delete_records

        response = delete_history_by_code("HPG", db_manager=db)

        self.assertEqual(response.deleted, 10_001)
        self.assertEqual(remaining, {})
        self.assertEqual(db.get_analysis_history_paginated.call_count, 2)

    def test_delete_history_by_code_rejects_blank_code_before_query(self) -> None:
        if delete_history_by_code is None:
            self.skipTest("fastapi is not installed in this test environment")

        record_id = self._save_history("query_delete_blank_code")
        with (
            patch.object(
                self.db,
                "get_analysis_history_paginated",
                wraps=self.db.get_analysis_history_paginated,
            ) as query,
            patch.object(
                self.db,
                "delete_analysis_history_records",
                wraps=self.db.delete_analysis_history_records,
            ) as delete,
        ):
            with self.assertRaises(Exception) as raised:
                delete_history_by_code(" ", db_manager=self.db)

        self.assertEqual(getattr(raised.exception, "status_code", None), 400)
        query.assert_not_called()
        delete.assert_not_called()
        with self.db.get_session() as session:
            self.assertIsNotNone(session.query(AnalysisHistory).filter(AnalysisHistory.id == record_id).first())

    def _build_result(self) -> AnalysisResult:
        """构造分析结果"""
        return AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            sentiment_score=78,
            trend_prediction="Tăng",
            operation_advice="Nắm giữ",
            analysis_summary="Cơ bản vững, ngắn hạn đi ngang",
        )

    def _save_history(self, query_id: str) -> int:
        """保存一条测试历史记录并返回主键 ID。"""
        result = self._build_result()
        saved = self.db.save_analysis_history(
            result=result,
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            return row.id

    def test_save_analysis_history_with_snapshot(self) -> None:
        """保存历史记录并写入上下文快照"""
        result = self._build_result()
        result.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "理想买入点：125.5元",
                    "secondary_buy": "120",
                    "stop_loss": "止损位：110元",
                    "take_profit": "目标位：150.0元",
                }
            }
        }
        context_snapshot = {"enhanced_context": {"code": "HPG"}}

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_001",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=context_snapshot,
            save_snapshot=True
        )

        self.assertGreater(saved, 0)

        history = self.db.get_analysis_history(code="HPG", days=7, limit=10)
        self.assertEqual(len(history), 1)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            self.assertEqual(row.query_id, "query_001")
            self.assertIsNotNone(row.context_snapshot)
            self.assertEqual(row.ideal_buy, 125.5)
            self.assertEqual(row.secondary_buy, 120.0)
            self.assertEqual(row.stop_loss, 110.0)
            self.assertEqual(row.take_profit, 150.0)

    def test_save_analysis_history_persists_sniper_columns_via_shared_parser(self) -> None:
        """迁出 sniper parser 后历史狙击点位列仍按原规则保存。"""
        result = self._build_result()
        result.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "理想买入点：125.5元",
                    "secondary_buy": "1.52-1.53 (回踩MA5/10附近)",
                    "stop_loss": "—",
                    "take_profit": "目标位：150.0元",
                }
            }
        }

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_shared_sniper_parser",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False,
        )

        self.assertGreater(saved, 0)
        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "query_shared_sniper_parser"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            self.assertEqual(row.ideal_buy, 125.5)
            self.assertEqual(row.secondary_buy, 1.53)
            self.assertIsNone(row.stop_loss)
            self.assertEqual(row.take_profit, 150.0)

    def test_get_latest_analysis_history_id_filters_by_report_type_and_latest_record(self) -> None:
        """按 query/code/report_type 返回最新真实历史主键。"""
        for report_type in ("simple", "full", "simple"):
            saved = self.db.save_analysis_history(
                result=self._build_result(),
                query_id="query_latest_id",
                report_type=report_type,
                news_content="新闻摘要",
                context_snapshot=None,
                save_snapshot=False,
            )
            self.assertGreater(saved, 0)

        simple_id = self.db.get_latest_analysis_history_id(
            query_id="query_latest_id",
            code="HPG",
            report_type="simple",
        )
        full_id = self.db.get_latest_analysis_history_id(
            query_id="query_latest_id",
            code="HPG",
            report_type="full",
        )

        self.assertIsNotNone(simple_id)
        self.assertIsNotNone(full_id)
        self.assertGreater(simple_id, full_id)

    def test_get_latest_analysis_history_id_requires_report_type(self) -> None:
        """report_type 是必传参数，避免误取同 query/code 的其他报告。"""
        with self.assertRaises(TypeError):
            self.db.get_latest_analysis_history_id(query_id="query", code="HPG")

    def test_save_analysis_history_without_snapshot(self) -> None:
        """关闭快照保存时不写入 context_snapshot"""
        result = self._build_result()

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_002",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot={"foo": "bar"},
            save_snapshot=False
        )

        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            self.assertIsNone(row.context_snapshot)

    def test_save_analysis_history_persists_model_used(self) -> None:
        """model_used should be persisted in raw_result for history detail."""
        result = self._build_result()
        result.model_used = "gemini/gemini-2.0-flash"

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_003",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == "query_003").first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            payload = json.loads(row.raw_result or "{}")
            self.assertEqual(payload.get("model_used"), "gemini/gemini-2.0-flash")

    def test_update_analysis_history_diagnostics_preserves_snapshot_fields(self) -> None:
        """通知发送后补写 diagnostics 时，不应覆盖已有上下文字段。"""
        saved = self.db.save_analysis_history(
            result=self._build_result(),
            query_id="query_diag_patch",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot={
                "enhanced_context": {"code": "HPG"},
                "diagnostics": {
                    "trace_id": "trace-1",
                    "query_id": "query_diag_patch",
                    "stock_code": "HPG",
                    "notification_runs": [],
                },
            },
            save_snapshot=True,
        )
        self.assertGreater(saved, 0)

        updated = self.db.update_analysis_history_diagnostics(
            query_id="query_diag_patch",
            code="HPG",
            notification_runs=[
                {
                    "channel": "report",
                    "status": "success",
                    "success": True,
                }
            ],
        )

        self.assertEqual(updated, 1)
        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "query_diag_patch"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            snapshot = json.loads(row.context_snapshot or "{}")
            self.assertEqual(snapshot["enhanced_context"]["code"], "HPG")
            notification_run = snapshot["diagnostics"]["notification_runs"][-1]
            self.assertEqual(notification_run["status"], "success")
            self.assertEqual(notification_run["trace_id"], "trace-1")

    def test_history_detail_hides_placeholder_model_used(self) -> None:
        """Placeholder model values should be normalized to None in detail response."""
        result = self._build_result()
        result.model_used = "unknown"

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_004",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == "query_004").first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        service = HistoryService(self.db)
        detail = service.get_history_detail_by_id(record_id)
        self.assertIsNotNone(detail)
        self.assertIsNone(detail.get("model_used"))

    def test_history_list_includes_timeline_summary_fields(self) -> None:
        """History list items expose the fields needed by the same-stock timeline drawer."""
        result = self._build_result()
        result.model_used = "gemini/gemini-2.5-pro"
        context_snapshot = {
            "enhanced_context": {
                "realtime": {
                    "price": "51.5",
                    "change_pct": "-4.61%",
                    "volume_ratio": "1.17",
                    "turnover_rate": "11.46",
                },
            },
            "market_phase_summary": _market_phase_summary(),
        }

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_timeline_summary",
            report_type="detailed",
            news_content="新闻摘要",
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        self.assertGreater(saved, 0)

        service = HistoryService(self.db)
        payload = service.get_history_list(stock_code="HPG.VN", page=1, limit=5)

        self.assertEqual(payload["total"], 1)
        item = payload["items"][0]
        self.assertEqual(item["stock_code"], "HPG")
        self.assertEqual(item["trend_prediction"], "Tăng")
        self.assertEqual(item["analysis_summary"], "Cơ bản vững, ngắn hạn đi ngang")
        self.assertEqual(item["operation_advice"], "Nắm giữ")
        self.assertEqual(item["action"], "buy")
        self.assertEqual(item["action_label"], "Mua")
        self.assertEqual(item["model_used"], "gemini/gemini-2.5-pro")
        self.assertEqual(item["current_price"], 51.5)
        self.assertEqual(item["change_pct"], -4.61)
        self.assertEqual(item["volume_ratio"], 1.17)
        self.assertEqual(item["turnover_rate"], 11.46)
        self.assertEqual(item["market_phase_summary"]["phase"], "intraday")
        self.assertEqual(item["market_phase_summary"]["minutes_to_close"], 300)

    def test_history_persistence_keeps_softened_operation_advice_from_guardrail(self) -> None:
        """Conservative-market guardrail short operation_advice is persisted and exposed to history list."""
        result = self._build_result()
        result.decision_type = "buy"
        result.operation_advice = "Mua ngay và gia tăng tỷ trọng mạnh"

        apply_daily_market_context_guardrail(
            result,
            daily_market_context={
                "region": "vn",
                "trade_date": "2026-06-06",
                "summary": "Thị trường suy yếu, rủi ro cao, nên quan sát, tỷ trọng tối đa 30%.",
                "risk_tags": ["high_risk", "low_position_cap"],
            },
            report_language="vi",
        )

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_softened_operation_advice",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        service = HistoryService(self.db)
        payload = service.get_history_list(stock_code="HPG", page=1, limit=10)

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["operation_advice"], "Quan sát")
        self.assertLessEqual(len(payload["items"][0]["operation_advice"]), 20)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "query_softened_operation_advice"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            self.assertEqual(row.operation_advice, "Quan sát")

    def test_market_review_history_can_be_filtered_without_stock_records(self) -> None:
        """Market review records should be queryable as a dedicated history collection."""
        stock_result = self._build_result()
        market_result = AnalysisResult(
            code="MARKET",
            name="大盘复盘",
            sentiment_score=50,
            trend_prediction="大盘复盘",
            operation_advice="查看复盘",
            analysis_summary="大盘复盘摘要",
        )

        self.assertGreater(
            self.db.save_analysis_history(
                result=stock_result,
                query_id="query_stock_history",
                report_type="detailed",
                news_content="个股正文",
                context_snapshot=None,
                save_snapshot=False,
            ),
            0,
        )
        self.assertGreater(
            self.db.save_analysis_history(
                result=market_result,
                query_id="query_market_review_history",
                report_type="market_review",
                news_content="大盘复盘正文",
                context_snapshot={
                    "report_kind": "market_review",
                    "market_review_region": "jp,kr",
                    "market_review_payload": {
                        "kind": "market_review",
                        "region": "jp,kr",
                        "sections": [{"title": "复盘", "markdown": "结构化正文"}],
                    },
                },
                save_snapshot=True,
            ),
            0,
        )

        service = HistoryService(self.db)
        payload = service.get_history_list(
            stock_code="MARKET",
            report_type="market_review",
            page=1,
            limit=10,
        )

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["stock_code"], "MARKET")
        self.assertEqual(payload["items"][0]["report_type"], "market_review")
        self.assertEqual(payload["items"][0]["region"], "jp,kr")
        self.assertIsNone(payload["items"][0]["action"])
        self.assertIsNone(payload["items"][0]["action_label"])

        if get_history_list is not None:
            response = get_history_list(
                stock_code="MARKET",
                report_type="market_review",
                start_date=None,
                end_date=None,
                page=1,
                limit=10,
                db_manager=self.db,
            )
            self.assertEqual(response.items[0].region, "jp,kr")

    def test_distinct_stock_bar_excludes_market_review_records_by_default(self) -> None:
        """The stock bar aggregation should not mix MARKET into ordinary stock entries."""
        stock_result = self._build_result()
        market_result = AnalysisResult(
            code="MARKET",
            name="大盘复盘",
            sentiment_score=50,
            trend_prediction="大盘复盘",
            operation_advice="查看复盘",
            analysis_summary="大盘复盘摘要",
        )

        self.assertGreater(
            self.db.save_analysis_history(
                result=stock_result,
                query_id="query_stock_bar_stock",
                report_type="detailed",
                news_content="个股正文",
                context_snapshot=None,
                save_snapshot=False,
            ),
            0,
        )
        self.assertGreater(
            self.db.save_analysis_history(
                result=market_result,
                query_id="query_stock_bar_market",
                report_type="market_review",
                news_content="大盘复盘正文",
                context_snapshot=None,
                save_snapshot=False,
            ),
            0,
        )

        records = self.db.get_distinct_stocks_from_history(limit=10)

        self.assertEqual([record.code for record in records], ["HPG"])

    def test_stock_bar_item_derives_action_fields_from_legacy_advice(self) -> None:
        if get_stock_bar is None:
            self.skipTest("fastapi is not installed in this test environment")

        result = self._build_result()
        result.operation_advice = "Không nên mua"

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_stock_bar_action",
            report_type="detailed",
            news_content="个股正文",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        response = get_stock_bar(
            start_date=None,
            end_date=None,
            limit=10,
            db_manager=self.db,
        )

        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].operation_advice, "Không nên mua")
        self.assertEqual(response.items[0].action, "avoid")
        self.assertEqual(response.items[0].action_label, "Tránh")

    def test_stock_bar_item_aligns_score_and_legacy_advice(self) -> None:
        if get_stock_bar is None:
            self.skipTest("fastapi is not installed in this test environment")

        result = self._build_result()
        result.operation_advice = "Nắm giữ"
        result.sentiment_score = 78

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_stock_bar_score_align",
            report_type="detailed",
            news_content="个股正文",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        response = get_stock_bar(
            start_date=None,
            end_date=None,
            limit=10,
            db_manager=self.db,
        )

        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].operation_advice, "Nắm giữ")
        self.assertEqual(response.items[0].sentiment_score, 78)
        self.assertEqual(response.items[0].action, "buy")
        self.assertEqual(response.items[0].action_label, "Mua")

    def test_stock_bar_item_falls_back_to_raw_result_summary_fields(self) -> None:
        if get_stock_bar is None:
            self.skipTest("fastapi is not installed in this test environment")

        result = self._build_result()
        result.operation_advice = "Hold"
        result.report_language = "en"

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_stock_bar_raw_fallback",
            report_type="detailed",
            news_content="stock report",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.session_scope() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "query_stock_bar_raw_fallback"
            ).first()
            self.assertIsNotNone(row)
            row.sentiment_score = None
            row.operation_advice = None

        response = get_stock_bar(
            start_date=None,
            end_date=None,
            limit=10,
            db_manager=self.db,
        )

        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].sentiment_score, 78)
        self.assertEqual(response.items[0].operation_advice, "Hold")
        self.assertEqual(response.items[0].action, "buy")
        self.assertEqual(response.items[0].action_label, "Buy")

    def test_history_detail_uses_service_resolved_action_fields(self) -> None:
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        service = MagicMock()
        service.resolve_and_get_detail.return_value = {
            "id": 1,
            "query_id": "query_action_conflict",
            "stock_code": "HPG",
            "stock_name": "Hòa Phát",
            "report_type": "detailed",
            "report_language": "zh",
            "created_at": "2026-05-21T17:40:00",
            "sentiment_score": 45,
            "operation_advice": "持有观察",
            "action": "watch",
            "action_label": "观望",
            "trend_prediction": "震荡",
            "analysis_summary": "等待确认",
            "raw_result": {
                "operation_advice": "持有观察",
                "action": "watch",
                "report_language": "zh",
            },
        }

        with patch("api.v1.endpoints.history.HistoryService", return_value=service):
            response = get_history_detail("query_action_conflict", db_manager=self.db)

        self.assertEqual(response.summary.operation_advice, "持有观察")
        self.assertEqual(response.summary.action, "watch")
        self.assertEqual(response.summary.action_label, "观望")

    def test_history_list_matches_equivalent_suffixed_stock_codes(self) -> None:
        """Lịch sử cùng mã phải gộp bản ghi lưu dạng hậu tố ``.VN``.

        Same-stock history includes rows saved with the legacy ``.VN`` suffix.
        """

        def save_record(code: str, query_id: str) -> None:
            result = self._build_result()
            result.code = code
            saved = self.db.save_analysis_history(
                result=result,
                query_id=query_id,
                report_type="simple",
                news_content="Tóm tắt tin tức",
                context_snapshot=None,
                save_snapshot=False,
            )
            self.assertGreater(saved, 0)

        save_record("HPG.VN", "query_vn_suffix")
        save_record("HPG", "query_vn_plain")
        save_record("FPT", "query_other_stock")

        service = HistoryService(self.db)
        expected = {"HPG.VN", "HPG"}

        for query in ("HPG.VN", "HPG", "hpg"):
            payload = service.get_history_list(stock_code=query, page=1, limit=10)
            self.assertEqual(payload["total"], 2, query)
            self.assertEqual({item["stock_code"] for item in payload["items"]}, expected, query)

    def test_history_detail_preserves_zero_change_pct(self) -> None:
        """change_pct=0.0（平盘）应原样返回，而不是被当成缺失值丢失。

        Regression for issue #1084: history endpoint used `or` chains that
        treated 0.0 as falsy and silently dropped the daily change.
        """
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        context_snapshot = {
            "enhanced_context": {
                "realtime": {"price": 100.0, "change_pct": 0.0},
            }
        }
        query_id = "query_change_pct_zero"
        saved = self.db.save_analysis_history(
            result=self._build_result(),
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)
        self.assertEqual(report.meta.current_price, 100.0)
        self.assertEqual(report.meta.change_pct, 0.0)

    def test_history_detail_falls_back_to_realtime_quote_raw_change_pct(self) -> None:
        """缺少 enhanced_context.realtime.change_pct 时，应回退到 realtime_quote_raw。

        Regression for issue #1084: previously the realtime_quote_raw fallback
        was only consulted when current_price was missing, so reports with
        price-only enhanced_context lost their change_pct entirely.
        """
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        context_snapshot = {
            "enhanced_context": {
                "realtime": {"price": 200.0},
            },
            "realtime_quote_raw": {"change_pct": 1.23},
        }
        query_id = "query_change_pct_fallback"
        saved = self.db.save_analysis_history(
            result=self._build_result(),
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)
        self.assertEqual(report.meta.current_price, 200.0)
        self.assertEqual(report.meta.change_pct, 1.23)

    def test_history_detail_omits_asset_type_for_market_review(self) -> None:
        """Market review records must omit the optional asset_type field."""
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        result = self._build_result()
        result.code = "MARKET"
        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_asset_type_market_review",
            report_type="market_review",
            news_content="大盘复盘",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)
        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == "query_asset_type_market_review").first()
            self.assertIsNotNone(row)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)
        self.assertIsNone(report.meta.asset_type)

    @patch("src.auth.is_auth_enabled", return_value=False)
    def test_history_detail_ignores_non_dict_realtime_quote_raw(self, mock_auth) -> None:
        """GET /api/v1/history/{id} should tolerate truthy non-dict realtime_quote_raw."""
        if TestClient is None or create_app is None:
            self.skipTest("fastapi is not installed in this test environment")

        context_snapshot = {
            "enhanced_context": {
                "realtime": {"price": 300.0},
            },
            "realtime_quote_raw": "not-a-dict",
        }
        query_id = "query_change_pct_non_dict_raw"
        saved = self.db.save_analysis_history(
            result=self._build_result(),
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        static_dir = Path(self._temp_dir.name) / "empty-static"
        static_dir.mkdir(exist_ok=True)
        client = TestClient(create_app(static_dir=static_dir))

        response = client.get(f"/api/v1/history/{record_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["meta"]["current_price"], 300.0)
        self.assertIsNone(payload["meta"]["change_pct"])

    def test_history_detail_accepts_dict_raw_result(self) -> None:
        """_record_to_detail_dict should handle dict raw_result without json.loads errors."""
        result = self._build_result()
        result.model_used = "gemini/gemini-2.0-flash"
        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_005",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == "query_005").first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            row.raw_result = {"model_used": "unknown", "extra": "v"}

            service = HistoryService(self.db)
            detail = service._record_to_detail_dict(row)

        self.assertIsNotNone(detail)
        self.assertIsInstance(detail.get("raw_result"), dict)
        self.assertIsNone(detail.get("model_used"))

    def test_history_detail_prefers_raw_sniper_strings(self) -> None:
        """History detail should display the original sniper point strings from raw_result."""
        result = self._build_result()
        result.dashboard = {
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "理想买入点：125.5元",
                    "secondary_buy": "120-121 元分批",
                    "stop_loss": "跌破 110 元止损",
                    "take_profit": "目标位：150.0元",
                }
            }
        }

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_006",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == "query_006").first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        service = HistoryService(self.db)
        detail = service.get_history_detail_by_id(record_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.get("ideal_buy"), "理想买入点：125.5元")
        self.assertEqual(detail.get("secondary_buy"), "120-121 元分批")
        self.assertEqual(detail.get("stop_loss"), "跌破 110 元止损")
        self.assertEqual(detail.get("take_profit"), "目标位：150.0元")

    def test_history_detail_falls_back_to_numeric_sniper_columns(self) -> None:
        """History detail should still fall back to stored numeric sniper columns when raw strings are unavailable."""
        result = self._build_result()
        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_007",
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == "query_007").first()
            if row is None:
                self.fail("未找到保存的历史记录")
            row.ideal_buy = 125.5
            row.secondary_buy = 120.0
            row.stop_loss = 110.0
            row.take_profit = 150.0
            row.raw_result = json.dumps({"model_used": "gemini/gemini-2.0-flash"})
            session.commit()
            self.assertEqual(row.id, saved)
            record_id = row.id

        service = HistoryService(self.db)
        detail = service.get_history_detail_by_id(record_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.get("ideal_buy"), "125.5")
        self.assertEqual(detail.get("secondary_buy"), "120.0")
        self.assertEqual(detail.get("stop_loss"), "110.0")
        self.assertEqual(detail.get("take_profit"), "150.0")

    def test_history_detail_uses_fundamental_snapshot_fallback_when_context_missing(self) -> None:
        """When context_snapshot is disabled, detail API should fallback to fundamental_snapshot."""
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        result = self._build_result()
        query_id = "query_fundamental_fallback_001"
        saved = self.db.save_analysis_history(
            result=result,
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        self.db.save_fundamental_snapshot(
            query_id=query_id,
            code="HPG",
            payload={
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                        "dividend": {"ttm_dividend_yield_pct": 2.6, "ttm_cash_dividend_per_share": 1.3},
                    }
                }
            },
        )

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)
        self.assertEqual(report.details.financial_report["report_date"], "2025-12-31")
        self.assertEqual(report.details.dividend_metrics["ttm_dividend_yield_pct"], 2.6)

    def test_history_detail_returns_null_fundamental_fields_when_snapshot_absent(self) -> None:
        """Detail API should keep new fields nullable when no context/fundamental snapshot exists."""
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        query_id = "query_fundamental_fallback_002"
        saved = self.db.save_analysis_history(
            result=self._build_result(),
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)
        self.assertIsNone(report.details.financial_report)
        self.assertIsNone(report.details.dividend_metrics)

    def test_history_detail_returns_overview_and_sanitizes_snapshot(self) -> None:
        """History detail exposes the public overview separately from raw snapshot JSON."""
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        overview = _analysis_context_pack_overview()
        phase_summary = _market_phase_summary()
        query_id = "query_context_pack_overview_001"
        saved = self.db.save_analysis_history(
            result=self._build_result(),
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot={
                "enhanced_context": {"code": "HPG"},
                "analysis_context_pack_overview": overview,
                "market_phase_summary": {
                    **phase_summary,
                    "market_phase_context": {"raw": True},
                },
            },
            save_snapshot=True,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)
        self.assertEqual(
            report.details.analysis_context_pack_overview.metadata.trigger_source,
            "api",
        )
        self.assertEqual(
            report.details.analysis_context_pack_overview.data_quality.overall_score,
            100,
        )
        self.assertIsNotNone(report.meta.market_phase_summary)
        self.assertEqual(report.meta.market_phase_summary.phase, "intraday")
        self.assertEqual(report.meta.market_phase_summary.minutes_to_close, 300)
        self.assertEqual(report.details.analysis_context_pack_overview.metadata.news_result_count, 2)
        self.assertNotIn(
            "analysis_context_pack_overview",
            report.details.context_snapshot,
        )
        self.assertNotIn(
            "market_phase_summary",
            report.details.context_snapshot,
        )

    def test_history_detail_handles_missing_overview_when_snapshot_disabled(self) -> None:
        """SAVE_CONTEXT_SNAPSHOT=false style records should not require an overview."""
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        query_id = "query_context_pack_snapshot_disabled_001"
        saved = self.db.save_analysis_history(
            result=self._build_result(),
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot={
                "enhanced_context": {"code": "HPG"},
                "analysis_context_pack_overview": _analysis_context_pack_overview(),
            },
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.query_id == query_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id
            self.assertIsNone(row.context_snapshot)

        report = get_history_detail(str(record_id), db_manager=self.db)
        self.assertIsNone(report.meta.market_phase_summary)
        self.assertIsNone(report.details.analysis_context_pack_overview)
        self.assertIsNone(report.details.context_snapshot)

    def test_history_markdown_localizes_english_report_and_placeholder_name(self) -> None:
        """History markdown should preserve report_language for English reports."""
        result = AnalysisResult(
            code="FPT",
            name="Cổ phiếu FPT",
            sentiment_score=78,
            trend_prediction="Bullish",
            operation_advice="Buy",
            analysis_summary="Momentum remains constructive.",
            report_language="en",
            dashboard={
                "core_conclusion": {
                    "one_sentence": "Favor buying on pullbacks.",
                    "position_advice": {
                        "no_position": "Open a starter position.",
                        "has_position": "Hold and trail the stop.",
                    },
                },
                "intelligence": {
                    "risk_alerts": [],
                },
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "180-182",
                        "stop_loss": "172",
                        "take_profit": "195",
                    }
                },
            },
        )

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_english_markdown_001",
            report_type="full",
            news_content="news",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "query_english_markdown_001"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        markdown = HistoryService(self.db).get_markdown_report(str(record_id))

        self.assertIsNotNone(markdown)
        self.assertIn("Stock Analysis Report", markdown)
        self.assertIn("Core Conclusion", markdown)
        self.assertIn("Unnamed Stock (FPT)", markdown)
        self.assertNotIn("核心结论", markdown)

    def test_history_markdown_signal_metadata_uses_explicit_avoid_action(self) -> None:
        result = AnalysisResult(
            code="FPT",
            name="FPT Corp",
            sentiment_score=90,
            trend_prediction="Bullish",
            operation_advice="Hold",
            analysis_summary="Risk remains elevated.",
            report_language="en",
            action="avoid",
            action_label="Avoid",
        )

        markdown = HistoryService(self.db)._generate_single_stock_markdown(
            result,
            MagicMock(created_at=None),
        )

        self.assertIn("**🟡 Avoid** | Bullish", markdown)
        self.assertNotIn("Strong Buy", markdown)

    def test_history_markdown_handles_legacy_strategy_synthesis_shapes(self) -> None:
        service = HistoryService(self.db)
        record = MagicMock(created_at=None)

        for malformed in ("bad-shape", ["bad-shape"], 42, True):
            result = AnalysisResult(
                code="HPG",
                name="Hòa Phát",
                sentiment_score=50,
                trend_prediction="Đi ngang",
                operation_advice="Quan sát",
                report_language="vi",
                dashboard={
                    "core_conclusion": {"one_sentence": "Kiểm thử"},
                    "intelligence": {},
                    "battle_plan": {},
                    "strategy_synthesis": malformed,
                },
            )

            markdown = service._generate_single_stock_markdown(result, record)

            self.assertNotIn("Tổng hợp chiến lược", markdown)

        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            sentiment_score=50,
            trend_prediction="Đi ngang",
            operation_advice="Quan sát",
            report_language="vi",
            dashboard={
                "core_conclusion": {"one_sentence": "Kiểm thử"},
                "intelligence": {},
                "battle_plan": {},
                "strategy_synthesis": {
                    "final_signal": "hold",
                    "consensus_level": "insufficient",
                    "conflict_severity": "none",
                    "conflict_count": 0,
                    "supporting_skills": "bad-shape",
                    "opposing_skills": ["bad-shape"],
                    "conflicts": "bad-shape",
                    "summary_params": {"invalid_opinion_count": "3"},
                },
            },
        )

        markdown = service._generate_single_stock_markdown(result, record)

        self.assertIn("Tổng hợp chiến lược", markdown)
        self.assertIn("Có 3 chiến lược khác không tạo được tín hiệu hợp lệ", markdown)

    def test_history_markdown_returns_persisted_market_review_report(self) -> None:
        """Market review history should return the saved Markdown without rebuilding a stock report."""
        result = AnalysisResult(
            code="MARKET",
            name="大盘复盘",
            sentiment_score=50,
            trend_prediction="大盘复盘",
            operation_advice="查看复盘",
            analysis_summary="今日大盘复盘",
            raw_response="# 🎯 大盘复盘\n\n## 今日大盘\n\n复盘正文",
        )

        saved = self.db.save_analysis_history(
            result=result,
            query_id="market_review_query_001",
            report_type="market_review",
            news_content="## 今日大盘\n\n复盘正文",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "market_review_query_001"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        markdown = HistoryService(self.db).get_markdown_report(str(record_id))

        self.assertEqual(markdown, "# 🎯 大盘复盘\n\n## 今日大盘\n\n复盘正文")

    def test_history_detail_returns_persisted_market_review_report(self) -> None:
        """Market review detail should surface the saved recap content for Web history clicks."""
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        report_content = "# 🎯 大盘复盘\n\n## 今日大盘\n\n复盘正文"
        result = AnalysisResult(
            code="MARKET",
            name="大盘复盘",
            sentiment_score=50,
            trend_prediction="大盘复盘",
            operation_advice="查看复盘",
            analysis_summary="今日大盘复盘",
            raw_response=report_content,
        )

        saved = self.db.save_analysis_history(
            result=result,
            query_id="market_review_query_detail_001",
            report_type="market_review",
            news_content="## 今日大盘\n\n复盘正文",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "market_review_query_detail_001"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)

        self.assertEqual(report.meta.report_type, "market_review")
        self.assertEqual(report.summary.analysis_summary, "今日大盘复盘")
        self.assertIsNone(report.summary.action)
        self.assertIsNone(report.summary.action_label)
        self.assertEqual(report.details.news_content, report_content)

    def test_market_review_summary_falls_back_to_sanitized_excerpt(self) -> None:
        service = HistoryService(self.db)
        markdown = (
            "[dsa-market-region]: # (cn)\n\n"
            "# 🎯 大盘复盘\n\n"
            "## 今日观点\n\n"
            "**成交活跃**，关注 [科技板块](https://example.com)。\n\n"
            "| 指标 | 数值 |\n| --- | --- |\n| 涨跌 | +1% |\n\n"
            "```json\n{\"internal\": true}\n```"
        )

        summary = service._market_review_summary("  ", markdown)

        self.assertEqual(summary, "🎯 大盘复盘 今日观点 成交活跃，关注 科技板块。 指标 数值 涨跌 +1%")
        self.assertNotIn("dsa-market-region", summary)
        self.assertNotIn("internal", summary)

    def test_market_review_summary_prefers_persisted_summary_and_truncates_fallback(self) -> None:
        service = HistoryService(self.db)

        self.assertEqual(
            service._market_review_summary(" 已保存的短摘要 ", "# 不应使用"),
            "已保存的短摘要",
        )
        self.assertEqual(
            service._market_review_summary(None, "# " + "复" * 130),
            "复" * 120 + "…",
        )
        self.assertIsNone(service._market_review_summary(None, "[dsa-market-region]: # (cn)"))

    def test_history_detail_localizes_english_summary_fields(self) -> None:
        """History detail should localize summary enums for English reports."""
        if get_history_detail is None:
            self.skipTest("fastapi is not installed in this test environment")

        result = AnalysisResult(
            code="HPG",
            name="Cổ phiếu HPG",
            sentiment_score=78,
            trend_prediction="Tăng",
            operation_advice="Mua",
            analysis_summary="Momentum remains constructive.",
            report_language="en",
        )

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_english_detail_001",
            report_type="full",
            news_content="news",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "query_english_detail_001"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        report = get_history_detail(str(record_id), db_manager=self.db)

        self.assertEqual(report.meta.report_language, "en")
        self.assertEqual(report.meta.stock_name, "Unnamed Stock")
        self.assertEqual(report.summary.operation_advice, "Buy")
        self.assertEqual(report.summary.action, "buy")
        self.assertEqual(report.summary.action_label, "Buy")
        self.assertEqual(report.summary.trend_prediction, "Bullish")
        self.assertEqual(report.summary.sentiment_label, "Bullish")

    def test_history_markdown_uses_safe_bias_emoji_for_english_status(self) -> None:
        """English bias status should keep the correct non-risk emoji in markdown."""
        result = AnalysisResult(
            code="HPG",
            name="Cổ phiếu HPG",
            sentiment_score=80,
            trend_prediction="Bullish",
            operation_advice="Buy",
            analysis_summary="Momentum remains constructive.",
            report_language="en",
            dashboard={
                "data_perspective": {
                    "price_position": {
                        "current_price": 190.5,
                        "ma5": 188.0,
                        "ma10": 184.5,
                        "ma20": 179.2,
                        "bias_ma5": 1.33,
                        "bias_status": "Safe",
                        "support_level": 184.5,
                        "resistance_level": 195.0,
                    }
                }
            },
        )

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_english_markdown_bias_001",
            report_type="full",
            news_content="news",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        with self.db.get_session() as session:
            row = session.query(AnalysisHistory).filter(
                AnalysisHistory.query_id == "query_english_markdown_bias_001"
            ).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            self.assertEqual(row.id, saved)
            record_id = row.id

        markdown = HistoryService(self.db).get_markdown_report(str(record_id))

        self.assertIsNotNone(markdown)
        self.assertIn("✅Safe", markdown)
        self.assertNotIn("🚨Safe", markdown)

    def test_history_markdown_localizes_multi_agent_enums_for_vietnamese(self) -> None:
        """Enum ma_alignment/volume_status tiếng Anh của TechnicalAgent phải hiển thị tiếng Việt."""
        result = AnalysisResult(
            code="FPT",
            name="FPT Corporation",
            sentiment_score=45,
            trend_prediction="Giảm",
            operation_advice="Quan sát rũ bỏ",
            analysis_summary="Xu hướng giảm ngắn hạn.",
            report_language="vi",
            dashboard={
                "data_perspective": {
                    "trend_status": {"ma_alignment": "bearish", "is_bullish": False, "trend_score": 25},
                    "volume_analysis": {
                        "volume_ratio": "N/A",
                        "turnover_rate": "N/A",
                        "volume_status": "Normal Volume",
                        "volume_meaning": "Khối lượng chưa xác nhận lực mua.",
                    },
                }
            },
        )

        saved = self.db.save_analysis_history(
            result=result,
            query_id="query_vi_multi_agent_enums_001",
            report_type="full",
            news_content="news",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)

        markdown = HistoryService(self.db).get_markdown_report(str(saved))

        self.assertIsNotNone(markdown)
        self.assertIn("(Khối lượng bình thường)", markdown)
        self.assertNotIn("Normal Volume", markdown)
        self.assertIn("**Sắp xếp đường MA**: Giảm |", markdown)
        self.assertNotIn(": bearish", markdown)

    def test_delete_analysis_history_records_also_cleans_backtests_and_decision_signals(self) -> None:
        """删除历史记录时应一并清理关联回测结果和决策信号。"""
        record_id = self._save_history("query_delete_001")
        linked_signal_id = None

        with self.db.session_scope() as session:
            session.add(BacktestResult(
                analysis_history_id=record_id,
                code="HPG",
                analysis_date=None,
                eval_window_days=10,
                engine_version="v1",
                eval_status="pending",
            ))
            linked_signal = DecisionSignalRecord(
                stock_code="HPG",
                stock_name="Hòa Phát",
                market="vn",
                source_type="analysis",
                source_report_id=record_id,
                trace_id="trace-delete-linked",
                market_phase="intraday",
                trigger_source="api",
                action="buy",
                action_label="买入",
                reason="linked",
                plan_quality="minimal",
                status="active",
            )
            session.add(linked_signal)
            session.flush()
            linked_signal_id = linked_signal.id
            session.add(DecisionSignalOutcomeRecord(
                signal_id=linked_signal_id,
                horizon="3d",
                engine_version="decision-signal-v1",
                eval_status="completed",
                outcome="hit",
                action="buy",
                market="vn",
                source_type="analysis",
                plan_quality="minimal",
                holding_state="holding",
            ))
            session.add(DecisionSignalFeedbackRecord(
                signal_id=linked_signal_id,
                feedback_value="useful",
                source="api",
            ))
            session.add(DecisionSignalRecord(
                stock_code="FPT",
                stock_name="FPT Corp",
                market="vn",
                source_type="analysis",
                source_report_id=record_id + 999,
                trace_id="trace-delete-unrelated",
                market_phase="intraday",
                trigger_source="api",
                action="watch",
                action_label="观望",
                reason="unrelated",
                plan_quality="minimal",
                status="active",
            ))

        deleted = self.db.delete_analysis_history_records([record_id])
        self.assertEqual(deleted, 1)

        with self.db.get_session() as session:
            self.assertIsNone(session.query(AnalysisHistory).filter(AnalysisHistory.id == record_id).first())
            self.assertEqual(
                session.query(BacktestResult).filter(BacktestResult.analysis_history_id == record_id).count(),
                0,
            )
            self.assertEqual(
                session.query(DecisionSignalRecord).filter(DecisionSignalRecord.source_report_id == record_id).count(),
                0,
            )
            self.assertEqual(
                session.query(DecisionSignalOutcomeRecord).filter(
                    DecisionSignalOutcomeRecord.signal_id == linked_signal_id
                ).count(),
                0,
            )
            self.assertEqual(
                session.query(DecisionSignalFeedbackRecord).filter(
                    DecisionSignalFeedbackRecord.signal_id == linked_signal_id
                ).count(),
                0,
            )
            self.assertEqual(
                session.query(DecisionSignalRecord).filter(DecisionSignalRecord.trace_id == "trace-delete-unrelated").count(),
                1,
            )

    def test_delete_analysis_history_records_keeps_signals_for_nonexistent_history_id(self) -> None:
        """不存在的历史 ID 不应触发弱关联 DecisionSignal 清理。"""
        missing_id = 987654321

        with self.db.session_scope() as session:
            session.add(DecisionSignalRecord(
                stock_code="HPG",
                stock_name="Hòa Phát",
                market="vn",
                source_type="manual",
                source_report_id=missing_id,
                trace_id="trace-delete-missing-history",
                market_phase="intraday",
                trigger_source="api",
                action="watch",
                action_label="观望",
                reason="manual signal with unverified report id",
                plan_quality="minimal",
                status="active",
            ))

        deleted = self.db.delete_analysis_history_records([missing_id])
        self.assertEqual(deleted, 0)

        with self.db.get_session() as session:
            self.assertEqual(
                session.query(DecisionSignalRecord).filter(
                    DecisionSignalRecord.trace_id == "trace-delete-missing-history"
                ).count(),
                1,
            )

    def test_delete_analysis_history_records_keeps_manual_signal_with_same_report_id(self) -> None:
        """source_report_id 是弱引用，真实 history 删除不应误删 manual/pre-report 信号。"""
        record_id = self._save_history("query_delete_manual_collision")

        with self.db.session_scope() as session:
            session.add(DecisionSignalRecord(
                stock_code="HPG",
                stock_name="Hòa Phát",
                market="vn",
                source_type="analysis",
                source_report_id=record_id,
                trace_id="trace-delete-analysis-bound",
                market_phase="intraday",
                trigger_source="api",
                action="buy",
                action_label="买入",
                reason="history-bound signal",
                plan_quality="minimal",
                status="active",
            ))
            session.add(DecisionSignalRecord(
                stock_code="HPG",
                stock_name="Hòa Phát",
                market="vn",
                source_type="manual",
                source_report_id=record_id,
                trace_id="trace-delete-manual-weak-ref",
                market_phase="intraday",
                trigger_source="api",
                action="watch",
                action_label="观望",
                reason="manual signal with caller-supplied report id",
                plan_quality="minimal",
                status="active",
            ))

        deleted = self.db.delete_analysis_history_records([record_id])
        self.assertEqual(deleted, 1)

        with self.db.get_session() as session:
            self.assertEqual(
                session.query(DecisionSignalRecord).filter(
                    DecisionSignalRecord.trace_id == "trace-delete-analysis-bound"
                ).count(),
                0,
            )
            self.assertEqual(
                session.query(DecisionSignalRecord).filter(
                    DecisionSignalRecord.trace_id == "trace-delete-manual-weak-ref"
                ).count(),
                1,
            )

    def test_delete_analysis_history_records_cleans_only_existing_ids_in_mixed_batch(self) -> None:
        """混合存在/不存在 ID 时，只清理实际存在历史记录的关联数据。"""
        record_id = self._save_history("query_delete_mixed")
        missing_id = record_id + 987654

        with self.db.session_scope() as session:
            session.add(BacktestResult(
                analysis_history_id=record_id,
                code="HPG",
                analysis_date=None,
                eval_window_days=10,
                engine_version="v1",
                eval_status="pending",
            ))
            session.add(DecisionSignalRecord(
                stock_code="HPG",
                stock_name="Hòa Phát",
                market="vn",
                source_type="analysis",
                source_report_id=record_id,
                trace_id="trace-delete-mixed-linked",
                market_phase="intraday",
                trigger_source="api",
                action="buy",
                action_label="买入",
                reason="linked",
                plan_quality="minimal",
                status="active",
            ))
            session.add(DecisionSignalRecord(
                stock_code="FPT",
                stock_name="FPT Corp",
                market="vn",
                source_type="manual",
                source_report_id=missing_id,
                trace_id="trace-delete-mixed-missing",
                market_phase="intraday",
                trigger_source="api",
                action="watch",
                action_label="观望",
                reason="weak report id collision",
                plan_quality="minimal",
                status="active",
            ))

        deleted = self.db.delete_analysis_history_records([record_id, missing_id])
        self.assertEqual(deleted, 1)

        with self.db.get_session() as session:
            self.assertIsNone(session.query(AnalysisHistory).filter(AnalysisHistory.id == record_id).first())
            self.assertEqual(
                session.query(BacktestResult).filter(BacktestResult.analysis_history_id == record_id).count(),
                0,
            )
            self.assertEqual(
                session.query(DecisionSignalRecord).filter(
                    DecisionSignalRecord.trace_id == "trace-delete-mixed-linked"
                ).count(),
                0,
            )
            self.assertEqual(
                session.query(DecisionSignalRecord).filter(
                    DecisionSignalRecord.trace_id == "trace-delete-mixed-missing"
                ).count(),
                1,
            )

    @patch("src.auth.is_auth_enabled", return_value=False)
    def test_delete_history_api_deletes_selected_records(self, mock_auth) -> None:
        """DELETE /api/v1/history should remove only the requested records."""
        if TestClient is None or create_app is None:
            self.skipTest("fastapi is not installed in this test environment")

        record_id_1 = self._save_history("query_delete_api_001")
        record_id_2 = self._save_history("query_delete_api_002")

        static_dir = Path(self._temp_dir.name) / "empty-static"
        static_dir.mkdir(exist_ok=True)
        client = TestClient(create_app(static_dir=static_dir))

        response = client.request(
            "DELETE",
            "/api/v1/history",
            json={"record_ids": [record_id_1]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("deleted"), 1)

        with self.db.get_session() as session:
            self.assertIsNone(session.query(AnalysisHistory).filter(AnalysisHistory.id == record_id_1).first())
            self.assertIsNotNone(session.query(AnalysisHistory).filter(AnalysisHistory.id == record_id_2).first())

    def test_empty_news_state_round_trips_through_history_markdown(self) -> None:
        """持久化、重建和历史 Markdown 必须保留三态披露。"""
        no_channel = (
            "⚠️ Chưa cấu hình kênh tìm kiếm tin tức; "
            "phân tích này không sử dụng bằng chứng từ tin tức."
        )
        zero_hit = (
            "⚠️ Lần chạy này không lấy được dữ liệu tin tức nào; "
            "các kết luận bên dưới không dựa trên bằng chứng tin tức."
        )
        service = HistoryService(self.db)

        for suffix, count, expected in (
            ("none", None, no_channel),
            ("zero", 0, zero_hit),
            ("hits", 3, None),
        ):
            with self.subTest(state=suffix):
                result = self._build_result()
                result.news_result_count = count
                result.news_summary = ""
                query_id = f"query_empty_news_round_trip_{suffix}"
                record_id = self.db.save_analysis_history(
                    result=result,
                    query_id=query_id,
                    report_type="full",
                    news_content=None,
                    context_snapshot=None,
                    save_snapshot=False,
                )
                self.assertGreater(record_id, 0)

                with self.db.get_session() as session:
                    row = session.query(AnalysisHistory).filter(
                        AnalysisHistory.id == record_id
                    ).first()
                    if row is None:
                        self.fail("未找到保存的历史记录")
                    raw_result = json.loads(row.raw_result or "{}")
                    self.assertIn("news_result_count", raw_result)
                    self.assertEqual(raw_result["news_result_count"], count)
                    self.assertIs(raw_result["news_result_count_known"], True)
                    rebuilt = service._rebuild_analysis_result(raw_result, row)

                self.assertIsNotNone(rebuilt)
                self.assertEqual(rebuilt.news_result_count, count)
                self.assertTrue(rebuilt.news_result_count_known)
                markdown = service.get_markdown_report(str(record_id))
                self.assertIsNotNone(markdown)
                if expected is None:
                    self.assertNotIn(no_channel, markdown)
                    self.assertNotIn(zero_hit, markdown)
                else:
                    self.assertIn(expected, markdown)

                if get_history_detail is not None:
                    report = get_history_detail(str(record_id), db_manager=self.db)
                    self.assertEqual(report.details.empty_news_disclosure, expected)

    def test_legacy_history_without_news_count_stays_silent(self) -> None:
        """旧记录缺少计数字段时状态未知，不能倒推为未配置渠道。"""
        no_channel = (
            "⚠️ Chưa cấu hình kênh tìm kiếm tin tức; "
            "phân tích này không sử dụng bằng chứng từ tin tức."
        )
        zero_hit = (
            "⚠️ Lần chạy này không lấy được dữ liệu tin tức nào; "
            "các kết luận bên dưới không dựa trên bằng chứng tin tức."
        )
        record_id = self.db.save_analysis_history(
            result=self._build_result(),
            query_id="query_legacy_empty_news_unknown",
            report_type="full",
            news_content=None,
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(record_id, 0)

        with self.db.session_scope() as session:
            row = session.query(AnalysisHistory).filter(AnalysisHistory.id == record_id).first()
            if row is None:
                self.fail("未找到保存的历史记录")
            raw_result = json.loads(row.raw_result or "{}")
            raw_result.pop("news_result_count", None)
            raw_result.pop("news_result_count_known", None)
            row.raw_result = json.dumps(raw_result, ensure_ascii=False)

        record = self.db.get_analysis_history_by_id(record_id)
        self.assertIsNotNone(record)
        rebuilt = HistoryService(self.db)._rebuild_analysis_result(raw_result, record)
        self.assertIsNotNone(rebuilt)
        self.assertFalse(rebuilt.news_result_count_known)

        markdown = HistoryService(self.db).get_markdown_report(str(record_id))
        self.assertNotIn(no_channel, markdown or "")
        self.assertNotIn(zero_hit, markdown or "")
        if get_history_detail is not None:
            report = get_history_detail(str(record_id), db_manager=self.db)
            self.assertIsNone(report.details.empty_news_disclosure)

    # ------------------------------------------------------------------
    # asset_type theo parser (chỉ mã Việt Nam). / Parser asset_type (Vietnam codes only).
    # ------------------------------------------------------------------
    def _save_result_with_code(self, code: str, query_id: str) -> int:
        result = self._build_result()
        result.code = code
        saved = self.db.save_analysis_history(
            result=result,
            query_id=query_id,
            report_type="simple",
            news_content="新闻摘要",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(saved, 0)
        return saved

    def test_history_list_exposes_parser_asset_type(self):
        # Chỉ số VN (VNINDEX) không còn là asset_type riêng: parser trả "stock".
        # Vietnam indices are not a separate asset type; the parser reports "stock".
        self._save_result_with_code("HPG", "query_stock")
        self._save_result_with_code("FUEVN100", "query_fund")
        self._save_result_with_code("VNINDEX", "query_vn_index")

        listing = HistoryService(self.db).get_history_list(page=1, limit=10)
        by_query = {item["query_id"]: item for item in listing["items"]}
        self.assertEqual(by_query["query_stock"]["asset_type"], "stock")
        self.assertEqual(by_query["query_fund"]["asset_type"], "stock")
        self.assertEqual(by_query["query_vn_index"]["asset_type"], "stock")

    def test_history_list_omits_asset_type_for_market_review(self):
        self._save_result_with_code("MARKET", "query_market_review")

        listing = HistoryService(self.db).get_history_list(page=1, limit=10)
        by_query = {item["query_id"]: item for item in listing["items"]}
        self.assertTrue(by_query["query_market_review"]["asset_type"] is None)


class HistoryItemSchemaNegativeSentimentTest(unittest.TestCase):
    """Regression: HistoryItem / ReportSummary must accept out-of-range sentiment_score from DB rows."""

    @classmethod
    def setUpClass(cls) -> None:
        """Import schema classes once for all tests, skipping gracefully when deps are missing."""
        try:
            from api.v1.schemas.history import HistoryItem, ReportSummary  # type: ignore
        except ModuleNotFoundError:
            cls.HistoryItem = None
            cls.ReportSummary = None
        else:
            cls.HistoryItem = HistoryItem
            cls.ReportSummary = ReportSummary

    def test_negative_sentiment_score_does_not_raise(self) -> None:
        """Bug #942: sentiment_score=-22 in DB should not cause Pydantic ValidationError."""
        if self.HistoryItem is None:
            self.skipTest("fastapi / pydantic not installed in this test environment")

        item = self.HistoryItem(query_id="q1", stock_code="HPG", sentiment_score=-22)
        self.assertEqual(item.sentiment_score, -22)

    def test_out_of_range_high_sentiment_score_does_not_raise(self) -> None:
        """HistoryItem should also accept scores above 100 from legacy data."""
        if self.HistoryItem is None:
            self.skipTest("fastapi / pydantic not installed in this test environment")

        item = self.HistoryItem(query_id="q2", stock_code="HPG", sentiment_score=150)
        self.assertEqual(item.sentiment_score, 150)

    def test_none_sentiment_score_is_allowed(self) -> None:
        """HistoryItem.sentiment_score=None should still be valid (optional field)."""
        if self.HistoryItem is None:
            self.skipTest("fastapi / pydantic not installed in this test environment")

        item = self.HistoryItem(query_id="q3", stock_code="HPG", sentiment_score=None)
        self.assertIsNone(item.sentiment_score)

    def test_report_summary_negative_sentiment_score_does_not_raise(self) -> None:
        """ReportSummary.sentiment_score should also accept negative values from legacy DB rows."""
        if self.ReportSummary is None:
            self.skipTest("fastapi / pydantic not installed in this test environment")

        summary = self.ReportSummary(sentiment_score=-22)
        self.assertEqual(summary.sentiment_score, -22)

    def test_report_summary_out_of_range_high_sentiment_score_does_not_raise(self) -> None:
        """ReportSummary.sentiment_score should also accept scores above 100 from legacy data."""
        if self.ReportSummary is None:
            self.skipTest("fastapi / pydantic not installed in this test environment")

        summary = self.ReportSummary(sentiment_score=150)
        self.assertEqual(summary.sentiment_score, 150)

    def test_report_summary_none_sentiment_score_is_allowed(self) -> None:
        """ReportSummary.sentiment_score=None should still be valid (optional field)."""
        if self.ReportSummary is None:
            self.skipTest("fastapi / pydantic not installed in this test environment")

        summary = self.ReportSummary(sentiment_score=None)
        self.assertIsNone(summary.sentiment_score)


if __name__ == "__main__":
    unittest.main()
