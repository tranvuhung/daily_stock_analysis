# -*- coding: utf-8 -*-
"""Kiểm thử tra tên cổ phiếu Việt Nam -> mã (offline, dựa trên stock index đóng gói)."""

from unittest.mock import patch

import pytest

from src.services import name_to_code_resolver as resolver

_ROWS = [
    ["HPG", "HPG", "Hòa Phát", "hoaphat", "hp", [], "VN", "stock", True, 0],
    ["VCB", "VCB", "Vietcombank", "vietcombank", "vcb", [], "VN", "stock", True, 0],
    ["AAA", "AAA", "Nhựa An Phát", "nhuaanphat", "nap", [], "VN", "stock", True, 0],
    ["BBB", "BBB", "Nhựa An Phát", "nhuaanphat", "nap", [], "VN", "stock", True, 0],
]


@pytest.fixture(autouse=True)
def _fixed_index():
    resolver.clear_name_index_cache()
    with patch("src.data.stock_index_loader.get_stock_index_rows", return_value=_ROWS):
        yield
    resolver.clear_name_index_cache()


@pytest.mark.parametrize("raw, code", [("HPG", "HPG"), ("hpg.vn", "HPG"), ("vnindex", "VNINDEX")])
def test_code_like_input_is_normalized(raw: str, code: str) -> None:
    assert resolver.resolve_name_to_code(raw) == code


@pytest.mark.parametrize("name", ["Hòa Phát", "hoa phat", "HÒA PHÁT", "hoaphat"])
def test_exact_name_with_or_without_diacritics(name: str) -> None:
    assert resolver.resolve_name_to_code(name) == "HPG"


def test_ambiguous_name_returns_none() -> None:
    assert resolver.resolve_name_to_code("Nhựa An Phát") is None


def test_fuzzy_match_for_small_typo() -> None:
    assert resolver.resolve_name_to_code("Vietcombankk") == "VCB"


@pytest.mark.parametrize("name", ["", "   ", "không tồn tại công ty nào"])
def test_unknown_or_empty_returns_none(name: str) -> None:
    assert resolver.resolve_name_to_code(name) is None
