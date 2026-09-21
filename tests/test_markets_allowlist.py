# -*- coding: utf-8 -*-
"""市场白名单（SUPPORTED_MARKETS）注册表行为。

这些用例都显式设置 ``SUPPORTED_MARKETS``，不依赖 conftest 里给整个测试进程
设置的 ``all``。
"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from src.core import markets
from src.utils import market_review_region as mrr


@contextmanager
def supported_markets(value):
    """临时设置 allowlist，并保证前后都清掉注册表缓存。"""

    env = {} if value is None else {"SUPPORTED_MARKETS": value}
    with patch.dict(os.environ, env, clear=(value is None)):
        markets.reset_cache()
        try:
            yield
        finally:
            markets.reset_cache()


class ParseSupportedMarketsTestCase(unittest.TestCase):
    def test_default_is_vietnam_only(self):
        with supported_markets(None):
            self.assertEqual(markets.enabled_markets(), ("vn",))

    def test_all_expands_to_every_known_market(self):
        with supported_markets("all"):
            self.assertEqual(markets.enabled_markets(), markets.ALL_MARKET_CODES)

    def test_comma_list_is_case_and_whitespace_insensitive(self):
        with supported_markets(" CN , Us "):
            self.assertEqual(markets.enabled_markets(), ("us", "cn"))

    def test_result_is_ordered_by_detect_order_not_input_order(self):
        # vn 必须排在 us 之前：越南裸代码与美股 ticker 正则重叠，判定顺序决定归属
        with supported_markets("us,vn"):
            self.assertEqual(markets.enabled_markets(), ("vn", "us"))

    def test_unknown_tokens_are_ignored_but_valid_ones_kept(self):
        with supported_markets("vn,atlantis"):
            self.assertEqual(markets.enabled_markets(), ("vn",))

    def test_all_tokens_invalid_falls_back_to_default(self):
        with supported_markets("atlantis,narnia"):
            self.assertEqual(markets.enabled_markets(), ("vn",))

    def test_empty_value_falls_back_to_default(self):
        with supported_markets("   "):
            self.assertEqual(markets.enabled_markets(), ("vn",))

    def test_is_market_enabled_rejects_unknown_and_empty(self):
        with supported_markets("vn"):
            self.assertTrue(markets.is_market_enabled("vn"))
            self.assertTrue(markets.is_market_enabled(" VN "))
            self.assertFalse(markets.is_market_enabled("cn"))
            self.assertFalse(markets.is_market_enabled(""))
            self.assertFalse(markets.is_market_enabled(None))


class RegistryMetadataTestCase(unittest.TestCase):
    def test_vietnam_has_no_exchange_calendar(self):
        # exchange-calendars 未收录 HOSE/HNX/UPCOM；交易日判断走 fail-open 分支。
        # 如果将来该库加入了越南日历，这里应当同步填上 MIC 并更新本用例。
        self.assertIsNone(markets.ALL_MARKETS["vn"].xcals_code)
        self.assertEqual(markets.ALL_MARKETS["vn"].timezone, "Asia/Ho_Chi_Minh")
        self.assertEqual(markets.ALL_MARKETS["vn"].currency, "VND")

    def test_every_market_with_review_support_has_review_order(self):
        for spec in markets.ALL_MARKETS.values():
            if spec.supports_market_review:
                self.assertLess(
                    spec.review_order, 99, f"{spec.code} 缺少 review_order"
                )

    def test_enabled_market_exchanges_skips_markets_without_calendar(self):
        with supported_markets("vn,cn"):
            self.assertEqual(markets.enabled_market_exchanges(), {"cn": "XSHG"})
            self.assertEqual(
                markets.enabled_market_timezones(),
                {"vn": "Asia/Ho_Chi_Minh", "cn": "Asia/Shanghai"},
            )

    def test_market_label_falls_back_to_upper_code(self):
        self.assertEqual(markets.market_label("vn"), "Việt Nam")
        self.assertEqual(markets.market_label("vn", "en"), "Vietnam")
        self.assertEqual(markets.market_label("atlantis"), "ATLANTIS")


class MarketReviewRegionTestCase(unittest.TestCase):
    def test_review_regions_keep_legacy_order(self):
        with supported_markets("all"):
            self.assertEqual(
                mrr.enabled_market_review_regions(), ("cn", "hk", "us", "jp", "kr")
            )
            self.assertEqual(mrr.MARKET_REVIEW_REGION_ALL, "cn,hk,us,jp,kr")

    def test_vietnam_only_has_no_review_region(self):
        # 越南还没有复盘 profile/prompt，不能让它默认回退到 cn：那会用已停用的
        # A 股数据源生成一份没有数据支撑的复盘。
        with supported_markets("vn"):
            self.assertEqual(mrr.enabled_market_review_regions(), ())
            self.assertFalse(mrr.market_review_supported())
            self.assertIsNone(mrr.normalize_market_review_region_lenient("cn"))
            self.assertIsNone(mrr.normalize_market_review_region_lenient(None))
            with self.assertRaises(ValueError):
                mrr.normalize_market_review_region_strict("cn")

    def test_disabled_market_is_rejected_by_strict_parser(self):
        with supported_markets("vn,cn"):
            self.assertEqual(mrr.normalize_market_review_region_strict("cn"), "cn")
            with self.assertRaises(ValueError):
                mrr.normalize_market_review_region_strict("us")

    def test_both_expands_only_to_enabled_regions(self):
        with supported_markets("cn,us"):
            self.assertEqual(mrr.normalize_market_review_region_lenient("both"), "cn,us")
            self.assertEqual(mrr.normalize_market_review_region_strict("both"), "cn,us")


if __name__ == "__main__":
    unittest.main()
