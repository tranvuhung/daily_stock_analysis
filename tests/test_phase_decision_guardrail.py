# -*- coding: utf-8 -*-
"""Tests for Issue #1386 P5 phase decision guardrails."""

from types import SimpleNamespace

from src.analyzer import AnalysisResult
from src.phase_decision_guardrail import apply_phase_decision_guardrails


def _result(**kwargs) -> AnalysisResult:
    defaults = {
        "code": "FPT",
        "name": "FPT Corp",
        "trend_prediction": "Tăng",
        "sentiment_score": 76,
        "operation_advice": "Buy now",
        "decision_type": "buy",
        "confidence_level": "Cao",
        "analysis_summary": "Trong phiên khá mạnh",
        "dashboard": {
            "core_conclusion": {"one_sentence": "Buy now"},
            "phase_decision": {
                "action_window": "Theo dõi trong phiên",
                "immediate_action": "Buy now",
                "watch_conditions": ["Bứt phá kèm khối lượng"],
                "next_check_time": "14:00",
                "confidence_reason": "Xu hướng khá mạnh",
                "data_limitations": [],
            },
        },
    }
    defaults.update(kwargs)
    return AnalysisResult(**defaults)


def _phase(phase: str = "intraday") -> dict:
    return {
        "phase": phase,
        "market": "vn",
        "market_local_time": "2026-06-02T10:30:00+07:00",
        "is_trading_day": True,
        "is_market_open_now": phase == "intraday",
        "is_partial_bar": phase in {"intraday", "lunch_break", "closing_auction"},
        "warnings": ["calendar_unavailable"],
    }


def _overview(status: str = "stale") -> dict:
    return {
        "subject": {"code": "FPT", "stock_name": "FPT Corp", "market": "vn"},
        "blocks": [
            {
                "key": "quote",
                "label": "Giá",
                "status": status,
                "source": "tencent",
                "warnings": [],
                "missing_reasons": [],
            },
            {
                "key": "daily_bars",
                "label": "Nến ngày",
                "status": "available",
                "source": "akshare",
                "warnings": [],
                "missing_reasons": [],
            },
            {
                "key": "technical",
                "label": "Kỹ thuật",
                "status": "available",
                "source": "local",
                "warnings": [],
                "missing_reasons": [],
            },
        ],
        "data_quality": {
            "overall_score": 65,
            "level": "limited",
            "limitations": ["quote: stale"],
        },
    }


def test_degraded_core_data_caps_high_confidence_buy() -> None:
    result = _result()

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=_overview("stale"),
        report_language="vi",
    )

    assert "confidence_capped_core_data_degraded" in adjustments
    assert result.confidence_level == "Trung bình"
    pd = result.dashboard["phase_decision"]
    assert pd["phase_context"]["phase"] == "intraday"
    assert "Báo giá: dữ liệu trễ" in pd["data_limitations"]
    assert "Dữ liệu lõi" in pd["confidence_reason"]


def test_degraded_core_data_caps_high_confidence_hold_advice() -> None:
    result = _result(
        operation_advice="Do not add now, watch",
        decision_type="hold",
        confidence_level="High",
        dashboard={
            "core_conclusion": {"one_sentence": "Do not add now, watch"},
            "phase_decision": {
                "action_window": "Theo dõi trong phiên",
                "immediate_action": "Do not add now, watch",
                "watch_conditions": ["Bứt phá kèm khối lượng"],
                "next_check_time": "14:00",
                "confidence_reason": "Xu hướng chưa xác nhận",
                "data_limitations": [],
            },
        },
    )

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=_overview("stale"),
        report_language="vi",
    )

    assert "confidence_capped_core_data_degraded" in adjustments
    assert result.confidence_level == "Trung bình"
    assert "Dữ liệu lõi" in result.dashboard["phase_decision"]["confidence_reason"]
    assert "Báo giá: dữ liệu trễ" in result.dashboard["phase_decision"]["data_limitations"]


def test_premarket_high_confidence_immediate_action_is_conservative() -> None:
    result = _result()

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("premarket"),
        analysis_context_pack_overview=_overview("available"),
        report_language="vi",
    )

    assert "confidence_capped_non_intraday_action" in adjustments
    assert result.confidence_level == "Thấp"
    assert result.dashboard["phase_decision"]["immediate_action"] == "Chờ xác nhận trong phiên; không mua đuổi."


def test_premarket_medium_confidence_immediate_action_rewrites_action_only() -> None:
    result = _result(
        operation_advice="Hold unless confirmed",
        decision_type="hold",
        confidence_level="Medium",
        report_language="en",
        dashboard={
            "core_conclusion": {"one_sentence": "Wait"},
            "phase_decision": {
                "action_window": "Premarket plan",
                "immediate_action": "buy now",
                "watch_conditions": ["breakout with volume"],
                "next_check_time": "market open",
                "confidence_reason": "Setup is forming",
                "data_limitations": [],
            },
        },
    )

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("premarket"),
        analysis_context_pack_overview=_overview("available"),
        report_language="en",
    )

    assert "non_intraday_action_adjusted" in adjustments
    assert "confidence_capped_non_intraday_action" not in adjustments
    assert result.confidence_level == "Medium"
    assert result.dashboard["phase_decision"]["immediate_action"] == (
        "Wait for intraday confirmation; do not chase."
    )
    assert "buy now" not in result.dashboard["phase_decision"]["immediate_action"].lower()


