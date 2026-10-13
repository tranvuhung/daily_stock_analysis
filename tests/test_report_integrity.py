# -*- coding: utf-8 -*-
"""
===================================
Report Engine - Content integrity tests
===================================

Tests for check_content_integrity, apply_placeholder_fill, and retry/placeholder behavior.
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.analyzer import AnalysisResult, GeminiAnalyzer, check_content_integrity, apply_placeholder_fill


class TestCheckContentIntegrity(unittest.TestCase):
    """Content integrity check tests."""

    def test_pass_when_all_required_present(self) -> None:
        """Integrity passes when all mandatory fields are present."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110 VND"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_pass_when_signal_attribution_missing(self) -> None:
        """Signal attribution is optional and does not enter missing_fields."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110 VND"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_fail_when_analysis_summary_empty(self) -> None:
        """Integrity fails when analysis_summary is empty."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("analysis_summary", missing)

    def test_fail_when_one_sentence_missing(self) -> None:
        """Integrity fails when core_conclusion.one_sentence is missing."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("dashboard.core_conclusion.one_sentence", missing)

    def test_fail_when_one_sentence_blank(self) -> None:
        """Integrity fails when one_sentence is blank whitespace."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "   "},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("dashboard.core_conclusion.one_sentence", missing)

    def test_fail_when_stop_loss_missing_for_buy(self) -> None:
        """Integrity fails when stop_loss missing and decision_type is buy."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Mua",
            analysis_summary="Ổn định",
            decision_type="buy",
            dashboard={
                "core_conclusion": {"one_sentence": "Có thể mua"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("dashboard.battle_plan.sniper_points.stop_loss", missing)

    def test_pass_when_stop_loss_missing_for_sell(self) -> None:
        """Integrity passes when stop_loss missing and decision_type is sell."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Giảm",
            sentiment_score=35,
            operation_advice="Bán",
            analysis_summary="Yếu",
            decision_type="sell",
            dashboard={
                "core_conclusion": {"one_sentence": "Nên bán"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_fail_when_risk_alerts_missing(self) -> None:
        """Integrity fails when intelligence.risk_alerts field is missing."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ"},
                "intelligence": {},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("dashboard.intelligence.risk_alerts", missing)

    def test_phase_decision_missing_only_when_required(self) -> None:
        """Phase decision fields are required only for phase-aware analysis."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )

        ok, missing = check_content_integrity(result)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

        ok, missing = check_content_integrity(result, require_phase_decision=True)
        self.assertFalse(ok)
        self.assertIn("dashboard.phase_decision.phase_context", missing)
        self.assertIn("dashboard.phase_decision.watch_conditions", missing)
        self.assertIn("dashboard.phase_decision.data_limitations", missing)

    def test_fail_when_risk_alerts_is_none(self) -> None:
        """Integrity fails when risk_alerts is None."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ"},
                "intelligence": {"risk_alerts": None},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("dashboard.intelligence.risk_alerts", missing)

    def test_fail_when_risk_alerts_is_invalid_type(self) -> None:
        """Integrity fails when risk_alerts is not list."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ"},
                "intelligence": {"risk_alerts": "Cần lưu ý"},
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("dashboard.intelligence.risk_alerts", missing)

    def test_fail_when_stop_loss_is_blank(self) -> None:
        """Integrity fails when stop_loss is blank whitespace."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Mua",
            analysis_summary="Ổn định",
            decision_type="buy",
            dashboard={
                "core_conclusion": {"one_sentence": "Có thể mua"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "   "}},
            },
        )
        ok, missing = check_content_integrity(result)
        self.assertFalse(ok)
        self.assertIn("dashboard.battle_plan.sniper_points.stop_loss", missing)


class TestApplyPlaceholderFill(unittest.TestCase):
    """Placeholder fill tests."""

    def test_fills_missing_analysis_summary(self) -> None:
        """Placeholder fills analysis_summary when missing."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="",
            decision_type="hold",
            dashboard={},
        )
        apply_placeholder_fill(result, ["analysis_summary"])
        self.assertEqual(result.analysis_summary, "Chưa có")

    def test_fills_missing_analysis_summary_in_english(self) -> None:
        """English report should use English placeholder text for missing analysis_summary."""
        result = AnalysisResult(
            code="HPG",
            name="MacaoTech",
            report_language="en",
            trend_prediction="Bullish",
            sentiment_score=70,
            operation_advice="Buy",
            analysis_summary="",
            decision_type="buy",
            dashboard={},
        )
        apply_placeholder_fill(result, ["analysis_summary"])
        self.assertEqual(result.analysis_summary, "TBD")

    def test_fills_missing_stop_loss(self) -> None:
        """Placeholder fills stop_loss when missing."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Mua",
            analysis_summary="Ổn định",
            decision_type="buy",
            dashboard={"battle_plan": {"sniper_points": {}}},
        )
        apply_placeholder_fill(result, ["dashboard.battle_plan.sniper_points.stop_loss"])
        self.assertEqual(
            result.dashboard["battle_plan"]["sniper_points"]["stop_loss"],
            "Chưa có",
        )

    def test_fills_risk_alerts_empty_list(self) -> None:
        """Placeholder fills risk_alerts with empty list when missing."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={"intelligence": {}},
        )
        apply_placeholder_fill(result, ["dashboard.intelligence.risk_alerts"])
        self.assertEqual(result.dashboard["intelligence"]["risk_alerts"], [])

    def test_fills_risk_alerts_when_none(self) -> None:
        """Placeholder fills risk_alerts when value is None."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            risk_warning="注意融资",
            dashboard={"intelligence": {"risk_alerts": None}},
        )
        apply_placeholder_fill(result, ["dashboard.intelligence.risk_alerts"])
        self.assertEqual(result.dashboard["intelligence"]["risk_alerts"], ["注意融资"])

    def test_fills_risk_alerts_when_invalid_type(self) -> None:
        """Placeholder fills risk_alerts when value is non-list."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            dashboard={"intelligence": {"risk_alerts": "注意回撤"}},
        )
        apply_placeholder_fill(result, ["dashboard.intelligence.risk_alerts"])
        self.assertEqual(result.dashboard["intelligence"]["risk_alerts"], [])

    def test_fills_risk_alerts_when_risk_warning_is_list(self) -> None:
        """Placeholder handles list risk_warning and flattens valid text values."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            risk_warning=["回撤风险", "波动加大"],
            dashboard={"intelligence": {"risk_alerts": ""}},
        )
        apply_placeholder_fill(result, ["dashboard.intelligence.risk_alerts"])
        self.assertEqual(result.dashboard["intelligence"]["risk_alerts"], ["回撤风险", "波动加大"])

    def test_fills_risk_alerts_when_risk_warning_is_dict(self) -> None:
        """Placeholder serializes dict risk_warning into a string risk alert."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Nắm giữ",
            analysis_summary="Ổn định",
            decision_type="hold",
            risk_warning={"note": "技术面偏弱"},
            dashboard={"intelligence": {"risk_alerts": ""}},
        )
        apply_placeholder_fill(result, ["dashboard.intelligence.risk_alerts"])
        self.assertEqual(
            json.loads(result.dashboard["intelligence"]["risk_alerts"][0]),
            {"note": "技术面偏弱"},
        )

    def test_fills_stop_loss_when_blank(self) -> None:
        """Placeholder fills stop_loss when blank whitespace."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Mua",
            analysis_summary="Ổn định",
            decision_type="buy",
            dashboard={"battle_plan": {"sniper_points": {"stop_loss": "   "}}},
        )
        apply_placeholder_fill(result, ["dashboard.battle_plan.sniper_points.stop_loss"])
        self.assertEqual(
            result.dashboard["battle_plan"]["sniper_points"]["stop_loss"],
            "Chưa có",
        )

    def test_fills_stop_loss_when_invalid_type(self) -> None:
        """Placeholder fills stop_loss when value is invalid type."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Mua",
            analysis_summary="Ổn định",
            decision_type="buy",
            dashboard={"battle_plan": {"sniper_points": {"stop_loss": {}}}},
        )
        apply_placeholder_fill(result, ["dashboard.battle_plan.sniper_points.stop_loss"])
        self.assertEqual(
            result.dashboard["battle_plan"]["sniper_points"]["stop_loss"],
            "Chưa có",
        )

    def test_fills_none_dashboard_blocks_from_existing_context(self) -> None:
        """Placeholder fill handles null dashboard blocks and reuses existing result text."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="Tăng",
            sentiment_score=70,
            operation_advice="Mua",
            analysis_summary="已有趋势摘要",
            risk_warning="跌破支撑需减仓",
            decision_type="buy",
            dashboard={
                "core_conclusion": None,
                "intelligence": None,
                "battle_plan": None,
            },
        )

        apply_placeholder_fill(
            result,
            [
                "dashboard.core_conclusion.one_sentence",
                "dashboard.intelligence.risk_alerts",
                "dashboard.battle_plan.sniper_points.stop_loss",
            ],
        )

        self.assertEqual(result.dashboard["core_conclusion"]["one_sentence"], "已有趋势摘要")
        self.assertEqual(result.dashboard["intelligence"]["risk_alerts"], ["跌破支撑需减仓"])
        self.assertEqual(result.dashboard["battle_plan"]["sniper_points"]["stop_loss"], "Chưa có")

    def test_phase_decision_placeholder_fill_satisfies_integrity_contract(self) -> None:
        """Phase placeholders close the retry-exhausted integrity contract without fake conditions."""
        result = AnalysisResult(
            code="HPG",
            name="Hòa Phát",
            trend_prediction="震荡",
            sentiment_score=50,
            operation_advice="Nắm giữ",
            analysis_summary="已有摘要",
            decision_type="hold",
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "100"}},
                "phase_decision": {
                    "phase_context": "invalid",
                    "watch_conditions": "invalid",
                    "data_limitations": None,
                },
            },
        )

        ok, missing = check_content_integrity(result, require_phase_decision=True)
        self.assertFalse(ok)

        apply_placeholder_fill(result, missing)

        ok, missing = check_content_integrity(result, require_phase_decision=True)
        self.assertTrue(ok)
        self.assertEqual(missing, [])
        phase_decision = result.dashboard["phase_decision"]
        self.assertEqual(phase_decision["phase_context"], {})
        self.assertEqual(phase_decision["watch_conditions"], [])
        self.assertEqual(phase_decision["data_limitations"], [])
        self.assertEqual(phase_decision["action_window"], "Model did not provide a phase action window")
        self.assertEqual(phase_decision["immediate_action"], "Model did not provide a phase-aware immediate action")
        self.assertEqual(phase_decision["next_check_time"], "Model did not provide a next check point")
        self.assertEqual(phase_decision["confidence_reason"], "Model did not provide a phase confidence rationale")


class TestIntegrityRetryPrompt(unittest.TestCase):
    """Retry prompt construction tests."""

    def test_retry_prompt_includes_previous_response(self) -> None:
        """Retry prompt should carry previous response so补全是增量的。"""
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()
        prompt = analyzer._build_integrity_retry_prompt(
            "原始提示",
            '{"analysis_summary": "已有内容"}',
            ["dashboard.core_conclusion.one_sentence"],
        )
        self.assertIn("原始提示", prompt)
        self.assertIn('{"analysis_summary": "已有内容"}', prompt)
        self.assertIn("dashboard.core_conclusion.one_sentence", prompt)
