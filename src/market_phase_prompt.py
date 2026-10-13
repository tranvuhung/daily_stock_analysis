# -*- coding: utf-8 -*-
"""Prompt rendering for Issue #1386 runtime market phase context."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# Chỉ còn khung tiếng Anh (vi và en dùng chung). / English scaffolding only (shared by vi and en).
_PHASE_LABELS_EN = {
    "premarket": "pre-market",
    "intraday": "intraday",
    "lunch_break": "lunch break",
    "closing_auction": "near close",
    "postmarket": "post-market",
    "non_trading": "non-trading day",
    "unknown": "unknown phase",
}

_KNOWN_PHASES = set(_PHASE_LABELS_EN)

_WARNING_LABELS_EN = {
    "unknown_market": "unknown market",
    "calendar_unavailable": "trading calendar unavailable",
    "calendar_error": "trading calendar error",
}


def format_market_phase_prompt_section(
    market_phase_context: Optional[Dict[str, Any]],
    *,
    report_language: str = "vi",
) -> str:
    """Return a human-readable prompt section for a P1a market phase payload.

    The helper is intentionally narrow: callers pass the runtime dict produced
    by ``MarketPhaseContext.to_dict()`` when available. Missing optional fields
    are omitted, unknown phases use the conservative ``unknown`` template, and
    raw runtime keys such as ``market_phase_context`` are never rendered.
    """
    if not isinstance(market_phase_context, dict) or not market_phase_context:
        return ""

    # vi và en dùng chung khung tiếng Anh; chỉ thị ngôn ngữ đầu ra do decision agent đặt.
    # vi and en share the English scaffolding; the output-language directive is set elsewhere.
    raw_phase = market_phase_context.get("phase")
    phase = raw_phase if isinstance(raw_phase, str) and raw_phase in _KNOWN_PHASES else "unknown"

    return _format_en(market_phase_context, phase)


def _format_en(ctx: Dict[str, Any], phase: str) -> str:
    label = _PHASE_LABELS_EN[phase]
    lines = ["", "## Market Phase Context", f"- Current market phase: {label}"]
    lines.extend(_metadata_lines_en(ctx))
    lines.append(f"- Phase constraint: {_phase_rule_en(ctx, phase)}")

    warning_text = _warning_text(ctx.get("warnings"))
    if warning_text:
        lines.append(f"- Degradation note: {warning_text}; keep the analysis conservative.")

    return "\n".join(lines) + "\n"


def _metadata_lines_en(ctx: Dict[str, Any]) -> List[str]:
    items: List[str] = []
    market = _string_value(ctx.get("market"))
    market_time = _string_value(ctx.get("market_local_time"))
    effective_date = _string_value(ctx.get("effective_daily_bar_date"))
    minutes_to_open = _int_like(ctx.get("minutes_to_open"))
    minutes_to_close = _int_like(ctx.get("minutes_to_close"))

    if market:
        items.append(f"- Market: {market}")
    if market_time:
        items.append(f"- Market-local time: {market_time}")
    if effective_date:
        items.append(f"- Latest reusable complete daily bar date: {effective_date}")
    if minutes_to_open is not None:
        items.append(f"- About {minutes_to_open} minutes until the regular session opens.")
    if minutes_to_close is not None:
        items.append(f"- About {minutes_to_close} minutes until the regular session closes.")
    return items


def _phase_rule_en(ctx: Dict[str, Any], phase: str) -> str:
    effective_date = _string_value(ctx.get("effective_daily_bar_date"))
    date_hint = f" ({effective_date})" if effective_date else ""

    if phase == "premarket":
        return (
            f"The regular session has not opened. Do not describe today's price action as already happened; "
            f"use only the latest complete daily bar{date_hint} and pre-market information for the opening plan."
        )
    if phase in {"intraday", "lunch_break", "closing_auction"}:
        base = "This is not a post-market recap. Focus on the current intraday state, watch conditions, and next check point."
        if ctx.get("is_partial_bar") is True:
            base += " The latest daily bar may be unfinished; do not treat it as a complete daily candle."
        if phase == "lunch_break":
            base += " During the lunch break, later confirmation depends on the afternoon session."
        if phase == "closing_auction":
            base += " Near the close, emphasize end-of-day risk control and overnight-position decisions."
        return base
    if phase == "postmarket":
        return "The regular session has ended, so a complete-session recap style is acceptable."
    if phase == "non_trading":
        return (
            f"This is a non-trading day or forced run. Use the latest complete daily bar{date_hint} and known events; "
            "do not invent today's intraday movement."
        )
    return "The market phase cannot be inferred reliably. Do not invent pre-market or intraday facts, and keep conclusions conservative."


def _warning_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    rendered = [_WARNING_LABELS_EN[item] for item in value if isinstance(item, str) and item in _WARNING_LABELS_EN]
    return ", ".join(rendered)


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _int_like(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
