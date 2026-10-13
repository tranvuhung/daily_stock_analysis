# -*- coding: utf-8 -*-
"""Kiểm thử bộ hiệu chỉnh độ ổn định khuyến nghị theo cấu trúc giá (hỗ trợ/kháng cự).

Tests for structure-only decision stability calibration (no capital-flow input).
"""

from types import SimpleNamespace

from src.analyzer import AnalysisResult, stabilize_decision_with_structure

_LEVELS = SimpleNamespace(support_levels=[30.0], resistance_levels=[34.0])


def _result(
    *,
    decision_type: str,
    operation_advice: str,
    score: int,
    current_price: float,
    change_pct: float = 0.0,
) -> AnalysisResult:
    return AnalysisResult(
        code="HPG",
        name="Hòa Phát",
        sentiment_score=score,
        trend_prediction="Tăng" if decision_type == "buy" else "Giảm",
        operation_advice=operation_advice,
        decision_type=decision_type,
        report_language="vi",
        current_price=current_price,
        change_pct=change_pct,
        dashboard={
            "core_conclusion": {"one_sentence": "Kết luận gốc"},
            "data_perspective": {
                "price_position": {
                    "current_price": current_price,
                    "support_level": 30.0,
                    "resistance_level": 34.0,
                }
            },
        },
    )


def test_downgrades_buy_near_resistance() -> None:
    result = _result(decision_type="buy", operation_advice="Mua", score=65, current_price=33.4)

    stabilize_decision_with_structure(result, _LEVELS, {})

    assert result.decision_type == "hold"
    assert result.sentiment_score <= 59
    assert result.operation_advice == "Quan sát vùng dao động"
    assert result.dashboard["decision_stability"]["applied"] is True
    assert "sát vùng kháng cự" in result.risk_warning
    assert "capital_flow_bias" not in result.dashboard["decision_stability"]


def test_downgrades_buy_mid_range() -> None:
    result = _result(decision_type="buy", operation_advice="Mua", score=66, current_price=32.0)

    stabilize_decision_with_structure(result, _LEVELS, None)

    assert result.decision_type == "hold"
    assert result.sentiment_score <= 59
    assert result.operation_advice == "Quan sát vùng dao động"
    assert "giữa hỗ trợ và kháng cự" in result.risk_warning


def test_keeps_buy_on_breakout_above_resistance() -> None:
    result = _result(decision_type="buy", operation_advice="Mua", score=72, current_price=35.0)

    stabilize_decision_with_structure(result, _LEVELS, None)

    assert result.decision_type == "buy"
    assert result.operation_advice == "Mua"
    assert "decision_stability" not in result.dashboard


def test_keeps_buy_near_support() -> None:
    """Mua gần hỗ trợ không bị hạ cấp (trước đây bị hạ vì thiếu dòng tiền)."""
    result = _result(decision_type="buy", operation_advice="Mua", score=70, current_price=30.5)

    stabilize_decision_with_structure(result, _LEVELS, {"valuation": {"status": "partial"}})

    assert result.decision_type == "buy"
    assert result.sentiment_score == 70


def test_downgrades_sell_near_support_without_significant_risk() -> None:
    result = _result(
        decision_type="sell",
        operation_advice="Bán",
        score=30,
        current_price=30.4,
        change_pct=-2.1,
    )

    stabilize_decision_with_structure(result, _LEVELS, None)

    assert result.decision_type == "hold"
    assert result.sentiment_score >= 45
    assert result.operation_advice == "Quan sát rũ bỏ"
    assert "chưa đủ cơ sở để bán" in result.risk_warning


def test_preserves_sell_signal_when_significant_risk_exists_near_support() -> None:
    result = _result(
        decision_type="sell",
        operation_advice="Bán",
        score=30,
        current_price=30.4,
        change_pct=-2.1,
    )
    result.risk_warning = "Major risk: the company announced a large insider sell-down"
    result.dashboard["intelligence"] = {"risk_alerts": ["major shareholder sell-down"]}

    stabilize_decision_with_structure(result, _LEVELS, None)

    assert result.decision_type == "sell"
    assert result.operation_advice == "Bán"


def test_refines_hold_pullback_near_support_as_shakeout_watch() -> None:
    result = _result(
        decision_type="hold",
        operation_advice="Nắm giữ",
        score=52,
        current_price=30.5,
        change_pct=-1.6,
    )

    stabilize_decision_with_structure(result, _LEVELS, None)

    assert result.decision_type == "hold"
    assert result.operation_advice == "Quan sát rũ bỏ"
    assert "nhịp rũ bỏ" in result.risk_warning


def test_english_wording_for_mid_range_hold() -> None:
    result = _result(decision_type="hold", operation_advice="Hold", score=55, current_price=32.0)
    result.report_language = "en"

    stabilize_decision_with_structure(result, _LEVELS, None)

    assert result.operation_advice == "Range-bound watch"
    assert "without a clear signal" in result.risk_warning
