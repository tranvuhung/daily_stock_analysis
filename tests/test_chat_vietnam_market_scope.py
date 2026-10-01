# -*- coding: utf-8 -*-
"""Hỏi đáp chỉ hỗ trợ thị trường đang bật (mặc định Việt Nam): vai trò/hướng dẫn thị trường
và mục "Market scope" trong prompt chat lấy từ SUPPORTED_MARKETS."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.executor import build_chat_market_scope_section  # noqa: E402
from src.core.markets import reset_cache  # noqa: E402
from src.market_context import get_market_guidelines, get_market_role  # noqa: E402


@pytest.fixture
def supported_markets(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setenv("SUPPORTED_MARKETS", value)
        reset_cache()

    yield _set
    reset_cache()


@pytest.mark.parametrize("code", ["LAS", "FPT", "HPG", "VNINDEX", "", None])
def test_vietnam_only_deployment_uses_vietnam_market_role(supported_markets, code) -> None:
    supported_markets("vn")

    assert get_market_role(code, "vi") == "Vietnam stock"
    assert "Vietnamese stock" in get_market_guidelines(code, "vi")


def test_non_vietnamese_codes_keep_legacy_roles(supported_markets) -> None:
    supported_markets("vn")

    assert get_market_role("AAPL", "en") == "US stock"
    assert get_market_role("600519", "en") == "China A-shares"


def test_chat_market_scope_lists_only_enabled_markets(supported_markets) -> None:
    supported_markets("vn")
    section = build_chat_market_scope_section()

    assert "only supports these markets: **Vietnam**" in section
    assert "do not call any tools" in section
    assert "HOSE / HNX / UPCoM" in section


def test_chat_market_scope_is_empty_when_all_markets_enabled(supported_markets) -> None:
    supported_markets("all")

    assert build_chat_market_scope_section() == ""
