# -*- coding: utf-8 -*-
"""Tests for vi/en output-language directives in analysis prompts (legacy zh/ko map to vi)."""

import unittest

from src.agent.agents.decision_agent import DecisionAgent
from src.agent.protocols import AgentContext
from src.analysis_context_pack_prompt import normalize_analysis_context_pack_language
from src.market_phase_prompt import format_market_phase_prompt_section


def _phase_ctx():
    return {
        "market": "us",
        "phase": "premarket",
        "market_local_time": "2026-06-29T08:00:00-04:00",
        "effective_daily_bar_date": "2026-06-27",
        "minutes_to_open": 90,
        "warnings": [],
    }


class DecisionAgentLanguageDirectiveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = DecisionAgent(tool_registry=None, llm_adapter=None)

    def _system_prompt(self, language: str, *, chat: bool = False) -> str:
        meta = {"report_language": language}
        if chat:
            meta["response_mode"] = "chat"
        ctx = AgentContext(stock_code="HPG", stock_name="Hoa Phat Group", meta=meta)
        return self.agent.system_prompt(ctx)

    def test_vietnamese_dashboard_directive(self) -> None:
        prompt = self._system_prompt("vi")
        self.assertIn("Write all human-readable JSON values in Vietnamese (Tiếng Việt)", prompt)
        self.assertIn("`decision_type` must remain `buy|hold|sell`.", prompt)

    def test_vietnamese_chat_directive(self) -> None:
        prompt = self._system_prompt("vi", chat=True)
        self.assertIn("Always answer in Vietnamese (Tiếng Việt)", prompt)
        self.assertNotIn("Always answer in English.", prompt)

    def test_english_chat_directive(self) -> None:
        prompt = self._system_prompt("en", chat=True)
        self.assertIn("Always answer in English.", prompt)

    def test_english_directive_unchanged(self) -> None:
        prompt = self._system_prompt("en")
        self.assertIn("Write all human-readable JSON values in English.", prompt)

    def test_legacy_zh_and_ko_fall_back_to_vietnamese_directive(self) -> None:
        for legacy in ("zh", "ko"):
            prompt = self._system_prompt(legacy)
            self.assertIn("Write all human-readable JSON values in Vietnamese (Tiếng Việt)", prompt)
            self.assertNotRegex(prompt, "[\u4e00-\u9fff\uac00-\ud7af]")


class StructuralLanguageRoutingTestCase(unittest.TestCase):
    def test_context_pack_korean_reuses_english_scaffolding(self) -> None:
        self.assertEqual(normalize_analysis_context_pack_language("ko"), "en")
        self.assertEqual(normalize_analysis_context_pack_language("en"), "en")
        self.assertEqual(normalize_analysis_context_pack_language("zh"), "en")

    def test_market_phase_korean_matches_english_structure(self) -> None:
        ko_section = format_market_phase_prompt_section(_phase_ctx(), report_language="ko")
        en_section = format_market_phase_prompt_section(_phase_ctx(), report_language="en")
        self.assertEqual(ko_section, en_section)
        self.assertIn("## Market Phase Context", ko_section)


if __name__ == "__main__":
    unittest.main()
