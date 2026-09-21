# -*- coding: utf-8 -*-
"""SsiFetcher：越南全市场实时快照与日线兜底。

重点覆盖两件事：
1. 全市场快照的合并与部分失败降级
2. 图表接口"千盾"与快照/VCI"盾"之间的单位换算 —— 这是本次接入里最容易
   静默出错的地方（差 1000 倍且不会抛异常）
"""

import unittest
from unittest.mock import MagicMock, patch

from data_provider import ssi_fetcher as module
from data_provider.base import DataFetchError
from data_provider.ssi_fetcher import SsiFetcher

# 取自真实响应（HPG 于 hose）
_HPG = {
    "stockSymbol": "HPG",
    "exchange": "hose",
    "companyNameVi": "Công ty Cổ phần Tập đoàn Hòa Phát",
    "companyNameEn": "Hoa Phat Group Joint Stock Company",
    "tradingCurrencyISOCode": "VND",
    "matchedPrice": 20900,
    "priceChange": -50,
    "priceChangePercent": -0.24,
    "refPrice": 20950,
    "priorClosePrice": 20950,
    "openPrice": 20800,
    "highest": 20900,
    "lowest": 20750,
    "ceiling": 22400,
    "floor": 19500,
    "nmTotalTradedQty": 1019700,
    "nmTotalTradedValue": 21218970000,
}
_FPT = dict(_HPG, stockSymbol="FPT", companyNameVi="Công ty Cổ phần FPT")


def _snapshot_response(records):
    response = MagicMock()
    response.json.return_value = {"code": "SUCCESS", "data": records}
    response.raise_for_status.return_value = None
    return response


def _chart_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class SsiSnapshotTestCase(unittest.TestCase):
    def setUp(self):
        module.reset_snapshot_cache()
        self.fetcher = SsiFetcher()
        self.fetcher.min_interval = 0.0

    def test_snapshot_merges_all_three_exchanges(self):
        responses = {
            "hose": _snapshot_response([_HPG]),
            "hnx": _snapshot_response([dict(_HPG, stockSymbol="SHS")]),
            "upcom": _snapshot_response([dict(_HPG, stockSymbol="BSR")]),
        }

        def fake_get(url, **_):
            return responses[url.rsplit("/", 1)[-1]]

        with patch.object(self.fetcher._session, "get", side_effect=fake_get):
            snapshot = self.fetcher._load_snapshot(force=True)

        self.assertEqual(set(snapshot), {"HPG", "SHS", "BSR"})

    def test_one_exchange_failing_does_not_block_the_others(self):
        # 三个板块是独立端点，UPCOM 抖动不应让 HOSE 行情一起不可用
        def fake_get(url, **_):
            if url.endswith("upcom"):
                raise RuntimeError("upcom down")
            return _snapshot_response([_HPG])

        with patch.object(self.fetcher._session, "get", side_effect=fake_get):
            snapshot = self.fetcher._load_snapshot(force=True)

        self.assertIn("HPG", snapshot)

    def test_all_exchanges_failing_raises(self):
        with patch.object(
            self.fetcher._session, "get", side_effect=RuntimeError("down")
        ):
            with self.assertRaises(DataFetchError):
                self.fetcher._load_snapshot(force=True)

    def test_snapshot_is_cached_between_calls(self):
        with patch.object(
            self.fetcher._session, "get", return_value=_snapshot_response([_HPG])
        ) as get:
            self.fetcher._load_snapshot(force=True)
            self.fetcher._load_snapshot()
        self.assertEqual(get.call_count, 3, "第二次应命中缓存，只有首次拉取三个板块")

    def test_prefetch_reports_coverage_of_requested_codes(self):
        with patch.object(
            self.fetcher._session, "get", return_value=_snapshot_response([_HPG, _FPT])
        ):
            covered = self.fetcher.prefetch_realtime_quotes(["HPG", "FPT", "ZZZ"])
        self.assertEqual(covered, 2)

    def test_prefetch_failure_returns_zero_instead_of_raising(self):
        with patch.object(
            self.fetcher._session, "get", side_effect=RuntimeError("down")
        ):
            self.assertEqual(self.fetcher.prefetch_realtime_quotes(["HPG"]), 0)


