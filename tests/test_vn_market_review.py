# -*- coding: utf-8 -*-
"""Điểm lại thị trường Việt Nam (region ``vn``).

Mock chỉ đặt ở biên HTTP (``requests.Session``) cho fetcher, để chuẩn hoá dữ
liệu và định tuyến chạy đúng code thật. Phần analyzer/endpoint mock ở ranh
giới LLM/task queue.
"""

import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
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


def _recent_day_epochs(count):
    # Timestamp tính lùi từ hôm nay: timestamp cố định sẽ rơi ra ngoài khoảng
    # ngày mà get_daily_data lọc, khiến test hỏng theo thời gian.
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return [str(int((today - timedelta(days=count - 1 - i)).timestamp())) for i in range(count)]


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
            "t": _recent_day_epochs(3),
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
            ["VNINDEX", "VN30", "HNXIndex", "UPCOMINDEX"],
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
            ["VNINDEX", "HNXIndex", "UPCOMINDEX"],
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


def _ssi_index_chart(closes):
    return {
        "code": "SUCCESS",
        "data": {
            "t": list(range(len(closes))),
            "o": closes,
            "h": [c + 5 for c in closes],
            "l": [c - 5 for c in closes],
            "c": closes,
            "v": [1000] * len(closes),
        },
    }


# Điểm chỉ số theo mã SSI (viết hoa), lấy xấp xỉ từ phản hồi thật ngày 29/09/2026
_SSI_INDEX_CLOSES = {
    "VNINDEX": [1780.68, 1777.73],
    "VN30": [1922.42, 1914.35],
    "HNXINDEX": [275.0, 274.0],
    "HNXUPCOMINDEX": [125.22, 125.5],
}


class SsiMainIndicesTestCase(unittest.TestCase):
    """SSI là nguồn chỉ số VN dự phòng khi VCI lỗi (vd. Vietcap trả 403)."""

    def setUp(self):
        self.fetcher = SsiFetcher()
        self.fetcher.min_interval = 0.0
        self.requested = []

    def _fake_get(self, closes_by_symbol):
        def fake_get(url, params=None, **_):
            symbol = params["symbol"]
            self.requested.append(symbol)
            if symbol not in closes_by_symbol:
                return _response({"code": "ERR", "message": "not found"})
            return _response(_ssi_index_chart(closes_by_symbol[symbol]))

        return fake_get

    def test_non_vn_region_returns_none(self):
        with patch.object(self.fetcher._session, "get") as get:
            self.assertIsNone(self.fetcher.get_main_indices(region="cn"))
        get.assert_not_called()

    def test_returns_vn_indices_in_points_without_price_scaling(self):
        with patch.object(
            self.fetcher._session, "get", side_effect=self._fake_get(_SSI_INDEX_CLOSES)
        ):
            indices = self.fetcher.get_main_indices(region="vn")

        # Mã SSI phải viết hoa; "HNXIndex" trả chuỗi giá sai
        self.assertEqual(self.requested, ["VNINDEX", "VN30", "HNXINDEX", "HNXUPCOMINDEX"])
        # Mã trong payload thống nhất với VciFetcher
        self.assertEqual(
            [i["code"] for i in indices], ["VNINDEX", "VN30", "HNXIndex", "UPCOMINDEX"]
        )
        self.assertEqual(
            [i["name"] for i in indices], ["VN-Index", "VN30", "HNX-Index", "UPCoM-Index"]
        )
        vnindex = indices[0]
        # Điểm chỉ số giữ nguyên, không nhân 1000 như giá cổ phiếu
        self.assertEqual(vnindex["current"], 1777.73)
        self.assertEqual(vnindex["prev_close"], 1780.68)
        self.assertEqual(vnindex["change"], -2.95)
        self.assertEqual(vnindex["change_pct"], -0.17)
        for key in ("open", "high", "low", "volume", "amount", "amplitude"):
            self.assertIn(key, vnindex)

    def test_one_failing_index_is_skipped(self):
        closes = {k: v for k, v in _SSI_INDEX_CLOSES.items() if k != "VN30"}
        with patch.object(self.fetcher._session, "get", side_effect=self._fake_get(closes)):
            indices = self.fetcher.get_main_indices(region="vn")

        self.assertEqual([i["code"] for i in indices], ["VNINDEX", "HNXIndex", "UPCOMINDEX"])

    def test_all_failing_returns_none(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._fake_get({})):
            self.assertIsNone(self.fetcher.get_main_indices(region="vn"))


