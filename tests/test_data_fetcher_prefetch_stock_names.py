# -*- coding: utf-8 -*-
"""
Kiểm thử hồi quy prefetch tên cổ phiếu (thị trường Việt Nam).

Regression tests for stock-name prefetch behavior.
"""

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import DataFetcherManager
from src.core.pipeline import StockAnalysisPipeline


class _DummyFetcher:
    name = "DummyFetcher"

    @staticmethod
    def get_stock_name(_stock_code):
        return "Cổ phần Thử nghiệm"


class _FallbackNameFetcher:
    name = "FallbackNameFetcher"
    priority = 99

    def __init__(self, name: str = "Tên dự phòng"):
        self.return_name = name
        self.calls = []

    def get_stock_name(self, stock_code):
        self.calls.append(stock_code)
        return self.return_name


class _ThreadUnsafeStockListFetcher:
    name = "ThreadUnsafeStockListFetcher"

    def __init__(self):
        self._active = False
        self.call_count = 0

    def get_stock_list(self):
        if self._active:
            raise AssertionError("concurrent get_stock_list access")
        self._active = True
        self.call_count += 1
        try:
            time.sleep(0.05)
            return pd.DataFrame(
                [
                    {"code": "HPG", "name": "Hòa Phát"},
                    {"code": "VCB", "name": "Vietcombank"},
                ]
            )
        finally:
            self._active = False


