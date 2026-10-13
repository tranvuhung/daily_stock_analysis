# -*- coding: utf-8 -*-
"""Kiểm thử parser STOCK_LIST / mục tiêu phân tích (chỉ thị trường Việt Nam)."""

import pytest

from src.services.stock_list_parser import (
    ParseStatus,
    parse_analysis_target,
    parse_stock_list,
    serialize_stock_list,
    split_stock_list,
)


def test_split_and_serialize_handle_common_separators() -> None:
    assert split_stock_list("HPG, fpt；VCB\nVNM") == ["HPG", "fpt", "VCB", "VNM"]
    assert serialize_stock_list(" HPG ,FPT;VCB ") == "HPG,FPT,VCB"


@pytest.mark.parametrize(
    "raw, canonical",
    [
        ("HPG", "HPG"),
        ("hpg", "HPG"),
        ("HPG.VN", "HPG"),
        ("vnHPG", "HPG"),
        ("FUEVN100", "FUEVN100"),
        ("vnFUEVN100", "FUEVN100"),
        ("VNINDEX", "VNINDEX"),
        ("vn30", "VN30"),
        ("HNXINDEX", "HNXIndex"),
        ("UPCOM", "HNXUpcomIndex"),
    ],
)
def test_vietnam_codes_parse_as_stock_targets(raw: str, canonical: str) -> None:
    target = parse_analysis_target(raw)
    assert target.asset_type == ParseStatus.STOCK
    assert target.canonical_id == canonical
    assert target.exchange == "VN"
    assert target.unsupported_reason is None


@pytest.mark.parametrize("raw", ["600519", "hk00700", "AAPL", "sh000300", "930955.CSI", "CHPG2401"])
def test_non_vietnam_codes_are_unsupported(raw: str) -> None:
    target = parse_analysis_target(raw)
    assert target.asset_type == ParseStatus.UNSUPPORTED
    assert target.unsupported_reason


def test_empty_input_is_unsupported() -> None:
    target = parse_analysis_target("   ")
    assert target.asset_type == ParseStatus.UNSUPPORTED
    assert target.unsupported_reason == "empty input"


def test_parse_stock_list_returns_target_per_token() -> None:
    targets = parse_stock_list("HPG,AAPL,VNINDEX")
    assert [t.asset_type for t in targets] == [
        ParseStatus.STOCK,
        ParseStatus.UNSUPPORTED,
        ParseStatus.STOCK,
    ]
