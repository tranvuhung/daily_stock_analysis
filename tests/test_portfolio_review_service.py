# -*- coding: utf-8 -*-
"""Tests for the AI portfolio review service (PortfolioAgent on real holdings)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services import portfolio_review_service as review_module
from src.services.portfolio_review_service import (
    DEFAULT_REVIEW_TIMEOUT_S,
    PortfolioReviewService,
    normalize_assessment,
    resolve_portfolio_agent_timeout,
    run_portfolio_agent,
)


def _snapshot():
    return {
        "as_of": "2026-01-05",
        "currency": "VND",
        "cost_method": "fifo",
        "total_equity": 1000.0,
        "total_market_value": 800.0,
        "total_cash": 200.0,
        "data_quality": "ok",
        "limitations": [],
        "accounts": [
            {
                "account_id": 1,
                "positions": [
                    {"symbol": "HPG", "market": "vn", "quantity": 100, "total_cost": 400.0,
                     "market_value_base": 500.0, "unrealized_pnl_base": 100.0, "last_price": 5.0,
                     "price_stale": False, "price_available": True},
                    {"symbol": "FPT", "market": "vn", "quantity": 100, "total_cost": 200.0,
                     "market_value_base": 200.0, "unrealized_pnl_base": 0.0, "last_price": 2.0,
                     "price_stale": True, "price_available": True},
                ],
            },
            {
                "account_id": 2,
                "positions": [
                    {"symbol": "HPG", "market": "vn", "quantity": 20, "total_cost": 80.0,
                     "market_value_base": 100.0, "unrealized_pnl_base": 20.0, "last_price": 5.0,
                     "price_stale": False, "price_available": True},
                ],
            },
        ],
    }


def _signal(code, action, signal_id, reason="lý do"):
    return {"id": signal_id, "stock_code": code, "market": "vn", "action": action,
            "confidence": 0.7, "reason": reason, "status": "active"}


def _risk(**overrides):
    risk = {
        "thresholds": {"concentration_alert_pct": 35.0},
        "concentration": {"alert": True, "top_weight_pct": 75.0, "top_positions": []},
        "drawdown": {"alert": False},
        "stop_loss": {"items": [{"symbol": "FPT", "loss_pct": 12.0, "is_triggered": True}]},
    }
    risk.update(overrides)
    return risk


class PortfolioReviewServiceTestCase(unittest.TestCase):
    def _service(self, *, snapshot=None, risk=None, risk_error=None, signals=None, config=None):
        portfolio_service = MagicMock()
        portfolio_service.get_portfolio_snapshot.return_value = snapshot or _snapshot()
        risk_service = MagicMock()
        if risk_error is not None:
            risk_service.get_risk_report.side_effect = risk_error
        else:
            risk_service.get_risk_report.return_value = risk or _risk()
        signal_service = MagicMock()
        items = signals if signals is not None else []
        signal_service.list_signals.return_value = {"items": items, "total": len(items)}
        return PortfolioReviewService(
            portfolio_service=portfolio_service,
            risk_service=risk_service,
            decision_signal_service=signal_service,
            config=config or SimpleNamespace(report_language="vi", agent_portfolio_agent_timeout_s=0,
                                             agent_orchestrator_timeout_s=600),
        )

    def test_build_context_merges_accounts_and_computes_weights(self):
        ctx, meta = self._service().build_context()

        holdings = {item["code"]: item for item in ctx.data["portfolio_holdings"]}
        self.assertEqual(list(holdings), ["HPG", "FPT"])  # sắp theo giá trị
        self.assertAlmostEqual(holdings["HPG"]["quantity"], 120)
        self.assertAlmostEqual(holdings["HPG"]["current_weight"], 0.6)
        self.assertAlmostEqual(holdings["HPG"]["avg_cost"], 4.0)
        self.assertAlmostEqual(holdings["HPG"]["unrealized_pnl_pct"], 25.0)
        self.assertAlmostEqual(holdings["FPT"]["current_weight"], 0.2)
        self.assertTrue(holdings["FPT"]["price_stale"])
        self.assertAlmostEqual(ctx.data["portfolio_summary"]["cash_ratio"], 0.2)
        self.assertEqual(meta["holding_count"], 2)
        self.assertTrue(meta["risk_available"])
        self.assertEqual(ctx.meta["report_language"], "vi")

    def test_newest_signal_of_any_action_wins(self):
        # list_signals trả mới nhất trước: buy (mới) phải thắng reduce (cũ).
        signals = [_signal("HPG", "buy", 2, "tín hiệu mới"), _signal("HPG", "reduce", 1, "tín hiệu cũ")]
        ctx, meta = self._service(signals=signals).build_context()

        self.assertEqual(ctx.data["stock_opinions"]["HPG"]["signal"], "buy")
        self.assertEqual(ctx.data["stock_opinions"]["HPG"]["summary"], "tín hiệu mới")
        self.assertEqual(meta["without_signal"], ["FPT"])

    def test_risk_failure_degrades_instead_of_failing(self):
        ctx, meta = self._service(risk_error=RuntimeError("boom")).build_context()

        self.assertNotIn("portfolio_risk", ctx.data)
        self.assertFalse(meta["risk_available"])
        self.assertEqual(meta["holding_count"], 2)

    def test_risk_alerts_become_readable_risk_flags(self):
        ctx, _ = self._service().build_context()

        categories = [flag["category"] for flag in ctx.risk_flags]
        self.assertIn("concentration", categories)
        self.assertIn("stop_loss", categories)
        stop = next(flag for flag in ctx.risk_flags if flag["category"] == "stop_loss")
        self.assertIn("FPT", stop["description"])
        self.assertEqual(stop["severity"], "high")

    def test_sector_concentration_reaches_agent_context(self):
        sector = {
            "alert": True,
            "top_weight_pct": 60.0,
            "top_sectors": [{"sector": "Ngân hàng", "weight_pct": 60.0, "symbol_count": 2, "is_alert": True}],
            "coverage": {"classified_count": 2, "unclassified_count": 0, "failed_count": 0},
        }
        ctx, _ = self._service(risk=_risk(sector_concentration=sector)).build_context()

        compact = ctx.data["portfolio_risk"]["sector_concentration"]
        self.assertEqual(compact["top_sectors"][0]["sector"], "Ngân hàng")
        self.assertEqual(compact["coverage"]["classified_count"], 2)
        flag = next(flag for flag in ctx.risk_flags if flag["category"] == "sector_concentration")
        self.assertIn("Ngân hàng", flag["description"])

    def test_unclassified_sector_alert_is_not_flagged(self):
        sector = {
            "alert": True,
            "top_weight_pct": 100.0,
            "top_sectors": [{"sector": "UNCLASSIFIED", "weight_pct": 100.0, "symbol_count": 2, "is_alert": True}],
        }
        ctx, _ = self._service(risk=_risk(sector_concentration=sector)).build_context()

        self.assertNotIn("sector_concentration", [flag["category"] for flag in ctx.risk_flags])

    def test_no_positions_does_not_call_llm(self):
        empty = {**_snapshot(), "accounts": [], "total_equity": 0.0}
        service = self._service(snapshot=empty)
        with patch.object(review_module, "run_portfolio_agent") as run_agent:
            result = service.review()

        run_agent.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "no_positions")

    def test_review_success_normalizes_assessment(self):
        service = self._service(signals=[_signal("HPG", "hold", 1)])

        def fake_run(config, ctx, *, timeout_s):
            self.assertEqual(timeout_s, DEFAULT_REVIEW_TIMEOUT_S)
            ctx.data["portfolio_assessment"] = {
                "portfolio_risk_score": 7,
                "summary": "Tập trung cao vào HPG",
                "positions": [
                    {"code": "HPG", "current_weight": 0.9, "suggested_weight": 1.4, "signal": "reduce", "priority": "high"},
                    {"code": "VNM", "suggested_weight": 0.1, "signal": "buy"},
                ],
                "suggested_cash_ratio": 0.3,
                "rebalance_suggestions": "Giảm HPG",
            }
            return SimpleNamespace(success=True, tokens_used=123), False

        with patch.object(review_module, "run_portfolio_agent", side_effect=fake_run):
            result = service.review()

        self.assertTrue(result.success)
        assessment = result.assessment
        self.assertEqual([p["code"] for p in assessment["positions"]], ["HPG"])  # bỏ mã không nắm giữ
        self.assertAlmostEqual(assessment["positions"][0]["current_weight"], 0.6)  # server thắng
        self.assertEqual(assessment["positions"][0]["suggested_weight"], 1.0)  # clamp
        self.assertEqual(assessment["rebalance_suggestions"], ["Giảm HPG"])
        self.assertAlmostEqual(assessment["cash_ratio"], 0.2)
        self.assertIn("Chưa có tín hiệu AI: FPT", assessment["data_gaps"])
        self.assertEqual(result.tokens_used, 123)

    def test_review_reports_timeout(self):
        service = self._service()
        with patch.object(review_module, "run_portfolio_agent", return_value=(None, True)):
            result = service.review()

        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.error, "timeout")


class TimeoutResolutionTestCase(unittest.TestCase):
    def test_precedence(self):
        explicit = SimpleNamespace(agent_portfolio_agent_timeout_s=30.0, agent_orchestrator_timeout_s=600)
        self.assertEqual(resolve_portfolio_agent_timeout(explicit), 30.0)
        self.assertEqual(resolve_portfolio_agent_timeout(explicit, budget_s=10), 10.0)

        orchestrator_only = SimpleNamespace(agent_portfolio_agent_timeout_s=0, agent_orchestrator_timeout_s=90)
        self.assertEqual(resolve_portfolio_agent_timeout(orchestrator_only), 90.0)

        long_orchestrator = SimpleNamespace(agent_portfolio_agent_timeout_s=0, agent_orchestrator_timeout_s=600)
        self.assertEqual(resolve_portfolio_agent_timeout(long_orchestrator), DEFAULT_REVIEW_TIMEOUT_S)

        self.assertEqual(resolve_portfolio_agent_timeout(SimpleNamespace()), DEFAULT_REVIEW_TIMEOUT_S)

    def test_hard_timeout_returns_quickly(self):
        def slow_run(self, ctx, progress_callback=None, timeout_seconds=None):
            time.sleep(1.0)

        with patch.object(review_module, "_HARD_TIMEOUT_GRACE_S", 0.0), \
                patch("src.agent.factory.get_tool_registry", return_value=MagicMock()), \
                patch("src.agent.llm_adapter.LLMToolAdapter", return_value=MagicMock()), \
                patch("src.agent.agents.portfolio_agent.PortfolioAgent.run", new=slow_run):
            started = time.monotonic()
            result, timed_out = run_portfolio_agent(SimpleNamespace(), MagicMock(), timeout_s=0.05)

        self.assertIsNone(result)
        self.assertTrue(timed_out)
        self.assertLess(time.monotonic() - started, 0.5)


class NormalizeAssessmentTestCase(unittest.TestCase):
    def test_invalid_enums_and_scores_are_sanitized(self):
        holdings = [{"code": "HPG", "current_weight": 0.5}]
        result = normalize_assessment(
            {"portfolio_risk_score": 42, "positions": [{"code": "hpg", "signal": "moon", "priority": "urgent"}]},
            holdings,
            {"cash_ratio": 0.1, "without_signal": []},
        )
        self.assertEqual(result["portfolio_risk_score"], 10.0)
        self.assertIsNone(result["positions"][0]["signal"])
        self.assertIsNone(result["positions"][0]["priority"])
        self.assertIsNone(result["positions"][0]["suggested_weight"])
        self.assertEqual(result["data_gaps"], [])


class PortfolioReviewPersistenceTestCase(unittest.TestCase):
    """Lưu kết quả đánh giá AI vào bảng portfolio_ai_reviews (DB SQLite tạm)."""

    def setUp(self):
        from src.config import Config
        from src.repositories.portfolio_repo import PortfolioRepository
        from src.storage import DatabaseManager

        self._db_cls = DatabaseManager
        self._config_cls = Config
        self.temp_dir = tempfile.TemporaryDirectory()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        self.db = DatabaseManager(db_url=f"sqlite:///{Path(self.temp_dir.name) / 'review.db'}")
        self.repo = PortfolioRepository(db_manager=self.db)

    def tearDown(self):
        self._db_cls.reset_instance()
        self._config_cls.reset_instance()
        self.temp_dir.cleanup()

    def _service(self, *, limit=50, repo=None):
        portfolio_service = MagicMock()
        portfolio_service.get_portfolio_snapshot.return_value = _snapshot()
        risk_service = MagicMock()
        risk_service.get_risk_report.return_value = _risk()
        signal_service = MagicMock()
        signal_service.list_signals.return_value = {"items": [], "total": 0}
        return PortfolioReviewService(
            portfolio_service=portfolio_service,
            risk_service=risk_service,
            decision_signal_service=signal_service,
            config=SimpleNamespace(report_language="vi", agent_portfolio_agent_timeout_s=0,
                                   agent_orchestrator_timeout_s=600, portfolio_ai_review_history_limit=limit),
            repo=repo or self.repo,
        )

    @staticmethod
    def _fake_success(config, ctx, *, timeout_s):
        ctx.data["portfolio_assessment"] = {"portfolio_risk_score": 6, "summary": "ổn",
                                            "positions": [{"code": "HPG", "signal": "hold"}]}
        return SimpleNamespace(success=True, tokens_used=50), False

    def test_success_is_persisted_and_readable(self):
        service = self._service()
        with patch.object(review_module, "run_portfolio_agent", side_effect=self._fake_success):
            result = service.review(account_id=None, cost_method="avg", query="  Nên giảm HPG?  ")

        self.assertIsNotNone(result.review_id)
        self.assertTrue(result.created_at)
        listing = service.list_reviews(account_id=None, cost_method="avg")
        self.assertEqual(listing["total"], 1)
        item = listing["items"][0]
        self.assertEqual(item["id"], result.review_id)
        self.assertEqual(item["risk_score"], 6.0)
        self.assertEqual(item["question"], "Nên giảm HPG?")
        self.assertEqual(item["as_of"], "2026-01-05")
        saved = service.get_review(result.review_id)
        self.assertEqual(saved["assessment"]["summary"], "ổn")
        self.assertEqual(saved["assessment"]["positions"][0]["code"], "HPG")
        self.assertEqual(saved["tokens_used"], 50)
        self.assertEqual(saved["signal_coverage"]["without_signal"], ["HPG", "FPT"])
        # Phạm vi khác (fifo) không thấy bản avg.
        self.assertEqual(service.list_reviews(account_id=None, cost_method="fifo")["total"], 0)

    def test_failures_are_not_persisted(self):
        service = self._service()
        with patch.object(review_module, "run_portfolio_agent", return_value=(None, True)):
            self.assertIsNone(service.review().review_id)
        failed = SimpleNamespace(success=False, error="llm_error", tokens_used=0, failure_reason=None)
        with patch.object(review_module, "run_portfolio_agent", return_value=(failed, False)):
            self.assertIsNone(service.review().review_id)
        self.assertEqual(service.list_reviews(account_id=None)["total"], 0)

    def test_storage_error_is_fail_open(self):
        broken = MagicMock()
        broken.add_ai_review.side_effect = RuntimeError("database is locked")
        service = self._service(repo=broken)
        with patch.object(review_module, "run_portfolio_agent", side_effect=self._fake_success):
            result = service.review()

        self.assertTrue(result.success)
        self.assertIsNone(result.review_id)
        self.assertEqual(result.assessment["portfolio_risk_score"], 6.0)

    def test_history_is_pruned_per_scope(self):
        account_id = self.repo.create_account(name="A", broker="Demo", market="vn", base_currency="VND").id
        service = self._service(limit=2)
        ids = []
        with patch.object(review_module, "run_portfolio_agent", side_effect=self._fake_success):
            other = service.review(account_id=account_id).review_id
            for _ in range(3):
                ids.append(service.review(account_id=None).review_id)

        listing = service.list_reviews(account_id=None)
        self.assertEqual(listing["total"], 2)
        self.assertEqual([item["id"] for item in listing["items"]], [ids[2], ids[1]])
        self.assertIsNone(service.get_review(ids[0]))
        # Phạm vi tài khoản riêng không bị xoá khi tỉa phạm vi tất cả tài khoản.
        self.assertEqual([item["id"] for item in service.list_reviews(account_id=account_id)["items"]], [other])

    def test_delete_review(self):
        service = self._service()
        with patch.object(review_module, "run_portfolio_agent", side_effect=self._fake_success):
            review_id = service.review().review_id

        self.assertTrue(service.delete_review(review_id))
        self.assertFalse(service.delete_review(review_id))
        self.assertIsNone(service.get_review(review_id))


if __name__ == "__main__":
    unittest.main()
