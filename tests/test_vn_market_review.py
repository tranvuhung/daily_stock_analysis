# -*- coding: utf-8 -*-
"""Điểm lại thị trường Việt Nam (region ``vn``).

Mock chỉ đặt ở biên HTTP (``requests.Session``) cho fetcher, để chuẩn hoá dữ
liệu và định tuyến chạy đúng code thật. Phần analyzer/endpoint mock ở ranh
giới LLM/task queue.
"""

import os
import unittest
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from data_provider import ssi_fetcher as ssi_module
from data_provider import vci_fetcher as vci_module
from data_provider.base import BaseFetcher, DataFetcherManager
from data_provider.ssi_fetcher import SsiFetcher
from data_provider.vci_fetcher import VciFetcher
from src.core import markets
from src.core import market_review as market_review_module
from src.core import trading_calendar
from src.market_analyzer import MarketAnalyzer, MarketOverview


@contextmanager
def supported_markets(value):
    with patch.dict(os.environ, {"SUPPORTED_MARKETS": value}, clear=False):
        markets.reset_cache()
        try:
            yield
        finally:
            markets.reset_cache()


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _bars(symbol, closes, value=20_000_000.0):
    # Ba nến ngày liên tiếp; accumulatedValue có đơn vị triệu đồng.
    return [
        {
            "symbol": symbol,
            "o": [c - 5 for c in closes],
            "h": [c + 10 for c in closes],
            "l": [c - 10 for c in closes],
            "c": closes,
            "v": [500_000_000, 520_000_000, 540_000_000],
            "t": ["1789430400", "1789516800", "1789603200"],
            "accumulatedValue": [value, value, value],
        }
    ]


class VciMainIndicesTestCase(unittest.TestCase):
    def setUp(self):
        vci_module.reset_symbols_cache()
        self.fetcher = VciFetcher(min_interval=0.0)

    def test_non_vn_region_returns_none(self):
        with patch.object(self.fetcher._session, "post") as post:
            self.assertIsNone(self.fetcher.get_main_indices(region="cn"))
        post.assert_not_called()

    def test_returns_all_keys_required_by_market_analyzer(self):
        def fake_post(url, json=None, **_):
            symbol = json["symbols"][0]
            return _response(_bars(symbol, [1600.0, 1610.0, 1626.1]))

        with patch.object(self.fetcher._session, "post", side_effect=fake_post):
            indices = self.fetcher.get_main_indices(region="vn")

        self.assertEqual(
            [item["code"] for item in indices],
            ["VNINDEX", "VN30", "HNXIndex", "HNXUpcomIndex"],
        )
        required = {
            "code", "name", "current", "change", "change_pct", "open", "high",
            "low", "prev_close", "volume", "amount", "amplitude",
        }
        vnindex = indices[0]
        self.assertTrue(required.issubset(vnindex))
        self.assertEqual(vnindex["name"], "VN-Index")
        self.assertAlmostEqual(vnindex["current"], 1626.1)
        self.assertAlmostEqual(vnindex["prev_close"], 1610.0)
        self.assertAlmostEqual(vnindex["change"], 16.1)
        self.assertAlmostEqual(vnindex["change_pct"], 1.0)
        # Giá trị giao dịch quy về VND (triệu đồng × 1e6)
        self.assertAlmostEqual(vnindex["amount"], 20_000_000.0 * 1_000_000)

    def test_one_failing_index_is_skipped(self):
        def fake_post(url, json=None, **_):
            symbol = json["symbols"][0]
            if symbol == "VN30":
                return _response({"error": True, "message": "boom"})
            return _response(_bars(symbol, [100.0, 101.0, 102.0]))

        with patch.object(self.fetcher._session, "post", side_effect=fake_post):
            indices = self.fetcher.get_main_indices(region="vn")

        self.assertEqual(
            [item["code"] for item in indices],
            ["VNINDEX", "HNXIndex", "HNXUpcomIndex"],
        )


_BASE_RECORD = {
    "exchange": "hose",
    "matchedPrice": 20000,
    "priceChange": 0,
    "priceChangePercent": 0,
    "ceiling": 21400,
    "floor": 18600,
    "nmTotalTradedValue": 1_000_000_000,
}


