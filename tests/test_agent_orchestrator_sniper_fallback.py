# -*- coding: utf-8 -*-
"""Regression tests for AgentOrchestrator sniper point fallbacks."""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.agent.orchestrator import AgentOrchestrator
from src.agent.protocols import AgentContext


class TestAgentOrchestratorSniperFallback(unittest.TestCase):
    def test_secondary_buy_does_not_duplicate_ideal_buy(self):
        orch = AgentOrchestrator(
            tool_registry=MagicMock(),
            llm_adapter=MagicMock(),
        )
        ctx = AgentContext(query="test", stock_code="301308", stock_name="江波龙")

        payload = {
            "decision_type": "buy",
            "analysis_summary": "趋势仍强，等待回踩。",
            "dashboard": {
                "key_levels": {
                    "support": 301.61,
                    "stop_loss": 295.0,
                    "resistance": 340.44,
                }
            },
        }

        normalized = orch._finalize_dashboard_payload(payload, ctx)

        self.assertIsNotNone(normalized)
        sniper = normalized["dashboard"]["battle_plan"]["sniper_points"]
        self.assertEqual(sniper["ideal_buy"], 301.61)
        self.assertEqual(sniper["secondary_buy"], "N/A")

    def test_secondary_buy_numeric_string_does_not_duplicate_ideal_buy(self):
        orch = AgentOrchestrator(
            tool_registry=MagicMock(),
            llm_adapter=MagicMock(),
        )
        ctx = AgentContext(query="test", stock_code="301308", stock_name="江波龙")

        payload = {
            "decision_type": "buy",
            "analysis_summary": "趋势仍强，等待回踩。",
            "dashboard": {
                "battle_plan": {
                    "sniper_points": {
                        "secondary_buy": "301.61",
                    }
                },
                "key_levels": {
                    "support": 301.61,
                    "stop_loss": 295.0,
                    "resistance": 340.44,
                },
            },
        }

        normalized = orch._finalize_dashboard_payload(payload, ctx)

        self.assertIsNotNone(normalized)
        sniper = normalized["dashboard"]["battle_plan"]["sniper_points"]
        self.assertEqual(sniper["ideal_buy"], 301.61)
        self.assertEqual(sniper["secondary_buy"], "N/A")


    def test_long_analysis_summary_is_not_cut_at_old_limit(self):
        # Giới hạn cũ 220 ký tự cắt giữa câu tiếng Việt. / The old 220-char cap cut sentences.
        orch = AgentOrchestrator(tool_registry=MagicMock(), llm_adapter=MagicMock())
        ctx = AgentContext(query="test", stock_code="FPT", stock_name="FPT")
        summary = "FPT giữ nền tảng cơ bản mạnh, " * 16
        one_sentence = "Quan sát: giá sát hỗ trợ, chờ xác nhận lực cầu trước khi giải ngân thêm."

        normalized = orch._finalize_dashboard_payload(
            {
                "decision_type": "hold",
                "analysis_summary": summary,
                "dashboard": {"core_conclusion": {"one_sentence": one_sentence}},
            },
            ctx,
        )

        self.assertEqual(normalized["analysis_summary"], summary.strip())
        self.assertEqual(normalized["dashboard"]["core_conclusion"]["one_sentence"], one_sentence)


    def test_string_key_points_from_decision_agent_are_kept(self):
        orch = AgentOrchestrator(tool_registry=MagicMock(), llm_adapter=MagicMock())
        ctx = AgentContext(query="test", stock_code="FPT", stock_name="FPT")

        normalized = orch._finalize_dashboard_payload(
            {
                "decision_type": "hold",
                "analysis_summary": "Tóm tắt.",
                "key_points": "Lợi nhuận kỷ lục, khối ngoại bán ròng",
                "dashboard": {},
            },
            ctx,
        )

        self.assertEqual(normalized["key_points"], ["Lợi nhuận kỷ lục, khối ngoại bán ròng"])


if __name__ == "__main__":
    unittest.main()
