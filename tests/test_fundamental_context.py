# -*- coding: utf-8 -*-
"""
Kiểm thử fundamental context có cấu trúc (thị trường Việt Nam).

Việt Nam có khối định giá lấy từ snapshot realtime; khi FiinTrade được bật thì có
thêm earnings (cổ tức, phát hành, ngày KQKD) và institution (giao dịch nội bộ, sở
hữu nước ngoài). Khối tăng trưởng luôn ``not_supported``.

Tests for the structured fundamental context (Vietnam only).
"""

import time
import unittest
from datetime import date, timedelta
from threading import BoundedSemaphore, Event
from types import SimpleNamespace
from unittest.mock import patch

from data_provider.base import FUNDAMENTAL_BLOCKS, DataFetcherManager


def _cfg(**overrides):
    values = dict(
        enable_fundamental_pipeline=True,
        fundamental_cache_ttl_seconds=0,
        fundamental_stage_timeout_seconds=1.5,
        fundamental_fetch_timeout_seconds=0.8,
        fundamental_retry_max=1,
        fundamental_cache_max_entries=16,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _quote(**fields):
    base = dict(pe_ratio=None, pb_ratio=None, total_mv=None, circ_mv=None)
    base.update(fields)
    return SimpleNamespace(**base)


class TestFundamentalContext(unittest.TestCase):
    def test_blocks_are_fixed(self) -> None:
        self.assertEqual(FUNDAMENTAL_BLOCKS, ("valuation", "growth", "earnings", "institution"))

    def test_not_supported_builder_uses_existing_fundamental_schema(self) -> None:
        manager = DataFetcherManager(fetchers=[])

        context = manager.build_not_supported_fundamental_context(
            "VNINDEX", "index target: fundamental modules skipped"
        )

        self.assertEqual(context["status"], "not_supported")
        self.assertEqual(set(context["coverage"]), set(FUNDAMENTAL_BLOCKS))
        self.assertTrue(all(status == "not_supported" for status in context["coverage"].values()))
        self.assertEqual(context["errors"], ["index target: fundamental modules skipped"])

    def test_failed_builder_marks_every_block_failed(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        context = manager.build_failed_fundamental_context("HPG", "boom")
        self.assertEqual(context["market"], "vn")
        self.assertEqual(context["status"], "failed")
        self.assertTrue(all(status == "failed" for status in context["coverage"].values()))

    def test_vn_valuation_from_quote_and_other_blocks_not_supported(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        quote = _quote(pe_ratio=10.5, pb_ratio=1.8, total_mv=1.5e14)
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote", return_value=quote) as mocked:
            ctx = manager.get_fundamental_context("HPG")

        mocked.assert_called_once_with("HPG")
        self.assertEqual(ctx["market"], "vn")
        self.assertEqual(ctx["status"], "partial")
        self.assertEqual(ctx["coverage"]["valuation"], "ok")
        self.assertEqual(ctx["valuation"]["data"]["pe_ratio"], 10.5)
        for block in ("growth", "earnings", "institution"):
            self.assertEqual(ctx["coverage"][block], "not_supported", block)
            self.assertEqual(ctx[block]["status"], "not_supported", block)

    def test_valuation_all_none_fields_should_not_be_ok(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote", return_value=_quote()):
            ctx = manager.get_fundamental_context("HPG")

        self.assertEqual(ctx["coverage"].get("valuation"), "partial")
        self.assertEqual(ctx["status"], "partial")

    def test_missing_quote_marks_valuation_not_supported(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote", return_value=None):
            ctx = manager.get_fundamental_context("VCB")

        self.assertEqual(ctx["coverage"]["valuation"], "not_supported")
        self.assertEqual(ctx["status"], "not_supported")

    def test_quote_error_marks_valuation_failed(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote", side_effect=RuntimeError("down")):
            ctx = manager.get_fundamental_context("VCB")

        self.assertEqual(ctx["coverage"]["valuation"], "not_supported")
        self.assertIn("down", ctx["valuation"]["errors"])

    def test_non_vn_code_returns_not_supported_without_provider_calls(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote") as mocked:
            ctx = manager.get_fundamental_context("AAPL")

        mocked.assert_not_called()
        self.assertEqual(ctx["status"], "not_supported")
        self.assertEqual(ctx["market"], "unknown")

    def test_pipeline_disabled_returns_not_supported(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        with patch("src.config.get_config", return_value=_cfg(enable_fundamental_pipeline=False)), \
                patch.object(manager, "get_realtime_quote") as mocked:
            ctx = manager.get_fundamental_context("HPG")

        mocked.assert_not_called()
        self.assertEqual(ctx["status"], "not_supported")
        self.assertEqual(ctx["errors"], ["fundamental pipeline disabled"])

    def test_cache_reuses_context_within_ttl(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        quote = _quote(pe_ratio=12.0)
        with patch("src.config.get_config", return_value=_cfg(fundamental_cache_ttl_seconds=120)), \
                patch.object(manager, "get_realtime_quote", return_value=quote) as mocked:
            first = manager.get_fundamental_context("FPT")
            second = manager.get_fundamental_context("FPT")

        self.assertIs(first, second)
        self.assertEqual(mocked.call_count, 1)

    def test_run_with_timeout_limits_hanging_workers(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        manager._fundamental_timeout_slots = BoundedSemaphore(1)

        unblock = Event()

        def _hanging_task():
            unblock.wait(timeout=0.5)
            return 1

        try:
            result, err, _ = manager._run_with_timeout(_hanging_task, 0.01, "hang")
            self.assertIsNone(result)
            self.assertIn("timeout", err or "")

            result2, err2, _ = manager._run_with_timeout(_hanging_task, 0.01, "hang")
            self.assertIsNone(result2)
            self.assertIn("worker pool exhausted", err2 or "")
        finally:
            unblock.set()
            time.sleep(0.02)

    def test_infer_block_status_treats_all_null_payload_as_non_ok(self) -> None:
        self.assertEqual(
            DataFetcherManager._infer_block_status({"pe_ratio": None, "summary": ""}, "partial"),
            "partial",
        )
        self.assertEqual(
            DataFetcherManager._infer_block_status({"pe_ratio": None}, "not_supported"),
            "not_supported",
        )
        self.assertEqual(DataFetcherManager._infer_block_status({"pe_ratio": 0.0}, "partial"), "ok")

    def test_fundamental_cache_key_isolated_by_budget_bucket(self) -> None:
        manager = DataFetcherManager(fetchers=[])
        key_default = manager._get_fundamental_cache_key("HPG")
        key_low = manager._get_fundamental_cache_key("HPG", 0.4)
        key_high = manager._get_fundamental_cache_key("HPG", 1.5)

        self.assertNotEqual(key_default, key_low)
        self.assertNotEqual(key_low, key_high)
        self.assertIn("budget=", key_low)



class _FiinTradeStub:
    """Nguồn FiinTrade giả cho fundamentals / FiinTrade stub for fundamentals."""

    name = "FiinTradeFetcher"
    priority = -1

    def __init__(self, quote=None, events=None, events_error=None):
        self._quote = quote
        self._events = events
        self._events_error = events_error

    def get_realtime_quote(self, stock_code, **_):
        return self._quote

    def get_corporate_events(self, stock_code):
        if self._events_error:
            raise RuntimeError(self._events_error)
        return self._events


def _days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


class TestFundamentalContextWithFiinTrade(unittest.TestCase):
    def _quote(self):
        from data_provider.realtime_types import UnifiedRealtimeQuote

        return UnifiedRealtimeQuote(
            code="FPT", price=50000.0, pe_ratio=10.8, pb_ratio=2.7, total_mv=1.08e14,
            eps_ttm=5323.8, roe=26.47, foreign_ownership_pct=27.92, foreign_room_remaining=3.6e8,
        )

    def _events(self):
        return {
            "dividends": [
                {"ex_date": _days_ago(30), "record_date": _days_ago(29), "cash_per_share": 1000.0},
                {"ex_date": _days_ago(200), "record_date": _days_ago(199), "cash_per_share": 1000.0},
                # Ngoài 12 tháng: không tính vào TTM / Outside the TTM window
                {"ex_date": _days_ago(400), "record_date": _days_ago(399), "cash_per_share": 1000.0},
            ],
            "share_issuances": [
                {"method_en": "Bonus Issue", "ex_date": _days_ago(20), "volume": 1.7e8, "ratio": 0.1},
            ],
            "insider_transactions": [
                {"name": "A", "action": "buy", "shares_executed": 8700.0, "public_date": _days_ago(10)},
                {"name": "B", "action": "sell", "shares_executed": 2000.0, "public_date": _days_ago(40)},
                # Ngoài 90 ngày / Outside the 90-day window
                {"name": "C", "action": "buy", "shares_executed": 99999.0, "public_date": _days_ago(120)},
            ],
            "earnings_releases": [{"year": 2026, "quarter": 2, "public_date": _days_ago(60)}],
        }

    def test_fiintrade_fills_valuation_earnings_and_institution(self) -> None:
        stub = _FiinTradeStub(quote=self._quote(), events=self._events())
        manager = DataFetcherManager(fetchers=[stub])
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote") as regular_quote:
            ctx = manager.get_fundamental_context("FPT")

        # Định giá lấy thẳng từ FiinTrade, không qua chuỗi realtime thường.
        regular_quote.assert_not_called()
        self.assertEqual(ctx["coverage"]["valuation"], "ok")
        self.assertEqual(ctx["valuation"]["data"]["eps_ttm"], 5323.8)

        dividend = ctx["earnings"]["data"]["dividend"]
        self.assertEqual(dividend["ttm_cash_dividend_per_share"], 2000.0)
        self.assertEqual(dividend["ttm_event_count"], 2)
        self.assertEqual(dividend["ttm_dividend_yield_pct"], 4.0)
        self.assertEqual(dividend["events"][0]["ex_dividend_date"], _days_ago(30))
        self.assertEqual(ctx["earnings"]["data"]["earnings_releases"][0]["quarter"], 2)
        # Chưa có báo cáo tài chính nên earnings chỉ "partial".
        self.assertEqual(ctx["coverage"]["earnings"], "partial")

        institution = ctx["institution"]["data"]
        self.assertEqual(ctx["coverage"]["institution"], "ok")
        self.assertEqual(institution["insider_transaction_count"], 2)
        self.assertEqual(institution["insider_net_shares"], 6700.0)
        self.assertEqual(institution["foreign_ownership_pct"], 27.92)

        self.assertEqual(ctx["coverage"]["growth"], "not_supported")
        self.assertEqual(ctx["status"], "partial")

    def test_valuation_kept_before_first_match(self) -> None:
        # Phiên ATO: giá khớp = 0 nhưng FiinTrade vẫn có P/E.
        # During ATO the matched price is 0 but FiinTrade still has PE.
        from data_provider.realtime_types import UnifiedRealtimeQuote

        quote = UnifiedRealtimeQuote(code="FPT", price=0.0, pe_ratio=10.78, pb_ratio=2.72)
        stub = _FiinTradeStub(quote=quote, events=self._events())
        manager = DataFetcherManager(fetchers=[stub])
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote") as regular_quote:
            ctx = manager.get_fundamental_context("FPT")

        regular_quote.assert_not_called()
        self.assertEqual(ctx["valuation"]["data"]["pe_ratio"], 10.78)
        # Không có giá thì không tính lợi suất cổ tức / No price, no yield
        self.assertIsNone(ctx["earnings"]["data"]["dividend"]["ttm_dividend_yield_pct"])

    def test_events_failure_is_fail_open(self) -> None:
        stub = _FiinTradeStub(quote=self._quote(), events_error="boom")
        manager = DataFetcherManager(fetchers=[stub])
        with patch("src.config.get_config", return_value=_cfg()):
            ctx = manager.get_fundamental_context("FPT")

        self.assertEqual(ctx["coverage"]["valuation"], "ok")
        self.assertEqual(ctx["coverage"]["earnings"], "failed")
        self.assertEqual(ctx["coverage"]["institution"], "failed")
        self.assertEqual(ctx["status"], "partial")

    def test_unresolved_symbol_quote_falls_back_to_regular_chain(self) -> None:
        stub = _FiinTradeStub(
            quote=None,
            events={"dividends": [], "share_issuances": [], "insider_transactions": [], "earnings_releases": []},
        )
        manager = DataFetcherManager(fetchers=[stub])
        fallback = _quote(pe_ratio=None)
        with patch("src.config.get_config", return_value=_cfg()), \
                patch.object(manager, "get_realtime_quote", return_value=fallback) as regular_quote:
            ctx = manager.get_fundamental_context("ACV")

        regular_quote.assert_called_once_with("ACV")
        # Sự kiện rỗng hoàn toàn (mã không tra được) không được báo thành "cổ tức = 0".
        # All-empty events (symbol not resolved) must not be reported as "zero dividends".
        self.assertEqual(ctx["coverage"]["earnings"], "not_supported")
        self.assertEqual(ctx["coverage"]["institution"], "not_supported")
        self.assertEqual(ctx["earnings"]["data"], {})


if __name__ == "__main__":
    unittest.main()