class SsiRealtimeQuoteTestCase(unittest.TestCase):
    def setUp(self):
        module.reset_snapshot_cache()
        self.fetcher = SsiFetcher()
        self.fetcher.min_interval = 0.0

    def _quote(self, code="HPG"):
        with patch.object(
            self.fetcher._session, "get", return_value=_snapshot_response([_HPG])
        ):
            return self.fetcher.get_realtime_quote(code)

    def test_maps_matched_price_not_auction_price(self):
        # 记录里同时有 matchedPrice 和 expectedMatchedPrice（集合竞价用），
        # 实时价必须取前者
        quote = self._quote()
        self.assertEqual(quote.price, 20900)
        self.assertEqual(quote.change_pct, -0.24)
        self.assertEqual(quote.change_amount, -50)

    def test_maps_volume_and_amount(self):
        quote = self._quote()
        self.assertEqual(quote.volume, 1019700)
        self.assertEqual(quote.amount, 21218970000)

    def test_market_and_currency_are_vietnamese(self):
        quote = self._quote()
        self.assertEqual(quote.market, "vn")
        self.assertEqual(quote.currency, "VND")
        self.assertEqual(quote.name, "Công ty Cổ phần Tập đoàn Hòa Phát")

    def test_amplitude_is_derived_from_reference_price(self):
        quote = self._quote()
        expected = round((20900 - 20750) / 20950 * 100, 2)
        self.assertEqual(quote.amplitude, expected)

    def test_ceiling_and_floor_are_attached(self):
        # 涨跌停价是越南市场常用风控参考，UnifiedRealtimeQuote 没有对应字段
        quote = self._quote()
        self.assertEqual(getattr(quote, "vn_ceiling"), 22400)
        self.assertEqual(getattr(quote, "vn_floor"), 19500)
        self.assertEqual(getattr(quote, "vn_exchange"), "hose")

    def test_unknown_symbol_returns_none(self):
        self.assertIsNone(self._quote("ZZZ"))

    def test_dot_vn_suffix_resolves_to_same_record(self):
        self.assertIsNotNone(self._quote("HPG.VN"))


class SsiDailyUnitTestCase(unittest.TestCase):
    """图表接口的价格单位是千盾，必须换算成盾。"""

    def setUp(self):
        module.reset_snapshot_cache()
        self.fetcher = SsiFetcher()
        self.fetcher.min_interval = 0.0

    def test_chart_prices_are_scaled_to_vnd(self):
        payload = {
            "code": "SUCCESS",
            "data": {
                "t": [1789516800, 1789603200],
                "o": [21.2, 20.8],
                "h": [21.25, 21.2],
                "l": [20.95, 20.75],
                "c": [20.95, 21.1],
                "v": [12277500, 1943100],
            },
        }
        with patch.object(
            self.fetcher._session, "get", return_value=_chart_response(payload)
        ):
            df = self.fetcher.get_daily_data(
                "HPG", start_date="2026-09-01", end_date="2026-09-17"
            )

        # 与 VciFetcher 的同日收盘价一致（盾），而不是 20.95
        self.assertEqual(list(df["close"]), [20950.0, 21100.0])
        self.assertEqual(list(df["open"]), [21200.0, 20800.0])

    def test_error_envelope_raises_so_manager_can_continue(self):
        payload = {"code": "ERR", "message": "boom"}
        with patch.object(
            self.fetcher._session, "get", return_value=_chart_response(payload)
        ):
            with self.assertRaises(DataFetchError):
                self.fetcher.get_daily_data(
                    "HPG", start_date="2026-09-01", end_date="2026-09-17"
                )

    def test_empty_series_raises(self):
        payload = {"code": "SUCCESS", "data": {"t": []}}
        with patch.object(
            self.fetcher._session, "get", return_value=_chart_response(payload)
        ):
            with self.assertRaises(DataFetchError):
                self.fetcher.get_daily_data(
                    "HPG", start_date="2026-09-01", end_date="2026-09-17"
                )


if __name__ == "__main__":
    unittest.main()
