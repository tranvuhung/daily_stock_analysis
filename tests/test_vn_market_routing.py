# -*- coding: utf-8 -*-
"""Nhận diện, chuẩn hoá mã và định tuyến nguồn dữ liệu cho thị trường Việt Nam.

Vietnam market code tagging, normalization and data-source routing.
"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import pandas as pd

from data_provider.base import (
    DataFetchError,
    DataFetcherManager,
    _market_tag,
    is_vn_index_code,
    normalize_stock_code,
)
from src.core import markets
from src.core.pipeline import StockAnalysisPipeline
from src.services.stock_list_parser import ParseStatus, parse_analysis_target


@contextmanager
def supported_markets(value):
    with patch.dict(os.environ, {"SUPPORTED_MARKETS": value}):
        markets.reset_cache()
        try:
            yield
        finally:
            markets.reset_cache()


class _StubFetcher:
    """Fetcher giả tối thiểu cho định tuyến manager. / Minimal fetcher stub."""

    def __init__(self, name, priority, frame=None, error=None):
        self.name = name
        self.priority = priority
        self._frame = frame
        self._error = error
        self.calls = []

    def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
        self.calls.append(stock_code)
        if self._error is not None:
            raise self._error
        return self._frame


def _frame(close=21000.0):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-16", "2026-09-17"]),
            "open": [close, close],
            "high": [close, close],
            "low": [close, close],
            "close": [close, close],
            "volume": [1000, 2000],
            "amount": [close * 1000, close * 2000],
            "pct_chg": [0.0, 0.0],
        }
    )


class MarketTagTestCase(unittest.TestCase):
    def test_vietnam_only_recognises_bare_three_char_codes(self):
        with supported_markets("vn"):
            for code in ("HPG", "FPT", "VNM", "X77", "VW3"):
                self.assertEqual(_market_tag(code), "vn", code)

    def test_vietnam_indices_are_recognised_case_insensitively(self):
        with supported_markets("vn"):
            for code in ("VNINDEX", "vnindex", "VN30", "HNXIndex", "HNXINDEX"):
                self.assertEqual(_market_tag(code), "vn", code)

    def test_non_vn_codes_report_unknown(self):
        with supported_markets("vn"):
            for code in ("600519", "AAPL", "HK00700", "7203.T", "2330.TW"):
                self.assertEqual(_market_tag(code), "unknown", code)

    def test_explicit_vn_forms_are_recognised(self):
        # HPG.VN và vnHPG luôn là mã Việt Nam. / Explicit VN spellings stay VN.
        with supported_markets("vn"):
            self.assertEqual(_market_tag("HPG.VN"), "vn")
            self.assertEqual(_market_tag("vnHPG"), "vn")

    def test_vn_fund_certificates_are_recognised(self):
        # Chứng chỉ quỹ ETF 8 ký tự luôn là Việt Nam (SUPPORTED_MARKETS cũ bị bỏ qua).
        # ETF fund certificates are always VN (legacy SUPPORTED_MARKETS is ignored).
        for value in ("vn", "vn,us"):
            with supported_markets(value):
                for code in ("FUEVN100", "E1VFVN30", "FUCVREIT", "FUEVN100.VN", "vnFUEVN100"):
                    self.assertEqual(_market_tag(code), "vn", f"{value}:{code}")

    def test_warrants_and_futures_are_not_vn_funds(self):
        with supported_markets("vn"):
            for code in ("CHPG2401", "VN30F2410"):
                self.assertNotEqual(_market_tag(code), "vn", code)


class NormalizeVnCodeTestCase(unittest.TestCase):
    def test_all_vn_spellings_collapse_to_one_bare_code(self):
        for raw in ("HPG", "hpg.vn", "HPG.VN", "vnHPG"):
            self.assertEqual(normalize_stock_code(raw).upper(), "HPG", raw)

    def test_vn_fund_spellings_collapse_to_one_bare_code(self):
        for raw in ("FUEVN100", "fuevn100.vn", "FUEVN100.VN", "vnFUEVN100", "VNFUEVN100"):
            self.assertEqual(normalize_stock_code(raw).upper(), "FUEVN100", raw)

    def test_index_codes_are_not_mistaken_for_vn_prefix(self):
        # ``VN30`` bắt đầu bằng VN nhưng là mã chỉ số, không được bị cắt thành ``30``
        self.assertEqual(normalize_stock_code("VN30"), "VN30")
        self.assertEqual(normalize_stock_code("VNINDEX"), "VNINDEX")
        # ``VNM`` là mã cổ phiếu; cắt tiền tố chỉ còn 1 ký tự nên giữ nguyên
        self.assertEqual(normalize_stock_code("VNM"), "VNM")


class ParseAnalysisTargetVnTestCase(unittest.TestCase):
    def test_vn_spellings_share_one_canonical_id(self):
        # canonical_id được ghi vào stock_daily.canonical_id và dùng để khử trùng lặp;
        # các cách viết khác nhau phải về cùng một canonical_id.
        with supported_markets("vn"):
            ids = {
                parse_analysis_target(raw).canonical_id
                for raw in ("HPG", "hpg", "HPG.VN", "vnHPG")
            }
            self.assertEqual(ids, {"HPG"})

    def test_vn_fund_target_routes_to_vn(self):
        for value in ("vn", "vn,us"):
            with supported_markets(value):
                for raw in ("FUEVN100", "fuevn100", "FUEVN100.VN", "vnFUEVN100"):
                    target = parse_analysis_target(raw)
                    self.assertEqual(target.exchange, "VN", f"{value}:{raw}")
                    self.assertEqual(target.canonical_id, "FUEVN100", f"{value}:{raw}")
                    self.assertEqual(target.asset_type, ParseStatus.STOCK, f"{value}:{raw}")

    def test_vn_fund_codes_are_code_like(self):
        # Endpoint /analyze dùng is_code_like để quyết định mã hay tên; nếu mã quỹ
        # không code-like thì "FUEVN100" (chữ + số) bị từ chối như chuỗi rác.
        from src.services.stock_code_utils import is_code_like

        for code in ("FUEVN100", "fuevn100", "E1VFVN30", "FUEVN100.VN", "vnFUEVN100", "HPG.VN"):
            self.assertTrue(is_code_like(code), code)
        for code in ("CHPG2401", "VN30F2410"):
            self.assertFalse(is_code_like(code), code)

    def test_analysis_endpoint_accepts_vn_fund_codes(self):
        from tests.litellm_stub import ensure_litellm_stub

        ensure_litellm_stub()
        from data_provider.base import normalize_stock_code as _normalize
        from fastapi import HTTPException
        from api.v1.endpoints.analysis import _resolve_analysis_input

        with supported_markets("vn"):
            for raw in ("FUEVN100", "fuevn100", "FUEVN100.VN", "vnFUEVN100"):
                code, _target = _resolve_analysis_input(raw)
                # Mã trả về phải quy về đúng mã quỹ và được nhận là Việt Nam ở bước sau.
                self.assertEqual(_normalize(code), "FUEVN100", raw)
                self.assertEqual(_market_tag(code), "vn", raw)
            with self.assertRaises(HTTPException):
                _resolve_analysis_input("CHPG2401")

    def test_vn_target_is_stock_not_index(self):
        # Chỉ số Việt Nam đi chuỗi nến ngày thông thường (ParseStatus.STOCK).
        with supported_markets("vn"):
            target = parse_analysis_target("VNINDEX")
            self.assertEqual(target.asset_type, ParseStatus.STOCK)
            self.assertEqual(target.exchange, "VN")
            self.assertEqual(target.canonical_id, "VNINDEX")

    def test_index_alias_normalises_to_provider_casing(self):
        with supported_markets("vn"):
            self.assertEqual(
                parse_analysis_target("hnxindex").canonical_id, "HNXIndex"
            )
            self.assertEqual(parse_analysis_target("upcom").canonical_id, "UPCOMINDEX")
            # Không có mã chỉ số nào tên HNXUPCOMINDEX (đó chỉ là cách viết của VCI/SSI).
            # No index is called HNXUPCOMINDEX; that is only a provider spelling.
            self.assertFalse(is_vn_index_code("HNXUPCOMINDEX"))

class ManagerGatingTestCase(unittest.TestCase):
    def test_daily_data_rejects_non_vn_code_without_touching_fetchers(self):
        fetcher = _StubFetcher("VciFetcher", 0, frame=_frame())
        with supported_markets("vn"):
            manager = DataFetcherManager(fetchers=[fetcher])
            with self.assertRaises(DataFetchError) as ctx:
                manager.get_daily_data("AAPL", days=10)
        self.assertIn("not a Vietnam", str(ctx.exception))
        self.assertEqual(fetcher.calls, [], "Mã ngoài VN không được gọi nguồn dữ liệu nào")

    def test_daily_data_routes_vn_code_through_generic_chain(self):
        vci = _StubFetcher("VciFetcher", 0, frame=_frame(21000.0))
        with supported_markets("vn"):
            manager = DataFetcherManager(fetchers=[vci])
            df, source = manager.get_daily_data("HPG", days=10)
        self.assertEqual(source, "VciFetcher")
        self.assertEqual(vci.calls, ["HPG"])
        self.assertFalse(df.empty)

    def test_daily_data_falls_back_from_vci_to_ssi(self):
        vci = _StubFetcher("VciFetcher", 0, error=DataFetchError("boom"))
        ssi = _StubFetcher("SsiFetcher", 1, frame=_frame(20950.0))
        with supported_markets("vn"):
            DataFetcherManager.reset_daily_source_health()
            manager = DataFetcherManager(fetchers=[vci, ssi])
            df, source = manager.get_daily_data("HPG", days=10)
            DataFetcherManager.reset_daily_source_health()
        self.assertEqual(source, "SsiFetcher")
        self.assertEqual(vci.calls, ["HPG"])
        self.assertEqual(ssi.calls, ["HPG"])
        self.assertEqual(df["close"].iloc[-1], 20950.0)

    def test_realtime_quote_returns_none_for_disabled_market(self):
        with supported_markets("vn"):
            manager = DataFetcherManager(fetchers=[])
            self.assertIsNone(manager.get_realtime_quote("AAPL"))

class PipelineFilterTestCase(unittest.TestCase):
    def test_disabled_market_codes_are_dropped_before_thread_pool(self):
        with supported_markets("vn"):
            codes, targets = StockAnalysisPipeline._filter_codes_by_enabled_markets(
                ["HPG", "600519", "AAPL", "FPT"], None
            )
        self.assertEqual(codes, ["HPG", "FPT"])
        self.assertIsNone(targets)

    def test_targets_stay_aligned_with_codes(self):
        sentinel = object()
        with supported_markets("vn"):
            codes, targets = StockAnalysisPipeline._filter_codes_by_enabled_markets(
                ["HPG", "AAPL", "FPT"], [sentinel, "dropped", None]
            )
        self.assertEqual(codes, ["HPG", "FPT"])
        self.assertEqual(targets, [sentinel, None])

    def test_misaligned_inputs_raise(self):
        with supported_markets("vn"):
            with self.assertRaises(ValueError):
                StockAnalysisPipeline._filter_codes_by_enabled_markets(["HPG"], [])


class DataCapabilityOverviewTestCase(unittest.TestCase):
    def _providers(self):
        from src.services.data_capability_service import DataCapabilityService

        return {p["name"]: p for p in DataCapabilityService().get_overview()["providers"]}

    def test_vn_providers_are_declared(self):
        with supported_markets("vn"):
            providers = self._providers()
        self.assertEqual(providers["vci"]["markets"], ["vn"])
        self.assertEqual(providers["ssi"]["markets"], ["vn"])
        # Realtime: TVSI tick -> SSI -> VCI, nên cả hai đều khai quote.realtime.
        self.assertIn("quote.realtime", providers["vci"]["datasets"])
        self.assertIn("quote.realtime", providers["ssi"]["datasets"])

    def test_only_vn_providers_are_listed(self):
        self.assertEqual(set(self._providers()), {"fiintrade", "vci", "ssi", "vnstock", "yfinance"})

    def test_yfinance_serves_vn_daily_only(self):
        """Yahoo là dự phòng cấp 3 cho nến ngày VN, không được khai gì thêm.

        Thực đo: Yahoo chỉ phủ sàn HOSE (20/20 mã mẫu), HNX 0/20, UPCOM 0/20, và
        không có lịch sử chỉ số Việt Nam (``^VNINDEX.VN`` xin 1 năm trả đúng 1 nến).
        Khai thêm quote.realtime hay index.daily cho ``vn`` sẽ là pseudo-fallback.
        """

        with supported_markets("vn"):
            providers = self._providers()

        yfinance = providers["yfinance"]
        self.assertIn("vn", yfinance["markets"])
        self.assertNotIn("market_disabled", yfinance["warnings"])

        datasets = yfinance["dataset_markets"]
        self.assertEqual(datasets, {"kline.daily": ["vn"]})

    def test_vnstock_is_unconfigured_but_not_market_disabled(self):
        with supported_markets("vn"):
            providers = self._providers()
        self.assertEqual(providers["vnstock"]["status"], "unconfigured")
        self.assertNotIn("market_disabled", providers["vnstock"]["warnings"])



if __name__ == "__main__":
    unittest.main()
