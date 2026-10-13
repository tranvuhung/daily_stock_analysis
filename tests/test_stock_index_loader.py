# -*- coding: utf-8 -*-
"""
Kiểm thử bộ nạp stock index Việt Nam (``src/data/stock_index_loader.py``).

Tests for the bundled Vietnam stock index loader.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.data import stock_index_loader


def _row(canonical, display, name, aliases=None, active=True):
    # Dòng index dạng tuple nén. / Compressed tuple row.
    return [canonical, display, name, "", "", list(aliases or []), "VN", "stock", active, 100]


def _write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


class TestStockIndexLoader(unittest.TestCase):
    def setUp(self):
        stock_index_loader._clear_stock_index_cache_for_tests()

    def tearDown(self):
        stock_index_loader._clear_stock_index_cache_for_tests()

    def _patch_paths(self, *paths):
        return patch.object(stock_index_loader, "get_stock_index_candidate_paths", return_value=tuple(paths))

    def test_default_candidate_paths_are_bundled_web_then_static(self):
        paths = stock_index_loader.get_stock_index_candidate_paths()
        self.assertEqual(len(paths), 2)
        self.assertTrue(paths[0].as_posix().endswith("apps/dsa-web/public/stocks.index.json"))
        self.assertTrue(paths[1].as_posix().endswith("static/stocks.index.json"))

    def test_get_index_stock_name_supports_display_and_canonical_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "stocks.index.json"
            _write(index_path, [_row("HPG", "HPG", "Hòa Phát"), _row("VCB", "VCB", "Vietcombank")])
            with self._patch_paths(index_path):
                self.assertEqual(stock_index_loader.get_index_stock_name("HPG"), "Hòa Phát")
                self.assertEqual(stock_index_loader.get_index_stock_name("hpg"), "Hòa Phát")
                self.assertEqual(stock_index_loader.get_index_stock_name("VCB"), "Vietcombank")
                self.assertIsNone(stock_index_loader.get_index_stock_name("FPT"))

    def test_get_stock_name_index_map_is_cached_after_first_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "stocks.index.json"
            _write(index_path, [_row("HPG", "HPG", "Hòa Phát")])
            with self._patch_paths(index_path):
                first = stock_index_loader.get_stock_name_index_map()
                _write(index_path, [_row("HPG", "HPG", "Tên đã đổi")])
                second = stock_index_loader.get_stock_name_index_map()

            self.assertIs(first, second)
            self.assertEqual(stock_index_loader.get_index_stock_name("HPG"), "Hòa Phát")

    def test_get_index_stock_name_returns_none_when_index_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self._patch_paths(Path(temp_dir) / "stocks.index.json"):
                self.assertEqual(stock_index_loader.get_stock_name_index_map(), {})
                self.assertIsNone(stock_index_loader.get_index_stock_name("HPG"))

    def test_invalid_utf8_falls_through_to_next_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_path = Path(temp_dir) / "invalid.json"
            valid_path = Path(temp_dir) / "stocks.index.json"
            invalid_path.write_bytes(b"\xff\xfe\xfd")
            _write(valid_path, [_row("VCB", "VCB", "Vietcombank")])
            with self._patch_paths(invalid_path, valid_path):
                self.assertEqual(stock_index_loader.get_index_stock_name("VCB"), "Vietcombank")

    def test_unexpected_json_shape_falls_through_to_next_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            malformed_path = Path(temp_dir) / "malformed.json"
            valid_path = Path(temp_dir) / "stocks.index.json"
            malformed_path.write_text(json.dumps({"code": "VCB"}), encoding="utf-8")
            _write(valid_path, [_row("VCB", "VCB", "Vietcombank")])
            with self._patch_paths(malformed_path, valid_path):
                self.assertEqual(stock_index_loader.get_index_stock_name("VCB"), "Vietcombank")

    def test_first_valid_candidate_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "a.json"
            second = Path(temp_dir) / "b.json"
            _write(first, [_row("HPG", "HPG", "Hòa Phát")])
            _write(second, [_row("HPG", "HPG", "Tên khác"), _row("FPT", "FPT", "FPT Corp")])
            with self._patch_paths(first, second):
                self.assertEqual(stock_index_loader.get_index_stock_name("HPG"), "Hòa Phát")
                self.assertIsNone(stock_index_loader.get_index_stock_name("FPT"))

    def test_resolve_index_stock_code_matches_code_and_alias_exactly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "stocks.index.json"
            _write(index_path, [_row("HPG", "HPG", "Hòa Phát", aliases=["HPG.VN"])])
            with self._patch_paths(index_path):
                self.assertEqual(stock_index_loader.resolve_index_stock_code("hpg"), "HPG")
                self.assertEqual(stock_index_loader.resolve_index_stock_code("hpg.vn"), "HPG")
                self.assertIsNone(stock_index_loader.resolve_index_stock_code("HP"))
                self.assertIsNone(stock_index_loader.resolve_index_stock_code(""))

    def test_inactive_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "stocks.index.json"
            _write(index_path, [_row("OLD", "OLD", "Đã huỷ niêm yết", active=False), _row("HPG", "HPG", "Hòa Phát")])
            with self._patch_paths(index_path):
                self.assertIsNone(stock_index_loader.resolve_index_stock_code("OLD"))
                self.assertEqual(len(stock_index_loader.get_stock_index_rows()), 1)

    def test_clear_stock_index_cache_reloads_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "stocks.index.json"
            _write(index_path, [_row("HPG", "HPG", "Hòa Phát")])
            with self._patch_paths(index_path):
                self.assertEqual(stock_index_loader.resolve_index_stock_code("HPG"), "HPG")
                _write(index_path, [_row("FPT", "FPT", "FPT Corp")])
                stock_index_loader.clear_stock_index_cache()
                self.assertIsNone(stock_index_loader.resolve_index_stock_code("HPG"))
                self.assertEqual(stock_index_loader.resolve_index_stock_code("FPT"), "FPT")

    def test_bundled_index_contains_vn_codes(self):
        # Bản đóng gói thật phải tra được mã VN phổ biến. / Real bundle resolves common VN codes.
        self.assertEqual(stock_index_loader.resolve_index_stock_code("HPG"), "HPG")
        self.assertTrue(stock_index_loader.get_index_stock_name("VCB"))


if __name__ == "__main__":
    unittest.main()