class TestPrefetchStockNames(unittest.TestCase):
    def test_prefetch_stock_names_calls_get_stock_name_without_realtime(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager.get_stock_name = MagicMock(return_value="")

        DataFetcherManager.prefetch_stock_names(
            manager, ["HPG.VN", "VCB", "VNINDEX", "AAPL"], use_bulk=False
        )

        # Mã ngoài VN (AAPL) bị bỏ qua. / Non-VN codes are skipped.
        manager.get_stock_name.assert_has_calls(
            [
                call("HPG", allow_realtime=False),
                call("VCB", allow_realtime=False),
                call("VNINDEX", allow_realtime=False),
            ]
        )
        self.assertEqual(manager.get_stock_name.call_count, 3)

    def test_get_stock_name_skips_realtime_when_allow_realtime_false(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [_DummyFetcher()]
        manager.get_realtime_quote = MagicMock(return_value=MagicMock(name="Tên realtime"))

        with patch("data_provider.base.get_index_stock_name", return_value=None):
            name = DataFetcherManager.get_stock_name(manager, "FPT", allow_realtime=False)

        self.assertEqual(name, "Cổ phần Thử nghiệm")
        manager.get_realtime_quote.assert_not_called()

    def test_get_stock_name_prefers_static_mapping_before_remote_fetchers(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        remote_fetcher = MagicMock()
        remote_fetcher.name = "RemoteFetcher"
        remote_fetcher.get_stock_name.return_value = "Tên từ xa"
        manager._fetchers = [remote_fetcher]
        manager.get_realtime_quote = MagicMock()

        with patch.dict("data_provider.base.STOCK_NAME_MAP", {"HPG": "Hòa Phát"}, clear=True), \
                patch("data_provider.base.get_index_stock_name", return_value=None):
            name = DataFetcherManager.get_stock_name(manager, "HPG", allow_realtime=False)

        self.assertEqual(name, "Hòa Phát")
        manager.get_realtime_quote.assert_not_called()
        remote_fetcher.get_stock_name.assert_not_called()
        self.assertEqual(manager._stock_name_cache["HPG"], "Hòa Phát")

    def test_get_stock_name_prefers_index_mapping_before_remote_fetchers(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        remote_fetcher = MagicMock()
        remote_fetcher.name = "RemoteFetcher"
        remote_fetcher.get_stock_name.return_value = "Tên từ xa"
        manager._fetchers = [remote_fetcher]
        manager.get_realtime_quote = MagicMock()

        with patch("data_provider.base.get_index_stock_name", return_value="Tên trong index"):
            name = DataFetcherManager.get_stock_name(manager, "FPT", allow_realtime=False)

        self.assertEqual(name, "Tên trong index")
        manager.get_realtime_quote.assert_not_called()
        remote_fetcher.get_stock_name.assert_not_called()
        self.assertEqual(manager._stock_name_cache["FPT"], "Tên trong index")

    def test_get_stock_name_prefers_static_mapping_before_index_hits(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = []
        manager.get_realtime_quote = MagicMock()

        with patch.dict("data_provider.base.STOCK_NAME_MAP", {"VNM": "Vinamilk"}, clear=True):
            with patch("data_provider.base.get_index_stock_name", return_value="VINAMILK INDEX"):
                name = DataFetcherManager.get_stock_name(manager, "VNM")

        self.assertEqual(name, "Vinamilk")
        manager.get_realtime_quote.assert_not_called()
        self.assertEqual(manager._stock_name_cache["VNM"], "Vinamilk")

    def test_get_stock_name_prefers_index_mapping_before_realtime_quote(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = []
        manager.get_realtime_quote = MagicMock(return_value=SimpleNamespace(name="Tên realtime"))

        with patch.dict("data_provider.base.STOCK_NAME_MAP", {}, clear=True):
            with patch("data_provider.base.get_index_stock_name", return_value="Tên trong index"):
                name = DataFetcherManager.get_stock_name(manager, "FPT", allow_realtime=True)

        self.assertEqual(name, "Tên trong index")
        manager.get_realtime_quote.assert_not_called()
        self.assertEqual(manager._stock_name_cache["FPT"], "Tên trong index")

    def test_get_stock_name_preserves_raw_exchange_hint_for_realtime_lookup(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = []
        manager.get_realtime_quote = MagicMock(return_value=SimpleNamespace(name="Vietcombank"))

        with patch.dict("data_provider.base.STOCK_NAME_MAP", {}, clear=True):
            with patch("data_provider.base.get_index_stock_name", return_value=None):
                name = DataFetcherManager.get_stock_name(manager, "VCB.VN")

        self.assertEqual(name, "Vietcombank")
        manager.get_realtime_quote.assert_called_once_with("VCB.VN", log_final_failure=False)

    def test_fetch_and_save_stock_data_uses_lightweight_name_lookup(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.fetcher_manager = MagicMock()
        pipeline.db = MagicMock()
        pipeline.fetcher_manager.get_stock_name.return_value = "Hòa Phát"
        pipeline.db.has_today_data.return_value = False
        pipeline.fetcher_manager.get_daily_data.return_value = (
            pd.DataFrame(
                [
                    {
                        "date": "2026-03-27",
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1,
                        "amount": 1.0,
                        "pct_chg": 0.0,
                    }
                ]
            ),
            "dummy",
        )
        pipeline.db.save_daily_data.return_value = 1

        success, error = StockAnalysisPipeline.fetch_and_save_stock_data(pipeline, "HPG")

        self.assertTrue(success)
        self.assertIsNone(error)
        pipeline.fetcher_manager.get_stock_name.assert_called_once_with("HPG", allow_realtime=False)


    def test_batch_get_stock_names_serializes_shared_fetcher_access(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [_ThreadUnsafeStockListFetcher()]

        barrier = threading.Barrier(2)
        errors = []
        results = []

        def worker():
            try:
                barrier.wait(timeout=1)
                result = DataFetcherManager.batch_get_stock_names(manager, ["HPG", "VCB"])
                results.append(result)
            except Exception as exc:  # pragma: no cover - thread collection
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result["HPG"], "Hòa Phát")
            self.assertEqual(result["VCB"], "Vietcombank")
        self.assertGreaterEqual(manager._fetchers[0].call_count, 1)
        self.assertEqual(manager._stock_name_cache["HPG"], "Hòa Phát")
        self.assertEqual(manager._stock_name_cache["VCB"], "Vietcombank")


if __name__ == "__main__":
    unittest.main()