class _FailingVciFetcher(BaseFetcher):
    name = "VciFetcher"
    priority = 0

    def _fetch_raw_data(self, *args, **kwargs):  # pragma: no cover - không dùng
        raise NotImplementedError

    def _normalize_data(self, *args, **kwargs):  # pragma: no cover - không dùng
        raise NotImplementedError

    def get_main_indices(self, region="cn"):
        # Giống VCI khi bị Vietcap trả 403 cho mọi chỉ số
        return None


class MainIndicesRoutingTestCase(unittest.TestCase):
    """VCI lỗi thì phải lấy chỉ số VN từ SSI, không bao giờ lấy chỉ số A 股 từ Yahoo."""

    def setUp(self):
        from data_provider.yfinance_fetcher import YfinanceFetcher

        self.ssi = SsiFetcher()
        self.ssi.min_interval = 0.0
        self.yahoo = YfinanceFetcher()

    def _get(self, manager, ssi_closes):
        def fake_get(url, params=None, **_):
            symbol = params["symbol"]
            if symbol not in ssi_closes:
                return _response({"code": "ERR", "message": "not found"})
            return _response(_ssi_index_chart(ssi_closes[symbol]))

        with patch.object(self.ssi._session, "get", side_effect=fake_get), patch(
            "yfinance.download", side_effect=AssertionError("Yahoo không được gọi cho vn")
        ), patch("yfinance.Ticker", side_effect=AssertionError("Yahoo không được gọi cho vn")):
            return manager.get_main_indices(region="vn")

    def test_vci_failure_falls_back_to_ssi_vn_indices(self):
        manager = DataFetcherManager(fetchers=[_FailingVciFetcher(), self.ssi, self.yahoo])

        indices = self._get(manager, _SSI_INDEX_CLOSES)

        self.assertEqual([i["name"] for i in indices], ["VN-Index", "VN30", "HNX-Index", "UPCoM-Index"])

    def test_no_vn_source_yields_empty_instead_of_a_share_indices(self):
        manager = DataFetcherManager(fetchers=[_FailingVciFetcher(), self.ssi, self.yahoo])

        self.assertEqual(self._get(manager, {}), [])

    def test_yahoo_returns_none_for_vn(self):
        with patch("yfinance.download") as download, patch("yfinance.Ticker") as ticker:
            self.assertIsNone(self.yahoo.get_main_indices(region="vn"))
        download.assert_not_called()
        ticker.assert_not_called()


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

        stats = manager.get_market_stats(purpose="test", region="vn")

        self.assertEqual(stats["up_count"], 200)
        self.assertEqual(cn.calls, 0)
        self.assertEqual(vn.calls, 0)

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

    def test_legacy_supported_markets_value_still_reviews_vn(self):
        # SUPPORTED_MARKETS cũ (tw) bị bỏ qua nên vẫn điểm lại vn.
        # Legacy SUPPORTED_MARKETS values are ignored, so vn is still reviewed.
        with supported_markets("tw"):
            response, task_queue = self._trigger("cn")

        self.assertEqual(response.region, "vn")
        self.assertEqual(task_queue.submit_background_task.call_args.kwargs["region"], "vn")


class VnTradingCalendarTestCase(unittest.TestCase):
    def test_vn_is_closed_on_weekends(self):
        self.assertTrue(trading_calendar.is_market_open("vn", date(2026, 9, 25)))  # thứ 6
        self.assertFalse(trading_calendar.is_market_open("vn", date(2026, 9, 26)))  # thứ 7
        self.assertFalse(trading_calendar.is_market_open("vn", date(2026, 9, 27)))  # CN

    def test_unknown_market_stays_fail_open(self):
        self.assertTrue(trading_calendar.is_market_open("atlantis", date(2026, 9, 26)))


_MONEY_FLOW = {
    "scope": "HOSE",
    "unit": "VND bn",
    "foreign": {
        "today": {
            "from": "2026-10-21", "to": "2026-10-21", "buy": 2502.4, "sell": 2308.26, "net": 194.15,
            "top_net_buy": [{"ticker": "TCB", "net": 289.8, "change_pct": 1.33}],
            "top_net_sell": [{"ticker": "FPT", "net": -173.16, "change_pct": 0.0}],
        },
        "ytd": {"from": "2026-01-05", "to": "2026-10-20", "buy": 543970.69, "sell": 663235.22,
                "net": -119264.53, "top_net_buy": [], "top_net_sell": []},
    },
    "proprietary": {
        "today": {"from": "2026-10-20", "to": "2026-10-21", "buy": 1957.66, "sell": 2133.53, "net": -175.87,
                  "top_net_buy": [{"ticker": "VJC", "net": 285.24, "change_pct": 0.47}], "top_net_sell": []},
    },
}


