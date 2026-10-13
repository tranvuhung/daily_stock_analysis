# -*- coding: utf-8 -*-
"""
Kiểm thử hợp đồng đầu ra của tool get_stock_info (thị trường Việt Nam).

Contract tests for get_stock_info tool output semantics (Vietnam only).
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.tools.data_tools import _handle_get_stock_info


class _DummyManager:
    def __init__(self):
        self._context = {
            "market": "vn",
            "status": "partial",
            "coverage": {
                "valuation": "ok",
                "growth": "not_supported",
                "earnings": "not_supported",
                "institution": "not_supported",
            },
            "valuation": {
                "status": "ok",
                "data": {
                    "pe_ratio": 12.3,
                    "pb_ratio": 2.1,
                    "total_mv": 1.0e14,
                    "circ_mv": 7.0e13,
                },
            },
            "growth": {"status": "not_supported", "data": {}},
            "earnings": {"status": "not_supported", "data": {}},
            "institution": {"status": "not_supported", "data": {}},
        }

    def get_fundamental_context(self, _stock_code: str):
        return self._context

    def build_failed_fundamental_context(self, _stock_code: str, _reason: str):
        return {"status": "failed"}

    def get_stock_name(self, _stock_code: str):
        return "Hòa Phát"


class _FailingManager(_DummyManager):
    def get_fundamental_context(self, _stock_code: str):
        raise RuntimeError("boom")

    def get_stock_name(self, _stock_code: str):
        raise RuntimeError("name boom")


class TestGetStockInfoContract(unittest.TestCase):
    def test_get_stock_info_returns_valuation_and_compact_context(self) -> None:
        manager = _DummyManager()
        with patch("src.agent.tools.data_tools._get_fetcher_manager", return_value=manager):
            result = _handle_get_stock_info("hpg")

        self.assertEqual(result["name"], "Hòa Phát")
        self.assertEqual(result["code"], "HPG")
        self.assertEqual(result["pe_ratio"], 12.3)
        self.assertEqual(result["pb_ratio"], 2.1)
        self.assertEqual(result["total_mv"], 1.0e14)
        self.assertIn("fundamental_context", result)
        # Các khối bảng ngành/dòng tiền A-share đã bị loại bỏ.
        # Removed A-share board / capital-flow fields are no longer emitted.
        for removed in ("belong_boards", "boards", "sector_rankings"):
            self.assertNotIn(removed, result)

    def test_get_stock_info_fails_open_when_pipeline_and_name_fail(self) -> None:
        with patch("src.agent.tools.data_tools._get_fetcher_manager", return_value=_FailingManager()):
            result = _handle_get_stock_info("FPT")

        self.assertEqual(result["code"], "FPT")
        self.assertEqual(result["name"], "FPT")
        self.assertIsNone(result["pe_ratio"])


if __name__ == "__main__":
    unittest.main()
