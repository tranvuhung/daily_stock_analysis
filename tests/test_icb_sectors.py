# -*- coding: utf-8 -*-
"""Kiểm tra quy đổi mã ICB cấp 4 -> tên ngành cấp 2."""

import unittest

from src.core.icb_sectors import icb_supersector_name


class IcbSupersectorNameTestCase(unittest.TestCase):
    def test_maps_level4_codes_to_supersector(self):
        self.assertEqual(icb_supersector_name("8355"), "Ngân hàng")
        self.assertEqual(icb_supersector_name("1757"), "Tài nguyên cơ bản")
        self.assertEqual(icb_supersector_name("9537"), "Công nghệ Thông tin")
        self.assertEqual(icb_supersector_name("8633"), "Bất động sản")
        self.assertEqual(icb_supersector_name("8777"), "Dịch vụ tài chính")
        self.assertEqual(icb_supersector_name("0533"), "Dầu khí")
        self.assertEqual(icb_supersector_name("8985"), "Chứng chỉ quỹ")

    def test_unknown_or_invalid_codes_return_none(self):
        for code in (None, "", "abc", "83", "1000", "8355x"):
            self.assertIsNone(icb_supersector_name(code), code)


if __name__ == "__main__":
    unittest.main()
