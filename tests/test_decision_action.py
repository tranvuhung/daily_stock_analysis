# -*- coding: utf-8 -*-
"""Tests for Issue #1390 P0 decision action taxonomy helpers."""

import pytest

from src.schemas.decision_action import (
    build_action_fields,
    display_action_fields_for_result,
    display_decision_type_for_result,
    display_operation_advice_for_result,
    localize_action_label,
    normalize_decision_action,
)
from src.schemas.decision_scale import action_for_score, decision_type_for_score, signal_key_for_score


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("strong_buy", "buy"),
        ("Mua mạnh", "buy"),
        ("Mua", "buy"),
        ("mua vào", "buy"),
        ("giải ngân", "buy"),
        ("add", "add"),
        ("Mua thêm", "add"),
        ("tăng tỷ trọng", "add"),
        ("accumulate", "add"),
        ("hold", "hold"),
        ("Nắm giữ", "hold"),
        ("nắm giữ và quan sát", "hold"),
        ("tiếp tục nắm giữ", "hold"),
        ("watch", "watch"),
        ("Quan sát", "watch"),
        ("chờ tín hiệu", "watch"),
        ("wait", "watch"),
        ("reduce", "reduce"),
        ("Giảm tỷ trọng", "reduce"),
        ("chốt bớt", "reduce"),
        ("trim", "reduce"),
        ("sell", "sell"),
        ("Bán", "sell"),
        ("thoát hàng", "sell"),
        ("strong_sell", "sell"),
        ("Bán mạnh", "sell"),
        ("avoid", "avoid"),
        ("Tránh", "avoid"),
        ("tránh mua", "avoid"),
        ("không nên mua", "avoid"),
        ("do not buy", "avoid"),
        ("alert", "alert"),
        ("Cảnh báo", "alert"),
        ("cảnh báo rủi ro", "alert"),
        ("risk alert", "alert"),
    ],
)
def test_normalize_decision_action_matrix(value: str, expected: str) -> None:
    assert normalize_decision_action(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "waiting to buy",
        "mua vào hoặc bán ra",
        "buy or sell",
        "no buyback announced",
        "cannot buyback shares now",
        "share buy-back announced",
        "share buy back announced",
        "no selloff risk",
        "not selloff yet",
        "sell-off risk remains low",
        "sell off risk remains low",
        "no sell-off pressure",
        "risk alert, avoid buying",
        "cảnh báo rủi ro, tránh mua",
        "ghi chú tổng kết thông thường",
    ],
)
def test_normalize_decision_action_unknown_or_ambiguous_returns_none(value: str | None) -> None:
    assert normalize_decision_action(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("no buy", "avoid"),
        ("no need to buy", "avoid"),
        ("need not buy", "avoid"),
        ("cannot buy", "avoid"),
        ("can't buy", "avoid"),
        ("not a buy yet", "avoid"),
        ("not to buy", "avoid"),
        ("avoid buying", "avoid"),
        ("avoid buying into weakness", "avoid"),
        ("no add", "hold"),
        ("no need to add", "hold"),
        ("need not add", "hold"),
        ("cannot add", "hold"),
        ("not to add", "hold"),
        ("no accumulate", "hold"),
        ("can't accumulate", "hold"),
        ("not to accumulate", "hold"),
        ("không nên bán", "hold"),
        ("chưa nên bán", "hold"),
        ("không cần bán", "hold"),
        ("no sell", "hold"),
        ("no need to sell", "hold"),
        ("cannot sell", "hold"),
        ("can't sell", "hold"),
        ("not a sell yet", "hold"),
        ("not to sell", "hold"),
        ("không nên giảm tỷ trọng", "hold"),
        ("không cần giảm tỷ trọng", "hold"),
        ("no reduce", "hold"),
        ("no need to reduce", "hold"),
        ("cannot reduce", "hold"),
        ("not to reduce", "hold"),
        ("no trim", "hold"),
        ("can't trim", "hold"),
        ("not a trim yet", "hold"),
        ("not to trim", "hold"),
        ("avoid selling into weakness", "hold"),
        ("avoid trimming before earnings", "hold"),
        ("avoid reducing exposure before earnings", "hold"),
    ],
)
def test_normalize_decision_action_handles_negated_trade_actions(value: str, expected: str) -> None:
    assert normalize_decision_action(value) == expected


@pytest.mark.parametrize(
    "advice",
    [
        "không nên mua, chờ xác nhận",
        "no buy until breakout",
        "no need to buy before confirmation",
        "cannot buy before confirmation",
        "can't buy before confirmation",
        "not a buy yet",
        "not to buy",
    ],
)
def test_build_action_fields_prioritizes_negated_buy_advice_over_embedded_buy_phrase(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": "avoid",
        "action_label": "Tránh",
    }


@pytest.mark.parametrize(
    "advice",
    [
        "không nên bán, tiếp tục nắm giữ",
        "chưa nên giảm tỷ trọng, chờ xác nhận",
        "no add before confirmation",
        "cannot add before confirmation",
        "no need to accumulate here",
        "can't accumulate here",
        "no sell before earnings",
        "cannot sell before earnings",
        "no need to reduce exposure",
        "can't reduce exposure",
        "no trim while trend holds",
        "cannot trim while trend holds",
        "not a sell yet",
        "not a trim yet",
        "not to sell",
        "not to trim",
        "avoid selling into weakness",
        "avoid trimming before earnings",
        "avoid reducing exposure before earnings",
    ],
)
def test_build_action_fields_prioritizes_negated_hold_advice_over_embedded_trade_phrase(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": "hold",
        "action_label": "Nắm giữ",
    }


