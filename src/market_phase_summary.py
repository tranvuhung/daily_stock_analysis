# -*- coding: utf-8 -*-
"""Low-sensitivity public summary for Issue #1386 market phase context."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from src.core.trading_calendar import MarketPhase


MARKET_PHASE_SUMMARY_KEY = "market_phase_summary"

_ALLOWED_PHASES = tuple(phase.value for phase in MarketPhase)
_BOOLEAN_KEYS = ("is_trading_day", "is_market_open_now", "is_partial_bar")
_INTEGER_KEYS = ("minutes_to_open", "minutes_to_close")
_TEXT_KEYS = (
    "market",
    "market_local_time",
    "session_date",
    "effective_daily_bar_date",
    "trigger_source",
    "analysis_intent",
)
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "credential",
    "webhook",
)
_INTRADAY_BUCKET_PHASES = {"intraday", "lunch_break", "closing_auction"}
_SUPPORTED_MANUAL_ANALYSIS_PHASES = {"premarket", "intraday", "postmarket"}
_SUPPORTED_ANALYSIS_INTENTS = {"auto", *_SUPPORTED_MANUAL_ANALYSIS_PHASES}
_PUBLIC_SOURCE_LABELS_EN = {
    "alert_trigger_market_context": "alert trigger context",
    "analysis_history_snapshot": "recent analysis snapshot",
    "evaluator_snapshot": "evaluator snapshot",
    "legacy_text": "legacy text",
}
_PUBLIC_SOURCE_LABELS_VI = {
    "alert_trigger_market_context": "ngữ cảnh lúc kích hoạt cảnh báo",
    "analysis_history_snapshot": "snapshot phân tích gần nhất",
    "evaluator_snapshot": "snapshot của bộ đánh giá",
    "legacy_text": "văn bản cũ",
}
# Nhãn dòng trích đoạn phase/pack theo ngôn ngữ báo cáo.
# Excerpt line labels per report language.
_EXCERPT_LABELS = {
    "vi": {
        "phase": "giai đoạn",
        "market": "thị trường",
        "trigger": "nguồn kích hoạt",
        "source": "nguồn tóm tắt",
        "partial_bar": "cảnh báo nến chưa đóng: dữ liệu trong phiên có thể chưa đầy đủ",
        "data_quality": "chất lượng dữ liệu",
        "limitation": "hạn chế",
        "sep": ": ",
    },
    "en": {
        "phase": "phase",
        "market": "market",
        "trigger": "trigger",
        "source": "source",
        "partial_bar": "partial-bar warning: intraday data may be incomplete",
        "data_quality": "data quality",
        "limitation": "limitation",
        "sep": ": ",
    },
}
_MARKET_STATUS_PREFIX = {
    "vi": "Trạng thái thị trường",
    "en": "Market status",
}
# Chỉ còn thị trường Việt Nam. / Only the Vietnam market remains.
_MARKET_LABELS_EN = {"vn": "Vietnam"}
_MARKET_LABELS_VI = {"vn": "Việt Nam"}
_PHASE_LABELS_VI = {
    "premarket": "Trước phiên",
    "intraday": "Trong phiên",
    "lunch_break": "Nghỉ trưa",
    "closing_auction": "Sắp đóng cửa",
    "postmarket": "Sau phiên",
    "non_trading": "Ngày nghỉ",
    "unknown": "Chưa rõ giai đoạn",
}
_PHASE_LABELS_EN = {
    "premarket": "Pre-market",
    "intraday": "Intraday",
    "lunch_break": "Lunch break",
    "closing_auction": "Near close",
    "postmarket": "Post-market",
    "non_trading": "Non-trading",
    "unknown": "Unknown phase",
}


def render_market_phase_summary(phase_context: Any) -> Optional[Dict[str, Any]]:
    """Project a runtime MarketPhaseContext dict into a stable public summary."""
    payload = _as_mapping(phase_context)
    if not payload:
        return None

    phase = _safe_phase(payload.get("phase"))
    if phase is None:
        return None

    summary: Dict[str, Any] = {"phase": phase}
    for key in _TEXT_KEYS:
        summary[key] = _safe_text(payload.get(key)) or None
    for key in _BOOLEAN_KEYS:
        summary[key] = payload.get(key) if isinstance(payload.get(key), bool) else None
    for key in _INTEGER_KEYS:
        summary[key] = _safe_int(payload.get(key))
    summary["warnings"] = _list_strings(payload.get("warnings"))
    return summary


def extract_market_phase_summary(context_snapshot: Any) -> Optional[Dict[str, Any]]:
    """Extract and re-sanitize a persisted market phase summary."""
    snapshot = _as_mapping(context_snapshot)
    if not snapshot:
        return None
    summary = snapshot.get(MARKET_PHASE_SUMMARY_KEY)
    if not isinstance(summary, Mapping):
        return None
    return render_market_phase_summary(summary)


def rebuild_market_phase_summary_for_stock_code(
    stock_code: Any,
    context_snapshot: Any,
) -> Optional[Dict[str, Any]]:
    """Trả về phase summary đã làm sạch cho mã cổ phiếu.

    Chỉ còn thị trường Việt Nam nên không cần tính lại các trường dẫn xuất
    theo múi giờ JP/KR/TW như trước. ``stock_code`` được giữ để tương thích API.

    Return the sanitized phase summary for a stock code. Only Vietnam remains, so
    JP/KR/TW derived-field rebuilding is no longer needed; ``stock_code`` is kept
    for API compatibility.
    """
    summary = extract_market_phase_summary(context_snapshot)
    if not isinstance(summary, Mapping):
        return None
    return dict(summary)


def normalize_analysis_phase_bucket(value: Any) -> str:
    """Fold detailed phase labels into the public backtest/statistics buckets."""
    phase = _safe_text(value)
    if phase == "premarket":
        return "premarket"
    if phase in _INTRADAY_BUCKET_PHASES:
        return "intraday"
    if phase == "postmarket":
        return "postmarket"
    return "unknown"


def format_public_phase_pack_excerpt(
    market_phase_summary: Any,
    analysis_context_pack_overview: Any = None,
    *,
    source: Optional[str] = None,
    report_language: str = "vi",
) -> str:
    """Format a low-sensitivity phase/pack excerpt for notifications."""
    phase_summary = _as_mapping(market_phase_summary)
    overview = _as_mapping(analysis_context_pack_overview)
    if not phase_summary and not overview:
        return ""
    lang = _resolve_lang(report_language)
    source_label = _source_label(source, lang)

    labels = _EXCERPT_LABELS[lang]
    sep = labels["sep"]

    lines: List[str] = []
    if phase_summary:
        phase = _safe_text(phase_summary.get("phase")) or "unknown"
        market = _safe_text(phase_summary.get("market"))
        trigger_source = _safe_text(phase_summary.get("trigger_source"))
        parts = [f"{labels['phase']}{sep}{phase}"]
        if market:
            parts.append(f"{labels['market']}{sep}{market}")
        if trigger_source:
            parts.append(f"{labels['trigger']}{sep}{trigger_source}")
        if source_label:
            parts.append(f"{labels['source']}{sep}{source_label}")
        lines.append("- " + " | ".join(parts))
        if phase_summary.get("is_partial_bar") is True:
            lines.append(f"- {labels['partial_bar']}")

    quality = overview.get("data_quality") if isinstance(overview, Mapping) else None
    if isinstance(quality, Mapping):
        level = _safe_text(quality.get("level"))
        if level:
            # Giữ dấu ": " ASCII như trước cho mọi ngôn ngữ (test/consumer cũ dựa vào định dạng này).
            # Keep the ASCII ": " separator for every language, as before.
            lines.append(f"- {labels['data_quality']}: {level}")
        limitations = _list_strings(quality.get("limitations"), limit=2)
        for item in limitations:
            lines.append(f"- {labels['limitation']}: {item}")

    return "\n".join(lines)


def format_public_market_status_line(
    market_phase_summary: Any,
    *,
    report_language: str = "vi",
) -> str:
    """Format one compact market/phase line for aggregate reports."""
    phase_summary = _as_mapping(market_phase_summary)
    if not phase_summary:
        return ""
    phase = _safe_phase(phase_summary.get("phase"))
    if phase is None:
        return ""

    lang = _resolve_lang(report_language)
    phase_labels = _PHASE_LABELS_EN if lang == "en" else _PHASE_LABELS_VI
    market_labels = _MARKET_LABELS_EN if lang == "en" else _MARKET_LABELS_VI
    phase_label = phase_labels.get(phase, phase)
    market = _safe_text(phase_summary.get("market"))
    market_key = market.lower()
    if market_key:
        market_label = market_labels.get(market_key, market.upper())
        value = f"{market_label} · {phase_label}"
    else:
        value = phase_label
    return f"{_MARKET_STATUS_PREFIX[lang]}: {value}"


def _resolve_lang(report_language: Any) -> str:
    """Chỉ còn 'en' và 'vi'; mọi giá trị khác quy về 'vi'. / Only 'en' and 'vi'; anything else is 'vi'."""
    return "en" if str(report_language or "").strip().lower().startswith("en") else "vi"


def _as_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _safe_phase(value: Any) -> Optional[str]:
    text = _safe_text(value)
    return text if text in _ALLOWED_PHASES else None


def _source_label(value: Any, lang: str) -> Optional[str]:
    source = _safe_text(value)
    if not source:
        return None
    labels = _PUBLIC_SOURCE_LABELS_EN if lang == "en" else _PUBLIC_SOURCE_LABELS_VI
    return labels.get(source, source)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (Mapping, list, tuple, set)):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "[REDACTED]"
    return text


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _list_strings(value: Any, *, limit: int = 5) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = _safe_text(item)
        if text and text not in result:
            result.append(text)
    return result[:limit]