def test_unknown_low_confidence_immediate_action_rewrites_action_only() -> None:
    result = _result(
        operation_advice="Chủ yếu quan sát",
        decision_type="hold",
        confidence_level="Thấp",
        dashboard={
            "core_conclusion": {"one_sentence": "Chủ yếu quan sát"},
            "phase_decision": {
                "action_window": "Quan sát khi chưa rõ giai đoạn",
                "immediate_action": "buy now",
                "watch_conditions": ["Xác nhận trạng thái mở cửa"],
                "next_check_time": "Sau khi xác nhận giai đoạn",
                "confidence_reason": "Chưa rõ giai đoạn",
                "data_limitations": [],
            },
        },
    )

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("unknown"),
        analysis_context_pack_overview=_overview("available"),
        report_language="vi",
    )

    assert "non_intraday_action_adjusted" in adjustments
    assert "confidence_capped_non_intraday_action" not in adjustments
    assert result.confidence_level == "Thấp"
    assert result.dashboard["phase_decision"]["immediate_action"] == "Chờ xác nhận trong phiên; không mua đuổi."


def test_premarket_degraded_immediate_action_uses_strongest_cap() -> None:
    result = _result()

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("premarket"),
        analysis_context_pack_overview=_overview("stale"),
        report_language="vi",
    )

    assert "confidence_capped_core_data_degraded" in adjustments
    assert "confidence_capped_non_intraday_action" in adjustments
    assert result.confidence_level == "Thấp"
    assert result.dashboard["phase_decision"]["immediate_action"] == "Chờ xác nhận trong phiên; không mua đuổi."


def test_intraday_postmarket_recap_wording_is_adjusted_in_vi_and_en() -> None:
    vi_result = _result(
        operation_advice="Điểm lại sau phiên cho thấy có thể mua",
        analysis_summary="Tâm điểm ngày mai là bứt phá",
        dashboard={
            "core_conclusion": {"one_sentence": "Sau khi đóng cửa hôm nay, xu hướng mạnh"},
            "phase_decision": {"immediate_action": "Tâm điểm ngày mai là bứt phá", "watch_conditions": []},
        },
    )

    vi_adjustments = apply_phase_decision_guardrails(
        vi_result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=_overview("available"),
        report_language="vi",
    )

    assert "postmarket_recap_wording_adjusted" in vi_adjustments
    assert "sau khi đóng cửa hôm nay" not in vi_result.dashboard["core_conclusion"]["one_sentence"].lower()
    assert "tâm điểm ngày mai" not in vi_result.analysis_summary.lower()

    en_result = _result(
        operation_advice="Buy after today's close",
        analysis_summary="Focus tomorrow on the breakout",
        confidence_level="High",
        report_language="en",
        dashboard={
            "core_conclusion": {"one_sentence": "After today's close, buy"},
            "phase_decision": {"immediate_action": "focus tomorrow", "watch_conditions": []},
        },
    )

    en_adjustments = apply_phase_decision_guardrails(
        en_result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=_overview("available"),
        report_language="en",
    )

    assert "postmarket_recap_wording_adjusted" in en_adjustments
    assert "after today's close" not in en_result.dashboard["core_conclusion"]["one_sentence"].lower()
    assert "focus tomorrow" not in en_result.analysis_summary.lower()


def test_postmarket_recap_and_missing_inputs_are_fail_open() -> None:
    postmarket = _result(
        operation_advice="Sau khi đóng cửa hôm nay có thể nắm giữ",
        dashboard={
            "core_conclusion": {"one_sentence": "Sau khi đóng cửa hôm nay có thể nắm giữ"},
            "phase_decision": {"watch_conditions": ["Giữ được hỗ trợ"]},
        },
    )

    adjustments = apply_phase_decision_guardrails(
        postmarket,
        market_phase_summary=_phase("postmarket"),
        analysis_context_pack_overview=None,
        report_language="vi",
    )

    assert adjustments == []
    assert "Sau khi đóng cửa hôm nay" in postmarket.dashboard["core_conclusion"]["one_sentence"]
    assert postmarket.dashboard["phase_decision"]["watch_conditions"] == ["Giữ được hỗ trợ"]

    missing = _result(dashboard={})
    adjustments = apply_phase_decision_guardrails(
        missing,
        market_phase_summary=None,
        analysis_context_pack_overview=None,
        report_language="vi",
    )

    assert adjustments == []
    assert missing.dashboard["phase_decision"]["watch_conditions"] == []
    assert missing.dashboard["phase_decision"]["data_limitations"] == []


