# -*- coding: utf-8 -*-
"""Decision guardrail using daily market context for Issue #1381."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, List

from src.report_language import (
    localize_confidence_level,
    localize_operation_advice,
    normalize_report_language,
)


_CONSERVATIVE_TAGS = {"high_risk", "market_cooling", "conservative", "low_position_cap"}
# Dấu hiệu bằng tiếng Việt/tiếng Anh (đã bỏ zh/ko). / Vietnamese/English markers (zh/ko removed).
_CONSERVATIVE_TEXT_MARKERS = (
    "high risk", "risk-off", "risk off", "watch", "cautious", "conservative", "position cap", "position limit",
    "rủi ro cao", "quan sát", "thận trọng", "phòng thủ", "giới hạn tỷ trọng", "tỷ trọng thấp",
)
_AGGRESSIVE_BUY_MARKERS = (
    "buy now", "strong buy", "aggressive buy", "chase", "add aggressively",
    "mua ngay", "mua mạnh", "mua đuổi", "gia tăng tỷ trọng mạnh",
)
_NEGATION_HINTS = (" not ", "do not", "don't", "no ", "never", "avoid", "không", "chưa", "tránh", "đừng")
_NEGATION_LOOKBACK = 16
_GUARDRAIL_SENTIMENT_SCORE = 52


def _normalize_language(language: Any) -> str:
    """Chỉ còn 'en' và 'vi'; mọi giá trị khác quy về 'vi'. / Only 'en' and 'vi' remain."""
    return "en" if normalize_report_language(language) == "en" else "vi"


def _softened_operation_advice(language: str) -> str:
    return localize_operation_advice("watch", language)


def _negation_hints_for(language: str) -> tuple[str, ...]:
    return _NEGATION_HINTS


def apply_daily_market_context_guardrail(
    result: Any,
    *,
    daily_market_context: Any,
    report_language: str = "vi",
) -> List[str]:
    """Soften aggressive buy advice when daily market context is conservative."""

    if result is None or not _is_conservative_context(daily_market_context):
        return []

    language = _normalize_language(report_language or getattr(result, "report_language", "vi"))
    if not _has_aggressive_buy_signal(result, language=language):
        return []

    adjustments: List[str] = []
    if str(getattr(result, "decision_type", "") or "").lower() == "buy":
        result.decision_type = "hold"
        adjustments.append("daily_market_context_buy_softened")
    elif _contains_any(str(getattr(result, "operation_advice", "") or ""), _buy_markers(language)):
        adjustments.append("daily_market_context_buy_softened")

    softened_advice = _softened_operation_advice(language)
    result.operation_advice = softened_advice

    if _is_high_confidence(getattr(result, "confidence_level", "")):
        result.confidence_level = localize_confidence_level("medium", language)
        adjustments.append("confidence_capped_daily_market_context")

    result.sentiment_score = _cap_conservative_sentiment_score(
        getattr(result, "sentiment_score", 0)
    )

    dashboard = getattr(result, "dashboard", None)
    if not isinstance(dashboard, dict):
        dashboard = {}
        result.dashboard = dashboard

    _sync_softened_dashboard_fields(
        dashboard,
        softened_advice=softened_advice,
        language=language,
    )

    phase_decision = dashboard.get("phase_decision")
    if not isinstance(phase_decision, dict):
        phase_decision = {}
        dashboard["phase_decision"] = phase_decision
    _append_softening_limitation(phase_decision, language=language)

    return adjustments


def _sync_softened_dashboard_fields(
    dashboard: dict[str, Any],
    *,
    softened_advice: str,
    language: str,
) -> None:
    dashboard["sentiment_score"] = _cap_conservative_sentiment_score(
        dashboard.get("sentiment_score", _GUARDRAIL_SENTIMENT_SCORE)
    )
    dashboard["operation_advice"] = softened_advice
    dashboard["decision_type"] = "hold"

    core = dashboard.get("core_conclusion")
    if isinstance(core, dict):
        core["one_sentence"] = softened_advice
        core["position_advice"] = _softened_position_advice(language)

    battle_plan = dashboard.get("battle_plan")
    if isinstance(battle_plan, dict):
        battle_plan["position_strategy"] = _softened_position_strategy(language)


def _softened_position_advice(language: str) -> dict[str, str]:
    # vi và en dùng chung câu chữ tiếng Anh như trước. / vi and en share the English wording as before.
    return {
        "no_position": "Do not open a new position until market risk eases or confirmation appears.",
        "has_position": "Hold only a small position; do not increase exposure, and reduce if risk controls break.",
    }


def _softened_position_strategy(language: str) -> dict[str, str]:
    position_advice = _softened_position_advice(language)
    return {
        "suggested_position": "Small/defensive position",
        "entry_plan": position_advice["no_position"],
        "risk_control": "Do not increase exposure before market risk eases; control drawdown strictly.",
    }


def _append_softening_limitation(phase_decision: dict[str, Any], *, language: str) -> None:
    limitations = phase_decision.get("data_limitations")
    if not isinstance(limitations, list):
        limitations = []
    limitation = "Daily market context is conservative/high risk; aggressive buy advice was softened."
    if limitation not in limitations:
        limitations.append(limitation)
    phase_decision["data_limitations"] = limitations
    reason = str(phase_decision.get("confidence_reason") or "").strip()
    reason_note = "Market context requires conservative sizing."
    phase_decision["confidence_reason"] = (
        f"{reason}; {reason_note}" if reason else reason_note
    )


def _is_conservative_context(context: Any) -> bool:
    if not isinstance(context, Mapping):
        return False
    tags = context.get("risk_tags")
    if isinstance(tags, list) and any(str(tag) in _CONSERVATIVE_TAGS for tag in tags):
        return True
    if str(context.get("position_cap") or "").strip():
        return True
    lowered = str(context.get("summary") or "").lower()
    return any(marker in lowered for marker in _CONSERVATIVE_TEXT_MARKERS)


def _has_aggressive_buy_signal(result: Any, *, language: str) -> bool:
    decision_type = str(getattr(result, "decision_type", "") or "").lower()
    if decision_type == "buy":
        advice = str(getattr(result, "operation_advice", "") or "")
        markers = _buy_markers(language)
        if _contains_any(advice, markers, language=language):
            return True
        if _contains_any(advice, markers, language=language, require_negation=True):
            return False
        return True
    advice = str(getattr(result, "operation_advice", "") or "")
    return _contains_any(advice, _buy_markers(language), language=language)


def _buy_markers(language: str) -> tuple[str, ...]:
    return _AGGRESSIVE_BUY_MARKERS


def _contains_any(
    text: str,
    markers: tuple[str, ...],
    *,
    language: str = "vi",
    require_negation: bool = False,
) -> bool:
    lowered = text.lower()
    negation_hints = _negation_hints_for(language)
    for marker in markers:
        marker_lower = marker.lower()
        marker_pos = 0
        while True:
            marker_pos = lowered.find(marker_lower, marker_pos)
            if marker_pos == -1:
                break
            context = lowered[max(0, marker_pos - _NEGATION_LOOKBACK):marker_pos]
            has_negation = _contains_negation_near_marker(context, negation_hints)
            if require_negation:
                if has_negation:
                    return True
            elif not has_negation:
                return True
            marker_pos += len(marker_lower)
    return False


def _contains_negation_near_marker(context: str, negation_hints: tuple[str, ...]) -> bool:
    separators = (",", ".", ";", ":", "?", "!", ")", "(")
    tail = context
    sep_pos = -1
    for separator in separators:
        candidate = context.rfind(separator)
        if candidate > sep_pos:
            sep_pos = candidate
    if sep_pos >= 0:
        tail = context[sep_pos + 1 :]
    return any(hint in tail for hint in negation_hints)


def _cap_conservative_sentiment_score(value: Any) -> int:
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        return _GUARDRAIL_SENTIMENT_SCORE
    return min(_GUARDRAIL_SENTIMENT_SCORE, max(0, score))

def _is_high_confidence(value: Any) -> bool:
    return str(value or "").strip().lower() in {"high", "cao"}
