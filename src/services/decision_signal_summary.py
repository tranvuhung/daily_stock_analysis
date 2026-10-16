# -*- coding: utf-8 -*-
"""Low-sensitive DecisionSignal summaries for notifications and risk views."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.core.markets import get_spec
from src.report_language import get_report_labels
from src.utils.sanitize import sanitize_decision_signal_payload, sanitize_decision_signal_text


SUMMARY_FIELDS = (
    "id",
    "stock_code",
    "stock_name",
    "market",
    "action",
    "action_label",
    "confidence",
    "score",
    "horizon",
    "status",
    "source_type",
    "source_report_id",
    "entry_low",
    "entry_high",
    "stop_loss",
    "target_price",
    "reason",
    "watch_conditions",
    "invalidation",
    "risk_summary",
    "catalyst_summary",
    "created_at",
    "expires_at",
)

# Múi giờ hiển thị khi signal không mang market hợp lệ.
# Display timezone used when the signal carries no valid market.
_DEFAULT_MARKET = "vn"


def summarize_decision_signal(item: Any) -> Optional[Dict[str, Any]]:
    """Return a low-sensitive summary from a serialized DecisionSignal item."""

    if not isinstance(item, dict):
        return None
    summary: Dict[str, Any] = {}
    for field_name in SUMMARY_FIELDS:
        value = item.get(field_name)
        if value in (None, "", [], {}):
            continue
        summary[field_name] = sanitize_decision_signal_payload(value)
    return summary or None


def format_decision_signal_excerpt(summary: Any, report_language: str = "vi") -> str:
    """Format a compact public DecisionSignal excerpt for notification text."""

    if not isinstance(summary, dict) or not summary:
        return ""
    # Chỉ còn nhãn vi/en; ngôn ngữ khác (kể cả zh/ko cũ) quy về tiếng Việt.
    # Only vi/en labels exist; any other language (incl. legacy zh/ko) falls back to Vietnamese.
    raw_language = str(report_language or "").lower()
    language = "en" if raw_language.startswith("en") else "vi"
    labels = {
        "vi": {
            "heading": "Tín hiệu quyết định AI",
            "action": "Hành động",
            "horizon": "Khung thời gian",
            "confidence": "Độ tin cậy",
            "score": "Điểm",
            "entry": "Vùng mua",
            "stop_loss": "Cắt lỗ",
            "target_price": "Mục tiêu",
            "reason": "Lý do",
            "watch_conditions": "Điều kiện theo dõi",
            "invalidation": "Điều kiện vô hiệu",
            "risk_summary": "Rủi ro",
            "catalyst_summary": "Chất xúc tác",
            "source_report_id": "Báo cáo",
            "expires_at": "Hiệu lực đến",
        },
        "en": {
            "heading": "AI decision signal",
            "action": "Action",
            "horizon": "Horizon",
            "confidence": "Confidence",
            "score": "Score",
            "entry": "Entry",
            "stop_loss": "Stop loss",
            "target_price": "Target",
            "reason": "Reason",
            "watch_conditions": "Watch",
            "invalidation": "Invalidation",
            "risk_summary": "Risk",
            "catalyst_summary": "Catalysts",
            "source_report_id": "Report",
            "expires_at": "Valid until",
        },
    }[language]

    lines = [f"**{labels['heading']}**"]

    parts = []
    action_label = _public_scalar(summary.get("action_label") or summary.get("action"), max_length=32)
    if action_label:
        parts.append(f"{labels['action']}: {action_label}")
    horizon = _public_scalar(summary.get("horizon"), max_length=16)
    if horizon:
        parts.append(f"{labels['horizon']}: {horizon}")
    source_report_id = _public_scalar(summary.get("source_report_id"), max_length=24)
    if source_report_id:
        parts.append(f"{labels['source_report_id']}: #{source_report_id}")
    if parts:
        lines.append(" | ".join(parts))

    # Độ tin cậy lưu dạng 0..1 → hiển thị phần trăm; điểm hiển thị nguyên.
    # Confidence is stored as 0..1 and shown as a percentage; score is shown as-is.
    quality_parts = []
    confidence = _as_number(summary.get("confidence"))
    if confidence is not None:
        quality_parts.append(f"{labels['confidence']}: {_format_number(confidence * 100, language, max_decimals=0)}%")
    score = _as_number(summary.get("score"))
    if score is not None:
        quality_parts.append(f"{labels['score']}: {_format_number(score, language, max_decimals=1)}")
    if quality_parts:
        lines.append(" | ".join(quality_parts))

    price_parts = []
    entry = _format_price_range(summary.get("entry_low"), summary.get("entry_high"), language)
    if entry:
        price_parts.append(f"{labels['entry']}: {entry}")
    for key in ("stop_loss", "target_price"):
        price = _as_number(summary.get(key))
        if price is not None:
            price_parts.append(f"{labels[key]}: {_format_number(price, language)}")
    if price_parts:
        lines.append(" | ".join(price_parts))

    # Giữ trọn văn bản (chỉ sanitize, không cắt); danh sách nhiều mục tách thành dòng con.
    # Keep full sanitized text (no truncation); multi-item lists become sub-lines.
    for key in ("reason", "watch_conditions", "invalidation", "risk_summary", "catalyst_summary"):
        items = _public_items(summary.get(key))
        if not items:
            continue
        if len(items) == 1:
            lines.append(f"- {labels[key]}: {items[0]}")
        else:
            lines.append(f"- {labels[key]}:")
            lines.extend(f"  • {item}" for item in items)

    expires_at = _format_local_datetime(summary.get("expires_at"), summary.get("market"))
    if expires_at:
        lines.append(f"{labels['expires_at']}: {expires_at}")

    lines.append(get_report_labels(language)["not_investment_advice"])
    return "\n".join(lines)


def _public_scalar(value: Any, *, max_length: int) -> str:
    if value in (None, ""):
        return ""
    return sanitize_decision_signal_text(value)[:max_length]


def _coerce_structured(value: Any) -> Any:
    """Giải mã chuỗi JSON list/dict (dạng lưu trong DB); lỗi thì giữ nguyên chuỗi.

    Decode a JSON list/dict string (as persisted in the DB); keep the raw string on failure.
    """

    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def _public_items(value: Any) -> List[str]:
    value = _coerce_structured(value)
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (list, tuple)):
        raw_items = [str(item).strip() for item in value if str(item or "").strip()]
    elif isinstance(value, dict):
        raw_items = [
            f"{key}: {item}"
            for key, item in value.items()
            if str(key or "").strip() and str(item or "").strip()
        ]
    else:
        raw_items = [str(value).strip()]
    sanitized = (sanitize_decision_signal_text(item) for item in raw_items)
    return [item for item in sanitized if item]


def _as_number(value: Any) -> Optional[float]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value: float, language: str, *, max_decimals: int = 2) -> str:
    """Định dạng số: vi dùng '.' phân cách nghìn và ',' thập phân; en ngược lại.

    Format a number: vi uses '.' for thousands and ',' for decimals; en uses the reverse.
    """

    text = f"{value:,.{max_decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if language == "vi":
        text = text.translate(str.maketrans({",": ".", ".": ","}))
    return text


def _format_price_range(low: Any, high: Any, language: str) -> str:
    low_number = _as_number(low)
    high_number = _as_number(high)
    if low_number is None and high_number is None:
        return ""
    if low_number is None or high_number is None or low_number == high_number:
        single = low_number if low_number is not None else high_number
        return _format_number(single, language)
    return f"{_format_number(low_number, language)}–{_format_number(high_number, language)}"


def _format_local_datetime(value: Any, market: Any) -> str:
    """Đổi mốc thời gian (naive = UTC, như DB lưu) sang giờ của thị trường.

    Convert a timestamp (naive means UTC, as stored in the DB) to the market's local time.
    """

    if value in (None, ""):
        return ""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    spec = get_spec(str(market or "").lower()) or get_spec(_DEFAULT_MARKET)
    if spec is not None:
        try:
            parsed = parsed.astimezone(ZoneInfo(spec.timezone))
        except Exception:
            pass
    return parsed.strftime("%d/%m/%Y %H:%M")
