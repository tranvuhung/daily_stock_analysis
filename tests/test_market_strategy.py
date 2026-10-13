# -*- coding: utf-8 -*-
"""Kiểm thử khung chiến lược điểm lại thị trường Việt Nam.

Tests for the Vietnam market strategy blueprint and its prompt injection.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.market_strategy import VN_BLUEPRINT, get_market_strategy_blueprint
from src.market_analyzer import MarketAnalyzer, MarketOverview


class TestMarketStrategyBlueprint(unittest.TestCase):
    def test_vn_blueprint_contains_action_framework(self):
        block = get_market_strategy_blueprint("vn").to_prompt_block()

        self.assertIn("Chiến lược điểm lại thị trường Việt Nam", block)
        self.assertIn("Action Framework", block)
        self.assertIn("Tấn công", block)
        self.assertIn("VN-Index", block)

    def test_any_region_resolves_to_vn_blueprint(self):
        # Vùng cũ (cn/us/...) đều trả khung Việt Nam. / Legacy regions map to VN.
        for region in ("vn", "cn", "us", ""):
            with self.subTest(region=region):
                self.assertIs(get_market_strategy_blueprint(region), VN_BLUEPRINT)

    def test_markdown_block_is_non_empty(self):
        self.assertIn("Khung chiến lược", VN_BLUEPRINT.to_markdown_block())


class TestMarketAnalyzerStrategyPrompt(unittest.TestCase):
    def _prompt(self, language: str) -> str:
        with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language=language)):
            analyzer = MarketAnalyzer(region="vn")
            return analyzer._build_review_prompt(MarketOverview(date="2026-02-24"), [])

    def test_vi_prompt_contains_strategy_plan_and_vietnamese_shell(self):
        prompt = self._prompt("vi")

        self.assertIn("Vietnam market analyst", prompt)
        self.assertIn("Vietnamese (Tiếng Việt)", prompt)
        self.assertIn("Strategy Plan", prompt)
        self.assertIn("Chiến lược điểm lại thị trường Việt Nam", prompt)

    def test_en_prompt_uses_english_shell(self):
        prompt = self._prompt("en")

        self.assertIn("must be in English", prompt)
        self.assertIn("Vietnam Market Recap", prompt)
        self.assertIn("Strategy Plan", prompt)

    def test_legacy_region_is_normalized_to_vn(self):
        with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="vi")):
            analyzer = MarketAnalyzer(region="cn")
        self.assertEqual(analyzer.region, "vn")

    def test_market_stats_passes_market_review_purpose(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        analyzer.region = "vn"
        analyzer.data_manager = MagicMock()
        analyzer.data_manager.get_market_stats.return_value = {
            "up_count": 3,
            "down_count": 2,
            "flat_count": 1,
            "limit_up_count": 0,
            "limit_down_count": 0,
            "total_amount": 12.0,
        }
        overview = MarketOverview(date="2026-02-24")

        analyzer._get_market_statistics(overview)

        analyzer.data_manager.get_market_stats.assert_called_once_with(
            purpose="market_review:vn",
            region="vn",
        )
        self.assertEqual(overview.up_count, 3)


if __name__ == "__main__":
    unittest.main()
