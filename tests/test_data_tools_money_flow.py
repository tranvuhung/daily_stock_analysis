# -*- coding: utf-8 -*-
"""
Kiểm thử công cụ agent cho khối ngoại / tự doanh theo mã và trường bổ sung của báo giá.

Contract tests for the per-stock money-flow tool and the extra realtime-quote fields.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
from src.agent.tools.data_tools import (
    ALL_DATA_TOOLS,
    _handle_get_realtime_quote,
    _handle_get_stock_money_flow,
)


class RealtimeQuoteToolExtraFieldsTestCase(unittest.TestCase):
    def test_quote_tool_keeps_foreign_and_profitability_fields(self):
        # Trước đây công cụ bỏ mất các trường này dù báo giá FiinTrade có.
        quote = UnifiedRealtimeQuote(
            code="TCB", name="", source=RealtimeSource.FALLBACK, price=34200.0,
            foreign_buy_value=280690221500.0, foreign_sell_value=128629721500.0,
            foreign_net_value=152060500000.0, foreign_ownership_pct=20.57, roe=16.06,
        )
        manager = MagicMock(get_realtime_quote=MagicMock(return_value=quote))
        with patch("src.agent.tools.data_tools._get_fetcher_manager", return_value=manager):
            result = _handle_get_realtime_quote("TCB")

        self.assertEqual(result["foreign_net_value"], 152060500000.0)
        self.assertEqual(result["foreign_ownership_pct"], 20.57)
        self.assertEqual(result["roe"], 16.06)
        self.assertNotIn("eps_ttm", result)  # None thì không thêm / omitted when missing


class StockMoneyFlowToolTestCase(unittest.TestCase):
    def test_tool_returns_provider_payload(self):
        payload = {"stock_code": "TCB", "unit": "VND bn", "foreign": {"today": {"net": 152.06}}}
        manager = MagicMock(get_stock_money_flow=MagicMock(return_value=payload))
        with patch("src.agent.tools.data_tools._get_fetcher_manager", return_value=manager):
            self.assertEqual(_handle_get_stock_money_flow("TCB"), payload)

    def test_tool_reports_unavailable_without_inventing_data(self):
        manager = MagicMock(get_stock_money_flow=MagicMock(return_value={}))
        with patch("src.agent.tools.data_tools._get_fetcher_manager", return_value=manager):
            result = _handle_get_stock_money_flow("VNINDEX")

        self.assertIn("error", result)
        self.assertFalse(result["retriable"])

    def test_tool_is_registered(self):
        self.assertIn("get_stock_money_flow", [tool.name for tool in ALL_DATA_TOOLS])


if __name__ == "__main__":
    unittest.main()
