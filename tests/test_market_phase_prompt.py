# -*- coding: utf-8 -*-
"""Tests for Issue #1386 P2-min market phase prompt rendering."""

import unittest

from src.market_phase_prompt import format_market_phase_prompt_section


def _ctx(**overrides):
    payload = {
        "market": "vn",
        "phase": "intraday",
        "market_local_time": "2026-03-27T10:00:00+07:00",
        "effective_daily_bar_date": "2026-03-26",
        "is_partial_bar": True,
        "minutes_to_open": None,
        "minutes_to_close": 300,
        "warnings": [],
        "trigger_source": "system",
        "analysis_intent": "auto",
    }
    payload.update(overrides)
    return payload


class MarketPhasePromptTestCase(unittest.TestCase):
    def test_empty_or_invalid_context_returns_empty_section(self):
        self.assertEqual(format_market_phase_prompt_section(None), "")
        self.assertEqual(format_market_phase_prompt_section({}), "")
        self.assertEqual(format_market_phase_prompt_section("intraday"), "")

    def test_premarket_mentions_opening_plan_and_completed_daily_bar(self):
        section = format_market_phase_prompt_section(
            _ctx(phase="premarket", is_partial_bar=False, minutes_to_open=30)
        )

        self.assertIn("Market Phase Context", section)
        self.assertIn("pre-market", section)
        self.assertIn("has not opened", section)
        self.assertIn("Do not describe today's price action as already happened", section)
        self.assertIn("latest complete daily bar", section)
        self.assertIn("2026-03-26", section)
        self.assertIn("About 30 minutes until the regular session opens", section)

    def test_default_and_legacy_languages_use_shared_english_scaffolding(self):
        ctx = _ctx(phase="premarket", is_partial_bar=False)
        default = format_market_phase_prompt_section(ctx)
        for language in ("vi", "en", "zh", "ko"):
            with self.subTest(language=language):
                self.assertEqual(format_market_phase_prompt_section(ctx, report_language=language), default)
        self.assertNotIn("市场阶段上下文", default)

    def test_intraday_partial_bar_warns_against_full_daily_recap(self):
        section = format_market_phase_prompt_section(_ctx())

        self.assertIn("Current market phase: intraday", section)
        self.assertIn("This is not a post-market recap", section)
        self.assertIn("latest daily bar may be unfinished", section)
        self.assertIn("do not treat it as a complete daily candle", section)
        self.assertIn("About 300 minutes until the regular session closes", section)

    def test_lunch_break_and_closing_auction_add_phase_specific_guidance(self):
        lunch = format_market_phase_prompt_section(_ctx(phase="lunch_break"))
        closing = format_market_phase_prompt_section(_ctx(phase="closing_auction"))

        self.assertIn("lunch break", lunch)
        self.assertIn("afternoon session", lunch)
        self.assertIn("near close", closing)
        self.assertIn("overnight-position decisions", closing)

    def test_postmarket_keeps_recap_semantics(self):
        section = format_market_phase_prompt_section(
            _ctx(phase="postmarket", is_partial_bar=False, minutes_to_close=None)
        )

        self.assertIn("post-market", section)
        self.assertIn("complete-session recap style is acceptable", section)

    def test_non_trading_prevents_fake_intraday_movement(self):
        section = format_market_phase_prompt_section(
            _ctx(phase="non_trading", is_partial_bar=False, minutes_to_close=None)
        )

        self.assertIn("non-trading day", section)
        self.assertIn("do not invent today's intraday movement", section)
        self.assertIn("2026-03-26", section)

    def test_unknown_phase_and_warnings_are_conservative_without_raw_codes(self):
        section = format_market_phase_prompt_section(
            _ctx(phase="not_a_phase", warnings=["calendar_unavailable", "unknown_warning"])
        )

        self.assertIn("unknown phase", section)
        self.assertIn("cannot be inferred reliably", section)
        self.assertIn("trading calendar unavailable", section)
        self.assertNotIn("calendar_unavailable", section)
        self.assertNotIn("unknown_warning", section)

    def test_missing_phase_uses_unknown_template(self):
        payload = _ctx()
        payload.pop("phase")

        section = format_market_phase_prompt_section(payload)

        self.assertIn("unknown phase", section)
        self.assertIn("cannot be inferred reliably", section)

    def test_warnings_non_list_is_ignored(self):
        section = format_market_phase_prompt_section(_ctx(warnings="calendar_unavailable"))

        self.assertNotIn("Degradation note", section)
        self.assertIn("intraday", section)

    def test_english_mode_outputs_readable_english_constraints(self):
        section = format_market_phase_prompt_section(
            _ctx(phase="premarket", is_partial_bar=False),
            report_language="en",
        )

        self.assertIn("Market Phase Context", section)
        self.assertIn("pre-market", section)
        self.assertIn("has not opened", section)
        self.assertIn("Do not describe today's price action as already happened", section)
        self.assertNotIn("(premarket)", section)

    def test_output_does_not_leak_runtime_raw_keys(self):
        section = format_market_phase_prompt_section(_ctx())

        self.assertNotIn("market_phase_context", section)
        self.assertNotIn("is_partial_bar", section)
        self.assertNotIn("trigger_source", section)
        self.assertNotIn("analysis_intent", section)
        self.assertNotIn("(intraday)", section)


if __name__ == "__main__":
    unittest.main()
