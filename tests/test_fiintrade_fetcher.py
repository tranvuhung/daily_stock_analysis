# -*- coding: utf-8 -*-
"""FiinTradeFetcher: nến ngày Việt Nam qua API biểu đồ FiinTrade (tuỳ chọn).

Mock chỉ đặt ở biên HTTP (``requests.Session``); chuẩn hoá, làm sạch, tính chỉ báo
và vòng chuyển nguồn của manager đều chạy code thật.
Mocks sit at the HTTP boundary only; normalisation and manager failover are real.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from datetime import date
from types import SimpleNamespace

from data_provider import fiintrade_fetcher as module
from data_provider.base import DataFetchError, DataFetcherManager, STANDARD_COLUMNS
from data_provider.fiintrade_fetcher import FiinTradeFetcher, is_fiintrade_enabled

# Mặc định coi bảng organCode là rỗng (ticker = organCode) để mỗi test chỉ mock đúng
# endpoint nó kiểm; phần ánh xạ có test riêng ở FiinTradeOrganCodeTestCase.
# By default the organCode map is empty so tests only mock the endpoint under test.
_ORG_MAP_PATCHER = patch.object(FiinTradeFetcher, "_organization_map", return_value={})


def setUpModule():
    module.reset_organizations_cache()
    _ORG_MAP_PATCHER.start()


def tearDownModule():
    _ORG_MAP_PATCHER.stop()
    module.reset_organizations_cache()

# Lấy từ response thật (FPT, 2025-11-26 ~ 2025-11-28): giá đã điều chỉnh có phần lẻ
# dài, ngày GDKHQ 2025-11-28 có rateAdjusted khác 0. Close/volume khớp tuyệt đối VCI.
_REAL_PAYLOAD = {
    "page": 0,
    "pageSize": 0,
    "totalCount": 0,
    "items": [
        {"tradingDate": "2025-11-26T00:00:00", "openPrice": 88572.485787416,
         "closePrice": 87862.485816072, "highestPrice": 88927.485773088,
         "lowestPrice": 87773.735819654, "totalMatchVolume": 5843562.0,
         "rateAdjusted": 0.0, "openInterest": 0.0},
        {"tradingDate": "2025-11-27T00:00:00", "openPrice": 88039.985809056,
         "closePrice": 88306.235744436, "highestPrice": 88572.485787416,
         "lowestPrice": 87063.735848308, "totalMatchVolume": 4796150.0,
         "rateAdjusted": 0.0, "openInterest": 0.0},
        {"tradingDate": "2025-11-28T00:00:00", "openPrice": 87951.235812490,
         "closePrice": 86176.2360882888, "highestPrice": 88217.485801746,
         "lowestPrice": 86176.2360882888, "totalMatchVolume": 6239769.0,
         "rateAdjusted": 0.9897, "openInterest": 0.0},
    ],
    "packageId": None,
    "status": "Success",
    "errors": None,
}

# Nến dở trong phiên: tradingDate có kèm giờ và múi giờ.
_PARTIAL_PAYLOAD = {
    "items": [
        {"tradingDate": "2026-10-20T00:00:00", "openPrice": 57000.0, "closePrice": 57400.0,
         "highestPrice": 57800.0, "lowestPrice": 56800.0, "totalMatchVolume": 3502586.0,
         "rateAdjusted": 0.0, "openInterest": 0.0},
        {"tradingDate": "2026-10-21T14:30:00.068+07:00", "openPrice": 58300.0,
         "closePrice": 57500.0, "highestPrice": 58300.0, "lowestPrice": 56900.0,
         "totalMatchVolume": 8406100.0, "rateAdjusted": 0.0, "openInterest": 0.0},
    ],
    "status": "Success",
    "errors": None,
}

# Mã không tra được trên FiinTrade (ví dụ gửi ticker thay vì organCode) trả HTTP 200
# kèm status Failed.
_NOT_FOUND_PAYLOAD = {
    "page": 0, "pageSize": 0, "totalCount": 0, "items": None, "packageId": None,
    "status": "Failed", "errors": ["Sequence contains no matching element"],
}


def _response(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class FiinTradeDailyDataTestCase(unittest.TestCase):
    def setUp(self):
        self.fetcher = FiinTradeFetcher(min_interval=0.0, origin="https://partner.example")

    def test_normalizes_items_into_standard_columns_in_vnd(self):
        with patch.object(self.fetcher._session, "get", return_value=_response(_REAL_PAYLOAD)):
            df = self.fetcher.get_daily_data("FPT", start_date="2025-11-26", end_date="2025-11-28")

        self.assertEqual(len(df), 3)
        for column in STANDARD_COLUMNS:
            self.assertIn(column, df.columns)
        # Giá tính bằng VND, không nhân 1000; làm tròn 2 chữ số như VCI.
        self.assertEqual(list(df["close"]), [87862.49, 88306.24, 86176.24])
        self.assertEqual(list(df["volume"]), [5843562, 4796150, 6239769])
        self.assertEqual(
            [str(value.date()) for value in df["date"]],
            ["2025-11-26", "2025-11-27", "2025-11-28"],
        )
        self.assertAlmostEqual(df["pct_chg"].iloc[1], 0.51, places=2)

    def test_amount_is_estimated_from_mid_price_and_volume(self):
        with patch.object(self.fetcher._session, "get", return_value=_response(_REAL_PAYLOAD)):
            df = self.fetcher.get_daily_data("FPT", start_date="2025-11-26", end_date="2025-11-28")

        row = df.iloc[0]
        self.assertAlmostEqual(row["amount"], (row["high"] + row["low"]) / 2 * row["volume"], places=0)

    def test_partial_intraday_bar_keeps_trading_day(self):
        with patch.object(self.fetcher._session, "get", return_value=_response(_PARTIAL_PAYLOAD)):
            df = self.fetcher.get_daily_data("FPT", start_date="2026-10-20", end_date="2026-10-21")

        self.assertEqual(
            [str(value.date()) for value in df["date"]], ["2026-10-20", "2026-10-21"]
        )
        self.assertEqual(df["close"].iloc[-1], 57500.0)

    def test_request_window_and_params(self):
        with patch.object(
            self.fetcher._session, "get", return_value=_response(_REAL_PAYLOAD)
        ) as mocked:
            self.fetcher.get_daily_data("FPT", start_date="2025-11-26", end_date="2025-11-28")

        params = mocked.call_args.kwargs["params"]
        self.assertEqual(params["Code"], "FPT")
        self.assertEqual(params["Type"], "Stock")
        self.assertEqual(params["Frequency"], "Daily")
        # Nến ngày đóng dấu 00:00 giờ VN = 17:00 UTC hôm trước.
        self.assertEqual(params["From"], "2025-11-25T17:00:00.000Z")
        self.assertEqual(params["To"], "2025-11-29T00:00:00.000Z")

    def test_index_codes_use_fiintrade_spelling(self):
        cases = {
            "VNINDEX": "VNINDEX",
            "HNXINDEX": "HNXIndex",
            "HNX30": "HNX30",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(FiinTradeFetcher.to_provider_symbol(code), (expected, "Index"))
        self.assertEqual(FiinTradeFetcher.to_provider_symbol("hpg.vn"), ("HPG", "Stock"))

    def test_upcom_index_uses_fiintrade_code(self):
        # Chủ dự án chọn FiinTrade trước cho UPCoM-Index, lỗi mới sang VCI (2026-10-22).
        # UPCoM-Index comes from FiinTrade first, falling back to VCI on failure.
        for code in ("UPCOM", "UPCOMINDEX"):
            with self.subTest(code=code):
                self.assertEqual(FiinTradeFetcher.to_provider_symbol(code), ("UpcomIndex", "Index"))

    def test_unknown_symbol_returns_empty_frame(self):
        with patch.object(self.fetcher._session, "get", return_value=_response(_NOT_FOUND_PAYLOAD)):
            df = self.fetcher.get_daily_data("ACV", start_date="2026-10-01", end_date="2026-10-21")

        self.assertTrue(df.empty)

    def test_other_failed_status_raises(self):
        payload = {"items": None, "status": "Failed", "errors": ["TimeFrequency not valid: Weekly"]}
        with patch.object(self.fetcher._session, "get", return_value=_response(payload)):
            with self.assertRaises(DataFetchError):
                self.fetcher.get_daily_data("FPT", start_date="2026-10-01", end_date="2026-10-21")

    def test_origin_header_comes_from_configuration(self):
        self.assertEqual(self.fetcher._session.headers["Origin"], "https://partner.example")
        self.assertEqual(self.fetcher._session.headers["Referer"], "https://partner.example/")


class _StubFetcher:
    """Nguồn dự phòng giả, đứng sau FiinTrade / Fallback stub ranked after FiinTrade."""

    name = "VciFetcher"
    priority = 0

    def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
        return pd.DataFrame(
            {"date": [pd.Timestamp("2026-10-21")], "open": [1.0], "high": [1.0],
             "low": [1.0], "close": [1.0], "volume": [1.0], "amount": [1.0], "pct_chg": [0.0]}
        )


class FiinTradeManagerFallbackTestCase(unittest.TestCase):
    def setUp(self):
        DataFetcherManager.reset_daily_source_health()

    def tearDown(self):
        DataFetcherManager.reset_daily_source_health()

    def test_unknown_symbols_fall_back_without_tripping_breaker(self):
        # Nhiều mã không tra được liên tiếp không được làm FiinTrade bị ngắt mạch cho mã khác.
        # Repeated unresolved symbols must not open the breaker for other symbols.
        fiintrade = FiinTradeFetcher(min_interval=0.0, origin="https://partner.example")
        manager = DataFetcherManager(fetchers=[fiintrade, _StubFetcher()])

        with patch.object(fiintrade._session, "get", return_value=_response(_NOT_FOUND_PAYLOAD)):
            for code in ("ACV", "BSR", "VEA", "MCH"):
                _, source = manager.get_daily_data(code, start_date="2026-10-01", end_date="2026-10-21")
                self.assertEqual(source, "VciFetcher")

        self.assertTrue(
            DataFetcherManager._daily_source_health.is_available("daily_data:vn:FiinTradeFetcher")
        )


class FiinTradeRegistrationTestCase(unittest.TestCase):
    def test_on_by_default_but_requires_origin(self):
        # Mặc định bật: chỉ cần origin; thiếu origin hoặc tắt tường minh thì không chạy.
        # On by default: an origin is enough; no origin or an explicit false disables it.
        with patch.dict(os.environ, {"FIINTRADE_ORIGIN": "https://x"}):
            os.environ.pop("FIINTRADE_ENABLED", None)
            self.assertTrue(is_fiintrade_enabled())
        with patch.dict(os.environ, {"FIINTRADE_ORIGIN": ""}):
            os.environ.pop("FIINTRADE_ENABLED", None)
            self.assertFalse(is_fiintrade_enabled())
        with patch.dict(os.environ, {"FIINTRADE_ENABLED": "false", "FIINTRADE_ORIGIN": "https://x"}):
            self.assertFalse(is_fiintrade_enabled())

    def test_not_registered_without_origin(self):
        with patch.dict(os.environ, {"FIINTRADE_ORIGIN": "", "VNSTOCK_ENABLED": "false"}):
            os.environ.pop("FIINTRADE_ENABLED", None)
            manager = DataFetcherManager()
        names = [f.name for f in manager._get_fetchers_snapshot()]
        self.assertNotIn("FiinTradeFetcher", names)
        self.assertEqual(names[0], "VciFetcher")

    def test_registered_ahead_of_vci_when_enabled(self):
        # Chỉ đặt origin (không đặt cờ) là đủ để FiinTrade đứng trước VCI.
        env = {"FIINTRADE_ORIGIN": "https://partner.example", "VNSTOCK_ENABLED": "false"}
        with patch.dict(os.environ, env):
            os.environ.pop("FIINTRADE_ENABLED", None)
            manager = DataFetcherManager()
        names = [f.name for f in manager._get_fetchers_snapshot()]
        self.assertEqual(names[:2], ["FiinTradeFetcher", "VciFetcher"])


# Lấy từ response PriceDepth thật (TCB, 2026-10-21, đã rút gọn bước giá 4-10).
_PRICE_DEPTH_PAYLOAD = {
    "items": [
        {
            "price": {
                "organCode": "TCB", "comGroupCode": "VNINDEX", "ticker": "TCB",
                "tradingDate": "2026-10-21T14:54:00.068+07:00",
                "ceilingPrice": 36300.0, "floorPrice": 31600.0, "referencePrice": 33950.0,
                "openPrice": 34000.0, "closePrice": 34400.0, "matchPrice": 34400.0,
                "priceChange": 450.0, "percentPriceChange": 0.0133,
                "highestPrice": 34400.0, "lowestPrice": 33800.0, "averagePrice": 0.0,
                "totalMatchVolume": 21000000.0, "totalMatchValue": 718000000000.0,
                "totalDealVolume": 1500000.0, "totalDealValue": 51000000000.0,
                "foreignBuyValueTotal": 80000000000.0, "foreignSellValueTotal": 120000000000.0,
                "foreignBuyVolumeTotal": 2300000.0, "foreignSellVolumeTotal": 3500000.0,
                "foreignTotalRoom": 1597139381.0, "foreignCurrentRoom": 143832851.0,
                "marketStatus": "0",
            },
            "bidAsk": {
                "best1Bid": 34400.0, "best1BidVolume": 300000.0,
                "best2Bid": 34350.0, "best2BidVolume": 200000.0,
                "best3Bid": 34300.0, "best3BidVolume": 100000.0,
                "best1Offer": 34450.0, "best1OfferVolume": 100000.0,
                "best2Offer": 34500.0, "best2OfferVolume": 100000.0,
                "best3Offer": 34550.0, "best3OfferVolume": 200000.0,
            },
            "extraInfo": {
                "averageVolume1Month": 23017712.0, "averageVolume2Week": 18563813.0,
                "rtd14": 3829.49064669, "rtd25": 1.34279313, "rtq12": 0.15716361,
                "rtd21": 8.86540878, "rtd35": 1.14334511, "rtd11": 240577862055300.0,
                "foreignerPercentage": 0.20374543,
            },
            "rank": 7,
            "totalRank": 28,
        }
    ],
    "status": "Success",
    "errors": None,
}

# Lấy từ response GetStockEvents thật (FPT).
_EVENTS_PAYLOAD = {
    "items": [
        {"type": "Earning", "revenue": 16658335760739.0, "profit": 3141032584460.0,
         "yearReport": 2025, "lengthReport": 2, "publicDate": "2025-08-21T00:00:00", "id": 85465},
        {"type": "InternalTransaction", "fullName": "Đỗ Hữu Binh", "actionTypeCode": "B",
         "shareAcquire": 8700.0, "shareRegister": 8700.0,
         "publicDate": "2026-09-25T00:00:00", "id": 1304007, "tradeStatusCode": "D"},
        {"type": "InternalTransaction", "fullName": "Nguyễn Văn A", "actionTypeCode": "S",
         "shareAcquire": 2000.0, "shareRegister": 5000.0,
         "publicDate": "2026-10-10T00:00:00", "id": 1304099, "tradeStatusCode": "D"},
        {"type": "Dividend", "valuePershare": 1000.0, "exerciseRate": 0.1,
         "exrightDate": "2025-12-01T00:00:00", "publicDate": "2025-11-19T00:00:00", "id": 627506},
        {"type": "Dividend", "valuePershare": 1000.0, "exerciseRate": 0.1,
         "exrightDate": "2026-05-28T00:00:00", "publicDate": "2026-05-22T00:00:00", "id": 627909},
        {"type": "ShareIssuance", "issueMethodName": "Cổ phiếu thưởng",
         "en_IssueMethodName": "Bonus Issue", "issueVolumn": 171401226.0,
         "planVolumn": 171432642.0, "exerciseRatio": 0.1, "exrightDate": "2026-09-21T00:00:00",
         "publicDate": "2026-09-16T00:00:00", "id": 472051, "issueStatusCode": "D"},
    ],
    "status": "Success",
    "errors": None,
}


class FiinTradeRealtimeQuoteTestCase(unittest.TestCase):
    def setUp(self):
        self.fetcher = FiinTradeFetcher(min_interval=0.0, origin="https://partner.example")

    def test_price_depth_maps_to_quote(self):
        with patch.object(
            self.fetcher._session, "get", return_value=_response(_PRICE_DEPTH_PAYLOAD)
        ) as mocked:
            quote = self.fetcher.get_realtime_quote("TCB")

        self.assertTrue(mocked.call_args.args[0].endswith("/PriceDepth/GetPriceDepth"))
        self.assertEqual(quote.code, "TCB")
        self.assertEqual(quote.price, 34400.0)
        # percentPriceChange là tỷ lệ: 0.0133 -> 1.33%.
        self.assertEqual(quote.change_pct, 1.33)
        self.assertEqual(quote.pre_close, 33950.0)
        self.assertEqual(quote.amount, 718000000000.0)
        self.assertEqual(quote.pe_ratio, 8.87)
        self.assertEqual(quote.pb_ratio, 1.34)
        self.assertEqual(quote.eps_ttm, 3829.0)
        self.assertEqual(quote.total_mv, 240577862055300.0)
        self.assertAlmostEqual(quote.roe, 15.72, places=2)
        self.assertAlmostEqual(quote.foreign_ownership_pct, 20.37, places=2)
        self.assertEqual(quote.foreign_net_value, -40000000000.0)
        self.assertEqual(quote.put_through_value, 51000000000.0)
        # 600k mua / (600k mua + 400k bán) trên 3 bước giá tốt nhất.
        self.assertEqual(quote.bid_ask_imbalance, 0.6)
        # Turnover trên tổng số CP = KL / (vốn hoá / giá).
        shares = 240577862055300.0 / 34400.0
        self.assertAlmostEqual(quote.turnover_rate, 21000000.0 / shares * 100, places=3)
        self.assertEqual((quote.vn_ceiling, quote.vn_floor, quote.vn_exchange), (36300.0, 31600.0, "hose"))
        self.assertIsNone(quote.circ_mv)
        self.assertIn("circ_mv", quote.missing_fields)
        self.assertTrue(quote.has_basic_data())

    def test_failed_status_returns_none_for_unresolved_symbol(self):
        payload = {"items": None, "status": "Failed", "errors": ["Sequence contains no matching element"]}
        with patch.object(self.fetcher._session, "get", return_value=_response(payload)):
            self.assertIsNone(self.fetcher.get_realtime_quote("ACV"))

    def test_index_returns_none_without_request(self):
        with patch.object(self.fetcher._session, "get") as mocked:
            self.assertIsNone(self.fetcher.get_realtime_quote("VNINDEX"))
        mocked.assert_not_called()


class FiinTradeCorporateEventsTestCase(unittest.TestCase):
    def setUp(self):
        module.reset_events_cache()
        self.fetcher = FiinTradeFetcher(min_interval=0.0, origin="https://partner.example")

    def tearDown(self):
        module.reset_events_cache()

    def test_events_are_normalized_and_ex_date_precedes_record_date(self):
        with patch.object(self.fetcher._session, "get", return_value=_response(_EVENTS_PAYLOAD)):
            events = self.fetcher.get_corporate_events("FPT", today=date(2026, 10, 21))

        dividends = events["dividends"]
        self.assertEqual([d["record_date"] for d in dividends], ["2026-05-28", "2025-12-01"])
        # exrightDate là ngày ĐKCC; GDKHQ là phiên trước đó (thứ Hai 01/12 -> thứ Sáu 28/11).
        self.assertEqual([d["ex_date"] for d in dividends], ["2026-05-27", "2025-11-28"])
        self.assertEqual(dividends[0]["cash_per_share"], 1000.0)
        self.assertEqual(events["share_issuances"][0]["ex_date"], "2026-09-18")
        self.assertEqual(events["insider_transactions"][0]["action"], "sell")
        self.assertEqual(events["earnings_releases"][0], {"year": 2025, "quarter": 2, "public_date": "2025-08-21"})
        # Không giữ số liệu revenue/profit chưa rõ ngữ nghĩa.
        self.assertNotIn("profit", events["earnings_releases"][0])

    def test_events_are_cached(self):
        with patch.object(
            self.fetcher._session, "get", return_value=_response(_EVENTS_PAYLOAD)
        ) as mocked:
            self.fetcher.get_corporate_events("FPT", today=date(2026, 10, 21))
            self.fetcher.get_corporate_events("FPT", today=date(2026, 10, 21))
        self.assertEqual(mocked.call_count, 1)


class _QuoteStub:
    def __init__(self, name, quote):
        self.name = name
        self.priority = 0
        self._quote = quote
        self.calls = 0

    def get_realtime_quote(self, stock_code, **_):
        self.calls += 1
        return self._quote


class FiinTradeRealtimeOrderTestCase(unittest.TestCase):
    def _quote(self, price):
        from data_provider.realtime_types import UnifiedRealtimeQuote

        return UnifiedRealtimeQuote(code="FPT", price=price)

    def _cfg(self, priority):
        return SimpleNamespace(
            enable_realtime_quote=True,
            realtime_source_priority=priority,
            realtime_feed_stale_seconds=30,
            realtime_cache_ttl=600,
        )

    def test_fiintrade_goes_first_when_registered_and_not_listed(self):
        fiin = _QuoteStub("FiinTradeFetcher", self._quote(57400.0))
        ssi = _QuoteStub("SsiFetcher", self._quote(57300.0))
        manager = DataFetcherManager(fetchers=[ssi, fiin])
        with patch("src.config.get_config", return_value=self._cfg("ssi,vci")):
            self.assertEqual(
                manager._vn_realtime_source_order(), ["FiinTradeFetcher", "SsiFetcher", "VciFetcher"]
            )
            quote = manager.get_realtime_quote("FPT")
        self.assertEqual(quote.price, 57400.0)
        self.assertEqual(ssi.calls, 0)

    def test_explicit_position_is_respected(self):
        manager = DataFetcherManager(fetchers=[_QuoteStub("FiinTradeFetcher", None)])
        with patch("src.config.get_config", return_value=self._cfg("ssi,fiintrade,vci")):
            self.assertEqual(
                manager._vn_realtime_source_order(), ["SsiFetcher", "FiinTradeFetcher", "VciFetcher"]
            )

    def test_not_registered_keeps_default_order(self):
        manager = DataFetcherManager(fetchers=[_QuoteStub("SsiFetcher", None)])
        with patch("src.config.get_config", return_value=self._cfg("ssi,vci")):
            self.assertEqual(manager._vn_realtime_source_order(), ["SsiFetcher", "VciFetcher"])

    def test_unresolved_symbol_falls_back_to_ssi(self):
        fiin = _QuoteStub("FiinTradeFetcher", None)
        ssi = _QuoteStub("SsiFetcher", self._quote(41700.0))
        manager = DataFetcherManager(fetchers=[fiin, ssi])
        with patch("src.config.get_config", return_value=self._cfg("ssi,vci")):
            quote = manager.get_realtime_quote("ACV")
        self.assertEqual(quote.price, 41700.0)
        self.assertEqual(fiin.calls, 1)


def _index_item(code, value, ref, chg, up, down, flat, ceil, floor, matched, deal, fbuy, fsell):
    return {
        "comGroupCode": code, "indexValue": value, "referenceIndex": ref, "indexChange": round(value - ref, 2),
        "percentIndexChange": chg, "openIndex": ref, "highestIndex": value, "lowestIndex": ref,
        "totalMatchVolume": 1000.0, "totalMatchValue": matched, "totalDealVolume": 100.0,
        "totalDealValue": deal, "totalStockUpPrice": up, "totalStockDownPrice": down,
        "totalStockNoChangePrice": flat, "totalStockOverCeiling": ceil, "totalStockUnderFloor": floor,
        "foreignBuyValueTotal": fbuy, "foreignSellValueTotal": fsell,
    }


# Rút gọn từ response GetLatestIndices thật (2026-10-21).
_LATEST_INDICES_PAYLOAD = {
    "items": [
        _index_item("VNINDEX", 1738.67, 1691.97, 0.0276, 246, 78, 54, 16.0, 3.0,
                    17071097794490.0, 3219491891180.0, 2502403869400.0, 2308257158450.0),
        _index_item("VN30", 1880.57, 1845.51, 0.019, 25, 1, 4, 3.0, 0.0,
                    8684820556650.0, 1666029569780.0, 1.0e12, 0.8e12),
        _index_item("HNXIndex", 261.67, 257.96, 0.0144, 86, 57, 51, 9.0, 7.0,
                    1076103307700.0, 38435860000.0, 1.0e10, 4.1405935e10),
        _index_item("UpcomIndex", 126.24, 125.22, 0.0081, 126, 90, 82, 18.0, 16.0,
                    548361902500.0, 44067108800.0, 1.0e9, 2.8126374e9),
        _index_item("VNREAL", 3056.31, 2875.29, 0.063, 34, 5, 6, 6.0, 1.0, 1.0e12, 0.0, 0.0, 0.0),
    ],
    "status": "Success",
}

_FOREIGN_FLOW_PAYLOAD = {
    "items": [{
        "comGroupCode": "VNINDEX",
        "today": {
            "fromDate": "2026-10-21T00:00:00+07:00", "toDate": "2026-10-21T00:00:00+07:00",
            "foreignBuyValue": 2502403869400.0, "foreignSellValue": 2308257158450.0,
            # Tổng các mã mua ròng, KHÔNG phải ròng thị trường.
            "foreignNetBuyValue": 1090061167000.0, "foreignNetSellValue": 900206448900.0,
            "netBuy": [{"ticker": "TCB", "foreignNetBuyValue": 289795860550.0, "percentPriceChange": 0.0133}],
            "netSell": [{"ticker": "FPT", "foreignNetBuyValue": -173161592100.0, "percentPriceChange": 0.0}],
        },
        "oneWeek": {
            "fromDate": "2026-10-14T00:00:00", "toDate": "2026-10-20T00:00:00",
            "foreignBuyValue": 7695445476380.0, "foreignSellValue": 10710462741840.0,
            "netBuy": [], "netSell": [],
        },
    }],
    "status": "Success",
}

_PROPRIETARY_FLOW_PAYLOAD = {
    "items": [{
        "comGroupCode": "VNINDEX",
        "today": {
            "fromDate": "2026-10-20T00:00:00", "toDate": "2026-10-21T00:00:00",
            "totalBuyTradeValue": 1957660000000.0, "totalSellTradeValue": 2133530000000.0,
            "netBuy": [
                # Chứng quyền phải bị lọc khỏi top / Covered warrants are filtered out
                {"ticker": "CMSN2624", "totalNetBuyTradeValue": 9.0e11, "percentPriceChange": 0.11},
                {"ticker": "VJC", "totalNetBuyTradeValue": 285240000000.0, "percentPriceChange": 0.0047},
            ],
            "netSell": [{"ticker": "VCB", "totalNetBuyTradeValue": -104440000000.0, "percentPriceChange": 0.01}],
        },
    }],
    "status": "Success",
}


class FiinTradeMarketTestCase(unittest.TestCase):
    def setUp(self):
        module.reset_market_cache()
        self.fetcher = FiinTradeFetcher(min_interval=0.0, origin="https://partner.example")

    def tearDown(self):
        module.reset_market_cache()

    def test_main_indices_keep_vci_codes_and_include_put_through(self):
        with patch.object(
            self.fetcher._session, "get", return_value=_response(_LATEST_INDICES_PAYLOAD)
        ) as mocked:
            indices = self.fetcher.get_main_indices("vn")

        self.assertIn("wlgw-market", mocked.call_args.args[0])
        self.assertEqual(
            [(i["code"], i["name"]) for i in indices],
            [("VNINDEX", "VN-Index"), ("VN30", "VN30"), ("HNXIndex", "HNX-Index"),
             ("UPCOMINDEX", "UPCoM-Index")],
        )
        vnindex = indices[0]
        self.assertEqual(vnindex["change_pct"], 2.76)
        self.assertEqual(vnindex["prev_close"], 1691.97)
        # Khớp + thoả thuận, cùng ngữ nghĩa amount của VCI (20.290 tỷ).
        self.assertEqual(vnindex["amount"], 17071097794490.0 + 3219491891180.0)
        self.assertEqual(vnindex["put_through_amount"], 3219491891180.0)
        self.assertAlmostEqual(vnindex["foreign_net_amount"], 194146710950.0)

    def test_market_stats_sum_three_exchanges_and_keep_old_keys(self):
        with patch.object(self.fetcher._session, "get", return_value=_response(_LATEST_INDICES_PAYLOAD)):
            stats = self.fetcher.get_region_market_stats("vn")

        self.assertEqual(
            {k: stats[k] for k in ("up_count", "down_count", "flat_count", "limit_up_count", "limit_down_count")},
            {"up_count": 458, "down_count": 225, "flat_count": 187, "limit_up_count": 43, "limit_down_count": 26},
        )
        # total_amount chỉ gồm khớp lệnh (tỷ đồng), như SSI.
        self.assertEqual(stats["total_amount"], 18695.56)
        self.assertEqual(stats["put_through_amount"], 3301.99)
        self.assertEqual(stats["foreign_net_amount"], 160.93)
        self.assertEqual(stats["by_exchange"]["hnx"]["up_count"], 86)

    def test_indices_snapshot_is_shared_between_calls(self):
        with patch.object(
            self.fetcher._session, "get", return_value=_response(_LATEST_INDICES_PAYLOAD)
        ) as mocked:
            self.fetcher.get_main_indices("vn")
            self.fetcher.get_region_market_stats("vn")
        self.assertEqual(mocked.call_count, 1)

    def _flow_get(self, foreign=_FOREIGN_FLOW_PAYLOAD, proprietary=_PROPRIETARY_FLOW_PAYLOAD):
        def _get(url, params=None, timeout=None):
            if url.endswith("/MoneyFlow/GetForeign"):
                if isinstance(foreign, Exception):
                    raise foreign
                return _response(foreign)
            return _response(proprietary)
        return _get

    def test_money_flow_uses_buy_minus_sell_and_filters_warrants(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._flow_get()):
            flow = self.fetcher.get_market_money_flow("vn")

        self.assertEqual(flow["scope"], "HOSE")
        today = flow["foreign"]["today"]
        # Ròng = mua − bán (194 tỷ), không phải foreignNetBuyValue của khối tổng (1.090 tỷ).
        self.assertEqual(today["net"], 194.15)
        self.assertEqual(today["top_net_buy"][0]["ticker"], "TCB")
        self.assertEqual(today["top_net_sell"][0]["net"], -173.16)
        self.assertEqual(flow["foreign"]["one_week"]["net"], -3015.02)

        prop = flow["proprietary"]["today"]
        # Giữ nguyên khoảng ngày API báo (có thể gộp hai phiên).
        self.assertEqual((prop["from"], prop["to"]), ("2026-10-20", "2026-10-21"))
        self.assertEqual([row["ticker"] for row in prop["top_net_buy"]], ["VJC"])

    def test_money_flow_branch_failure_is_fail_open(self):
        with patch.object(
            self.fetcher._session, "get",
            side_effect=self._flow_get(foreign=DataFetchError("boom")),
        ):
            flow = self.fetcher.get_market_money_flow("vn")

        self.assertNotIn("foreign", flow)
        self.assertIn("proprietary", flow)


def _contrib_row(organ, ticker, points, change):
    return {"organCode": organ, "ticker": ticker, "contribution": points, "percentPriceChange": change}


# Rút gọn từ response thật (2026-10-22) của GetContribution / GetTopBreakout / GetIndexSeries.
_CONTRIBUTION_PAYLOAD = {
    "items": [{
        "series": {"comGroupCode": "VNINDEX", "indexChange": 21.96},
        "contrib1Day": {
            # organCode NHN là VHM: phải dùng ticker / organCode NHN is VHM, use ticker
            "topGainers": [_contrib_row("VIC", "VIC", 14.8835, 0.0416), _contrib_row("NHN", "VHM", 3.7261, 0.0356)],
            "topLosers": [_contrib_row("BID", "BID", -0.4812, -0.0088)],
        },
        "contrib5Day": {
            # percentPriceChange ở đây vẫn là % hôm nay (TCB −0,73%) nên bị bỏ
            "topGainers": [_contrib_row("TCB", "TCB", 1.83, -0.0073)],
            "topLosers": [],
        },
    }],
    "status": "Success",
}

_BREAKOUT_PAYLOAD = {
    "items": [
        {"ticker": "TPB", "sectorName": "Ngân hàng", "volume": 27474500.0, "averageMatchVolume": 15800950.0,
         "value": 349108525000.0, "percentPriceChange": 0.0079},
        {"ticker": "FPT", "sectorName": "Phần mềm", "volume": 10088500.0, "averageMatchVolume": 5054437.0,
         "value": 582190000000.0, "percentPriceChange": 0.0},
        # Chứng quyền bị lọc / Covered warrants are filtered out
        {"ticker": "CFPT2601", "sectorName": None, "volume": 9.0e6, "averageMatchVolume": 1.0e6,
         "value": 9.0e11, "percentPriceChange": 0.1},
    ],
    "status": 0,
}


def _series_point(stamp, value, total):
    return {"comGroupCode": "VNINDEX", "tradingDate": f"2026-10-22T{stamp}:00", "indexValue": value,
            "totalMatchValue": total}


_INDEX_SERIES_PAYLOAD = {
    "items": [
        _series_point("09:15", 1751.62, 2.4e11),
        _series_point("09:20", 1748.10, 9.0e11),
        _series_point("10:42", 1763.25, 4.0e12),
        _series_point("11:30", 1759.83, 6.0e12),
        _series_point("14:30", 1756.00, 1.2e13),
        _series_point("15:00", 1760.63, 1.3e13),
    ],
    "status": "Success",
}


class FiinTradeMarketDriversTestCase(unittest.TestCase):
    def setUp(self):
        module.reset_market_cache()
        self.fetcher = FiinTradeFetcher(min_interval=0.0, origin="https://partner.example")

    def tearDown(self):
        module.reset_market_cache()

    @staticmethod
    def _get(contribution=_CONTRIBUTION_PAYLOAD, breakout=_BREAKOUT_PAYLOAD, series=_INDEX_SERIES_PAYLOAD):
        def _inner(url, params=None, timeout=None):
            for suffix, payload in (
                ("/MoneyFlow/GetContribution", contribution),
                ("/TopMover/GetTopBreakout", breakout),
                ("/MarketInDepth/GetIndexSeries", series),
            ):
                if url.endswith(suffix):
                    if isinstance(payload, Exception):
                        raise payload
                    return _response(payload)
            raise AssertionError(url)
        return _inner

    def test_contribution_uses_ticker_and_drops_period_change(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._get()):
            drivers = self.fetcher.get_market_drivers("vn")

        contribution = drivers["index_contribution"]
        self.assertEqual(contribution["index_change"], 21.96)
        today = contribution["periods"]["1d"]
        self.assertEqual(
            today["top_gainers"],
            [{"ticker": "VIC", "points": 14.88, "change_pct": 4.16},
             {"ticker": "VHM", "points": 3.73, "change_pct": 3.56}],
        )
        self.assertEqual(today["top_losers"][0]["points"], -0.48)
        self.assertEqual(contribution["periods"]["5d"]["top_gainers"], [{"ticker": "TCB", "points": 1.83}])

    def test_breakouts_rank_by_value_and_filter_warrants(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._get()):
            breakouts = self.fetcher.get_market_drivers("vn")["volume_breakouts"]

        self.assertEqual([row["ticker"] for row in breakouts["items"]], ["FPT", "TPB"])
        self.assertEqual(breakouts["items"][1], {
            "ticker": "TPB", "sector": "Ngân hàng", "change_pct": 0.79, "volume_ratio": 1.74, "value": 349.11,
        })

    def test_intraday_path_and_closing_window_share(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._get()):
            intraday = self.fetcher.get_market_drivers("vn")["intraday"]

        self.assertEqual(intraday["date"], "2026-10-22")
        self.assertEqual(intraday["morning_close"], {"time": "11:30", "value": 1759.83})
        self.assertEqual(intraday["high"], {"time": "10:42", "value": 1763.25})
        self.assertEqual(intraday["low"], {"time": "09:20", "value": 1748.10})
        self.assertEqual(intraday["last"], {"time": "15:00", "value": 1760.63})
        # (1,3e13 − 1,2e13) / 1,3e13
        self.assertEqual(intraday["closing_window_value_share"], 7.7)

    def test_intraday_skips_closing_share_before_window(self):
        series = {"items": _INDEX_SERIES_PAYLOAD["items"][:4], "status": "Success"}
        with patch.object(self.fetcher._session, "get", side_effect=self._get(series=series)):
            intraday = self.fetcher.get_market_drivers("vn")["intraday"]
        self.assertNotIn("closing_window_value_share", intraday)

    def test_branch_failure_is_fail_open_and_cached(self):
        failed = {"status": "Failed", "errors": ["boom"]}
        with patch.object(
            self.fetcher._session, "get", side_effect=self._get(contribution=failed),
        ) as mocked:
            drivers = self.fetcher.get_market_drivers("vn")
            self.fetcher.get_market_drivers("vn")

        self.assertNotIn("index_contribution", drivers)
        self.assertIn("volume_breakouts", drivers)
        self.assertIn("intraday", drivers)
        self.assertEqual(mocked.call_count, 3)

    def test_non_vn_region_returns_none(self):
        self.assertIsNone(self.fetcher.get_market_drivers("us"))


class _MarketStub:
    def __init__(self, name, indices=None, stats=None, error=None):
        self.name = name
        self.priority = 0
        self._indices, self._stats, self._error = indices, stats, error

    def get_main_indices(self, region="vn"):
        if self._error:
            raise self._error
        return self._indices

    def get_region_market_stats(self, region):
        if self._error:
            raise self._error
        return self._stats

    def get_market_money_flow(self, region="vn"):
        if self._error:
            raise self._error
        return None

    def get_market_drivers(self, region="vn"):
        if self._error:
            raise self._error
        return None


class FiinTradeMarketFallbackTestCase(unittest.TestCase):
    def test_manager_falls_back_when_fiintrade_market_fails(self):
        fiin = _MarketStub("FiinTradeFetcher", error=DataFetchError("403"))
        ssi = _MarketStub("SsiFetcher", indices=[{"code": "VNINDEX"}], stats={"up_count": 1})
        manager = DataFetcherManager(fetchers=[fiin, ssi])

        self.assertEqual(manager.get_main_indices("vn"), [{"code": "VNINDEX"}])
        self.assertEqual(manager.get_market_stats(region="vn"), {"up_count": 1})
        self.assertEqual(manager.get_market_money_flow("vn"), {})
        self.assertEqual(manager.get_market_drivers("vn"), {})


# Rút gọn từ response Master/GetListOrganization thật (2026-10-22).
_ORGANIZATIONS_PAYLOAD = {
    "items": [
        {"organCode": "FPT", "ticker": "FPT", "comGroupCode": "VNINDEX"},
        {"organCode": "NHN", "ticker": "VHM", "comGroupCode": "VNINDEX"},
        {"organCode": "ACVN", "ticker": "ACV", "comGroupCode": "UpcomIndex"},
        {"organCode": "TLSC", "ticker": "MBS", "comGroupCode": "HNXIndex"},
    ],
    "status": "Success",
}


class FiinTradeOrganCodeTestCase(unittest.TestCase):
    """API theo từng mã nhận organCode, không phải ticker (VHM -> NHN, ACV -> ACVN)."""

    def setUp(self):
        _ORG_MAP_PATCHER.stop()
        module.reset_organizations_cache()
        module.reset_events_cache()
        self.fetcher = FiinTradeFetcher(min_interval=0.0, origin="https://partner.example")

    def tearDown(self):
        module.reset_organizations_cache()
        module.reset_events_cache()
        _ORG_MAP_PATCHER.start()

    def _router(self, depth_payload=None):
        def _get(url, params=None, timeout=None):
            if url.endswith("/Master/GetListOrganization"):
                return _response(_ORGANIZATIONS_PAYLOAD)
            if url.endswith("/PriceDepth/GetPriceDepth"):
                return _response(depth_payload or _PRICE_DEPTH_PAYLOAD)
            if url.endswith("/TradingView/GetStockEvents"):
                return _response(_EVENTS_PAYLOAD)
            return _response(_REAL_PAYLOAD)
        return _get

    def test_all_per_symbol_calls_send_organ_code(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._router()) as mocked:
            self.fetcher.get_daily_data("VHM", start_date="2025-11-26", end_date="2025-11-28")
            quote = self.fetcher.get_realtime_quote("ACV")
            self.fetcher.get_corporate_events("MBS", today=date(2026, 10, 21))

        sent = {
            call.args[0].rsplit("/", 1)[-1]: call.kwargs["params"]
            for call in mocked.call_args_list
        }
        self.assertEqual(sent["GetStockChartData"]["Code"], "NHN")
        self.assertEqual(sent["GetPriceDepth"]["Code"], "ACVN")
        self.assertEqual(sent["GetStockEvents"]["OrganCode"], "TLSC")
        # Quote trả mã giao dịch, không trả organCode nội bộ.
        self.assertEqual(quote.code, "ACV")

    def test_organization_map_is_cached(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._router()) as mocked:
            self.fetcher.get_realtime_quote("ACV")
            self.fetcher.get_realtime_quote("VHM")
        org_calls = [c for c in mocked.call_args_list if c.args[0].endswith("/Master/GetListOrganization")]
        self.assertEqual(len(org_calls), 1)

    def test_unknown_ticker_and_failed_map_fall_back_to_ticker(self):
        with patch.object(self.fetcher._session, "get", side_effect=self._router()):
            self.assertEqual(self.fetcher._resolve_code("ZZZ"), ("ZZZ", "Stock", "ZZZ"))

        module.reset_organizations_cache()
        with patch.object(self.fetcher._session, "get", side_effect=DataFetchError("down")):
            self.assertEqual(self.fetcher._resolve_code("VHM"), ("VHM", "Stock", "VHM"))


if __name__ == "__main__":
    unittest.main()
