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

# Mã không có trên FiinTrade (toàn bộ UPCOM) trả HTTP 200 kèm status Failed.
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

    def test_upcom_index_is_left_to_vci_without_request(self):
        # UpcomIndex của FiinTrade lệch close so với VCI, không được trộn vào stock_daily.
        # FiinTrade's UpcomIndex close drifts from VCI, so it must never be used.
        for code in ("UPCOM", "UPCOMINDEX", "HNXUPCOMINDEX"):
            with self.subTest(code=code):
                with patch.object(self.fetcher._session, "get") as mocked:
                    df = self.fetcher.get_daily_data(code, start_date="2026-10-01", end_date="2026-10-21")
                self.assertTrue(df.empty)
                mocked.assert_not_called()

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
        # Nhiều mã UPCOM liên tiếp không được làm FiinTrade bị ngắt mạch cho mã HOSE.
        # Repeated UPCOM misses must not open the breaker for HOSE symbols.
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

    def test_failed_status_returns_none_for_upcom(self):
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

    def test_upcom_falls_back_to_ssi(self):
        fiin = _QuoteStub("FiinTradeFetcher", None)
        ssi = _QuoteStub("SsiFetcher", self._quote(41700.0))
        manager = DataFetcherManager(fetchers=[fiin, ssi])
        with patch("src.config.get_config", return_value=self._cfg("ssi,vci")):
            quote = manager.get_realtime_quote("ACV")
        self.assertEqual(quote.price, 41700.0)
        self.assertEqual(fiin.calls, 1)


if __name__ == "__main__":
    unittest.main()
