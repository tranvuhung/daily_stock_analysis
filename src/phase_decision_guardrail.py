# -*- coding: utf-8 -*-
"""Phase-aware decision guardrails for Issue #1386 P5."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.analysis_context_pack_prompt import CORE_DEGRADED_STATUSES
from src.market_phase_summary import render_market_phase_summary
from src.report_language import (
    localize_confidence_level,
    localize_data_limitation,
    normalize_report_language,
)
from src.utils.sniper_points import extract_sniper_points

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult


INTRADAY_PHASES = {"intraday", "lunch_break", "closing_auction"}
CONSERVATIVE_ACTION_PHASES = {"premarket", "non_trading", "unknown"}
CORE_DATA_BLOCKS = {"quote", "daily_bars", "technical"}

_PHASE_CONTEXT_KEYS = (
    "phase",
    "market",
    "market_local_time",
    "session_date",
    "effective_daily_bar_date",
    "is_trading_day",
    "is_market_open_now",
    "is_partial_bar",
    "minutes_to_open",
    "minutes_to_close",
    "trigger_source",
    "analysis_intent",
    "warnings",
)

# Mẫu nhận diện (vi/en; đã bỏ zh/ko). / Detection patterns (vi/en; zh/ko removed).
_EN_POSTMARKET_RECAP_PATTERNS = (
    "after today's close",
    "after today’s close",
    "after the close",
    "post-market recap",
    "post market recap",
    "focus tomorrow",
    "tomorrow's focus",
    "tomorrow’s focus",
    "sau khi đóng cửa hôm nay",
    "điểm lại sau phiên",
    "tâm điểm ngày mai",
)

_IMMEDIATE_ACTION_MARKERS_EN = ("buy now", "sell now", "immediate buy", "immediate sell", "add now", "reduce now")
_NEGATION_PREFIXES_EN = ("do not", "don't", "dont", "not", "no", "avoid", "hold off", "without")

def _recap_patterns_for(language: str) -> tuple[str, ...]:
    return _EN_POSTMARKET_RECAP_PATTERNS


def _immediate_markers_for(language: str) -> tuple[str, ...]:
    return _IMMEDIATE_ACTION_MARKERS_EN


def _negations_for(language: str) -> tuple[str, ...]:
    return _NEGATION_PREFIXES_EN


def _reason_text(language: str, *, en: str, vi: str) -> str:
    # Câu do guardrail tự chèn phải theo ngôn ngữ báo cáo.
    # Guardrail-inserted wording follows the report language.
    return en if language == "en" else vi


def apply_phase_decision_guardrails(
    result: "AnalysisResult",
    *,
    market_phase_summary: Optional[Dict[str, Any]],
    analysis_context_pack_overview: Optional[Dict[str, Any]],
    report_language: str = "vi",
) -> List[str]:
    """Apply phase/data-quality guardrails to an AnalysisResult in place."""

    if result is None:
        return []

    # Chỉ còn 'en' và 'vi'. / Only 'en' and 'vi' remain.
    language = "en" if normalize_report_language(report_language or getattr(result, "report_language", "vi")) == "en" else "vi"
    phase_summary = _safe_phase_summary(market_phase_summary)
    overview = analysis_context_pack_overview if isinstance(analysis_context_pack_overview, Mapping) else None
    adjustments: List[str] = []

    dashboard_value = getattr(result, "dashboard", None)
    if not isinstance(dashboard_value, dict):
        dashboard_value = {}
        setattr(result, "dashboard", dashboard_value)
    dashboard = dashboard_value
    phase_decision = dashboard.get("phase_decision")
    if not isinstance(phase_decision, dict):
        phase_decision = {}
    dashboard["phase_decision"] = phase_decision

    _ensure_phase_decision_shape(phase_decision)

    if phase_summary:
        phase_decision["phase_context"] = _phase_context_from_summary(phase_summary)

    llm_limitations = _list_strings(phase_decision.get("data_limitations"))
    merged_limitations = _merge_limitations(
        llm_limitations,
        _phase_warning_limitations(phase_summary, language=language),
        _overview_limitations(overview, language=language, already_stated=llm_limitations),
    )
    phase_decision["data_limitations"] = merged_limitations

    phase = _safe_text(phase_summary.get("phase")) if phase_summary else ""
    core_degraded = _has_core_degraded_block(overview)
    initially_high_confidence = _is_high_confidence(getattr(result, "confidence_level", ""))

    if core_degraded and initially_high_confidence:
        result.confidence_level = localize_confidence_level("medium", language)
        reason = _reason_text(
            language,
            en="Core quote, daily-bar, or technical data is degraded; high confidence was capped.",
            vi="Dữ liệu lõi (báo giá, nến ngày hoặc kỹ thuật) bị suy giảm nên độ tin cậy cao đã được hạ xuống.",
        )
        _append_reason(phase_decision, reason)
        adjustments.append("confidence_capped_core_data_degraded")

    has_non_intraday_action = (
        phase in CONSERVATIVE_ACTION_PHASES
        and _has_immediate_buy_sell_signal(result, phase_decision, language=language)
    )
    if has_non_intraday_action:
        phase_decision["immediate_action"] = _safe_wait_action(language)
        reason = _reason_text(
            language,
            en="Current market phase does not support immediate intraday buy/sell action.",
            vi="Phiên thị trường hiện tại không phù hợp để mua/bán ngay trong phiên.",
        )
        _append_reason(phase_decision, reason)
        adjustments.append("non_intraday_action_adjusted")
        if initially_high_confidence:
            result.confidence_level = localize_confidence_level("low", language)
            adjustments.append("confidence_capped_non_intraday_action")

    if phase in INTRADAY_PHASES and _contains_postmarket_recap(result, phase_decision, language=language):
        reason = _reason_text(
            language,
            en="Intraday output contained post-market recap wording; replaced with phase-safe action wording.",
            vi="Kết quả trong phiên có câu chữ kiểu điểm lại sau phiên; đã thay bằng hành động phù hợp với phiên.",
        )
        _replace_postmarket_recap_fields(result, phase_decision, language=language)
        _append_reason(phase_decision, reason)
        adjustments.append("postmarket_recap_wording_adjusted")

    level_issues = _sniper_level_order_issues(result, language=language)
    if level_issues:
        reason = _reason_text(
            language,
            en="Plan price levels are inconsistent ({issues}); re-check them before trading.",
            vi="Các mức giá trong kế hoạch không nhất quán ({issues}); cần kiểm tra lại trước khi giao dịch.",
        ).format(issues="; ".join(level_issues))
        _append_reason(phase_decision, reason)
        adjustments.append("sniper_level_order_invalid")

    if adjustments:
        phase_decision["data_limitations"] = _merge_limitations(
            phase_decision.get("data_limitations"),
            [_adjustment_limitation_text(item, language=language) for item in adjustments],
        )

    return adjustments


def _ensure_phase_decision_shape(phase_decision: Dict[str, Any]) -> None:
    phase_decision.setdefault("phase_context", None)
    phase_decision.setdefault("action_window", None)
    phase_decision.setdefault("immediate_action", None)
    phase_decision["watch_conditions"] = _list_strings(phase_decision.get("watch_conditions"))
    phase_decision.setdefault("next_check_time", None)
    phase_decision.setdefault("confidence_reason", None)
    phase_decision["data_limitations"] = _list_strings(phase_decision.get("data_limitations"))


def _safe_phase_summary(value: Any) -> Optional[Dict[str, Any]]:
    summary = render_market_phase_summary(value)
    if not summary:
        return None
    return summary


def _phase_context_from_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: summary.get(key) for key in _PHASE_CONTEXT_KEYS if key in summary}


def _has_core_degraded_block(overview: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(overview, Mapping):
        return False
    blocks = overview.get("blocks")
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        key = _safe_text(block.get("key"))
        status = _safe_text(block.get("status"))
        if key in CORE_DATA_BLOCKS and status in CORE_DEGRADED_STATUSES:
            return True
    return False


# Từ khoá nhận diện một giới hạn "<block>: <status>" mà LLM đã tự viết bằng lời.
# Keywords recognising a "<block>: <status>" limitation the LLM already wrote in prose.
_LIMITATION_BLOCK_KEYWORDS = {
    "quote": ("quote", "báo giá"),
    "daily_bars": ("daily_bars", "daily bar", "nến ngày"),
    "technical": ("technical", "kỹ thuật"),
    "news": ("news", "tin tức"),
    "fundamentals": ("fundamental", "cơ bản"),
}
_LIMITATION_STATUS_KEYWORDS = {
    "fallback": ("fallback", "dự phòng"),
    "stale": ("stale", "trễ", "cũ"),
    "missing": ("missing", "thiếu"),
    "partial": ("partial", "một phần"),
    "estimated": ("estimated", "ước lượng"),
    "fetch_failed": ("fetch_failed", "fetch failed", "lỗi"),
    "not_supported": ("not_supported", "not supported", "không hỗ trợ"),
}


def _limitation_already_stated(code: str, stated: List[str]) -> bool:
    """LLM đã nêu cùng block + trạng thái chưa (vd. "Báo giá ... dự phòng (fallback)")?

    Whether the LLM already stated the same block and status in its own words.
    """
    block, _, status = code.partition(":")
    block_words = _LIMITATION_BLOCK_KEYWORDS.get(block.strip())
    status_words = _LIMITATION_STATUS_KEYWORDS.get(status.strip())
    if not block_words or not status_words:
        return False
    for item in stated:
        text = item.lower()
        if any(word in text for word in block_words) and any(word in text for word in status_words):
            return True
    return False


def _overview_limitations(
    overview: Optional[Mapping[str, Any]],
    *,
    language: str,
    already_stated: Optional[List[str]] = None,
) -> List[str]:
    if not isinstance(overview, Mapping):
        return []
    data_quality = overview.get("data_quality")
    if not isinstance(data_quality, Mapping):
        return []
    stated = already_stated or []
    # Mã chuẩn ("quote: stale") được dịch khi hiển thị; bỏ mã LLM đã nêu để tránh trùng ý.
    # Canonical codes are localized; skip ones the LLM already stated to avoid duplicates.
    return [
        localize_data_limitation(item, language)
        for item in _list_strings(data_quality.get("limitations"))
        if not _limitation_already_stated(item, stated)
    ]


def _phase_warning_limitations(summary: Optional[Mapping[str, Any]], *, language: str) -> List[str]:
    if not isinstance(summary, Mapping):
        return []
    warnings = _list_strings(summary.get("warnings"))
    if not warnings:
        return []
    prefix = "market phase warning" if language == "en" else "Cảnh báo phiên"
    return [f"{prefix}: {item}" for item in warnings]


def _merge_limitations(*groups: Any, limit: int = 5) -> List[str]:
    merged: List[str] = []
    for group in groups:
        for item in _list_strings(group):
            if item not in merged:
                merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def _is_high_confidence(value: Any) -> bool:
    text = _safe_text(value).lower()
    return text in {"high", "cao"}


def _has_immediate_buy_sell_signal(
    result: "AnalysisResult",
    phase_decision: Mapping[str, Any],
    *,
    language: str,
) -> bool:
    haystack = " ".join(
        _safe_text(value)
        for value in (
            getattr(result, "operation_advice", ""),
            phase_decision.get("immediate_action"),
        )
    ).lower()
    immediate_markers = _immediate_markers_for(language)
    if _contains_non_negated_marker(haystack, immediate_markers, language=language):
        return True
    return _safe_text(getattr(result, "decision_type", "")).lower() in {"buy", "sell"}


def _contains_non_negated_marker(text: str, markers: tuple[str, ...], *, language: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    for marker in markers:
        marker_text = marker.lower()
        start = 0
        while True:
            index = lowered.find(marker_text, start)
            if index < 0:
                break
            if not _is_negated_marker(lowered, index, language=language):
                return True
            start = index + len(marker_text)
    return False


def _is_negated_marker(text: str, marker_index: int, *, language: str) -> bool:
    window = 24
    prefix = text[max(0, marker_index - window):marker_index].rstrip()
    negations = _negations_for(language)
    return any(prefix.endswith(item) for item in negations)


def _contains_postmarket_recap(result: "AnalysisResult", phase_decision: Mapping[str, Any], *, language: str) -> bool:
    dashboard_value = getattr(result, "dashboard", None)
    dashboard = dashboard_value if isinstance(dashboard_value, dict) else {}
    core = dashboard.get("core_conclusion")
    core = core if isinstance(core, Mapping) else {}
    values = (
        core.get("one_sentence"),
        getattr(result, "operation_advice", ""),
        getattr(result, "analysis_summary", ""),
        phase_decision.get("immediate_action"),
    )
    patterns = _recap_patterns_for(language)
    return any(_contains_any(value, patterns) for value in values)


def _replace_postmarket_recap_fields(
    result: "AnalysisResult",
    phase_decision: Dict[str, Any],
    *,
    language: str,
) -> None:
    dashboard_value = getattr(result, "dashboard", None)
    dashboard = dashboard_value if isinstance(dashboard_value, dict) else {}
    core = dashboard.get("core_conclusion")
    if not isinstance(core, dict):
        core = {}
        dashboard["core_conclusion"] = core
    safe_action = _safe_wait_action(language)
    safe_summary = _reason_text(
        language,
        en=(
            "This is an intraday phase; use live state, watch conditions, and the next "
            "check point rather than post-market recap wording."
        ),
        vi=(
            "Đang trong phiên giao dịch; dựa vào diễn biến hiện tại, điều kiện theo dõi và "
            "mốc kiểm tra tiếp theo thay vì câu chữ điểm lại sau phiên."
        ),
    )
    if _contains_any(core.get("one_sentence"), _patterns(language)):
        core["one_sentence"] = safe_action
    if _contains_any(getattr(result, "operation_advice", ""), _patterns(language)):
        result.operation_advice = safe_action
    if _contains_any(getattr(result, "analysis_summary", ""), _patterns(language)):
        result.analysis_summary = safe_summary
    if _contains_any(phase_decision.get("immediate_action"), _patterns(language)):
        phase_decision["immediate_action"] = safe_action


def _append_reason(phase_decision: Dict[str, Any], reason: str) -> None:
    existing = _safe_text(phase_decision.get("confidence_reason"))
    if not existing:
        phase_decision["confidence_reason"] = reason
        return
    if reason not in existing:
        # Câu trước đã kết thúc bằng dấu câu thì chỉ cần khoảng trắng, tránh ".;".
        # Avoid ".;" when the previous sentence already ends with punctuation.
        sep = " " if existing.endswith((".", "!", "?", "…")) else "; "
        phase_decision["confidence_reason"] = f"{existing}{sep}{reason}"


def _adjustment_limitation_text(adjustment: str, *, language: str) -> str:
    if adjustment == "postmarket_recap_wording_adjusted":
        return _reason_text(
            language,
            en="post-market recap wording adjusted",
            vi="đã chỉnh câu chữ điểm lại sau phiên",
        )
    if adjustment == "non_intraday_action_adjusted":
        return _reason_text(
            language,
            en="non-intraday immediate action adjusted",
            vi="đã chỉnh hành động tức thời ngoài phiên",
        )
    if adjustment == "confidence_capped_non_intraday_action":
        return _reason_text(
            language,
            en="confidence capped for non-intraday action",
            vi="đã hạ độ tin cậy vì hành động ngoài phiên",
        )
    if adjustment == "sniper_level_order_invalid":
        return _reason_text(
            language,
            en="plan price levels out of order",
            vi="các mức giá kế hoạch sai thứ tự",
        )
    if adjustment == "confidence_capped_core_data_degraded":
        return _reason_text(
            language,
            en="confidence capped due to degraded core data",
            vi="đã hạ độ tin cậy vì dữ liệu lõi bị suy giảm",
        )
    return adjustment


def _format_level(value: float, language: str) -> str:
    text = f"{value:,.0f}"
    # VND kiểu Việt Nam dùng "." hàng nghìn / VN style uses "." for thousands.
    return text if language == "en" else text.replace(",", ".")


def _sniper_level_order_issues(result: "AnalysisResult", *, language: str) -> List[str]:
    """Phát hiện cắt lỗ không nằm dưới điểm mua/chốt lời (không tự sửa mức giá).

    Detect a stop-loss that is not below the buy or take-profit levels. Levels
    are only reported, never rewritten, so no price is invented.
    """
    try:
        points = extract_sniper_points(result)
    except Exception:  # noqa: BLE001 - kiểm tra phụ, không được làm hỏng luồng chính
        return []
    stop_loss = points.get("stop_loss")
    if stop_loss is None:
        return []
    labels = {
        "ideal_buy": ("ideal buy", "điểm mua lý tưởng"),
        "secondary_buy": ("secondary buy", "điểm mua thứ cấp"),
        "take_profit": ("take-profit", "chốt lời"),
    }
    stop_label = "stop-loss" if language == "en" else "cắt lỗ"
    issues: List[str] = []
    for key, (en_label, vi_label) in labels.items():
        level = points.get(key)
        if level is None or stop_loss < level:
            continue
        label = en_label if language == "en" else vi_label
        issues.append(
            f"{stop_label} {_format_level(stop_loss, language)} ≥ {label} {_format_level(level, language)}"
        )
    return issues


def _safe_wait_action(language: str) -> str:
    return _reason_text(
        language,
        en="Wait for intraday confirmation; do not chase.",
        vi="Chờ xác nhận trong phiên; không mua đuổi.",
    )


def _patterns(language: str) -> tuple[str, ...]:
    return _recap_patterns_for(language)


def _contains_any(value: Any, patterns: tuple[str, ...]) -> bool:
    text = _safe_text(value).lower()
    return bool(text) and any(pattern.lower() in text for pattern in patterns)


def _list_strings(value: Any, *, limit: int = 20) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = _safe_text(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (Mapping, list, tuple, set)):
        return ""
    return str(value).strip()