class SsiRegionMarketStatsTestCase(unittest.TestCase):
    def setUp(self):
        ssi_module.reset_snapshot_cache()
        self.fetcher = SsiFetcher()
        self.fetcher.min_interval = 0.0

    def _load(self, records):
        def fake_get(url, **_):
            exchange = url.rsplit("/", 1)[-1]
            return _response(
                {"code": "SUCCESS", "data": [r for r in records if r["exchange"] == exchange]}
            )

        with patch.object(self.fetcher._session, "get", side_effect=fake_get):
            return self.fetcher.get_region_market_stats("vn")

    def test_non_vn_region_returns_none(self):
        self.assertIsNone(self.fetcher.get_region_market_stats("cn"))

    def test_counts_breadth_ceiling_floor_and_turnover(self):
        records = [
            dict(_BASE_RECORD, stockSymbol="HPG", priceChange=100),
            dict(_BASE_RECORD, stockSymbol="FPT", matchedPrice=21400, priceChange=1400),
            dict(_BASE_RECORD, stockSymbol="VIC", priceChange=-200),
            dict(_BASE_RECORD, stockSymbol="SHS", exchange="hnx", matchedPrice=18600, priceChange=-1400),
            dict(_BASE_RECORD, stockSymbol="BSR", exchange="upcom"),
            # Chưa khớp lệnh: bỏ qua
            dict(_BASE_RECORD, stockSymbol="ABC", exchange="upcom", matchedPrice=0),
            # Chứng quyền (mã 8 ký tự): bỏ qua
            dict(_BASE_RECORD, stockSymbol="CHPG2401", priceChange=50),
        ]

        stats = self._load(records)

        self.assertEqual(stats["up_count"], 2)
        self.assertEqual(stats["down_count"], 2)
        self.assertEqual(stats["flat_count"], 1)
        self.assertEqual(stats["limit_up_count"], 1)
        self.assertEqual(stats["limit_down_count"], 1)
        # 5 mã được tính × 1 tỷ đồng = 5 tỷ (ABC chưa khớp, chứng quyền bị loại)
        self.assertAlmostEqual(stats["total_amount"], 5.0)

    def test_nothing_traded_returns_none(self):
        stats = self._load([dict(_BASE_RECORD, stockSymbol="HPG", matchedPrice=0)])
        self.assertIsNone(stats)


class _FakeCnFetcher(BaseFetcher):
    name = "FakeCnFetcher"
    priority = 0

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, *args, **kwargs):  # pragma: no cover - không dùng
        raise NotImplementedError

    def _normalize_data(self, *args, **kwargs):  # pragma: no cover - không dùng
        raise NotImplementedError

    def get_market_stats(self):
        self.calls += 1
        return {"up_count": 999, "down_count": 1, "total_amount": 12345.0}


class _FakeVnFetcher(_FakeCnFetcher):
    name = "FakeVnFetcher"
    priority = 1

    def get_region_market_stats(self, region):
        if region != "vn":
            return None
        return {"up_count": 200, "down_count": 150, "total_amount": 21000.0}


class MarketStatsRoutingTestCase(unittest.TestCase):
    def test_vn_region_never_uses_a_share_fetchers(self):
        cn = _FakeCnFetcher()
        vn = _FakeVnFetcher()
        manager = DataFetcherManager(fetchers=[cn, vn])

        with patch.object(manager, "_get_tickflow_fetcher", return_value=None):
            stats = manager.get_market_stats(purpose="test", region="vn")

        self.assertEqual(stats["up_count"], 200)
        self.assertEqual(cn.calls, 0)
        self.assertEqual(vn.calls, 0)

    def test_default_region_keeps_cn_behaviour(self):
        cn = _FakeCnFetcher()
        manager = DataFetcherManager(fetchers=[cn])

        with patch.object(manager, "_get_tickflow_fetcher", return_value=None):
            stats = manager.get_market_stats(purpose="test")

        self.assertEqual(stats["up_count"], 999)
        self.assertEqual(cn.calls, 1)

    def test_vn_region_without_provider_returns_empty(self):
        manager = DataFetcherManager(fetchers=[_FakeCnFetcher()])
        self.assertEqual(manager.get_market_stats(purpose="test", region="vn"), {})


def _make_vn_analyzer(report_language="vi"):
    config = SimpleNamespace(report_language=report_language, market_review_color_scheme="green_up")
    with patch("src.market_analyzer.DataFetcherManager"):
        return MarketAnalyzer(region="vn", config=config)


