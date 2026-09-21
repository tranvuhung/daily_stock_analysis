# -*- coding: utf-8 -*-
"""越南市场的代码归属、归一化与数据源路由。"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import pandas as pd

from data_provider.base import (
    DataFetchError,
    DataFetcherManager,
    _market_tag,
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
    """最小可用的 fetcher 替身，只实现 manager 路由需要的接口。"""

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

    def test_disabled_markets_report_unknown(self):
        with supported_markets("vn"):
            for code in ("600519", "AAPL", "HK00700", "7203.T", "2330.TW"):
                self.assertEqual(_market_tag(code), "unknown", code)

    def test_enabling_all_markets_preserves_legacy_classification(self):
        # 回滚配置必须与改动前完全一致，否则 allowlist 就不是一个安全开关。
        with supported_markets("all"):
            expected = {
                "600519": "cn",
                "AAPL": "us",
                "00700": "hk",
                "HK00700": "hk",
                "7203.T": "jp",
                "005930.KS": "kr",
                "2330.TW": "tw",
                "SPX": "us",
            }
            for code, market in expected.items():
                self.assertEqual(_market_tag(code), market, code)

    def test_explicit_vn_forms_survive_when_both_markets_enabled(self):
        # 文档承诺 HPG.VN 和 vnHPG 是 vn+us 并存时的消歧写法，必须真的有效。
        # 市场判定发生在 normalize_stock_code 之前，否则 .VN 标记会被抹掉。
        with supported_markets("vn,us"):
            self.assertEqual(_market_tag("HPG.VN"), "vn")
            self.assertEqual(_market_tag("vnHPG"), "vn")

    def test_uppercase_vn_prefix_is_treated_as_us_ticker(self):
        # 沿用 us 前缀的既有约定：前缀必须小写。否则全大写的 5 位美股 ticker
        # （VNRDF 等）会被误剥成越南代码 RDF。
        with supported_markets("vn,us"):
            self.assertEqual(_market_tag("VNHPG"), "us")
            self.assertEqual(_market_tag("VNRDF"), "us")
            self.assertEqual(normalize_stock_code("VNRDF"), "VNRDF")
            self.assertEqual(parse_analysis_target("VNRDF").exchange, "US")

    def test_bare_vn_code_defers_to_us_when_both_enabled(self):
        # HPG 同时是合法的越南代码和合法的美股 ticker 形状。两个市场都启用时
        # 必须保持既有的美股语义，用户用 HPG.VN 显式消歧。
        with supported_markets("vn,us"):
            self.assertEqual(_market_tag("HPG"), "us")
            self.assertEqual(_market_tag("HPG.VN"), "vn")


class NormalizeVnCodeTestCase(unittest.TestCase):
    def test_all_vn_spellings_collapse_to_one_bare_code(self):
        for raw in ("HPG", "hpg.vn", "HPG.VN", "vnHPG"):
            self.assertEqual(normalize_stock_code(raw).upper(), "HPG", raw)

    def test_index_codes_are_not_mistaken_for_vn_prefix(self):
        # ``VN30`` 以 VN 开头，但它是指数代码，不能被剥成 ``30``
        self.assertEqual(normalize_stock_code("VN30"), "VN30")
        self.assertEqual(normalize_stock_code("VNINDEX"), "VNINDEX")
        # ``VNM`` 是股票代码，剥掉前缀只剩 1 个字符，不符合越南代码形状
        self.assertEqual(normalize_stock_code("VNM"), "VNM")


class ParseAnalysisTargetVnTestCase(unittest.TestCase):
    def test_vn_spellings_share_one_canonical_id(self):
        # canonical_id 会写入 stock_daily.canonical_id 并参与去重；三种写法落到
        # 不同 canonical_id 会造成重复行并让断点续传失效。
        with supported_markets("vn"):
            ids = {
                parse_analysis_target(raw).canonical_id
                for raw in ("HPG", "hpg", "HPG.VN", "vnHPG")
            }
            self.assertEqual(ids, {"HPG"})

    def test_vn_target_is_stock_not_index(self):
        # ParseStatus.INDEX 会被路由到 A 股专用指数链，越南指数必须走普通日线链
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
            self.assertEqual(parse_analysis_target("upcom").canonical_id, "HNXUpcomIndex")

    def test_legacy_markets_unchanged_when_all_enabled(self):
        with supported_markets("all"):
            self.assertEqual(parse_analysis_target("AAPL").exchange, "US")
            self.assertEqual(parse_analysis_target("600519").exchange, "SH")


class ManagerGatingTestCase(unittest.TestCase):
    def test_daily_data_rejects_disabled_market_without_touching_fetchers(self):
        fetcher = _StubFetcher("VciFetcher", 0, frame=_frame())
        with supported_markets("vn"):
            manager = DataFetcherManager(fetchers=[fetcher])
            with self.assertRaises(DataFetchError) as ctx:
                manager.get_daily_data("AAPL", days=10)
        self.assertIn("SUPPORTED_MARKETS", str(ctx.exception))
        self.assertEqual(fetcher.calls, [], "停用市场不应发出任何数据源调用")

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

    def test_vn_fetchers_are_not_tried_for_cn_codes(self):
        # 回归：能力过滤此前对 market == "cn" 整体跳过（当时所有数据源都支持
        # A 股）。接入越南数据源后不过滤会让 VciFetcher/SsiFetcher 被拿去跑
        # A 股代码并必然失败，浪费请求且污染失败日志。
        vci = _StubFetcher("VciFetcher", 0, error=DataFetchError("不应被调用"))
        ssi = _StubFetcher("SsiFetcher", 1, error=DataFetchError("不应被调用"))
        efinance = _StubFetcher("EfinanceFetcher", 2, frame=_frame(1700.0))
        with supported_markets("all"):
            DataFetcherManager.reset_daily_source_health()
            manager = DataFetcherManager(fetchers=[vci, ssi, efinance])
            df, source = manager.get_daily_data("600519", days=10)
            DataFetcherManager.reset_daily_source_health()
        self.assertEqual(source, "EfinanceFetcher")
        self.assertEqual(vci.calls, [])
        self.assertEqual(ssi.calls, [])
        self.assertFalse(df.empty)

    def test_cn_fetchers_are_not_tried_for_vn_codes(self):
        efinance = _StubFetcher("EfinanceFetcher", 0, error=DataFetchError("不应被调用"))
        vci = _StubFetcher("VciFetcher", 1, frame=_frame(21000.0))
        with supported_markets("all"):
            DataFetcherManager.reset_daily_source_health()
            manager = DataFetcherManager(fetchers=[efinance, vci])
            df, source = manager.get_daily_data("HPG.VN", days=10)
            DataFetcherManager.reset_daily_source_health()
        self.assertEqual(source, "VciFetcher")
        self.assertEqual(efinance.calls, [])

    def test_realtime_quote_returns_none_for_disabled_market(self):
        with supported_markets("vn"):
            manager = DataFetcherManager(fetchers=[])
            self.assertIsNone(manager.get_realtime_quote("AAPL"))

    def test_fetcher_filter_drops_sources_serving_only_disabled_markets(self):
        efinance = _StubFetcher("EfinanceFetcher", 0)
        vci = _StubFetcher("VciFetcher", 0)
        with supported_markets("vn"):
            kept = DataFetcherManager._filter_fetchers_by_enabled_markets([efinance, vci])
        self.assertEqual([f.name for f in kept], ["VciFetcher"])

    def test_fetcher_filter_keeps_unregistered_sources(self):
        # 未在 _DAILY_MARKET_FETCHER_SUPPORT 登记 = 市场覆盖未知，保守保留
        unknown = _StubFetcher("SomeThirdPartyFetcher", 0)
        with supported_markets("vn"):
            kept = DataFetcherManager._filter_fetchers_by_enabled_markets([unknown])
        self.assertEqual([f.name for f in kept], ["SomeThirdPartyFetcher"])


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
        # VCI 没有独立实时接口，不能声明 quote.realtime，否则是 pseudo-fallback
        self.assertNotIn("quote.realtime", providers["vci"]["datasets"])
        self.assertIn("quote.realtime", providers["ssi"]["datasets"])

    def test_disabled_market_providers_report_market_disabled_warning(self):
        # 状态沿用既有的 unconfigured 取值（ProviderCapabilityStatus 是对外契约，
        # 新增枚举值会破坏做穷举匹配的客户端），原因通过 warnings 传递。
        with supported_markets("vn"):
            providers = self._providers()
        # ``yfinance`` cố tình không nằm ở đây: nó phục vụ ``vn`` ở vai trò dự phòng
        # cấp 3 cho nến ngày, xem test_yfinance_serves_vn_daily_only bên dưới.
        for name in ("efinance", "akshare", "longbridge"):
            self.assertFalse(providers[name]["enabled"], name)
            self.assertEqual(providers[name]["status"], "unconfigured", name)
            self.assertIn("market_disabled", providers[name]["warnings"], name)

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
        self.assertIn("vn", datasets["kline.daily"])
        for dataset in ("quote.realtime", "index.daily", "market.overview",
                        "financial.snapshot"):
            self.assertNotIn("vn", datasets[dataset], dataset)

    def test_vnstock_is_unconfigured_but_not_market_disabled(self):
        with supported_markets("vn"):
            providers = self._providers()
        self.assertEqual(providers["vnstock"]["status"], "unconfigured")
        self.assertNotIn("market_disabled", providers["vnstock"]["warnings"])

    def test_enabling_all_markets_restores_legacy_providers(self):
        with supported_markets("all"):
            providers = self._providers()
        for name in ("efinance", "akshare", "yfinance"):
            self.assertNotIn("market_disabled", providers[name]["warnings"], name)


if __name__ == "__main__":
    unittest.main()
