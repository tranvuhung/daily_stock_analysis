# -*- coding: utf-8 -*-
"""Tests for analyzer news prompt hard constraints (Issue #697)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from tests.litellm_stub import ensure_litellm_stub

    ensure_litellm_stub()

from src.analyzer import (
    GeminiAnalyzer,
    _BULLISH_TREND_HINTS,
    _contains_trend_hint,
    _infer_trend_direction,
    _sanitize_trend_analysis_for_prompt,
)


class AnalyzerNewsPromptTestCase(unittest.TestCase):
    def test_contains_trend_hint_treats_non_adjacent_negation_as_negated(self) -> None:
        self.assertFalse(_contains_trend_hint("Chưa hình thành xu hướng tăng, tiếp tục quan sát.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("Vẫn chưa hình thành xu hướng tăng, tiếp tục quan sát.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("Hoàn toàn không hình thành xu hướng tăng, tiếp tục quan sát.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("Không có MA xếp tăng, tiếp tục quan sát.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("Hiện không có MA xếp tăng, vẫn cần quan sát.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("Chưa thuộc xu hướng tăng, nhịp hồi vẫn chờ xác nhận.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("Hiện không phải MA xếp tăng, vẫn cần quan sát.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("This is not a bullish trend yet.", _BULLISH_TREND_HINTS))

    def test_contains_trend_hint_scans_later_non_negated_occurrences(self) -> None:
        self.assertTrue(
            _contains_trend_hint(
                "Không phải MA xếp tăng, sau khi khối lượng tăng lại xuất hiện tín hiệu MA xếp tăng.",
                _BULLISH_TREND_HINTS,
            )
        )

    def test_contains_trend_hint_keeps_contrast_clause_target_hint(self) -> None:
        self.assertTrue(_contains_trend_hint("Không phải giảm mà là MA xếp tăng, xu hướng phục hồi.", _BULLISH_TREND_HINTS))
        self.assertFalse(_contains_trend_hint("Chưa chuyển sang xu hướng tăng, nhịp hồi vẫn chờ xác nhận.", _BULLISH_TREND_HINTS))

    def test_contains_trend_hint_ignores_negation_words_used_in_non_negating_phrases(self) -> None:
        self.assertTrue(_contains_trend_hint("Không chỉ MA xếp tăng mà xu hướng còn tiếp diễn.", _BULLISH_TREND_HINTS))
        self.assertTrue(_contains_trend_hint("Chưa kể xu hướng tăng sẽ được xác nhận thêm nếu khối lượng tăng.", _BULLISH_TREND_HINTS))
        self.assertEqual(
            _infer_trend_direction({"trend_status": "Không chỉ MA xếp tăng", "ma_alignment": "Chưa kể xu hướng tăng dần rõ ràng"}),
            "bullish",
        )

    def test_infer_trend_direction_recognizes_weak_bullish_and_bearish_states(self) -> None:
        self.assertEqual(
            _infer_trend_direction({"trend_status": "Tăng yếu", "ma_alignment": "Tăng yếu, MA5>MA10 nhưng MA10≤MA20"}),
            "bullish",
        )
        self.assertEqual(
            _infer_trend_direction({"trend_status": "Giảm yếu", "ma_alignment": "Giảm yếu, MA5<MA10 nhưng MA10≥MA20"}),
            "bearish",
        )

    def test_infer_trend_direction_ignores_negated_bullish_hints(self) -> None:
        self.assertEqual(
            _infer_trend_direction({"trend_status": "Chưa hình thành xu hướng tăng", "ma_alignment": "Hiện không phải MA xếp tăng"}),
            "neutral",
        )
        self.assertEqual(
            _infer_trend_direction({"trend_status": "Không có MA xếp tăng", "ma_alignment": "Hiện không có xu hướng tăng"}),
            "neutral",
        )

    def test_infer_trend_direction_keeps_contrast_clause_final_direction(self) -> None:
        self.assertEqual(
            _infer_trend_direction({"trend_status": "Không phải giảm mà là MA xếp tăng", "ma_alignment": ""}),
            "bullish",
        )

    def test_analysis_prompt_resolves_shared_skill_prompt_state_by_default(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        fake_state = SimpleNamespace(
            skill_instructions="### 技能 1: 波段低吸\n- 关注支撑确认",
            default_skill_policy="",
        )
        with patch("src.agent.factory.resolve_skill_prompt_state", return_value=fake_state):
            prompt = analyzer._get_analysis_system_prompt("zh", stock_code="HPG")

        self.assertIn("### 技能 1: 波段低吸", prompt)
        self.assertNotIn("trend-trading", prompt)

    def test_analysis_prompt_uses_injected_skill_sections_instead_of_hardcoded_trend_baseline(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 缠论\n- 关注中枢与背驰",
                default_skill_policy="",
            )

        prompt = analyzer._get_analysis_system_prompt("zh", stock_code="HPG")

        self.assertIn("### 技能 1: 缠论", prompt)
        self.assertNotIn("trend-trading", prompt)
        self.assertNotIn("Bullish alignment: MA5 > MA10 > MA20", prompt)

    def test_analysis_prompt_keeps_injected_default_policy_for_implicit_default_run(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 默认多头趋势",
                default_skill_policy="## 默认技能基线（必须严格遵守）\n- **多头排列必须条件**：MA5 > MA10 > MA20",
                use_legacy_default_prompt=True,
            )

        prompt = analyzer._get_analysis_system_prompt("zh", stock_code="HPG")

        self.assertIn("trend-trading", prompt)
        # LEGACY_DEFAULT_SYSTEM_PROMPT KHÔNG có chỗ cắm `{default_skill_policy_section}`
        # (chỉ SYSTEM_PROMPT mới có), nên chính sách truyền vào bị bỏ qua ở chế độ legacy;
        # cái xuất hiện trong prompt là baseline dựng sẵn CORE_TRADING_SKILL_POLICY_EN.
        # Trước đây assert này khớp nhờ trùng chữ với bản _ZH, nên không kiểm được gì.
        # LEGACY_DEFAULT_SYSTEM_PROMPT has no `{default_skill_policy_section}` slot, so the
        # injected policy is dropped in legacy mode; what lands in the prompt is the built-in
        # CORE_TRADING_SKILL_POLICY_EN baseline. The old assertion only matched by coincidence.
        self.assertIn("Bullish alignment requires", prompt)
        self.assertIn("Bullish alignment: MA5 > MA10 > MA20", prompt)

    def test_analysis_prompt_requires_phase_decision_in_main_and_legacy_modes(self) -> None:
        for legacy in (False, True):
            with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
                analyzer = GeminiAnalyzer(
                    skill_instructions="",
                    default_skill_policy="",
                    use_legacy_default_prompt=legacy,
                )

            prompt = analyzer._get_analysis_system_prompt("zh", stock_code="HPG")

            self.assertIn('"phase_decision"', prompt)
            self.assertIn('"watch_conditions"', prompt)
            self.assertIn('"data_limitations"', prompt)
            self.assertIn("quote/daily_bars/technical are stale, fallback, missing, fetch_failed, partial, or estimated", prompt)
            self.assertIn("`confidence_level` must not be High", prompt)

    def test_analysis_prompt_contains_actionability_guardrails(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        prompt = analyzer._get_analysis_system_prompt("zh", stock_code="002812")

        self.assertIn("Actionability and Stability Constraints", prompt)
        self.assertIn("Do not swing violently between", prompt)
        self.assertIn("support/resistance", prompt)
        self.assertIn("Shakeout watch", prompt)

    def test_analysis_prompt_score_scale_splits_reduce_and_sell_bands(self) -> None:
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
                    analyzer = GeminiAnalyzer(
                        skill_instructions="",
                        default_skill_policy="",
                        use_legacy_default_prompt=legacy,
                    )

                prompt = analyzer._get_analysis_system_prompt("zh", stock_code="HPG")

                self.assertIn("### Reduce (20-39)", prompt)
                self.assertIn("### Sell (0-19)", prompt)
                self.assertIn("20-39: Reduce, `action=reduce`, `decision_type=sell`.", prompt)
                self.assertIn("0-19: Sell, `action=sell`, `decision_type=sell`.", prompt)
                self.assertNotIn("### Sell / Reduce (0-39)", prompt)

    def test_prompt_contains_time_constraints(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-03-16",
            "today": {},
            "fundamental_context": {
                "earnings": {
                    "data": {
                        "financial_report": {"report_date": "2025-12-31", "revenue": 1000},
                        "dividend": {"ttm_cash_dividend_per_share": 1.2, "ttm_dividend_yield_pct": 2.4},
                    }
                }
            },
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="medium",  # 7 days
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "Hòa Phát", news_context="news")

        self.assertIn("from the last 7 days", prompt)
        self.assertIn("must carry a concrete date (YYYY-MM-DD)", prompt)
        self.assertIn("Ignore any news outside the last 7 days", prompt)
        self.assertIn("Ignore any news whose publication date cannot be determined", prompt)
        self.assertIn("Financials and Dividends (value-investing lens)", prompt)
        self.assertIn("Never make it up", prompt)

    def test_prompt_prefers_context_news_window_days(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-03-16",
            "today": {},
            "news_window_days": 1,
        }
        fake_cfg = SimpleNamespace(
            news_max_age_days=30,
            news_strategy_profile="long",  # 30 days if fallback is used
        )
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            prompt = analyzer._format_prompt(context, "Hòa Phát", news_context="news")

        self.assertIn("from the last 1 days", prompt)
        self.assertIn("Ignore any news outside the last 1 days", prompt)

    def test_format_prompt_injects_market_phase_and_pack_summary_before_technical_data(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-03-27",
            "today": {},
            "market_phase_context": {
                "market": "vn",
                "phase": "premarket",
                "market_local_time": "2026-03-27T09:00:00+07:00",
                "effective_daily_bar_date": "2026-03-26",
                "is_partial_bar": False,
                "minutes_to_open": 30,
                "warnings": [],
            },
        }

        prompt = analyzer._format_prompt(
            context,
            "Hòa Phát",
            news_context=None,
            analysis_context_pack_summary="\n## Analysis Context Pack Summary\n- Block status: quote available\n",
        )

        phase_index = prompt.index("Market Phase Context")
        pack_index = prompt.index("Analysis Context Pack Summary")
        technical_index = prompt.index("Technical Data")
        self.assertLess(phase_index, technical_index)
        self.assertLess(phase_index, pack_index)
        self.assertLess(pack_index, technical_index)
        self.assertIn("pre-market", prompt)
        self.assertIn("Do not describe today's price action as already happened", prompt)

    def test_format_prompt_omits_market_phase_section_without_context(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-03-27",
            "today": {},
        }

        prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

        self.assertNotIn("Market Phase Context", prompt)
        self.assertNotIn("Analysis Context Pack Summary", prompt)

    def test_format_prompt_labels_intraday_partial_quote_as_estimated(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-03-27",
            "today": {"close": 1880.0},
            "market_phase_context": {
                "phase": "intraday",
                "is_partial_bar": True,
                "warnings": [],
            },
        }

        prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

        self.assertIn("### Latest Quote", prompt)
        self.assertIn("| Intraday estimated price | 1880.0 VND |", prompt)
        self.assertNotIn("### Today's Quote", prompt)
        self.assertNotIn("| Close | 1880.0 VND |", prompt)

    def test_format_prompt_uses_complete_daily_labels_for_premarket_and_non_trading(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        for phase in ("premarket", "non_trading"):
            context = {
                "code": "HPG",
                "stock_name": "Hòa Phát",
                "date": "2026-03-27",
                "today": {
                    "close": 1870.0,
                    "open": 1860.0,
                    "high": 1880.0,
                    "low": 1855.0,
                },
                "market_phase_context": {
                    "phase": phase,
                    "is_partial_bar": False,
                    "warnings": [],
                },
            }

            prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

            self.assertIn("### Last Complete Trading Day", prompt)
            self.assertIn("| Last complete session close | 1870.0 VND |", prompt)
            self.assertIn("| Open | 1860.0 VND |", prompt)
            self.assertIn("| High | 1880.0 VND |", prompt)
            self.assertIn("| Low | 1855.0 VND |", prompt)
            self.assertNotIn("### Today's Quote", prompt)
            self.assertNotIn("| Close | 1870.0 VND |", prompt)

    def test_format_prompt_does_not_label_realtime_overlay_as_previous_close(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        for phase in ("premarket", "non_trading"):
            context = {
                "code": "HPG",
                "stock_name": "Hòa Phát",
                "date": "2026-03-27",
                "today": {
                    "close": 1882.5,
                    "open": 1878.0,
                    "high": 1885.0,
                    "low": 1876.0,
                    "pct_chg": 0.42,
                    "volume": 1200000,
                    "amount": 226000000,
                    "data_source": "realtime:tencent",
                    "is_estimated": True,
                    "estimated_fields": ["close", "open", "high", "low"],
                },
                "market_phase_context": {
                    "phase": phase,
                    "is_partial_bar": False,
                    "warnings": [],
                },
            }

            prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

            self.assertIn("### Latest Quote", prompt)
            self.assertIn("| Realtime estimated price | 1882.5 VND |", prompt)
            self.assertNotIn("### Last Complete Trading Day", prompt)
            self.assertNotIn("| Last complete session close | 1882.5 VND |", prompt)
            self.assertNotIn("| Open |", prompt)
            self.assertNotIn("| High |", prompt)
            self.assertNotIn("| Low |", prompt)
            self.assertIn("| Realtime change | 0.42% |", prompt)
            self.assertIn("| Realtime volume | 1.20 triệu cp |", prompt)
            self.assertIn("| Realtime turnover | 226.00 triệu VND |", prompt)
            self.assertNotIn("| Change | 0.42% |", prompt)
            self.assertNotIn("| Volume | 1.20 triệu cp |", prompt)
            self.assertNotIn("| Turnover | 226.00 triệu VND |", prompt)

    def test_format_prompt_does_not_label_date_mismatch_as_previous_close(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-03-27",
            "today": {
                "close": 1882.5,
                "open": 1878.0,
                "high": 1885.0,
                "low": 1876.0,
                "date": "2026-03-27",
            },
            "market_phase_context": {
                "phase": "premarket",
                "effective_daily_bar_date": "2026-03-26",
                "is_partial_bar": False,
                "warnings": [],
            },
        }

        prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

        self.assertIn("### Latest Quote", prompt)
        self.assertIn("| Latest price | 1882.5 VND |", prompt)
        self.assertNotIn("### Last Complete Trading Day", prompt)
        self.assertNotIn("| Last complete session close | 1882.5 VND |", prompt)
        self.assertNotIn("| Open |", prompt)
        self.assertNotIn("| High |", prompt)
        self.assertNotIn("| Low |", prompt)

    def test_format_prompt_keeps_legacy_quote_labels_without_partial_intraday_context(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()

        for phase_context in (
            {"phase": "intraday", "is_partial_bar": False, "warnings": []},
            {"phase": "intraday", "warnings": []},
            {"phase": "postmarket", "is_partial_bar": False, "warnings": []},
            {"phase": "unknown", "is_partial_bar": True, "warnings": []},
            None,
        ):
            context = {
                "code": "HPG",
                "stock_name": "Hòa Phát",
                "date": "2026-03-27",
                "today": {"close": 1880.0},
            }
            if phase_context is not None:
                context["market_phase_context"] = phase_context

            prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

            self.assertIn("### Today's Quote", prompt)
            self.assertIn("| Close | 1880.0 VND |", prompt)

    def test_format_prompt_omits_legacy_trend_checks_for_nondefault_skill_mode(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 缠论\n- 关注中枢与背驰",
                default_skill_policy="",
                use_legacy_default_prompt=False,
            )

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-03-16",
            "today": {"close": 100, "ma5": 99, "ma10": 98, "ma20": 97},
            "trend_analysis": {
                "trend_status": "震荡偏强",
                "ma_alignment": "粘合后发散",
                "trend_strength": 61,
                "bias_ma5": 1.2,
                "bias_ma10": 2.4,
                "volume_status": "平量",
                "volume_trend": "量能温和",
                "buy_signal": "观察",
                "signal_score": 58,
                "signal_reasons": ["结构待确认"],
                "risk_factors": ["无背驰确认"],
            },
        }
        prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

        self.assertIn("Does the current structure meet the activated skill's key trigger conditions", prompt)
        self.assertNotIn("Is the bullish alignment MA5>MA10>MA20 satisfied", prompt)
        self.assertNotIn("Above 5% you must flag \"do not chase\"", prompt)
        self.assertNotIn("MA5>MA10>MA20 is bullish", prompt)

    def test_format_prompt_removes_bullish_reasons_when_final_trend_is_bearish(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 缠论\n- 关注中枢与背驰",
                default_skill_policy="",
                use_legacy_default_prompt=False,
            )

        context = {
            "code": "603259",
            "stock_name": "药明康德",
            "date": "2026-04-28",
            "today": {"close": 58.6, "ma5": 57.2, "ma10": 58.8, "ma20": 60.4},
            "yesterday": {"close": 57.8},
            "volume_change_ratio": 12.4,
            "trend_analysis": {
                "trend_status": "MA xếp giảm",
                "ma_alignment": "MA xếp giảm MA5<MA10<MA20",
                "trend_strength": 34,
                "bias_ma5": 2.1,
                "bias_ma10": -0.8,
                "volume_status": "放量",
                "volume_trend": "放量震荡",
                "buy_signal": "观察",
                "signal_score": 41,
                "signal_reasons": ["MA xếp tăng, tăng liên tục", "事件催化存在但技术待确认"],
                "risk_factors": ["跌破MA20，趋势承压"],
            },
        }

        prompt = analyzer._format_prompt(
            context,
            "药明康德",
            news_context="2026-04-27 一季报超预期，订单增长。",
        )

        self.assertIn("MA xếp giảm MA5<MA10<MA20", prompt)
        self.assertNotIn("MA xếp tăng, tăng liên tục", prompt)
        self.assertIn("事件催化存在但技术待确认", prompt)
        self.assertIn("event first, technicals yet to confirm", prompt)
        self.assertIn("Volume anomaly", prompt)
        self.assertIn("Technical consistency", prompt)

    def test_format_prompt_removes_bearish_risks_when_final_trend_is_bullish(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 缠论\n- 关注中枢与背驰",
                default_skill_policy="",
                use_legacy_default_prompt=False,
            )

        context = {
            "code": "HPG",
            "stock_name": "Hòa Phát",
            "date": "2026-04-28",
            "today": {"close": 1688.0, "ma5": 1675.0, "ma10": 1660.0, "ma20": 1640.0},
            "trend_analysis": {
                "trend_status": "MA xếp tăng",
                "ma_alignment": "MA xếp tăng MA5>MA10>MA20",
                "trend_strength": 78,
                "bias_ma5": 1.8,
                "bias_ma10": 3.2,
                "volume_status": "平量",
                "volume_trend": "量价配合",
                "buy_signal": "偏强",
                "signal_score": 73,
                "signal_reasons": ["MA xếp tăng, tăng liên tục", "MA xếp giảm, giảm liên tục"],
                "risk_factors": ["MA xếp giảm, giảm liên tục", "财报披露前波动可能放大"],
            },
        }

        prompt = analyzer._format_prompt(context, "Hòa Phát", news_context=None)

        self.assertIn("MA xếp tăng MA5>MA10>MA20", prompt)
        self.assertIn("财报披露前波动可能放大", prompt)
        self.assertNotIn("MA xếp giảm, giảm liên tục\n", prompt)
        self.assertNotIn("MA xếp giảm, giảm liên tục", prompt)
        self.assertIn("bearish-structure reasons that directly conflict with the bullish view were removed", prompt)
        self.assertIn("bearish-structure risk statements that directly conflict with the bullish view were removed", prompt)

    def test_format_prompt_removes_bullish_reasons_when_final_trend_is_weak_bearish(self) -> None:
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(
                skill_instructions="### 技能 1: 缠论\n- 关注中枢与背驰",
                default_skill_policy="",
                use_legacy_default_prompt=False,
            )

        context = {
            "code": "300750",
            "stock_name": "宁德时代",
            "date": "2026-04-28",
            "today": {"close": 178.5, "ma5": 176.0, "ma10": 180.2, "ma20": 179.9},
            "trend_analysis": {
                "trend_status": "Giảm yếu",
                "ma_alignment": "Giảm yếu, MA5<MA10 nhưng MA10≥MA20",
                "trend_strength": 43,
                "bias_ma5": 1.4,
                "bias_ma10": -0.9,
                "volume_status": "平量",
                "volume_trend": "量能一般",
                "buy_signal": "观察",
                "signal_score": 45,
                "signal_reasons": ["Tăng yếu đang phục hồi", "MA xếp tăng, tăng liên tục", "事件催化存在但技术待确认"],
                "risk_factors": ["MA10 压制仍在"],
            },
        }

        prompt = analyzer._format_prompt(
            context,
            "宁德时代",
            news_context="2026-04-27 新产品发布，市场情绪回暖。",
        )

        self.assertIn("Giảm yếu, MA5<MA10 nhưng MA10≥MA20", prompt)
        self.assertNotIn("Tăng yếu đang phục hồi", prompt)
        self.assertNotIn("MA xếp tăng, tăng liên tục", prompt)
        self.assertIn("事件催化存在但技术待确认", prompt)
        self.assertIn("bullish-structure reasons that directly conflict with the bearish view were removed", prompt)

    def test_sanitize_trend_analysis_for_prompt_returns_derived_copy_only(self) -> None:
        original = {
            "trend_status": "MA xếp giảm",
            "ma_alignment": "MA xếp giảm MA5<MA10<MA20",
            "signal_reasons": ["MA xếp tăng, tăng liên tục", "事件催化存在但技术待确认"],
            "risk_factors": ["跌破MA20，趋势承压"],
        }

        sanitized = _sanitize_trend_analysis_for_prompt(original, volume_change_ratio=12.4)

        self.assertEqual(
            original["signal_reasons"],
            ["MA xếp tăng, tăng liên tục", "事件催化存在但技术待确认"],
        )
        self.assertNotIn("prompt_consistency_notes", original)
        self.assertNotIn("prompt_trend_direction", original)
        self.assertNotIn("MA xếp tăng, tăng liên tục", sanitized["signal_reasons"])
        self.assertEqual(sanitized["prompt_trend_direction"], "bearish")


if __name__ == "__main__":
    unittest.main()
