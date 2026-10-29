# -*- coding: utf-8 -*-
"""Kiểm thử đơn vị giá điểm bắn tỉa kiểu Việt Nam / VN-style sniper price units.

Hồi quy cho analysis_history #159: "51.500 VND" từng bị lưu thành 51.5.
Regression for analysis_history #159 where "51.500 VND" was stored as 51.5.
"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.sniper_points import (
    extract_sniper_points,
    normalize_price_unit,
    parse_sniper_value,
    strip_vn_thousands_separators,
)


class TestVnThousandsSeparator(unittest.TestCase):
    def test_dot_thousands_parsed_as_vnd(self):
        self.assertEqual(parse_sniper_value("51.500 VND"), 51500.0)
        self.assertEqual(parse_sniper_value("50.000 VNĐ"), 50000.0)
        self.assertEqual(parse_sniper_value("33.000 - 33.300"), 33300.0)
        self.assertEqual(parse_sniper_value("1.077.540 VND"), 1077540.0)
        self.assertEqual(
            parse_sniper_value("Cắt lỗ dứt khoát: Khi thủng 32.800 VND (dưới MA20)"),
            32800.0,
        )
        self.assertEqual(
            parse_sniper_value("33.100 - 33.300 VND (vùng kiểm định hỗ trợ MA20)"),
            33300.0,
        )

    def test_real_decimals_are_kept(self):
        # Số thập phân 1-2 chữ số không phải dấu hàng nghìn.
        # 1-2 digit fractions are decimals, not thousands groups.
        self.assertEqual(parse_sniper_value("100.5"), 100.5)
        self.assertEqual(parse_sniper_value("102.10-103.00 (gần MA5)"), 103.0)
        self.assertEqual(parse_sniper_value("22000.0 (Gần vùng hỗ trợ cứng)"), 22000.0)
        self.assertEqual(strip_vn_thousands_separators("MA5 55.14"), "MA5 55.14")


class TestReferencePriceNormalization(unittest.TestCase):
    def test_thousand_unit_values_rescaled_with_reference(self):
        self.assertEqual(parse_sniper_value(54.5, 56000), 54500.0)
        self.assertEqual(parse_sniper_value("51.5", 53300), 51500.0)
        self.assertEqual(parse_sniper_value("55.15 VND", 53300), 55150.0)

    def test_values_already_in_vnd_unchanged(self):
        self.assertEqual(parse_sniper_value(56000.0, 56000), 56000.0)
        self.assertEqual(parse_sniper_value("51.500 VND", 53300), 51500.0)

    def test_implausible_values_not_rescaled(self):
        # 5 * 1000 quá xa giá tham chiếu -> giữ nguyên, không đoán bừa.
        # 5 * 1000 is far from the reference -> leave unchanged.
        self.assertEqual(normalize_price_unit(5.0, 56000), 5.0)
        self.assertEqual(normalize_price_unit(51.5, None), 51.5)
        self.assertEqual(normalize_price_unit(51.5, "n/a"), 51.5)
        self.assertIsNone(normalize_price_unit(None, 53300))

    def test_bool_is_not_a_price(self):
        self.assertIsNone(parse_sniper_value(True))


class TestExtractSniperPoints(unittest.TestCase):
    def test_fpt_159_payload_uses_result_current_price(self):
        result = SimpleNamespace(
            current_price=53300.0,
            dashboard={
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "51.500 VND",
                        "secondary_buy": "50.000 VND",
                        "stop_loss": "51.000 VND",
                        "take_profit": 55.15,
                    }
                }
            },
        )
        self.assertEqual(
            extract_sniper_points(result),
            {
                "ideal_buy": 51500.0,
                "secondary_buy": 50000.0,
                "stop_loss": 51000.0,
                "take_profit": 55150.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
