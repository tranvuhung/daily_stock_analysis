# -*- coding: utf-8 -*-
"""Kiểm thử hồi quy định tuyến nguồn nến ngày Việt Nam và cầu chì sức khoẻ nguồn.

Regression tests for Vietnam daily-source routing and source health breaker.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.base import (
    BaseFetcher,
    DataFetchError,
    DataFetcherManager,
    STANDARD_COLUMNS,
)


class _StubFetcher:
    def __init__(self, name: str, priority: int):
        self.name = name
        self.priority = priority


class _EmptyRawFetcher(BaseFetcher):
    name = "EmptyRawFetcher"
    priority = 0
    allow_empty_daily_data = True

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        raise AssertionError("empty raw daily data should not be normalized")


class _DefaultEmptyRawFetcher(_EmptyRawFetcher):
    name = "DefaultEmptyRawFetcher"
    allow_empty_daily_data = False


def _make_daily_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-05-01",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000,
                "amount": 101000.0,
                "pct_chg": 1.0,
            }
        ]
    )


class TestFetcherSourceOptimization(unittest.TestCase):
    def test_base_fetcher_returns_opt_in_empty_daily_data_without_error(self):
        df = _EmptyRawFetcher().get_daily_data(
            "HPG",
            start_date="2026-05-01",
            end_date="2026-05-08",
        )

        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), STANDARD_COLUMNS)

    def test_base_fetcher_rejects_empty_daily_data_by_default(self):
        with self.assertRaises(DataFetchError):
            _DefaultEmptyRawFetcher().get_daily_data(
                "HPG",
                start_date="2026-05-01",
                end_date="2026-05-08",
            )

    def test_manager_skips_vnstock_unless_enabled(self):
        # vnstock là nguồn tuỳ chọn; chưa bật thì không được khởi tạo.
        # vnstock is optional and must not be instantiated unless enabled.
        with patch.dict("os.environ", {"VNSTOCK_ENABLED": "false"}), patch(
            "data_provider.vci_fetcher.VciFetcher", return_value=_StubFetcher("VciFetcher", 0)
        ), patch(
            "data_provider.ssi_fetcher.SsiFetcher", return_value=_StubFetcher("SsiFetcher", 1)
        ), patch(
            "data_provider.yfinance_fetcher.YfinanceFetcher", return_value=_StubFetcher("YfinanceFetcher", 4)
        ), patch.dict(
            sys.modules, {"data_provider.vnstock_fetcher": SimpleNamespace(VnstockFetcher=MagicMock())}
        ):
            mock_vnstock = sys.modules["data_provider.vnstock_fetcher"].VnstockFetcher
            manager = DataFetcherManager()

        self.assertEqual(manager.available_fetchers, ["VciFetcher", "SsiFetcher", "YfinanceFetcher"])
        mock_vnstock.assert_not_called()

    def test_manager_registers_vnstock_when_enabled(self):
        with patch.dict("os.environ", {"VNSTOCK_ENABLED": "true"}), patch(
            "data_provider.vci_fetcher.VciFetcher", return_value=_StubFetcher("VciFetcher", 0)
        ), patch(
            "data_provider.ssi_fetcher.SsiFetcher", return_value=_StubFetcher("SsiFetcher", 1)
        ), patch(
            "data_provider.yfinance_fetcher.YfinanceFetcher", return_value=_StubFetcher("YfinanceFetcher", 4)
        ), patch.dict(
            sys.modules,
            {
                # vnstock là phụ thuộc tuỳ chọn, không cài trong CI: thay module bằng bản giả.
                # vnstock is an optional dependency; stub the module.
                "data_provider.vnstock_fetcher": SimpleNamespace(
                    VnstockFetcher=MagicMock(return_value=_StubFetcher("VnstockFetcher", 2))
                )
            },
        ):
            mock_vnstock = sys.modules["data_provider.vnstock_fetcher"].VnstockFetcher
            manager = DataFetcherManager()

        self.assertEqual(
            manager.available_fetchers,
            ["VciFetcher", "SsiFetcher", "VnstockFetcher", "YfinanceFetcher"],
        )
        mock_vnstock.assert_called_once()

    @patch("src.config.get_config")
    def test_daily_fallback_tries_ssi_before_yfinance(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace()
        DataFetcherManager.reset_daily_source_health()
        try:
            vci = MagicMock()
            vci.name = "VciFetcher"
            vci.priority = 0
            vci.get_daily_data.side_effect = RuntimeError("temporary failure")

            ssi = MagicMock()
            ssi.name = "SsiFetcher"
            ssi.priority = 1
            ssi.get_daily_data.return_value = _make_daily_df()

            yfinance = MagicMock()
            yfinance.name = "YfinanceFetcher"
            yfinance.priority = 4
            yfinance.get_daily_data.return_value = _make_daily_df()

            manager = DataFetcherManager(fetchers=[yfinance, ssi, vci])
            df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")

            self.assertFalse(df.empty)
            self.assertEqual(source, "SsiFetcher")
            self.assertEqual(manager.available_fetchers, ["VciFetcher", "SsiFetcher", "YfinanceFetcher"])
            vci.get_daily_data.assert_called_once()
            ssi.get_daily_data.assert_called_once()
            yfinance.get_daily_data.assert_not_called()
        finally:
            DataFetcherManager.reset_daily_source_health()

    def test_non_vn_code_is_rejected_before_any_fetcher(self):
        vci = MagicMock()
        vci.name = "VciFetcher"
        vci.priority = 0
        manager = DataFetcherManager(fetchers=[vci])
        for code in ("000001", "AAPL", "HK00700"):
            with self.subTest(code=code), self.assertRaises(DataFetchError):
                manager.get_daily_data(code, start_date="2026-05-01", end_date="2026-05-08")
        vci.get_daily_data.assert_not_called()

    @patch("src.config.get_config")
    def test_daily_source_health_skips_repeatedly_failing_source(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace()
        DataFetcherManager.reset_daily_source_health()
        try:
            flaky = MagicMock()
            flaky.name = "VciFetcher"
            flaky.priority = 0
            flaky.get_daily_data.side_effect = RuntimeError("timeout")

            backup = MagicMock()
            backup.name = "SsiFetcher"
            backup.priority = 1
            backup.get_daily_data.return_value = _make_daily_df()

            manager = DataFetcherManager(fetchers=[flaky, backup])

            for _ in range(3):
                df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")
                self.assertFalse(df.empty)
                self.assertEqual(source, "SsiFetcher")

            flaky.get_daily_data.reset_mock(side_effect=True)
            flaky.get_daily_data.side_effect = RuntimeError("should be skipped")

            df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")

            self.assertFalse(df.empty)
            self.assertEqual(source, "SsiFetcher")
            flaky.get_daily_data.assert_not_called()
        finally:
            DataFetcherManager.reset_daily_source_health()

    @patch("src.config.get_config")
    def test_daily_source_health_does_not_skip_source_after_empty_results(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace()
        DataFetcherManager.reset_daily_source_health()
        try:
            primary = MagicMock()
            primary.name = "VciFetcher"
            primary.priority = 0
            primary.get_daily_data.return_value = pd.DataFrame()

            backup = MagicMock()
            backup.name = "SsiFetcher"
            backup.priority = 1
            backup.get_daily_data.return_value = _make_daily_df()

            manager = DataFetcherManager(fetchers=[primary, backup])

            for _ in range(3):
                df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")
                self.assertFalse(df.empty)
                self.assertEqual(source, "SsiFetcher")

            primary.get_daily_data.reset_mock()
            primary.get_daily_data.return_value = _make_daily_df()

            df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")

            self.assertFalse(df.empty)
            self.assertEqual(source, "VciFetcher")
            primary.get_daily_data.assert_called_once()
        finally:
            DataFetcherManager.reset_daily_source_health()

    @patch("src.config.get_config")
    def test_daily_source_health_does_not_preconsume_half_open_fallback(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace()
        DataFetcherManager.reset_daily_source_health()
        try:
            primary = MagicMock()
            primary.name = "VciFetcher"
            primary.priority = 0
            primary.get_daily_data.return_value = _make_daily_df()

            backup = MagicMock()
            backup.name = "SsiFetcher"
            backup.priority = 1
            backup.get_daily_data.return_value = _make_daily_df()

            manager = DataFetcherManager(fetchers=[primary, backup])
            health_key = DataFetcherManager._daily_health_key(backup, "vn")
            breaker = DataFetcherManager._daily_source_health
            for _ in range(breaker.failure_threshold):
                breaker.record_failure(health_key, error="timeout")
            with breaker._lock:
                breaker._states[health_key]["last_failure_time"] -= breaker.cooldown_seconds + 1

            df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")

            self.assertFalse(df.empty)
            self.assertEqual(source, "VciFetcher")
            backup.get_daily_data.assert_not_called()

            primary.get_daily_data.reset_mock()
            primary.get_daily_data.side_effect = RuntimeError("primary down")

            df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")

            self.assertFalse(df.empty)
            self.assertEqual(source, "SsiFetcher")
            backup.get_daily_data.assert_called_once()
        finally:
            DataFetcherManager.reset_daily_source_health()

    @patch("src.config.get_config")
    def test_daily_source_health_releases_half_open_probe_after_empty_result(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace()
        DataFetcherManager.reset_daily_source_health()
        try:
            primary = MagicMock()
            primary.name = "VciFetcher"
            primary.priority = 0
            primary.get_daily_data.side_effect = RuntimeError("primary down")

            half_open = MagicMock()
            half_open.name = "SsiFetcher"
            half_open.priority = 1
            half_open.get_daily_data.return_value = pd.DataFrame()

            backup = MagicMock()
            backup.name = "YfinanceFetcher"
            backup.priority = 2
            backup.get_daily_data.return_value = _make_daily_df()

            manager = DataFetcherManager(fetchers=[primary, half_open, backup])
            health_key = DataFetcherManager._daily_health_key(half_open, "vn")
            breaker = DataFetcherManager._daily_source_health
            for _ in range(breaker.failure_threshold):
                breaker.record_failure(health_key, error="timeout")
            with breaker._lock:
                breaker._states[health_key]["last_failure_time"] -= breaker.cooldown_seconds + 1

            df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")

            self.assertFalse(df.empty)
            self.assertEqual(source, "YfinanceFetcher")
            half_open.get_daily_data.assert_called_once()

            half_open.get_daily_data.reset_mock()
            half_open.get_daily_data.return_value = _make_daily_df()

            df, source = manager.get_daily_data("HPG", start_date="2026-05-01", end_date="2026-05-08")

            self.assertFalse(df.empty)
            self.assertEqual(source, "SsiFetcher")
            half_open.get_daily_data.assert_called_once()
        finally:
            DataFetcherManager.reset_daily_source_health()


if __name__ == "__main__":
    unittest.main()
