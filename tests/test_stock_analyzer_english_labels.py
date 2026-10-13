# -*- coding: utf-8 -*-
"""Nhãn phân tích kỹ thuật dùng tiếng Anh làm chuẩn: không còn chữ Hán trong dữ liệu gửi LLM,
bộ lọc lý do mâu thuẫn và bản địa hoá vẫn giữ nguyên hành vi."""

import json
import os
import re
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.analyzer import _sanitize_trend_analysis_for_prompt  # noqa: E402
from src.core.pipeline import StockAnalysisPipeline  # noqa: E402
from src.report_language import localize_trend_prediction  # noqa: E402
from src.stock_analyzer import (  # noqa: E402
    MACDStatus,
    StockTrendAnalyzer,
    TrendAnalysisResult,
    TrendStatus,
)

_HAN = re.compile(r"[㐀-䶿一-鿿]")


def _price_frame(drift: float, seed: int = 7, days: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = [10.0]
    for _ in range(days - 1):
        prices.append(prices[-1] * (1 + drift + rng.normal(0, 0.01)))
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=days, freq="D"),
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": rng.integers(1_000_000, 5_000_000, days),
    })


@pytest.mark.parametrize("drift", [0.01, -0.01, 0.0])
def test_trend_analysis_output_has_no_han_characters(drift: float) -> None:
    analyzer = StockTrendAnalyzer()
    with patch("src.stock_analyzer.get_config") as get_config:
        get_config.return_value.bias_threshold = 5.0
        result = analyzer.analyze(_price_frame(drift), "LAS")

    assert not _HAN.search(json.dumps(result.to_dict(), ensure_ascii=False))
    assert not _HAN.search(analyzer.format_analysis(result))


@pytest.mark.parametrize("handler_name", ["_handle_calculate_ma", "_handle_get_volume_analysis", "_handle_analyze_pattern"])
def test_analysis_tools_output_has_no_han_characters(handler_name: str) -> None:
    from src.agent.tools import analysis_tools

    frame = _price_frame(0.005, days=260)
    with patch("src.services.history_loader.load_history_df", return_value=(frame, "test")):
        output = getattr(analysis_tools, handler_name)("LAS")

    assert "error" not in output
    assert not _HAN.search(json.dumps(output, ensure_ascii=False))


def _result(trend: TrendStatus, alignment: str, macd: MACDStatus, macd_signal: str) -> dict:
    result = TrendAnalysisResult(code="LAS", trend_status=trend, ma_alignment=alignment)
    result.macd_status = macd
    result.macd_signal = macd_signal
    result.signal_reasons = [f"✅ {macd_signal}", "✅ MA5 support holding"]
    result.risk_factors = [f"⚠️ {macd_signal}"]
    return result.to_dict()


def test_bearish_trend_still_filters_bullish_macd_reason() -> None:
    sanitized = _sanitize_trend_analysis_for_prompt(
        _result(TrendStatus.BEAR, "Bearish alignment MA5<MA10<MA20", MACDStatus.GOLDEN_CROSS, "✅ Golden cross, uptrend")
    )

    assert sanitized["prompt_trend_direction"] == "bearish"
    assert sanitized["signal_reasons"] == ["✅ MA5 support holding"]


def test_bullish_trend_still_filters_bearish_macd_risk() -> None:
    sanitized = _sanitize_trend_analysis_for_prompt(
        _result(TrendStatus.STRONG_BULL, "Strong bullish alignment, MAs diverging upward", MACDStatus.DEATH_CROSS, "❌ Death cross, downtrend")
    )

    assert sanitized["prompt_trend_direction"] == "bullish"
    assert sanitized["risk_factors"] == []


@pytest.mark.parametrize(
    "trend, expected_vi",
    [
        (TrendStatus.STRONG_BULL, "Tăng mạnh"),
        (TrendStatus.BULL, "Tăng"),
        (TrendStatus.WEAK_BULL, "Tăng"),
        (TrendStatus.CONSOLIDATION, "Đi ngang"),
        (TrendStatus.WEAK_BEAR, "Giảm"),
        (TrendStatus.BEAR, "Giảm"),
        (TrendStatus.STRONG_BEAR, "Giảm mạnh"),
    ],
)
def test_trend_labels_localize_to_vietnamese(trend: TrendStatus, expected_vi: str) -> None:
    assert localize_trend_prediction(trend.value, "vi") == expected_vi
    result = TrendAnalysisResult(code="LAS", trend_status=trend)
    assert StockAnalysisPipeline._trend_label_fallback(result, "vi") == expected_vi