class VnMarketReviewMoneyFlowTestCase(unittest.TestCase):
    def test_prompt_includes_fund_flows_with_date_ranges(self):
        analyzer = _make_vn_analyzer("vi")
        overview = MarketOverview(
            date="2026-10-21", up_count=458, down_count=225, total_amount=18695.0,
            put_through_amount=3302.0, foreign_net_amount=160.9, money_flow=_MONEY_FLOW,
        )

        prompt = analyzer._build_review_prompt(overview, [])

        self.assertIn("## Fund Flows (HOSE, VND bn)", prompt)
        self.assertIn("- Latest (2026-10-21 → 2026-10-21): net +194 (buy 2,502 / sell 2,308)", prompt)
        self.assertIn("top net buy: TCB +290", prompt)
        self.assertIn("- Year to date (2026-01-05 → 2026-10-20): net -119,265", prompt)
        # Tự doanh phải kèm khoảng ngày, không được gán cho "hôm nay".
        self.assertIn("Proprietary trading", prompt)
        self.assertIn("cite its date range instead of calling it today", prompt)
        self.assertIn("- Latest (2026-10-20 → 2026-10-21): net -176", prompt)
        self.assertIn("Put-through (negotiated, excluded from turnover above): 3,302", prompt)
        self.assertIn("Foreign net, all exchanges today: +161", prompt)
        self.assertIn("use only the fund-flow figures given in Fund Flows", prompt)
        self.assertNotIn("do not invent or over-interpret unsupported breadth, fund-flow", prompt)

    def test_prompt_without_flows_keeps_old_restriction(self):
        analyzer = _make_vn_analyzer("vi")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-10-21", up_count=1), [])

        self.assertNotIn("## Fund Flows", prompt)
        self.assertNotIn("Put-through", prompt)
        self.assertIn("do not invent or over-interpret unsupported breadth, fund-flow", prompt)

    def test_index_line_shows_turnover_when_available(self):
        from src.market_analyzer import MarketIndex

        analyzer = _make_vn_analyzer("vi")
        overview = MarketOverview(date="2026-10-21")
        overview.indices = [
            MarketIndex(code="VNINDEX", name="VN-Index", current=1738.67, change_pct=2.76, amount=20290.59e9),
            MarketIndex(code="VN30", name="VN30", current=1880.57, change_pct=1.9, amount=0.0),
        ]
        prompt = analyzer._build_review_prompt(overview, [])

        self.assertIn("- VN-Index: 1738.67 (↑2.76%, turnover 20,291 VND bn)", prompt)
        self.assertIn("- VN30: 1880.57 (↑1.90%)", prompt)

    def test_overview_collects_flows_and_extra_breadth(self):
        analyzer = _make_vn_analyzer("vi")
        analyzer.data_manager.get_main_indices.return_value = []
        analyzer.data_manager.get_market_stats.return_value = {
            "up_count": 458, "down_count": 225, "flat_count": 187, "limit_up_count": 43,
            "limit_down_count": 26, "total_amount": 18695.56, "put_through_amount": 3301.99,
            "foreign_net_amount": 160.93,
        }
        analyzer.data_manager.get_market_money_flow.return_value = _MONEY_FLOW

        overview = analyzer.get_market_overview()

        self.assertEqual(overview.put_through_amount, 3301.99)
        self.assertEqual(overview.foreign_net_amount, 160.93)
        self.assertEqual(overview.money_flow["scope"], "HOSE")
        payload = analyzer.build_market_review_payload(overview, [], "report")
        self.assertEqual(payload["money_flow"]["scope"], "HOSE")
        self.assertEqual(payload["breadth"]["put_through_amount"], 3301.99)
        self.assertEqual(payload["breadth"]["foreign_net_amount"], 160.93)

    def test_money_flow_failure_is_fail_open(self):
        analyzer = _make_vn_analyzer("vi")
        analyzer.data_manager.get_main_indices.return_value = []
        analyzer.data_manager.get_market_stats.return_value = {"up_count": 1}
        analyzer.data_manager.get_market_money_flow.side_effect = RuntimeError("boom")

        overview = analyzer.get_market_overview()

        self.assertIsNone(overview.money_flow)
        self.assertEqual(overview.up_count, 1)


if __name__ == "__main__":
    unittest.main()
