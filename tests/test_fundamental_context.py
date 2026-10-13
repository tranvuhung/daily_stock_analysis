# -*- coding: utf-8 -*-
"""
Kiểm thử fundamental context có cấu trúc (thị trường Việt Nam).

Việt Nam chỉ có khối định giá lấy từ snapshot realtime; các khối tăng trưởng /
kết quả kinh doanh / sở hữu tổ chức luôn ``not_supported``.

Tests for the structured fundamental context (Vietnam only).
"""

import time
import unittest
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


if __name__ == "__main__":
    unittest.main()
