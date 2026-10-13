# -*- coding: utf-8 -*-
"""Hành vi registry thị trường (chỉ Việt Nam) và biến ``SUPPORTED_MARKETS`` cũ.

Market registry behavior (Vietnam only). ``SUPPORTED_MARKETS`` is still read for
backward compatibility, but any non-``vn`` value is ignored.
"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from src.core import markets
from src.utils import market_review_region as mrr


@contextmanager
def supported_markets(value):
    """Đặt tạm SUPPORTED_MARKETS và xoá cache registry trước/sau. / Temporarily set the env var."""

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

    def test_registry_contains_only_vietnam(self):
        self.assertEqual(markets.ALL_MARKET_CODES, ("vn",))

    def test_legacy_non_vn_values_are_ignored(self):
        # Giá trị cũ (all/cn/us) chỉ cảnh báo rồi bị bỏ qua. / Legacy values are ignored.
        for value in ("all", " CN , Us ", "us,vn"):
            with self.subTest(value=value), supported_markets(value):
                self.assertEqual(markets.enabled_markets(), ("vn",))

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
        # exchange-calendars không có lịch HOSE/HNX/UPCOM; ngày giao dịch đi nhánh fail-open.
        self.assertIsNone(markets.ALL_MARKETS["vn"].xcals_code)
        self.assertEqual(markets.ALL_MARKETS["vn"].timezone, "Asia/Ho_Chi_Minh")
        self.assertEqual(markets.ALL_MARKETS["vn"].currency, "VND")

    def test_every_market_with_review_support_has_review_order(self):
        for spec in markets.ALL_MARKETS.values():
            if spec.supports_market_review:
                self.assertLess(
                    spec.review_order, 99, f"{spec.code} thiếu review_order"
                )

    def test_enabled_market_exchanges_skips_markets_without_calendar(self):
        with supported_markets("vn,cn"):
            self.assertEqual(markets.enabled_market_exchanges(), {})
            self.assertEqual(markets.enabled_market_timezones(), {"vn": "Asia/Ho_Chi_Minh"})

    def test_market_label_falls_back_to_upper_code(self):
        self.assertEqual(markets.market_label("vn"), "Việt Nam")
        self.assertEqual(markets.market_label("vn", "en"), "Vietnam")
        self.assertEqual(markets.market_label("atlantis"), "ATLANTIS")


class MarketReviewRegionTestCase(unittest.TestCase):
    def test_review_regions_are_vietnam_only(self):
        self.assertEqual(mrr.MARKET_REVIEW_REGION_ORDER, ("vn",))
        self.assertEqual(mrr.MARKET_REVIEW_REGION_ALL, "vn")
        with supported_markets("all"):
            self.assertEqual(mrr.enabled_market_review_regions(), ("vn",))

    def test_vietnam_only_reviews_vietnam(self):
        with supported_markets("vn"):
            self.assertEqual(mrr.enabled_market_review_regions(), ("vn",))
            self.assertTrue(mrr.market_review_supported())
            self.assertEqual(mrr.normalize_market_review_region_lenient(None), "vn")
            self.assertEqual(mrr.normalize_market_review_region_strict("VN"), "vn")

    def test_legacy_region_values_fold_into_vn(self):
        # Client/cấu hình cũ gửi cn/both/... vẫn được quy về vn.
        # Legacy region tokens normalize to vn for backward compatibility.
        for value in ("both", "cn", "cn,us", "hk"):
            with self.subTest(value=value):
                self.assertEqual(mrr.normalize_market_review_region_lenient(value), "vn")
                self.assertEqual(mrr.normalize_market_review_region_strict(value), "vn")

    def test_unknown_region_rejected(self):
        self.assertIsNone(mrr.normalize_market_review_region_lenient("atlantis"))
        for value in ("atlantis", "", "vn,,"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mrr.normalize_market_review_region_strict(value)


if __name__ == "__main__":
    unittest.main()
