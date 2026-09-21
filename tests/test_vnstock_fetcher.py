# -*- coding: utf-8 -*-
"""VnstockFetcher：可选的 vnstock 补充源。

``vnstock`` 不在 requirements.txt 里（许可证为 Custom/Personal Use，且引入
遥测依赖 vnai），所以这里用 sys.modules 桩件注入，做法与仓库既有的
``tests/litellm_stub.py`` 一致。
"""

import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd


def _install_vnstock_stub(history_frame):
    """注入一个最小的 vnstock 桩件，返回 (module, 记录调用的 list)。"""

    calls = []

    class _Quote:
        def __init__(self, symbol="", source="VCI", **kwargs):
            self.symbol = symbol
            self.source = source

        def history(self, start=None, end=None, interval="1D", **kwargs):
            calls.append(
                {
                    "symbol": self.symbol,
                    "source": self.source,
                    "start": start,
                    "end": end,
                    "interval": interval,
                }
            )
            if isinstance(history_frame, Exception):
                raise history_frame
            return history_frame

    module = types.ModuleType("vnstock")
    module.Quote = _Quote
    return module, calls


# 真实 vnstock 4.0.8 (source=VCI) 的返回形状：列名为 time/open/high/low/close/volume，
# **价格单位是千越南盾**（已实测：HPG 报 21.20 而不是 21200）。
_REAL_SHAPE = pd.DataFrame(
    {
        "time": pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-17"]),
        "open": [21.0, 21.2, 20.8],
        "high": [21.20, 21.25, 21.35],
        "low": [21.00, 20.95, 20.75],
        "close": [21.20, 20.95, 21.35],
        "volume": [11937200, 12277500, 5113100],
    }
)


class VnstockFetcherTestCase(unittest.TestCase):
    def _load_fetcher(self, frame=_REAL_SHAPE):
        stub, calls = _install_vnstock_stub(frame)
        patcher = patch.dict(sys.modules, {"vnstock": stub})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("data_provider.vnstock_fetcher", None)
        self.addCleanup(sys.modules.pop, "data_provider.vnstock_fetcher", None)
        from data_provider.vnstock_fetcher import VnstockFetcher

        return VnstockFetcher(), calls

    def test_prices_are_scaled_from_thousand_vnd_to_vnd(self):
        # 这是接入 vnstock 最容易静默出错的点：不换算的话均线、止损价会差 1000 倍
        fetcher, _ = self._load_fetcher()
        df = fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")

        self.assertEqual(list(df["close"]), [21200.0, 20950.0, 21350.0])
        self.assertEqual(list(df["open"]), [21000.0, 21200.0, 20800.0])
        self.assertEqual(list(df["high"]), [21200.0, 21250.0, 21350.0])
        self.assertEqual(list(df["low"]), [21000.0, 20950.0, 20750.0])

    def test_volume_is_not_scaled(self):
        fetcher, _ = self._load_fetcher()
        df = fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")
        self.assertEqual(list(df["volume"]), [11937200, 12277500, 5113100])

    def test_amount_is_approximated_in_vnd(self):
        fetcher, _ = self._load_fetcher()
        df = fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")
        expected = (21200.0 + 21000.0) / 2 * 11937200
        self.assertAlmostEqual(df["amount"].iloc[0], expected, places=2)

    def test_dot_vn_suffix_is_stripped_before_calling_vnstock(self):
        fetcher, calls = self._load_fetcher()
        fetcher.get_daily_data("HPG.VN", start_date="2026-09-01", end_date="2026-09-17")
        self.assertEqual(calls[-1]["symbol"], "HPG")
        self.assertEqual(calls[-1]["interval"], "1D")

    def test_upstream_error_is_wrapped_as_data_fetch_error(self):
        from data_provider.base import DataFetchError

        fetcher, _ = self._load_fetcher(RuntimeError("upstream down"))
        with self.assertRaises(DataFetchError):
            fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")

    def test_alternative_date_column_name_is_accepted(self):
        frame = _REAL_SHAPE.rename(columns={"time": "date"})
        fetcher, _ = self._load_fetcher(frame)
        df = fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")
        self.assertEqual(len(df), 3)


class VnstockRegistrationTestCase(unittest.TestCase):
    def test_declared_as_vietnam_only_source(self):
        from data_provider.base import DataFetcherManager

        self.assertEqual(
            DataFetcherManager._DAILY_MARKET_FETCHER_SUPPORT["VnstockFetcher"], {"vn"}
        )


if __name__ == "__main__":
    unittest.main()