class VnMarketAnalyzerTestCase(unittest.TestCase):
    def test_vn_region_is_not_coerced_to_cn(self):
        analyzer = _make_vn_analyzer()
        self.assertEqual(analyzer.region, "vn")
        self.assertEqual(analyzer.profile.region, "vn")
        self.assertEqual(analyzer.profile.mood_index_code, "VNINDEX")
        self.assertTrue(analyzer.profile.has_market_stats)
        self.assertFalse(analyzer.profile.has_sector_rankings)
        self.assertEqual(analyzer.strategy.region, "vn")

    def test_market_stats_request_carries_vn_region(self):
        analyzer = _make_vn_analyzer()
        analyzer.data_manager = MagicMock()
        analyzer.data_manager.get_market_stats.return_value = {
            "up_count": 200,
            "down_count": 150,
            "flat_count": 50,
            "limit_up_count": 5,
            "limit_down_count": 2,
            "total_amount": 21000.0,
        }
        overview = MarketOverview(date="2026-09-24")

        analyzer._get_market_statistics(overview)

        analyzer.data_manager.get_market_stats.assert_called_once_with(
            purpose="market_review:vn", region="vn"
        )
        self.assertEqual(overview.total_amount, 21000.0)

    def test_vi_prompt_asks_for_vietnamese_output_with_vn_data(self):
        analyzer = _make_vn_analyzer("vi")
        overview = MarketOverview(date="2026-09-24", up_count=200, down_count=150, total_amount=21000.0)

        prompt = analyzer._build_review_prompt(overview, [])

        self.assertIn("Vietnamese (Tiếng Việt)", prompt)
        self.assertIn("Vietnam market", prompt)
        self.assertIn("Vietnam Market Recap", prompt)
        self.assertIn("VN-Index", prompt)
        self.assertIn("Turnover: 21000 (VND bn)", prompt)
        self.assertIn("Sector/theme ranking data is not available", prompt)

    def test_en_prompt_keeps_english_output(self):
        analyzer = _make_vn_analyzer("en")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-09-24"), [])
        self.assertIn("must be in English", prompt)

    def test_market_news_search_uses_vn_context(self):
        analyzer = _make_vn_analyzer()
        analyzer.search_service = MagicMock()
        analyzer.search_service.search_stock_news.return_value = SimpleNamespace(results=[])

        analyzer.search_market_news()

        kwargs = analyzer.search_service.search_stock_news.call_args.kwargs
        self.assertEqual(kwargs["stock_name"], "VNINDEX")


class VnRunMarketReviewTestCase(unittest.TestCase):
    def test_vietnam_only_deployment_produces_report(self):
        notifier = MagicMock()
        notifier.save_report_to_file.return_value = "/tmp/market_review.md"
        vn_analyzer = MagicMock()
        vn_analyzer.run_daily_review_with_snapshot.return_value = SimpleNamespace(
            report="VN body",
            market_light_snapshot=None,
        )
        config = SimpleNamespace(report_language="vi", market_review_region="")

        with supported_markets("vn"), patch.object(
            market_review_module, "MarketAnalyzer", return_value=vn_analyzer
        ) as analyzer_cls, patch.object(market_review_module, "_persist_market_review_history"):
            result = market_review_module.run_market_review(
                notifier, config=config, send_notification=False
            )

        self.assertEqual(result, "VN body")
        self.assertEqual(analyzer_cls.call_args.kwargs["region"], "vn")

    def test_vietnamese_wrapper_titles(self):
        text = market_review_module._get_market_review_text("vi")
        self.assertEqual(text["vn_title"], "# Điểm lại thị trường Việt Nam")
        self.assertIn("Điểm lại thị trường", text["root_title"])


class VnTriggerEndpointTestCase(unittest.TestCase):
    def setUp(self):
        try:
            from api.v1.endpoints import analysis as endpoint_module
        except Exception as exc:  # pragma: no cover - môi trường thiếu phụ thuộc API
            self.skipTest(f"analysis endpoint unavailable: {exc}")
        self.endpoint = endpoint_module

    def _trigger(self, market_review_region):
        task_queue = MagicMock()
        task_queue.submit_background_task.return_value = SimpleNamespace(task_id="task-vn")
        config = SimpleNamespace(
            trading_day_check_enabled=False,
            report_language="vi",
            market_review_region=market_review_region,
        )
        with patch.object(
            self.endpoint, "_try_acquire_market_review_lock", return_value=object()
        ), patch.object(self.endpoint, "_release_market_review_lock"), patch(
            "api.v1.endpoints.analysis.get_task_queue", return_value=task_queue
        ):
            response = self.endpoint.trigger_market_review(
                request=self.endpoint.MarketReviewRequest(send_notification=False),
                config=config,
            )
        return response, task_queue

    def test_vietnam_only_defaults_to_vn_instead_of_cn(self):
        with supported_markets("vn"):
            response, task_queue = self._trigger("")

        self.assertEqual(response.region, "vn")
        self.assertEqual(task_queue.submit_background_task.call_args.kwargs["region"], "vn")

    def test_no_review_capable_market_is_rejected_before_submit(self):
        from fastapi import HTTPException

        with supported_markets("tw"):
            with self.assertRaises(HTTPException) as ctx:
                self._trigger("")

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail["error"], "market_review_unsupported")


class VnTradingCalendarTestCase(unittest.TestCase):
    def test_vn_is_closed_on_weekends(self):
        if not trading_calendar._XCALS_AVAILABLE:
            self.skipTest("exchange-calendars unavailable")
        self.assertTrue(trading_calendar.is_market_open("vn", date(2026, 9, 25)))  # thứ 6
        self.assertFalse(trading_calendar.is_market_open("vn", date(2026, 9, 26)))  # thứ 7
        self.assertFalse(trading_calendar.is_market_open("vn", date(2026, 9, 27)))  # CN

    def test_unknown_market_stays_fail_open(self):
        if not trading_calendar._XCALS_AVAILABLE:
            self.skipTest("exchange-calendars unavailable")
        self.assertTrue(trading_calendar.is_market_open("atlantis", date(2026, 9, 26)))


if __name__ == "__main__":
    unittest.main()
