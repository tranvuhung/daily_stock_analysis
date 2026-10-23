# -*- coding: utf-8 -*-
"""Shared sanitization for model-authored Agent dashboard payloads."""

from __future__ import annotations

from typing import Any, Dict


RESERVED_EXPLANATION_FIELD = "agent_disagreement_explanation"


def sanitize_agent_dashboard_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Remove fields reserved for deterministic post-processing.

    Both supported forged locations are removed at the shared LLM dashboard
    boundary.  The input mapping and nested dashboard mapping are not mutated.
    """
    sanitized = dict(payload)
    sanitized.pop(RESERVED_EXPLANATION_FIELD, None)

    nested = sanitized.get("dashboard")
    if isinstance(nested, dict):
        nested = dict(nested)
        nested.pop(RESERVED_EXPLANATION_FIELD, None)
        sanitized["dashboard"] = nested
    return sanitized


# Schema JSON "Decision Dashboard" dùng chung cho executor single-agent và DecisionAgent
# multi-agent, để hai đường sinh báo cáo không lệch key (renderer đọc đúng các key này).
# Shared Decision Dashboard JSON schema for the single-agent executor and the multi-agent
# DecisionAgent, so both generation paths emit the keys the report renderers read.
_DECISION_DASHBOARD_JSON_SCHEMA_TEMPLATE = """\
{
    "stock_name": "Company name",
    "sentiment_score": integer 0-100,
    "trend_prediction": "Strong Bullish/Bullish/Sideways/Bearish/Strong Bearish",
    "operation_advice": "Buy/Add Position/Hold/Reduce/Sell/Watch",
    "decision_type": "buy/hold/sell",
    "confidence_level": "High/Medium/Low",
    "dashboard": {
        "core_conclusion": {
            "one_sentence": "One-sentence core conclusion (under 30 words)",
            "signal_type": "🟢 Buy Signal/🟡 Hold and Watch/🔴 Sell Signal/⚠️ Risk Alert",
            "time_sensitivity": "Act now/Today/This week/Not urgent",
            "position_advice": {
                "no_position": "Advice for a flat position",
                "has_position": "Advice for an existing position"
            }
        },
        "data_perspective": {
            "trend_status": {"ma_alignment": "", "is_bullish": true, "trend_score": 0},
            "price_position": {"current_price": 0, "ma5": 0, "ma10": 0, "ma20": 0, "bias_ma5": 0, "bias_status": "", "support_level": 0, "resistance_level": 0},
            "volume_analysis": {"volume_ratio": 0, "volume_status": "", "turnover_rate": 0, "volume_meaning": ""}
        },
        "intelligence": {
            "latest_news": "",
            "risk_alerts": [],
            "positive_catalysts": [],
            "earnings_outlook": "",
            "sentiment_summary": ""
        },
        "battle_plan": {
            "sniper_points": {"ideal_buy": "", "secondary_buy": "", "stop_loss": "", "take_profit": ""},
            "position_strategy": {"suggested_position": "", "entry_plan": "", "risk_control": ""},
            "action_checklist": []
        },
        "phase_decision": {
            "phase_context": {"phase": "premarket/intraday/lunch_break/closing_auction/postmarket/non_trading/unknown"},
            "action_window": "Pre-market plan/Intraday tracking/Midday confirmation/Pre-close risk control/Post-close review/Non-trading-day watch",
            "immediate_action": "Act now/Wait for confirmation/Watch/Stop-loss or take-profit alert/Do not chase/No intraday action",
            "watch_conditions": ["Watch condition 1", "Watch condition 2"],
            "next_check_time": "Next checkpoint, or the market's local time",
            "confidence_reason": "Why this confidence level, covering session-phase and data-quality limits",
            "data_limitations": ["Phase or data-quality limitation 1", "Phase or data-quality limitation 2"]
        },
        "signal_attribution": {
            "technical_indicators": technical-indicator contribution (0-100; valid non-zero contributions should sum to 100; all zeros means no valid signal),
            "news_sentiment": news-sentiment contribution (0-100; valid non-zero contributions should sum to 100; all zeros means no valid signal),
            "fundamentals": fundamentals contribution (0-100; valid non-zero contributions should sum to 100; all zeros means no valid signal),
            "market_conditions": market-environment contribution (0-100; valid non-zero contributions should sum to 100; all zeros means no valid signal),
            "strongest_bullish_signal": "Name of the strongest bullish signal",
            "strongest_bearish_signal": "Name of the strongest bearish signal"
        }
    },
    "analysis_summary": "Around 100 words of overall analysis",
    "key_points": "3-5 key points, comma separated",
    "risk_warning": "Risk warning",
    "buy_reason": "__BUY_REASON_HINT__",
    "trend_analysis": "Price-structure analysis",
    "short_term_outlook": "Short-term outlook (1-3 days)",
    "medium_term_outlook": "Medium-term outlook (1-2 weeks)",
    "technical_analysis": "Overall technical analysis",
    "ma_analysis": "Moving-average analysis",
    "volume_analysis": "Volume analysis",
    "pattern_analysis": "Candlestick pattern analysis",
    "fundamental_analysis": "Fundamental analysis",
    "sector_position": "Sector and industry analysis",
    "company_highlights": "Company highlights and risks",
    "news_summary": "News summary",
    "market_sentiment": "Market sentiment",
    "hot_topics": "Related hot topics"
}"""

DEFAULT_BUY_REASON_HINT = "Reasoning for the call, citing the trading principles"


def decision_dashboard_json_schema(buy_reason_hint: str = DEFAULT_BUY_REASON_HINT) -> str:
    """Trả về schema JSON dashboard (ngoặc đơn, chưa escape cho ``str.format``).

    Return the dashboard JSON schema with single braces (not escaped for ``str.format``).
    """
    return _DECISION_DASHBOARD_JSON_SCHEMA_TEMPLATE.replace("__BUY_REASON_HINT__", buy_reason_hint)


__all__ = [
    "DEFAULT_BUY_REASON_HINT",
    "RESERVED_EXPLANATION_FIELD",
    "decision_dashboard_json_schema",
    "sanitize_agent_dashboard_payload",
]
