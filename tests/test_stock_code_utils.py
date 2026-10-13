# -*- coding: utf-8 -*-
"""
Kiểm thử cho src/services/stock_code_utils.py (chỉ thị trường Việt Nam).

Tests for src/services/stock_code_utils.py (Vietnam market only).
Covers: is_code_like, normalize_code, daily identity/candidates and the
analysis-entry index resolver.
"""

from unittest.mock import patch

import pytest

from src.services.stock_code_utils import (
    build_daily_code_candidates,
    is_code_like,
    normalize_code,
    resolve_daily_stock_identity,
    resolve_index_stock_code_for_analysis,
)


class TestBuildDailyCodeCandidates:
    @pytest.mark.parametrize(
        "code",
        ["600519", "000001.SZ", "AAPL", "00700.HK", "HPG.HK", "", "Hòa Phát"],
    )
    def test_rejects_non_vn_codes(self, code):
        # Mã ngoài VN không sinh ứng viên nào. / Non-VN codes yield no candidates.
        assert build_daily_code_candidates(code) == []

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("HPG", ["HPG", "HPG.VN"]),
            ("hpg", ["HPG", "HPG.VN"]),
            ("HPG.VN", ["HPG.VN", "HPG"]),
            ("FUEVN100", ["FUEVN100", "FUEVN100.VN"]),
            ("VNINDEX", ["VNINDEX", "VNINDEX.VN"]),
        ],
    )
    def test_candidates_keep_raw_then_normalized_then_vn_suffix(self, code, expected):
        assert build_daily_code_candidates(code) == expected

    @pytest.mark.parametrize("code", ["HPG", "HPG.VN", "vnHPG", " hpg "])
    def test_one_identity_drives_candidates_market_and_refill(self, code):
        identity = resolve_daily_stock_identity(code)

        assert identity is not None
        assert identity.normalized_code == "HPG"
        assert identity.market == "vn"
        assert identity.refill_code == "HPG"
        assert code.strip().upper() in identity.code_candidates
        assert "HPG" in identity.code_candidates
        assert "HPG.VN" in identity.code_candidates

    def test_vn_market_hint_is_accepted_case_insensitively(self):
        identity = resolve_daily_stock_identity("VCB", market_hint="VN")
        assert identity is not None
        assert identity.market == "vn"

    @pytest.mark.parametrize("hint", ["cn", "hk", "us", "jp"])
    def test_legacy_non_vn_market_hint_returns_none(self, hint):
        # Dữ liệu cũ gắn thị trường khác bị bỏ qua. / Legacy non-VN rows are ignored.
        assert resolve_daily_stock_identity("HPG", market_hint=hint) is None


class TestIsCodeLike:
    @pytest.mark.parametrize(
        "value",
        ["HPG", "hpg", "VCB", "FPT", "HPG.VN", "vnHPG", "FUEVN100", "E1VFVN30", "VNINDEX", "VN30", "HNXINDEX"],
    )
    def test_vn_codes(self, value):
        assert is_code_like(value) is True

    @pytest.mark.parametrize(
        "value",
        ["", "Hòa Phát", "abc123", "600519", "00700", "AAPL", "HPG.HK"],
    )
    def test_negative_cases(self, value):
        assert is_code_like(value) is False


class TestNormalizeCode:
    def test_plain_code_uppercased(self):
        assert normalize_code("hpg") == "HPG"

    def test_whitespace_stripped(self):
        assert normalize_code("  FPT  ") == "FPT"

    def test_vn_suffix_strips(self):
        assert normalize_code("HPG.VN") == "HPG"

    def test_vn_prefix_strips(self):
        assert normalize_code("vnHPG") == "HPG"

    def test_fund_and_index_codes_kept(self):
        assert normalize_code("FUEVN100") == "FUEVN100"
        assert normalize_code("VNINDEX") == "VNINDEX"

    @pytest.mark.parametrize("value", ["", "Hòa Phát", "600519", "AAPL", "00700.HK"])
    def test_invalid_returns_none(self, value):
        assert normalize_code(value) is None


class TestResolveIndexStockCodeForAnalysis:
    def test_resolves_via_stock_index(self):
        with patch("src.data.stock_index_loader.resolve_index_stock_code", return_value="HPG"):
            assert resolve_index_stock_code_for_analysis("hpg") == "HPG"

    def test_falls_back_to_canonical_when_index_miss(self):
        with patch("src.data.stock_index_loader.resolve_index_stock_code", return_value=None):
            assert resolve_index_stock_code_for_analysis("fpt") == "FPT"

    def test_non_code_text_is_only_uppercased(self):
        # Văn bản không phải mã chỉ được chuẩn hoá chữ hoa để nơi gọi tự kiểm tra.
        # Non-code text is only canonicalized; callers apply their own validation.
        assert resolve_index_stock_code_for_analysis("aapl") == "AAPL"

    def test_empty_returns_empty_string(self):
        assert resolve_index_stock_code_for_analysis("   ") == ""
