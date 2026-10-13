# -*- coding: utf-8 -*-
"""Tests for Issue #1381 daily market context decision guardrail (chỉ vi/en)."""

from __future__ import annotations

from src.analyzer import AnalysisResult
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail


_CONSERVATIVE_VN_CONTEXT = {
    "region": "vn",
    "trade_date": "2026-06-06",
    "summary": "Thị trường hạ nhiệt, rủi ro cao, nên quan sát, giới hạn tỷ trọng 30%.",
    "risk_tags": ["high_risk", "low_position_cap"],
}

_SOFTENED_POSITION_ADVICE = {
    "no_position": "Do not open a new position until market risk eases or confirmation appears.",
    "has_position": "Hold only a small position; do not increase exposure, and reduce if risk controls break.",
}


def _result() -> AnalysisResult:
    return AnalysisResult(
        code="FPT",
        name="FPT Corp",
        sentiment_score=82,
        trend_prediction="Tăng",
        operation_advice="Mua ngay và gia tăng tỷ trọng mạnh",
        decision_type="buy",
        confidence_level="Cao",
        analysis_summary="Tín hiệu cổ phiếu mạnh",
        dashboard={
            "operation_advice": "Mua ngay và gia tăng tỷ trọng mạnh",
            "decision_type": "buy",
            "core_conclusion": {
                "one_sentence": "Mua ngay và gia tăng tỷ trọng mạnh",
                "position_advice": {
                    "no_position": "Mua ngay",
                    "has_position": "Tiếp tục gia tăng tỷ trọng",
                },
            },
            "battle_plan": {
                "position_strategy": {
                    "suggested_position": "Toàn bộ tỷ trọng",
                    "entry_plan": "Mua ngay khi bứt phá",
                    "risk_control": "Điều chỉnh thì mua thêm",
                },
            },
            "phase_decision": {
                "data_limitations": [],
                "confidence_reason": "Xu hướng mạnh",
            },
        },
    )


def test_conservative_market_context_softens_aggressive_buy() -> None:
    result = _result()

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context=dict(_CONSERVATIVE_VN_CONTEXT),
        report_language="vi",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"
    assert result.operation_advice == "Quan sát"
    assert len(result.operation_advice) <= 20
    assert result.confidence_level == "Trung bình"
    assert result.sentiment_score == 52
    assert result.dashboard["operation_advice"] == result.operation_advice
    assert result.dashboard["decision_type"] == "hold"
    assert result.dashboard["sentiment_score"] == 52
    core = result.dashboard["core_conclusion"]
    assert core["one_sentence"] == result.operation_advice
    assert core["position_advice"] == _SOFTENED_POSITION_ADVICE
    position_strategy = result.dashboard["battle_plan"]["position_strategy"]
    assert position_strategy == {
        "suggested_position": "Small/defensive position",
        "entry_plan": _SOFTENED_POSITION_ADVICE["no_position"],
        "risk_control": "Do not increase exposure before market risk eases; control drawdown strictly.",
    }
    phase_decision = result.dashboard["phase_decision"]
    assert any("Daily market context" in item for item in phase_decision["data_limitations"])
    assert "Market context requires conservative sizing." in phase_decision["confidence_reason"]


def test_legacy_zh_language_falls_back_to_vietnamese() -> None:
    result = _result()

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context=dict(_CONSERVATIVE_VN_CONTEXT),
        report_language="zh",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.operation_advice == "Quan sát"
    assert result.confidence_level == "Trung bình"


def test_position_cap_only_market_context_softens_aggressive_buy() -> None:
    cases = [
        ("vi", "Thị trường giằng co, tỷ trọng tối đa 30%.", "Mua ngay và gia tăng tỷ trọng mạnh", "Cao", "Quan sát"),
        ("en", "Major indices are mixed. Position limit 30%.", "Buy now and add aggressively.", "High", "Watch"),
    ]
    for language, summary, advice, confidence, expected_advice in cases:
        result = _result()
        result.operation_advice = advice
        result.confidence_level = confidence

        adjustments = apply_daily_market_context_guardrail(
            result,
            daily_market_context={
                "region": "vn",
                "trade_date": "2026-06-06",
                "summary": summary,
                "risk_tags": [],
                "position_cap": "30%",
            },
            report_language=language,
        )

        assert "daily_market_context_buy_softened" in adjustments
        assert result.decision_type == "hold"
        assert result.operation_advice == expected_advice


def test_neutral_market_context_leaves_hold_unchanged() -> None:
    result = _result()
    result.decision_type = "hold"
    result.operation_advice = "Nắm giữ và theo dõi"
    result.confidence_level = "Trung bình"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "vn",
            "trade_date": "2026-06-06",
            "summary": "Thị trường giằng co, phân hóa.",
            "risk_tags": [],
        },
        report_language="vi",
    )

    assert adjustments == []
    assert result.decision_type == "hold"
    assert result.operation_advice == "Nắm giữ và theo dõi"


def test_conservative_market_context_does_not_soften_negative_buy_language() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "Chưa mua ngay, tiếp tục nắm giữ."
    result.confidence_level = "Cao"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context=dict(_CONSERVATIVE_VN_CONTEXT),
        report_language="vi",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "Chưa mua ngay, tiếp tục nắm giữ."


def test_conservative_market_context_does_not_soften_no_action_in_english() -> None:
    result = _result()
    result.decision_type = "hold"
    result.operation_advice = "No add now; keep watching for confirmation."

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "vn",
            "trade_date": "2026-06-06",
            "summary": "Market cooling and elevated risk. Cautious on new positions.",
        },
        report_language="en",
    )

    assert adjustments == []
    assert result.decision_type == "hold"
    assert result.operation_advice == "No add now; keep watching for confirmation."


def test_conservative_market_context_does_not_soften_explicit_negative_chase() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "Không mua đuổi, chờ tín hiệu rõ hơn."
    result.confidence_level = "Cao"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context=dict(_CONSERVATIVE_VN_CONTEXT),
        report_language="vi",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "Không mua đuổi, chờ tín hiệu rõ hơn."


def test_conservative_market_context_softens_when_negated_chase_then_buy_now() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "Không mua đuổi, nhưng mua ngay khi điều chỉnh."
    result.confidence_level = "Cao"

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context=dict(_CONSERVATIVE_VN_CONTEXT),
        report_language="vi",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"
    assert result.operation_advice == "Quan sát"


def test_conservative_market_context_does_not_soften_buy_when_negated_explicitly_in_english() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "No buy now; avoid adding."

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context=dict(_CONSERVATIVE_VN_CONTEXT),
        report_language="en",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "No buy now; avoid adding."


def test_conservative_market_context_does_not_soften_do_not_buy_in_english() -> None:
    result = _result()
    result.decision_type = "buy"
    result.operation_advice = "Do not buy now; sell into strength."

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "vn",
            "trade_date": "2026-06-06",
            "summary": "Market cooling and elevated risk. Cautious on new positions.",
        },
        report_language="en",
    )

    assert adjustments == []
    assert result.decision_type == "buy"
    assert result.operation_advice == "Do not buy now; sell into strength."