def test_guardrail_creates_dashboard_for_agent_compatible_result_object() -> None:
    result = SimpleNamespace(
        confidence_level="Cao",
        decision_type="hold",
        operation_advice="Nắm giữ",
        analysis_summary="Tóm tắt thử nghiệm",
    )

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=_overview("available"),
        report_language="vi",
    )

    assert adjustments == []
    assert result.dashboard["phase_decision"]["phase_context"]["phase"] == "intraday"
    assert result.dashboard["phase_decision"]["watch_conditions"] == []


def test_vietnamese_guardrail_text_has_no_english() -> None:
    # Báo cáo chạy sau phiên từng lẫn câu tiếng Anh và mã thô "quote: stale".
    # Post-market reports used to carry English reasons and raw "quote: stale" codes.
    result = _result()
    phase = _phase("premarket")
    phase["warnings"] = ["quote_stale"]

    apply_phase_decision_guardrails(
        result,
        market_phase_summary=phase,
        analysis_context_pack_overview=_overview("stale"),
        report_language="vi",
    )

    pd = result.dashboard["phase_decision"]
    inserted = [pd["confidence_reason"], pd["immediate_action"], *pd["data_limitations"]]
    for english in ("Core quote", "Wait for", "confidence capped", "market phase warning", "quote: stale", "；"):
        assert not any(english in text for text in inserted), english
    assert "Cảnh báo phiên: quote_stale" in pd["data_limitations"]


def test_english_report_keeps_english_guardrail_text() -> None:
    result = _result(confidence_level="High", report_language="en")

    apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=_overview("stale"),
        report_language="en",
    )

    pd = result.dashboard["phase_decision"]
    assert "quote: stale" in pd["data_limitations"]
    assert "Core quote" in pd["confidence_reason"]


def _with_sniper(result: AnalysisResult, **points) -> AnalysisResult:
    result.dashboard["battle_plan"] = {"sniper_points": points}
    return result


def test_stop_loss_above_buy_level_is_flagged_without_rewriting_levels() -> None:
    # Hồi quy #159: cắt lỗ 51.000 cao hơn điểm mua thứ cấp 50.000.
    # Regression for #159: stop-loss 51,000 above the 50,000 secondary buy.
    result = _with_sniper(
        _result(current_price=53300.0),
        ideal_buy="51.500 VND",
        secondary_buy="50.000 VND",
        stop_loss="51.000 VND",
        take_profit="55.150 VND",
    )

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=None,
        report_language="vi",
    )

    assert "sniper_level_order_invalid" in adjustments
    pd = result.dashboard["phase_decision"]
    assert "cắt lỗ 51.000 ≥ điểm mua thứ cấp 50.000" in pd["confidence_reason"]
    assert "điểm mua lý tưởng" not in pd["confidence_reason"]
    assert "các mức giá kế hoạch sai thứ tự" in pd["data_limitations"]
    assert result.dashboard["battle_plan"]["sniper_points"]["secondary_buy"] == "50.000 VND"


def test_ordered_levels_are_not_flagged() -> None:
    result = _with_sniper(
        _result(current_price=53300.0),
        ideal_buy="51.500 VND",
        secondary_buy="50.500 VND",
        stop_loss="49.800 VND",
        take_profit="55.150 VND",
    )

    adjustments = apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=None,
        report_language="vi",
    )

    assert "sniper_level_order_invalid" not in adjustments


def test_overview_limitation_already_stated_by_llm_is_not_duplicated() -> None:
    # Hồi quy FPT #166: "Báo giá ... dự phòng (fallback)" và "Báo giá: dùng nguồn dự phòng" trùng ý.
    # Regression for FPT #166: the LLM line and the canonical code said the same thing.
    result = _result(confidence_level="Trung bình")
    pd = result.dashboard["phase_decision"]
    pd["data_limitations"] = ["Báo giá thời gian thực sử dụng chế độ dự phòng (fallback)"]
    overview = _overview("fallback")
    overview["data_quality"]["limitations"] = ["quote: fallback", "news: partial"]

    apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=overview,
        report_language="vi",
    )

    limitations = result.dashboard["phase_decision"]["data_limitations"]
    assert "Báo giá: dùng nguồn dự phòng" not in limitations
    assert "Báo giá thời gian thực sử dụng chế độ dự phòng (fallback)" in limitations
    # Giới hạn LLM chưa nêu vẫn được thêm / limitations the LLM did not state are still added.
    assert "Tin tức: một phần" in limitations


def test_appended_reason_after_full_stop_has_no_semicolon() -> None:
    result = _with_sniper(
        _result(current_price=53300.0, confidence_level="Trung bình"),
        ideal_buy="50.000 VND",
        stop_loss="50.000 VND",
    )
    result.dashboard["phase_decision"]["confidence_reason"] = "Phiên intraday chưa đóng cửa."

    apply_phase_decision_guardrails(
        result,
        market_phase_summary=_phase("intraday"),
        analysis_context_pack_overview=None,
        report_language="vi",
    )

    reason = result.dashboard["phase_decision"]["confidence_reason"]
    assert ".;" not in reason
    assert reason.startswith("Phiên intraday chưa đóng cửa. Các mức giá trong kế hoạch")
