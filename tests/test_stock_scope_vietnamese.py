# -*- coding: utf-8 -*-
"""Nhận diện ý định chuyển mã / so sánh cho câu hỏi tiếng Việt và tiếng Anh."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.stock_scope import resolve_stock_scope  # noqa: E402

_CTX = {"stock_code": "HAG", "stock_name": "Hoàng Anh Gia Lai"}


@pytest.mark.parametrize(
    "message",
    [
        "Phân tích mã LAS",
        "Xem LAS thế nào",
        "LAS có nên mua không?",
        "Đánh giá LAS",
        "Analyze LAS",
        # Mã viết thường chỉ được nhận khi có ý định chuyển mã và có trong stock index.
        # Lowercase tickers count only with switch intent and when present in the stock index.
        "phân tích las",
    ],
)
def test_vietnamese_and_english_switch_moves_scope_to_new_stock(message: str) -> None:
    scope = resolve_stock_scope(message, _CTX).stock_scope

    assert scope.mode == "switch"
    assert scope.expected_stock_code == "LAS"
    assert scope.allowed_stock_codes == {"LAS"}


@pytest.mark.parametrize(
    "message",
    ["So sánh HAG và LAS", "HAG với LAS mã nào tốt hơn", "Compare HAG and LAS", "so sánh hag và las"],
)
def test_vietnamese_and_english_compare_allows_both_stocks(message: str) -> None:
    scope = resolve_stock_scope(message, _CTX).stock_scope

    assert scope.mode == "compare"
    assert scope.allowed_stock_codes == {"HAG", "LAS"}


@pytest.mark.parametrize(
    "message",
    [
        "Phân tích tiếp",
        "Hỗ trợ của HAG ở đâu?",
        # Từ tiếng Việt thường ("xem", "ra", "sao", "đang", "mua") không phải ticker.
        # Plain Vietnamese words must not be mistaken for tickers.
        "xem ra sao",
        "đang xem thế nào",
        "có nên mua không",
    ],
)
def test_follow_ups_without_new_ticker_keep_current_stock(message: str) -> None:
    scope = resolve_stock_scope(message, _CTX).stock_scope

    assert scope.mode == "maintain"
    assert scope.allowed_stock_codes == {"HAG"}


def test_lowercase_index_ticker_is_extracted_with_switch_intent() -> None:
    # "phân tích hpg" -> HPG: mã viết thường có trong stock index.
    # Lowercase ticker present in the bundled stock index is extracted.
    scope = resolve_stock_scope("phân tích hpg", _CTX).stock_scope

    assert scope.mode == "switch"
    assert scope.expected_stock_code == "HPG"
