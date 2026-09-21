# -*- coding: utf-8 -*-
"""VciFetcher：越南日线与代码表。

Mock 只打在 HTTP 边界（``requests.Session``），归一化、清洗和指标计算都跑真实
代码路径，避免测试绕过真正有风险的那一层。
"""

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from data_provider import vci_fetcher as module
from data_provider.vci_fetcher import VciFetcher

# 取自真实接口响应（HPG，2026-09-15 ~ 2026-09-17）。
# t 是 epoch 秒的字符串，accumulatedValue 的单位是百万越南盾。
_REAL_PAYLOAD = [
    {
        "symbol": "HPG",
        "o": [21000, 21200, 20800],
        "h": [21200, 21250, 21200],
        "l": [21000, 20950, 20750],
        "c": [21200, 20950, 21100],
        "v": [11937200, 12277500, 1943100],
        "t": ["1789430400", "1789516800", "1789603200"],
        "accumulatedVolume": [11937200, 12277500, 1943100],
        "accumulatedValue": [251826.29, 258430.505, 40646.78],
    }
]


def _response(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class VciDailyDataTestCase(unittest.TestCase):
    def setUp(self):
        module.reset_symbols_cache()
        self.fetcher = VciFetcher(min_interval=0.0)

    def test_normalizes_parallel_arrays_into_standard_columns(self):
        with patch.object(
            self.fetcher._session, "post", return_value=_response(_REAL_PAYLOAD)
        ):
            df = self.fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")

        self.assertEqual(len(df), 3)
        self.assertEqual(list(df["close"]), [21200, 20950, 21100])
        self.assertEqual(
            [str(value.date()) for value in df["date"]],
            ["2026-09-15", "2026-09-16", "2026-09-17"],
        )

    def test_amount_is_converted_from_million_vnd_to_vnd(self):
        # accumulatedValue 是百万盾；不换算的话 amount 会比真实成交额小 100 万倍
        with patch.object(
            self.fetcher._session, "post", return_value=_response(_REAL_PAYLOAD)
        ):
            df = self.fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")

        self.assertAlmostEqual(df["amount"].iloc[0], 251826.29 * 1_000_000, places=2)
        # 成交额量级应当与 成交量 × 价格 相符（同一数量级内）
        implied = df["volume"].iloc[0] * df["close"].iloc[0]
        self.assertLess(abs(df["amount"].iloc[0] - implied) / implied, 0.1)

    def test_empty_series_raises_so_manager_can_fall_back(self):
        from data_provider.base import DataFetchError

        with patch.object(self.fetcher._session, "post", return_value=_response([])):
            with self.assertRaises(DataFetchError):
                self.fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")

    def test_error_envelope_is_surfaced(self):
        from data_provider.base import DataFetchError

        payload = {"error": True, "message": "Invalid params", "code": 400}
        with patch.object(self.fetcher._session, "post", return_value=_response(payload)):
            with self.assertRaises(DataFetchError):
                self.fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")

    def test_request_asks_for_a_single_symbol_only(self):
        # 已实测：传多个 symbol 时接口返回 []，所以每次只能查一个
        with patch.object(
            self.fetcher._session, "post", return_value=_response(_REAL_PAYLOAD)
        ) as post:
            self.fetcher.get_daily_data("HPG", start_date="2026-09-01", end_date="2026-09-17")

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["symbols"], ["HPG"])
        self.assertEqual(payload["timeFrame"], "ONE_DAY")
        self.assertGreater(payload["countBack"], 0)

    def test_start_date_trims_rows_returned_by_count_back(self):
        # countBack 只能"往回数 N 根"，无法表达起始日，因此要在客户端裁剪
        with patch.object(
            self.fetcher._session, "post", return_value=_response(_REAL_PAYLOAD)
        ):
            df = self.fetcher.get_daily_data(
                "HPG", start_date="2026-09-16", end_date="2026-09-17"
            )
        self.assertEqual(len(df), 2)
        self.assertEqual(str(df["date"].iloc[0].date()), "2026-09-16")