@pytest.mark.parametrize(
    "advice",
    [
        "risk alert, avoid buying",
        "cảnh báo rủi ro, tránh mua",
    ],
)
def test_build_action_fields_keeps_multi_guard_advice_empty(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": None,
        "action_label": None,
    }


@pytest.mark.parametrize(
    "advice",
    [
        "no buyback announced",
        "cannot buyback shares now",
        "no selloff risk",
        "not selloff yet",
    ],
)
def test_build_action_fields_keeps_financial_compound_terms_empty(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": None,
        "action_label": None,
    }


@pytest.mark.parametrize(
    "advice",
    [
        "share buy-back announced",
        "share buy back announced",
        "sell-off risk remains low",
        "sell off risk remains low",
        "no sell-off pressure",
    ],
)
def test_build_action_fields_keeps_hyphenated_financial_compound_terms_empty(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": None,
        "action_label": None,
    }


@pytest.mark.parametrize(
    ("advice", "expected_action", "expected_label"),
    [
        ("buy after sell-off", "buy", "Mua"),
        ("sell after buy-back rumor", "sell", "Bán"),
    ],
)
def test_financial_compound_mask_preserves_separate_action_terms(
    advice: str,
    expected_action: str,
    expected_label: str,
) -> None:
    assert normalize_decision_action(advice) == expected_action
    assert build_action_fields(operation_advice=advice) == {
        "action": expected_action,
        "action_label": expected_label,
    }


def test_localize_action_label_uses_report_language() -> None:
    assert localize_action_label("avoid", "vi") == "Tránh"
    assert localize_action_label("avoid", "en") == "Avoid"
    # Ngôn ngữ cũ (zh/ko) được chuẩn hoá về tiếng Việt. / Legacy zh/ko normalize to Vietnamese.
    assert localize_action_label("avoid", "zh") == "Tránh"


def test_build_action_fields_respects_market_review_exclusion() -> None:
    fields = build_action_fields(
        operation_advice="Mua",
        explicit_action="buy",
        report_type="market_review",
    )

    assert fields == {"action": None, "action_label": None}


def test_build_action_fields_prefers_explicit_action_over_advice() -> None:
    fields = build_action_fields(
        operation_advice="Mua",
        explicit_action="watch",
        report_language="vi",
    )

    assert fields == {"action": "watch", "action_label": "Quan sát"}


def test_build_action_fields_keeps_empty_action_without_advice_or_explicit_action() -> None:
    fields = build_action_fields(
        operation_advice=None,
        report_language="vi",
    )

    assert fields == {"action": None, "action_label": None}


@pytest.mark.parametrize(
    ("score", "expected_signal", "expected_action", "expected_decision_type"),
    [
        (28, "reduce", "reduce", "sell"),
        (38, "reduce", "reduce", "sell"),
        (42, "watch", "watch", "hold"),
        (55, "watch", "watch", "hold"),
        (60, "buy", "buy", "buy"),
        (66, "buy", "buy", "buy"),
        (72, "buy", "buy", "buy"),
    ],
)
def test_canonical_score_scale_boundaries(
    score: int,
    expected_signal: str,
    expected_action: str,
    expected_decision_type: str,
) -> None:
    assert signal_key_for_score(score) == expected_signal
    assert action_for_score(score) == expected_action
    assert decision_type_for_score(score) == expected_decision_type


def test_build_action_fields_can_align_neutral_action_with_directional_score() -> None:
    assert build_action_fields(
        operation_advice="Nắm giữ",
        sentiment_score=72,
        align_with_score=True,
    ) == {"action": "buy", "action_label": "Mua"}

    assert build_action_fields(
        operation_advice="Quan sát",
        sentiment_score=28,
        align_with_score=True,
    ) == {"action": "reduce", "action_label": "Giảm tỷ trọng"}


def test_build_action_fields_keeps_neutral_score_conflict_when_guardrail_is_explicit() -> None:
    assert build_action_fields(
        operation_advice="Nắm giữ/quan sát chờ điều chỉnh",
        sentiment_score=72,
        guardrail_reason="Chờ xác nhận nhịp điều chỉnh",
        align_with_score=True,
    ) == {"action": "watch", "action_label": "Quan sát"}


def test_display_helpers_share_score_aligned_action_with_report_rows_and_counts() -> None:
    result = type(
        "Result",
        (),
        {
            "operation_advice": "Hold",
            "action": None,
            "action_label": None,
            "sentiment_score": 72,
            "decision_type": "hold",
            "report_language": "en",
            "dashboard": {},
        },
    )()

    assert display_action_fields_for_result(result) == {"action": "buy", "action_label": "Buy"}
    assert display_operation_advice_for_result(result) == "Buy"
    assert display_decision_type_for_result(result) == "buy"


def test_display_helpers_preserve_neutral_action_when_guardrail_was_applied() -> None:
    result = type(
        "Result",
        (),
        {
            "operation_advice": "Hold",
            "action": "hold",
            "action_label": "Hold",
            "sentiment_score": 72,
            "decision_type": "hold",
            "report_language": "en",
            "dashboard": {
                "decision_stability": {
                    "applied": True,
                    "reason": "Wait for confirmation",
                }
            },
        },
    )()

    assert display_action_fields_for_result(result) == {"action": "hold", "action_label": "Hold"}
    assert display_operation_advice_for_result(result) == "Hold"
    assert display_decision_type_for_result(result) == "hold"
