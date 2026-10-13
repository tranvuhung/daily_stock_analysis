# -*- coding: utf-8 -*-
"""
===================================
Report Engine - Report renderer tests
===================================

Tests for Jinja2 report rendering and fallback behavior.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.analyzer import AnalysisResult
from src.services.report_renderer import render


def _make_result(
    code: str = "HPG",
    name: str = "Hòa Phát",
    sentiment_score: int = 72,
    operation_advice: str = "Nắm giữ",
    analysis_summary: str = "Ổn định",
    decision_type: str = "hold",
    dashboard: dict = None,
    report_language: str = "vi",
    model_used: str = None,
) -> AnalysisResult:
    if dashboard is None:
        dashboard = {
            "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
            "intelligence": {"risk_alerts": []},
            "battle_plan": {"sniper_points": {"stop_loss": "110"}},
        }
    return AnalysisResult(
        code=code,
        name=name,
        trend_prediction="Tăng",
        sentiment_score=sentiment_score,
        operation_advice=operation_advice,
        analysis_summary=analysis_summary,
        decision_type=decision_type,
        dashboard=dashboard,
        report_language=report_language,
        model_used=model_used,
    )


def _make_renderer_config(show_llm_model: bool = True) -> MagicMock:
    config = MagicMock()
    config.report_templates_dir = "templates"
    config.report_language = "vi"
    config.report_show_llm_model = show_llm_model
    return config


def _with_decision_signal_summary(result: AnalysisResult) -> AnalysisResult:
    result.decision_signal_summary = {
        "action": "sell",
        "action_label": "Bán",
        "horizon": "1d",
        "reason": "Kỹ thuật suy yếu",
    }
    return result


class TestReportRenderer(unittest.TestCase):
    """Report renderer tests."""

    def test_render_markdown_summary_only(self) -> None:
        """Markdown platform renders with summary_only."""
        r = _make_result()
        out = render("markdown", [r], summary_only=True)
        self.assertIsNotNone(out)
        self.assertIn("Bảng điều khiển quyết định", out)
        self.assertIn("Hòa Phát", out)
        self.assertIn("Mua", out)
        self.assertIn("🟢Mua:1", out)

    def test_render_markdown_preserves_guardrailed_neutral_action(self) -> None:
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "Chờ xác nhận"},
                "decision_stability": {"applied": True, "reason": "Chờ xác nhận điều chỉnh về hỗ trợ"},
            }
        )

        out = render("markdown", [r], summary_only=True)

        self.assertIsNotNone(out)
        self.assertIn("Nắm giữ", out)
        self.assertIn("🟡Quan sát:1", out)

    def test_render_markdown_uses_explicit_avoid_and_alert_text(self) -> None:
        avoid = _make_result(
            code="AVOID",
            name="Avoid Corp",
            sentiment_score=90,
            operation_advice="Buy",
            report_language="en",
        )
        avoid.action = "avoid"
        avoid.action_label = "Avoid"
        alert = _make_result(
            code="ALERT",
            name="Alert Corp",
            sentiment_score=85,
            operation_advice="Buy",
            report_language="en",
        )
        alert.action = "alert"
        alert.action_label = "Alert"

        out = render("markdown", [avoid, alert], summary_only=True)

        self.assertIsNotNone(out)
        self.assertIn("🟡 **Avoid Corp(AVOID)**: Avoid | Score 90", out)
        self.assertIn("🔴 **Alert Corp(ALERT)**: Alert | Score 85", out)
        self.assertIn("**Avoid Corp(AVOID)**: Avoid | Score 90", out)
        self.assertIn("**Alert Corp(ALERT)**: Alert | Score 85", out)
        self.assertNotIn("**Avoid Corp(AVOID)**: Buy", out)
        self.assertNotIn("**Alert Corp(ALERT)**: Buy", out)

    def test_render_markdown_full(self) -> None:
        """Markdown platform renders full report."""
        r = _make_result()
        out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(out)
        self.assertIn("Kết luận cốt lõi", out)
        self.assertIn("Kế hoạch giao dịch", out)
        self.assertNotIn("Hàng rào quyết định theo phiên", out)

    def test_render_markdown_omits_decision_signal_excerpt(self) -> None:
        """Markdown reports omit the duplicated DecisionSignal excerpt."""
        r = _with_decision_signal_summary(_make_result())

        summary_out = render("markdown", [r], summary_only=True)
        self.assertIsNotNone(summary_out)
        self.assertNotIn("Kỹ thuật suy yếu", summary_out)

        full_out = render("markdown", [r], summary_only=False)
        self.assertIsNotNone(full_out)
        self.assertNotIn("Kỹ thuật suy yếu", full_out)

    def test_render_markdown_phase_decision_section(self) -> None:
        """Markdown renders phase_decision when present."""
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "Chờ xác nhận"},
                "intelligence": {"risk_alerts": []},
                "phase_decision": {
                    "action_window": "Theo dõi trong phiên",
                    "immediate_action": "Chờ xác nhận",
                    "watch_conditions": ["Bứt phá kèm khối lượng"],
                    "next_check_time": "14:30",
                    "confidence_reason": "Chất lượng dữ liệu dùng được",
                    "data_limitations": ["quote: stale"],
                },
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            }
        )

        out = render("markdown", [r], summary_only=False)

        self.assertIsNotNone(out)
        self.assertIn("Hàng rào quyết định theo phiên", out)
        self.assertIn("Theo dõi trong phiên", out)
        self.assertIn("Bứt phá kèm khối lượng", out)
        self.assertIn("quote: stale", out)

    def test_render_markdown_skips_context_only_phase_decision_shape(self) -> None:
        """Markdown skips mechanically shaped phase_decision without actionable content."""
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
                "intelligence": {"risk_alerts": []},
                "phase_decision": {
                    "phase_context": {"phase": "intraday", "market": "vn"},
                    "action_window": None,
                    "immediate_action": None,
                    "watch_conditions": [],
                    "next_check_time": None,
                    "confidence_reason": None,
                    "data_limitations": [],
                },
                "battle_plan": {"sniper_points": {"stop_loss": "110"}},
            }
        )

        out = render("markdown", [r], summary_only=False)

        self.assertIsNotNone(out)
        self.assertNotIn("Hàng rào quyết định theo phiên", out)

    def test_render_brief(self) -> None:
        """Brief platform renders 3-5 sentence summary."""
        r = _make_result()
        out = render("brief", [r])
        self.assertIsNotNone(out)
        self.assertIn("Bản tin quyết định", out)
        self.assertIn("Hòa Phát", out)

    def test_render_brief_omits_decision_signal_excerpt(self) -> None:
        r = _with_decision_signal_summary(_make_result())

        out = render("brief", [r])

        self.assertIsNotNone(out)
        self.assertNotIn("Kỹ thuật suy yếu", out)

    def test_render_brief_respects_model_visibility_toggle(self) -> None:
        r = _make_result(model_used="gemini/gemini-2.5-flash")

        with patch("src.services.report_renderer.get_config", return_value=_make_renderer_config(True)):
            visible = render("brief", [r])
        with patch("src.services.report_renderer.get_config", return_value=_make_renderer_config(False)):
            hidden = render("brief", [r])

        self.assertIsNotNone(visible)
        self.assertIsNotNone(hidden)
        self.assertIn("Mô hình phân tích: gemini/gemini-2.5-flash", visible)
        self.assertNotIn("Mô hình phân tích", hidden)
        self.assertNotIn("gemini/gemini-2.5-flash", hidden)

    def test_render_templates_show_compact_market_status_only(self) -> None:
        r = _make_result()
        r.market_phase_summary = {
            "phase": "intraday",
            "market": "vn",
            "trigger_source": "api",
            "is_partial_bar": True,
        }
        r.analysis_context_pack_overview = {
            "data_quality": {
                "level": "limited",
                "limitations": ["quote: stale", "news: missing", "technical: fallback"],
            }
        }
        r.raw_response = "raw context pack should not appear"

        out = render("brief", [r])

        self.assertIsNotNone(out)
        self.assertIn("Trạng thái thị trường: Việt Nam · Trong phiên", out)
        self.assertNotIn("intraday", out)
        self.assertNotIn("limited", out)
        self.assertNotIn("quote: stale", out)
        self.assertNotIn("news: missing", out)
        self.assertNotIn("technical: fallback", out)
        self.assertNotIn("raw context pack", out)

    def test_render_templates_skip_phase_pack_excerpt_when_summary_missing(self) -> None:
        r = _make_result()

        out = render("brief", [r])

        self.assertIsNotNone(out)
        self.assertNotIn("summary source", out)
        self.assertNotIn("evaluator snapshot", out)

    def test_render_market_status_uses_first_result_phase(self) -> None:
        # Chỉ hiển thị một dòng trạng thái thị trường, lấy theo kết quả đầu tiên.
        # A single market-status line is rendered, taken from the first result.
        first = _make_result(code="HPG", name="Hòa Phát", sentiment_score=60)
        first.market_phase_summary = {"market": "vn", "phase": "postmarket"}
        second = _make_result(code="FPT", name="FPT Corp", sentiment_score=90)
        second.market_phase_summary = {"market": "vn", "phase": "premarket"}

        out = render("markdown", [first, second], summary_only=True)

        self.assertIsNotNone(out)
        self.assertIn("Trạng thái thị trường: Việt Nam · Sau phiên", out)
        self.assertEqual(out.count("Trạng thái thị trường"), 1)

    def test_render_markdown_footer_uses_consistent_separator(self) -> None:
        r = _make_result(model_used="gemini/gemini-2.5-flash")

        with patch("src.services.report_renderer.get_config", return_value=_make_renderer_config(True)):
            out = render("markdown", [r], summary_only=True)

        self.assertIsNotNone(out)
        # Tiếng Việt dùng dấu hai chấm nửa độ rộng + khoảng trắng.
        # Vietnamese uses a half-width colon followed by a space.
        self.assertIn("Thời điểm tạo báo cáo: ", out)
        self.assertIn("Mô hình phân tích: gemini/gemini-2.5-flash", out)
        self.assertNotIn("：", out)

    def test_render_markdown_in_english(self) -> None:
        """Markdown renderer switches headings and summary labels for English reports."""
        r = _make_result(
            name="Hoa Phat Group",
            operation_advice="Buy",
            analysis_summary="Momentum remains constructive.",
            report_language="en",
        )
        out = render("markdown", [r], summary_only=True)
        self.assertIsNotNone(out)
        self.assertIn("Decision Dashboard", out)
        self.assertIn("Summary", out)
        self.assertIn("Buy", out)

    def test_render_markdown_market_snapshot_uses_template_context(self) -> None:
        """Market snapshot macro should render localized labels with template context."""
        r = _make_result(
            code="FPT",
            name="FPT Corp",
            operation_advice="Buy",
            report_language="en",
        )
        r.market_snapshot = {
            "close": "180.10",
            "prev_close": "178.25",
            "open": "179.00",
            "high": "181.20",
            "low": "177.80",
            "pct_chg": "+1.04%",
            "change_amount": "1.85",
            "amplitude": "1.91%",
            "volume": "1200000",
            "amount": "215000000",
            "price": "180.35",
            "volume_ratio": "1.2",
            "turnover_rate": "0.8%",
            "source": "vci",
        }

        out = render("markdown", [r], summary_only=False)

        self.assertIsNotNone(out)
        self.assertIn("Market Snapshot", out)
        self.assertIn("Volume Ratio", out)

    def test_render_markdown_renders_strategy_synthesis_with_localized_labels(self) -> None:
        r = _make_result(
            dashboard={
                "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
                "strategy_synthesis": {
                    "final_signal": "buy",
                    "confidence": 0.8,
                    "conflict_count": 1,
                    "conflict_severity": "medium",
                    "consensus_level": "medium",
                    "summary_key": "strategy_synthesis.with_conflicts",
                    "summary_params": {
                        "opinion_count": 2,
                        "final_signal": "buy",
                        "consensus_level": "medium",
                        "conflict_severity": "medium",
                        "conflict_count": 1,
                    },
                    "supporting_skills": [{"skill_id": "bull_trend", "signal": "buy", "confidence": 0.8}],
                    "opposing_skills": [{"skill_id": "box_oscillation", "signal": "sell", "confidence": 0.75}],
                    "conflicts": [
                        {
                            "conflict_type": "directional_opposition",
                            "severity": "medium",
                            "description_key": "strategy_conflict.directional_opposition",
                            "participants": ["bull_trend", "box_oscillation"],
                        }
                    ],
                },
            }
        )

        out = render("markdown", [r], summary_only=False)

        self.assertIsNotNone(out)
        self.assertIn("Tổng hợp chiến lược", out)
        self.assertIn("Tín hiệu tổng hợp: Mua", out)
        self.assertIn("Xu hướng tăng mặc định/Mua/80%", out)
        self.assertIn("Dao động trong hộp/Bán/75%", out)
        self.assertNotIn("bull_trend/Mua", out)

    def test_render_templates_handle_legacy_strategy_synthesis_shapes(self) -> None:
        for platform in ("markdown",):
            for malformed in ("bad-shape", ["bad-shape"], 42, True):
                result = _make_result(
                    dashboard={
                        "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
                        "intelligence": {},
                        "battle_plan": {},
                        "strategy_synthesis": malformed,
                    }
                )

                out = render(platform, [result], summary_only=False)

                self.assertIsNotNone(out)
                self.assertNotIn("Tổng hợp chiến lược", out)

            result = _make_result(
                dashboard={
                    "core_conclusion": {"one_sentence": "Nắm giữ và quan sát"},
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
                }
            )

            out = render(platform, [result], summary_only=False)

            self.assertIsNotNone(out)
            self.assertIn("Tổng hợp chiến lược", out)
            self.assertIn("Có 3 chiến lược khác không tạo được tín hiệu hợp lệ", out)

    def test_render_unknown_platform_returns_none(self) -> None:
        """Unknown platform returns None (caller fallback)."""
        r = _make_result()
        out = render("unknown_platform", [r])
        self.assertIsNone(out)

    def test_render_empty_results_returns_content(self) -> None:
        """Empty results still produces header."""
        out = render("markdown", [], summary_only=True)
        self.assertIsNotNone(out)
        self.assertIn("0", out)