class VciSymbolTestCase(unittest.TestCase):
    def setUp(self):
        module.reset_symbols_cache()
        self.fetcher = VciFetcher(min_interval=0.0)

    def test_index_symbols_keep_provider_casing(self):
        # 已实测：HNXINDEX 返回空，HNXIndex 才有数据
        self.assertEqual(VciFetcher.to_provider_symbol("hnxindex"), "HNXIndex")
        self.assertEqual(VciFetcher.to_provider_symbol("HNXINDEX"), "HNXIndex")
        self.assertEqual(VciFetcher.to_provider_symbol("upcom"), "HNXUpcomIndex")
        self.assertEqual(VciFetcher.to_provider_symbol("vnindex"), "VNINDEX")

    def test_dot_vn_suffix_is_stripped(self):
        self.assertEqual(VciFetcher.to_provider_symbol("HPG.VN"), "HPG")
        self.assertEqual(VciFetcher.to_provider_symbol("hpg"), "HPG")

    def test_stock_name_prefers_vietnamese_short_name(self):
        records = [
            {
                "symbol": "HPG",
                "board": "HSX",
                "organShortName": "Hòa Phát",
                "organName": "Công ty Cổ phần Tập đoàn Hòa Phát",
                "enOrganName": "Hoa Phat Group Joint Stock Company",
            }
        ]
        with patch.object(self.fetcher._session, "get", return_value=_response(records)):
            self.assertEqual(self.fetcher.get_stock_name("HPG"), "Hòa Phát")

    def test_symbol_table_is_cached_across_calls(self):
        records = [{"symbol": "HPG", "organShortName": "Hòa Phát"}]
        with patch.object(
            self.fetcher._session, "get", return_value=_response(records)
        ) as get:
            self.fetcher.get_stock_name("HPG")
            self.fetcher.get_stock_name("HPG")
        self.assertEqual(get.call_count, 1)

    def test_unknown_symbol_returns_none_instead_of_raising(self):
        with patch.object(self.fetcher._session, "get", return_value=_response([])):
            self.assertIsNone(self.fetcher.get_stock_name("ZZZ"))

    def test_symbol_table_failure_is_fail_open(self):
        with patch.object(
            self.fetcher._session, "get", side_effect=RuntimeError("upstream down")
        ):
            self.assertIsNone(self.fetcher.get_stock_name("HPG"))
            self.assertEqual(self.fetcher.get_symbol_records(), [])

    def test_index_name_does_not_require_symbol_table(self):
        with patch.object(
            self.fetcher._session, "get", side_effect=AssertionError("不应发起请求")
        ):
            self.assertEqual(self.fetcher.get_stock_name("VNINDEX"), "VNINDEX")


class VciIndexRealtimeTestCase(unittest.TestCase):
    """指数实时行情由最新日线推导。

    SSI 的全市场快照按交易所返回成分股，不含 VNINDEX / VN30，因此指数在实时
    链路上没有来源，``/stocks/{code}/quote`` 会 404。这里覆盖 VCI 的兜底。
    """

    def setUp(self):
        module.reset_symbols_cache()
        self.fetcher = VciFetcher(min_interval=0.0)

    def _index_payload(self):
        return [
            {
                "symbol": "VNINDEX",
                "o": [1810.0, 1822.0],
                "h": [1816.0, 1840.0],
                "l": [1805.0, 1820.0],
                "c": [1822.0, 1838.69],
                "v": [500000000, 520000000],
                "t": ["1789516800", "1789603200"],
                "accumulatedValue": [20000.0, 21000.0],
            }
        ]

    def test_index_quote_is_derived_from_latest_daily_bar(self):
        with patch.object(
            self.fetcher._session, "post", return_value=_response(self._index_payload())
        ):
            quote = self.fetcher.get_realtime_quote("VNINDEX")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "VNINDEX")
        self.assertEqual(quote.market, "vn")
        self.assertEqual(quote.price, 1838.69)
        self.assertEqual(quote.pre_close, 1822.0)
        self.assertAlmostEqual(quote.change_pct, 0.92, places=2)
        # 由日线推导，必须带上该日线的时间戳，交给上层判定 is_stale，
        # 不能把隔夜数据当成实时行情。
        self.assertTrue(quote.provider_timestamp)

    def test_index_alias_is_accepted(self):
        with patch.object(
            self.fetcher._session, "post", return_value=_response(self._index_payload())
        ):
            self.assertIsNotNone(self.fetcher.get_realtime_quote("vnindex"))

    def test_non_index_codes_are_left_to_ssi(self):
        with patch.object(
            self.fetcher._session, "post", side_effect=AssertionError("不应发起请求")
        ):
            self.assertIsNone(self.fetcher.get_realtime_quote("HPG"))

    def test_empty_series_returns_none_instead_of_raising(self):
        with patch.object(self.fetcher._session, "post", return_value=_response([])):
            self.assertIsNone(self.fetcher.get_realtime_quote("VNINDEX"))


class VciNormalizeEdgeCaseTestCase(unittest.TestCase):
    def test_empty_frame_returns_standard_columns(self):
        from data_provider.base import STANDARD_COLUMNS

        fetcher = VciFetcher(min_interval=0.0)
        out = fetcher._normalize_data(pd.DataFrame(), "HPG")
        self.assertTrue(out.empty)
        self.assertEqual(list(out.columns), STANDARD_COLUMNS)


if __name__ == "__main__":
    unittest.main()
