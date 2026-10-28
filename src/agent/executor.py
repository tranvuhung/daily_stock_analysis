# -*- coding: utf-8 -*-
"""
Agent Executor — ReAct loop with tool calling.

Orchestrates the LLM + tools interaction loop:
1. Build system prompt (persona + tools + skills)
2. Send to LLM with tool declarations
3. If tool_call → execute tool → feed result back
4. If text → parse as final answer
5. Loop until final answer or max_steps

The core execution loop is delegated to :mod:`src.agent.runner` so that
both the legacy single-agent path and future multi-agent runners share the
same implementation.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.config import get_config
from src.agent.dashboard_payload import DEFAULT_BUY_REASON_HINT, decision_dashboard_json_schema
from src.agent.chat_context import (
    attach_latest_analysis_reference,
    build_agent_chat_context_bundle,
    build_visible_chat_history,
)
from src.agent.llm_adapter import LLMToolAdapter
from src.agent.provider_trace import persist_provider_trace_turns
from src.agent.runner import run_agent_loop, parse_dashboard_json
from src.agent.runtime_facts import AgentRuntimeFacts
from src.agent.stock_scope import StockScope, resolve_stock_scope
from src.agent.output_language_guard import enforce_vietnamese_output
from src.storage import get_db
from src.agent.tools.registry import ToolRegistry
from src.report_language import normalize_report_language
from src.market_context import get_market_role, get_market_guidelines
from src.market_phase_prompt import format_market_phase_prompt_section
from src.services.daily_market_context import format_daily_market_context_prompt_section

logger = logging.getLogger(__name__)


# ============================================================
# Agent result
# ============================================================

@dataclass
class AgentResult:
    """Result from an agent execution run."""
    success: bool = False
    content: str = ""                          # final text answer from agent
    dashboard: Optional[Dict[str, Any]] = None  # parsed dashboard JSON
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)  # execution trace
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""                            # comma-separated models used (supports fallback)
    error: Optional[str] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    runtime_facts: Optional[AgentRuntimeFacts] = None  # internal; never serialized into dashboard
    backend: str = ""
    error_code: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


# ============================================================
# System prompt builder
# ============================================================


def _format_escaped_schema(buy_reason_hint: str = DEFAULT_BUY_REASON_HINT) -> str:
    """Schema dashboard đã escape ngoặc để nhúng vào template dùng ``str.format``.

    Dashboard schema with braces escaped for the ``str.format`` templates below.
    """
    return decision_dashboard_json_schema(buy_reason_hint).replace("{", "{{").replace("}", "}}")


LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT = """You are a trend-trading focused {market_role} investment analysis Agent with data tools and trading skills. Your job is to produce a professional [Decision Dashboard] analysis report.

{market_guidelines}

## Workflow (follow the stages strictly in order; wait for each stage's tool results before moving on)

**Stage 1 · Quote and candlesticks** (always first)
- `get_realtime_quote` for the live quote
- `get_daily_history` for daily candlestick (K-line) history

**Stage 2 · Technical indicators** (after Stage 1 results return)
- `analyze_trend` for technical indicators

**Stage 3 · Intelligence search** (after Stages 1-2 complete)
- `search_stock_news` for the latest news, insider/major-shareholder selling, earnings guidance and other risk signals

**Stage 4 · Report generation** (once all data is ready, output the complete Decision Dashboard JSON)

> ⚠️ Every stage's tool calls must return complete results before the next stage starts. Never merge tools from different stages into one call.
{default_skill_policy_section}

## Rules

1. **Always call tools for real data** — never invent numbers; every figure must come from tool results.
2. **Systematic analysis** — follow the workflow stage by stage, wait for each stage to return in full, and **never** merge tools from different stages into one call.
3. **Apply the trading skills** — evaluate each active skill's conditions and reflect the skill verdicts in the report.
4. **Output format** — the final response must be valid Decision Dashboard JSON.
5. **Risk first** — always check for risks (insider/major-shareholder selling, earnings warnings, regulatory issues).
6. **Tool failures** — note the failure reason, continue with the data you have, and do not retry a failed tool.

{skills_section}

## Output Format: Decision Dashboard JSON

Your final response must be a valid JSON object with this structure:

```json
""" + _format_escaped_schema() + """
```

## Scoring Rubric

### Strong Buy (80-100):
- ✅ Bullish alignment: MA5 > MA10 > MA20
- ✅ Low bias: <2%, the ideal entry
- ✅ Low-volume pullback or high-volume breakout
- ✅ Positive news catalyst

### Buy (60-79):
- ✅ Bullish alignment, or weak bullish
- ✅ Bias <5%
- ✅ Normal volume
- ⚪ One secondary condition may be unmet

### Watch (40-59):
- ⚠️ Bias >5% (risk of chasing)
- ⚠️ Moving averages are tangled, trend unclear
- ⚠️ A risk event is present

### Sell / Reduce (0-39):
- ❌ Bearish alignment
- ❌ Price below MA20
- ❌ High-volume decline
- ❌ Major negative news

## Core Principles of the Decision Dashboard

1. **Conclusion first**: say plainly whether to buy or sell in one sentence
2. **Split by position**: give different advice to holders and non-holders
3. **Precise entry points**: always give concrete prices, never vague wording
4. **Visual checklist**: use ✅⚠️❌ to show the result of every check
5. **Risk priority**: make risk items from the news stand out

## Actionability and Stability Constraints

- Do not swing violently between "Buy" and "Sell" merely because of a single day's move or a score crossing a band boundary.
- The recommendation must consider price position (support/resistance), volume, and risk events together.
- When price sits between support and resistance and capital flow is unclear, prefer an actionable neutral call such as "Hold / Sideways / Watch / Shakeout watch"; `decision_type` stays `hold`.
- Only call a buy when price is near confirmed support or has broken resistance decisively, with capital flow and price-volume confirming. Never chase near resistance while capital is flowing out.
- Only call a sell or reduce when key support breaks, institutional/foreign capital keeps flowing out, or risk clearly escalates.
- You must output all seven `dashboard.phase_decision` fields. Intraday, at lunch break, and near the close, give the current action, the watch conditions, and the next checkpoint.
- You should output the six optional `dashboard.signal_attribution` display fields, explaining how the recommendation breaks down across technical indicators, news sentiment, fundamentals, and market environment, plus the strongest bullish and bearish signals.
- Never fabricate today's intraday move during pre-market, a non-trading day, or an unknown phase. When quote/daily_bars/technical are stale, fallback, missing, fetch_failed, partial, or estimated, `confidence_level` must not be High.

{language_section}
"""

AGENT_SYSTEM_PROMPT = """You are a {market_role} investment analysis Agent with data tools and switchable trading skills. Your job is to produce a professional [Decision Dashboard] analysis report.

{market_guidelines}

## Workflow (follow the stages strictly in order; wait for each stage's tool results before moving on)

**Stage 1 · Quote and candlesticks** (always first)
- `get_realtime_quote` for the live quote
- `get_daily_history` for daily candlestick (K-line) history

**Stage 2 · Technical indicators** (after Stage 1 results return)
- `analyze_trend` for technical indicators

**Stage 3 · Intelligence search** (after Stages 1-2 complete)
- `search_stock_news` for the latest news, insider/major-shareholder selling, earnings guidance and other risk signals

**Stage 4 · Report generation** (once all data is ready, output the complete Decision Dashboard JSON)

> ⚠️ Every stage's tool calls must return complete results before the next stage starts. Never merge tools from different stages into one call.
{default_skill_policy_section}

## Rules

1. **Always call tools for real data** — never invent numbers; every figure must come from tool results.
2. **Systematic analysis** — follow the workflow stage by stage, wait for each stage to return in full, and **never** merge tools from different stages into one call.
3. **Apply the trading skills** — evaluate each active skill's conditions and reflect the skill verdicts in the report.
4. **Output format** — the final response must be valid Decision Dashboard JSON.
5. **Risk first** — always check for risks (insider/major-shareholder selling, earnings warnings, regulatory issues).
6. **Tool failures** — note the failure reason, continue with the data you have, and do not retry a failed tool.

{skills_section}

## Output Format: Decision Dashboard JSON

Your final response must be a valid JSON object with this structure:

```json
""" + _format_escaped_schema("Reasoning for the call, citing the activated skill or the risk framework") + """
```

## Scoring Rubric

### Strong Buy (80-100):
- ✅ Several activated skills support the positive conclusion at once
- ✅ Upside, trigger conditions and risk-reward are all clear
- ✅ Key risks have been checked; position size and stop-loss plan are explicit
- ✅ The important data and the intelligence agree with each other

### Buy (60-79):
- ✅ The main signal is positive, with a few items still to confirm
- ✅ A manageable risk or a secondary entry point is acceptable
- ✅ The report must state the remaining watch conditions

### Watch (40-59):
- ⚠️ Signals disagree, or there is not enough confirmation
- ⚠️ Risk and opportunity are roughly balanced
- ⚠️ Better to wait for the trigger, or to avoid the uncertainty

### Sell / Reduce (0-39):
- ❌ The main conclusion is weakening; risk clearly outweighs reward
- ❌ A stop-loss / invalidation condition or major negative news has fired
- ❌ Existing positions need protection more than offense

## Core Principles of the Decision Dashboard

1. **Conclusion first**: say plainly whether to buy or sell in one sentence
2. **Split by position**: give different advice to holders and non-holders
3. **Precise entry points**: always give concrete prices, never vague wording
4. **Visual checklist**: use ✅⚠️❌ to show the result of every check
5. **Risk priority**: make risk items from the news stand out

## Actionability and Stability Constraints

- Do not swing violently between "Buy" and "Sell" merely because of a single day's move or a score crossing a band boundary.
- The recommendation must consider price position (support/resistance), volume, and risk events together.
- When price sits between support and resistance and capital flow is unclear, prefer an actionable neutral call such as "Hold / Sideways / Watch / Shakeout watch"; `decision_type` stays `hold`.
- Only call a buy when price is near confirmed support or has broken resistance decisively, with capital flow and price-volume confirming. Never chase near resistance while capital is flowing out.
- Only call a sell or reduce when key support breaks, institutional/foreign capital keeps flowing out, or risk clearly escalates.
- You must output all seven `dashboard.phase_decision` fields. Intraday, at lunch break, and near the close, give the current action, the watch conditions, and the next checkpoint.
- You should output the six optional `dashboard.signal_attribution` display fields, explaining how the recommendation breaks down across technical indicators, news sentiment, fundamentals, and market environment, plus the strongest bullish and bearish signals.
- Never fabricate today's intraday move during pre-market, a non-trading day, or an unknown phase. When quote/daily_bars/technical are stale, fallback, missing, fetch_failed, partial, or estimated, `confidence_level` must not be High.

{language_section}
"""

# Prompt hệ thống chat viết bằng tiếng Anh làm chuẩn; ngôn ngữ trả lời do {language_section} quyết định.
# Chat system prompts are canonical English; the reply language comes from {language_section}.
LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT = """You are a trend-trading focused {market_role} Agent with data tools and trading skills. Your job is to answer the user's stock investment questions.

{market_guidelines}

## Analysis workflow (follow the stages strictly; never skip or merge stages)

When the user asks about a stock, call the tools in the four stages below, in order. Wait for all tool results of a stage before moving to the next one:

**Stage 1 · Quote and candlesticks** (always first)
- Call `get_realtime_quote` for the live quote and current price
- Call `get_daily_history` for recent daily candlestick (K-line) history
- Call `get_stock_money_flow` for foreign and proprietary net flows (today / 1 week / 1 month / YTD, with dates)

**Stage 2 · Technicals** (after Stage 1 results return)
- Call `analyze_trend` for MA/MACD/RSI and other technical indicators

**Stage 3 · News and intelligence** (after Stages 1-2 complete)
- Call `search_stock_news` for the latest news, announcements, insider/major-holder selling, earnings warnings and other risk signals

**Stage 4 · Synthesis** (answer once all tool data is ready)
- Combine the real data above with the active skills and give an investment view

> ⚠️ Do not merge tools from different stages into one call (e.g. never request the quote, technical indicators and news in the same first call).
{default_skill_policy_section}

## Rules

1. **Always call tools for real data** — never invent numbers; every figure must come from tool results.
2. **Apply the trading skills** — evaluate each active skill's conditions and reflect the skill verdicts in the answer.
3. **Free-form conversation** — answer the user's question in natural prose; no JSON output is needed.
4. **Risk first** — always check for risks (major-holder selling, earnings warnings, regulatory issues).
5. **Tool failures** — note the failure reason, continue with the data you have, and do not retry a failed tool.
6. **No disclaimer line** — do not write your own "not investment advice" disclaimer; the system appends one automatically.
7. **Stay consistent with the saved analysis** — when a previous analysis summary is provided, it is the system's calibrated view: keep your action consistent with it, and deviate only when current data clearly contradicts it, stating the reference time, the reference action, your action and the evidence.

{skills_section}
{language_section}
"""

CHAT_SYSTEM_PROMPT = """You are a {market_role} Agent with data tools and switchable trading skills. Your job is to answer the user's stock investment questions.

{market_guidelines}

## Analysis workflow (follow the stages strictly; never skip or merge stages)

When the user asks about a stock, call the tools in the four stages below, in order. Wait for all tool results of a stage before moving to the next one:

**Stage 1 · Quote and candlesticks** (always first)
- Call `get_realtime_quote` for the live quote and current price
- Call `get_daily_history` for recent daily candlestick (K-line) history
- Call `get_stock_money_flow` for foreign and proprietary net flows (today / 1 week / 1 month / YTD, with dates)

**Stage 2 · Technicals** (after Stage 1 results return)
- Call `analyze_trend` for MA/MACD/RSI and other technical indicators

**Stage 3 · News and intelligence** (after Stages 1-2 complete)
- Call `search_stock_news` for the latest news, announcements, insider/major-holder selling, earnings warnings and other risk signals

**Stage 4 · Synthesis** (answer once all tool data is ready)
- Combine the real data above with the active skills and give an investment view

> ⚠️ Do not merge tools from different stages into one call (e.g. never request the quote, technical indicators and news in the same first call).
{default_skill_policy_section}

## Rules

1. **Always call tools for real data** — never invent numbers; every figure must come from tool results.
2. **Apply the trading skills** — evaluate each active skill's conditions and reflect the skill verdicts in the answer.
3. **Free-form conversation** — answer the user's question in natural prose; no JSON output is needed.
4. **Risk first** — always check for risks (major-holder selling, earnings warnings, regulatory issues).
5. **Tool failures** — note the failure reason, continue with the data you have, and do not retry a failed tool.
6. **No disclaimer line** — do not write your own "not investment advice" disclaimer; the system appends one automatically.
7. **Stay consistent with the saved analysis** — when a previous analysis summary is provided, it is the system's calibrated view: keep your action consistent with it, and deviate only when current data clearly contradicts it, stating the reference time, the reference action, your action and the evidence.

{skills_section}
{language_section}
"""

CODEX_CHAT_SYSTEM_PROMPT = """You are a {market_role} Agent that answers the user's stock investment questions using data already saved in DSA.

## Available data

- `get_analysis_context`: reads the most recent saved analysis context for a given stock.
- `get_skill_backtest_summary`: reads the saved backtest summary for a given trading skill.
- `get_strategy_backtest_summary`: reads the saved backtest summary for the overall trading strategy.

## How to work

1. For a specific stock, call `get_analysis_context` first, then answer from the saved data it returns.
2. When the user asks how a trading skill or strategy performed, call the matching backtest summary tool.
3. State clearly that conclusions are based on saved data; if the data carries an analysis time, mention its time range in the answer.
4. If the tools do not return the information needed, say that the saved data is insufficient; never fill in or guess data.
5. Answer the user in natural prose; no JSON output is needed.
6. Do not write your own "not investment advice" disclaimer; the system appends one automatically.

{language_section}
"""


def _build_language_section(report_language: str, *, chat_mode: bool = False) -> str:
    """Build output-language guidance for the agent prompt."""
    normalized = normalize_report_language(report_language)
    if chat_mode:
        if normalized == "vi":
            return """
## Output Language

- Reply in Vietnamese (Tiếng Việt), with correct diacritics.
- This applies even when the instructions, skill descriptions, tool results, or earlier messages are in Chinese or English: translate the content, do not copy Chinese text into the answer. Keep stock codes, tickers, and numbers unchanged.
- Never write any Chinese (Han) characters, not even in parentheses, quotes, or as the original term next to a translation (write only the Vietnamese term, never the Vietnamese term followed by the Chinese original in parentheses).
- If you output JSON, keep the keys unchanged and write every human-readable value in Vietnamese.
"""
        return """
## Output Language

- Reply in English.
- If you output JSON, keep the keys unchanged and write every human-readable value in English.
"""

    if normalized == "vi":
        return """

## Output Language (highest priority)

- Keep all JSON keys unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in Vietnamese (Tiếng Việt), with correct diacritics.
- Use standard Vietnamese securities terminology (mua / nắm giữ / bán, hỗ trợ / kháng cự, cắt lỗ, khối lượng).
- Use the company's common Vietnamese name when you are confident; otherwise keep the listed name instead of inventing one.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, nested dashboard text, checklist items, and all narrative summaries.
"""

    return """
## Output Language

- Keep every JSON key unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in English.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, all dashboard text, checklist items, and summaries.
"""


# ============================================================
# Shared chat request preparation
# ============================================================


@dataclass(frozen=True)
class PreparedAgentChat:
    """System prompt, visible history, and scope shared by Agent backends."""

    system_prompt: str
    history_messages: List[Dict[str, Any]]
    stock_scope: Optional[StockScope]
    report_language: str = "vi"


# Ghi chú lưu vào lịch sử khi lượt chat không thành công, theo ngôn ngữ báo cáo.
# Failure/stop/timeout notes persisted as the assistant message, per report language.
_CHAT_FAILURE_NOTES: Dict[str, Dict[str, str]] = {
    "vi": {
        "failed": "[Phân tích thất bại] {error}",
        "cancelled": "[Đã dừng] Lượt phân tích này đã được người dùng dừng.",
        "timeout": "[Quá thời gian] Lượt phân tích này đã kết thúc do vượt giới hạn thời gian.",
        "unknown_error": "Lỗi không xác định",
    },
    "en": {
        "failed": "[Analysis failed] {error}",
        "cancelled": "[Stopped] This analysis was stopped by the user.",
        "timeout": "[Timed out] This analysis ended at the time limit.",
        "unknown_error": "Unknown error",
    },
}


def build_chat_market_scope_section() -> str:
    """Tell the chat agent which markets this deployment supports (from SUPPORTED_MARKETS).

    Hỏi đáp chỉ hỗ trợ các thị trường đang bật (mặc định chỉ Việt Nam). Câu hỏi về mã ngoài
    phạm vi phải được từ chối lịch sự thay vì gọi công cụ (nguồn dữ liệu đã tắt).
    Repo chỉ còn thị trường Việt Nam nên registry luôn có đúng một thị trường; mục này
    luôn được sinh ra (trước đây trả "" khi bật "mọi" thị trường, nay điều kiện đó luôn
    đúng và làm mất hẳn mục phạm vi). / The registry is Vietnam-only, so the section is
    always emitted; returns "" only if no market is enabled.
    """
    from src.core.markets import enabled_markets, market_label

    markets = enabled_markets()
    if not markets:
        return ""
    labels = ", ".join(market_label(code, "en") for code in markets)
    lines = [
        "",
        "## Market scope",
        "",
        f"- This assistant only supports these markets: **{labels}**.",
        "- If the user asks about a stock or index outside these markets (for example a US, China A-share or "
        "Hong Kong ticker), do not call any tools for it; politely explain that only the supported markets are "
        "covered and suggest asking about a supported stock instead.",
    ]
    if "vn" in markets:
        lines.append(
            "- Vietnamese stocks trade on HOSE / HNX / UPCoM and are priced in VND; bare 3-letter tickers such as "
            "HPG, FPT or VNM are Vietnamese stocks."
        )
    return "\n".join(lines) + "\n"


def append_chat_disclaimer(content: Optional[str], report_language: Optional[str]) -> Optional[str]:
    """Append the localized "AI-generated, not investment advice" line to a chat answer.

    Gắn câu miễn trừ trách nhiệm (dùng chung nhãn ``not_investment_advice`` với báo cáo)
    vào cuối câu trả lời Hỏi đáp; không phụ thuộc việc model có tự viết hay không.
    Idempotent: answers that already end with the line are returned unchanged.
    """
    if not content or not content.strip():
        return content
    from src.report_language import get_report_labels

    disclaimer = get_report_labels(normalize_report_language(report_language or "vi"))["not_investment_advice"]
    if content.rstrip().endswith(f"*{disclaimer}*") or content.rstrip().endswith(disclaimer):
        return content
    return f"{content.rstrip()}\n\n---\n*{disclaimer}*"


def finalize_chat_answer(
    content: Optional[str],
    report_language: Optional[str],
    llm_adapter: Any = None,
) -> Optional[str]:
    """Apply the output-language guard, then append the investment disclaimer."""
    return append_chat_disclaimer(
        enforce_vietnamese_output(content, report_language, llm_adapter),
        report_language,
    )


def build_chat_failure_note(kind: str, error: Optional[str], report_language: Optional[str]) -> str:
    """Build the persisted assistant note for a failed/cancelled/timed-out chat turn."""
    normalized = normalize_report_language(report_language or "vi")
    notes = _CHAT_FAILURE_NOTES.get(normalized, _CHAT_FAILURE_NOTES["vi"])
    template = notes.get(kind, notes["failed"])
    return template.format(error=error or notes["unknown_error"])


def prepare_agent_chat(
    *,
    message: str,
    session_id: str,
    context: Optional[Dict[str, Any]],
    config: Any,
    context_llm_adapter: Any,
    skill_instructions: str,
    default_skill_policy: str,
    use_legacy_default_prompt: bool,
    use_codex_prompt: bool,
    include_provider_trace: bool,
    strict_initial_stock_scope: bool = False,
) -> PreparedAgentChat:
    """Build the existing Chat prompt order without choosing an Agent backend."""
    scope_resolution = resolve_stock_scope(
        message,
        context,
        strict_initial_scope=strict_initial_stock_scope,
    )
    # Báo cáo đã lưu ≤24h của mã làm tham chiếu khi Web chưa gửi ngữ cảnh báo cáo.
    # Latest saved analysis (≤24h) as reference when the Web sent no report context.
    effective_context = attach_latest_analysis_reference(
        scope_resolution.effective_context,
        message,
    )

    skills_section = ""
    if skill_instructions:
        skills_section = f"## Active trading skills\n\n{skill_instructions}"
    default_skill_policy_section = ""
    if default_skill_policy:
        default_skill_policy_section = f"\n{default_skill_policy}\n"
    report_language = normalize_report_language((effective_context or {}).get("report_language", "vi"))
    stock_code = (effective_context or {}).get("stock_code", "")
    if use_codex_prompt:
        prompt_template = CODEX_CHAT_SYSTEM_PROMPT
    elif use_legacy_default_prompt:
        prompt_template = LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT
    else:
        prompt_template = CHAT_SYSTEM_PROMPT
    system_prompt = prompt_template.format(
        market_role=get_market_role(stock_code, report_language),
        market_guidelines=get_market_guidelines(stock_code, report_language),
        default_skill_policy_section=default_skill_policy_section,
        skills_section=skills_section,
        # Phạm vi thị trường (SUPPORTED_MARKETS) đặt trước mục ngôn ngữ; cả 3 prompt chat đều có slot này.
        # Market scope goes before the language section, which every chat template has.
        language_section=build_chat_market_scope_section()
        + _build_language_section(report_language, chat_mode=True),
    )

    if include_provider_trace:
        history_messages = list(
            build_agent_chat_context_bundle(session_id, context_llm_adapter, config).context_messages
        )
    else:
        history_messages = list(
            build_visible_chat_history(
                session_id,
                context_llm_adapter,
                config,
                allow_llm_compression=False,
            )
        )

    if effective_context:
        context_parts = []
        if effective_context.get("stock_code"):
            context_parts.append(f"Stock code: {effective_context['stock_code']}")
        if effective_context.get("stock_name"):
            context_parts.append(f"Stock name: {effective_context['stock_name']}")
        # Giá của báo cáo trước, không phải giá hiện tại: giá hiện tại lấy qua get_realtime_quote.
        # Saved-report price, not the current one; the current price comes from get_realtime_quote.
        if effective_context.get("previous_price"):
            context_parts.append(
                f"Previous analysis price: {effective_context['previous_price']} "
                "(historical, NOT the current price)"
            )
        if effective_context.get("previous_change_pct"):
            context_parts.append(
                f"Previous change: {effective_context['previous_change_pct']}% (historical)"
            )
        if effective_context.get("previous_analysis_summary"):
            summary = effective_context["previous_analysis_summary"]
            summary_text = json.dumps(summary, ensure_ascii=False) if isinstance(summary, dict) else str(summary)
            context_parts.append(f"Previous analysis summary:\n{summary_text}")
        if effective_context.get("previous_strategy"):
            strategy = effective_context["previous_strategy"]
            strategy_text = json.dumps(strategy, ensure_ascii=False) if isinstance(strategy, dict) else str(strategy)
            context_parts.append(f"Previous strategy analysis:\n{strategy_text}")
        daily_market_context_section = format_daily_market_context_prompt_section(
            effective_context.get("daily_market_context"),
            report_language=report_language,
        )
        if daily_market_context_section:
            context_parts.append(daily_market_context_section.strip())
        if context_parts:
            history_messages.extend(
                [
                    {
                        "role": "user",
                        "content": "[System-provided historical analysis context for reference and comparison]\n" + "\n".join(context_parts),
                    },
                    {
                        "role": "assistant",
                        "content": "Understood. I have the historical analysis data for this stock. What would you like to know?",
                    },
                ]
            )

    return PreparedAgentChat(
        system_prompt=system_prompt,
        history_messages=history_messages,
        stock_scope=scope_resolution.stock_scope,
        report_language=report_language,
    )


# ============================================================
# Agent Executor
# ============================================================

class AgentExecutor:
    """ReAct agent loop with tool calling.

    Usage::

        executor = AgentExecutor(tool_registry, llm_adapter)
        result = executor.run("Analyze stock HPG")
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        default_skill_policy: str = "",
        use_legacy_default_prompt: bool = False,
        max_steps: int = 10,
        timeout_seconds: Optional[float] = None,
    ):
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.default_skill_policy = default_skill_policy
        self.use_legacy_default_prompt = use_legacy_default_prompt
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Execute the agent loop for a given task.

        Args:
            task: The user task / analysis request.
            context: Optional context dict (e.g., {"stock_code": "HPG"}).

        Returns:
            AgentResult with parsed dashboard or error.
        """
        # Build system prompt with skills
        skills_section = ""
        if self.skill_instructions:
            skills_section = f"## Activated Trading Skills\n\n{self.skill_instructions}"
        default_skill_policy_section = ""
        if self.default_skill_policy:
            default_skill_policy_section = f"\n{self.default_skill_policy}\n"
        report_language = normalize_report_language((context or {}).get("report_language", "vi"))
        stock_code = (context or {}).get("stock_code", "")
        market_role = get_market_role(stock_code, report_language)
        market_guidelines = get_market_guidelines(stock_code, report_language)
        prompt_template = (
            LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT
            if self.use_legacy_default_prompt
            else AGENT_SYSTEM_PROMPT
        )
        system_prompt = prompt_template.format(
            market_role=market_role,
            market_guidelines=market_guidelines,
            default_skill_policy_section=default_skill_policy_section,
            skills_section=skills_section,
            language_section=_build_language_section(report_language),
        )

        # Build tool declarations in OpenAI format (litellm handles all providers)
        tool_decls = self.tool_registry.to_openai_tools()

        # Initialize conversation
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self._build_user_message(task, context)},
        ]

        return self._run_loop(messages, tool_decls, parse_dashboard=True)

    def chat(self, message: str, session_id: str, progress_callback: Optional[Callable] = None, context: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Execute the agent loop for a free-form chat message.

        Args:
            message: The user's chat message.
            session_id: The conversation session ID.
            progress_callback: Optional callback for streaming progress events.
            context: Optional context dict from previous analysis for data reuse.

        Returns:
            AgentResult with the text response.
        """
        from src.agent.conversation import conversation_manager

        conversation_manager.get_or_create(session_id)
        config = getattr(self.llm_adapter, "_config", None) or get_config()
        prepared = prepare_agent_chat(
            message=message,
            session_id=session_id,
            context=context,
            config=config,
            context_llm_adapter=self.llm_adapter,
            skill_instructions=self.skill_instructions,
            default_skill_policy=self.default_skill_policy,
            use_legacy_default_prompt=self.use_legacy_default_prompt,
            use_codex_prompt=False,
            include_provider_trace=True,
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": prepared.system_prompt},
            *prepared.history_messages,
        ]
        messages.append({"role": "user", "content": message})
        baseline_len = len(messages)
        run_id = str(uuid.uuid4())

        # Persist the user turn immediately so the session appears in history during processing
        user_message_id = conversation_manager.add_message(session_id, "user", message)

        tool_decls = self.tool_registry.to_openai_tools()
        result = self._run_loop(
            messages,
            tool_decls,
            parse_dashboard=False,
            progress_callback=progress_callback,
            stock_scope=prepared.stock_scope,
        )

        # Persist assistant reply (or error note) for context continuity
        if result.success:
            result.content = finalize_chat_answer(
                result.content, prepared.report_language, self.llm_adapter
            )
            assistant_message_id = conversation_manager.add_message(session_id, "assistant", result.content)
            self._persist_provider_trace(
                session_id=session_id,
                run_id=run_id,
                messages=result.messages,
                baseline_len=baseline_len,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
        else:
            error_note = build_chat_failure_note(
                "failed", result.error, (context or {}).get("report_language")
            )
            conversation_manager.add_message(session_id, "assistant", error_note)

        return result

    def _persist_provider_trace(
        self,
        *,
        session_id: str,
        run_id: str,
        messages: List[Dict[str, Any]],
        baseline_len: int,
        user_message_id: int,
        assistant_message_id: int,
    ) -> None:
        persist_provider_trace_turns(
            session_id=session_id,
            run_id=run_id,
            messages=messages,
            baseline_len=baseline_len,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            db_factory=get_db,
            log=logger,
        )

    def _run_loop(
        self,
        messages: List[Dict[str, Any]],
        tool_decls: List[Dict[str, Any]],
        parse_dashboard: bool,
        progress_callback: Optional[Callable] = None,
        stock_scope: Optional[StockScope] = None,
    ) -> AgentResult:
        """Delegate to the shared runner and adapt the result.

        Dashboard mode exposes only the parsed canonical payload through both
        ``dashboard`` and ``content``; free-form mode preserves the raw text.
        """
        loop_result = run_agent_loop(
            messages=messages,
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            max_steps=self.max_steps,
            progress_callback=progress_callback,
            max_wall_clock_seconds=self.timeout_seconds,
            stock_scope=stock_scope,
        )

        model_str = loop_result.model

        if parse_dashboard and loop_result.success:
            dashboard = parse_dashboard_json(loop_result.content)
            return AgentResult(
                success=dashboard is not None,
                content=(
                    json.dumps(dashboard, ensure_ascii=False, indent=2)
                    if dashboard is not None
                    else loop_result.content
                ),
                dashboard=dashboard,
                tool_calls_log=loop_result.tool_calls_log,
                total_steps=loop_result.total_steps,
                total_tokens=loop_result.total_tokens,
                provider=loop_result.provider,
                model=model_str,
                error=None if dashboard else "Failed to parse dashboard JSON from agent response",
                messages=loop_result.messages,
            )

        return AgentResult(
            success=loop_result.success,
            content=loop_result.content,
            dashboard=None,
            tool_calls_log=loop_result.tool_calls_log,
            total_steps=loop_result.total_steps,
            total_tokens=loop_result.total_tokens,
            provider=loop_result.provider,
            model=model_str,
            error=loop_result.error,
            messages=loop_result.messages,
        )

    def _build_user_message(self, task: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Build the initial user message."""
        parts = [task]
        if context:
            report_language = normalize_report_language(context.get("report_language", "vi"))
            if context.get("stock_code"):
                parts.append(f"\nStock code: {context['stock_code']}")
            if context.get("report_type"):
                parts.append(f"Report type: {context['report_type']}")
            if report_language == "vi":
                parts.append(
                    "Ngôn ngữ đầu ra: Tiếng Việt (giữ nguyên toàn bộ khoá JSON, "
                    "mọi giá trị hiển thị cho người dùng viết bằng tiếng Việt có dấu)"
                )
            else:
                parts.append(
                    "Output language: English (keep every JSON key unchanged; "
                    "write every user-facing text value in English)"
                )

            market_phase_section = format_market_phase_prompt_section(
                context.get("market_phase_context"),
                report_language=report_language,
            )
            if market_phase_section:
                parts.append(market_phase_section)

            daily_market_context_section = format_daily_market_context_prompt_section(
                context.get("daily_market_context"),
                report_language=report_language,
            )
            if daily_market_context_section:
                parts.append(daily_market_context_section)

            analysis_context_pack_summary = context.get("analysis_context_pack_summary")
            if isinstance(analysis_context_pack_summary, str) and analysis_context_pack_summary:
                parts.append(analysis_context_pack_summary)

            # Inject pre-fetched context data to avoid redundant fetches
            if context.get("realtime_quote"):
                parts.append(f"\n[Real-time quote already fetched by the system]\n{json.dumps(context['realtime_quote'], ensure_ascii=False)}")
            if context.get("news_context"):
                parts.append(f"\n[News and sentiment intelligence already fetched by the system]\n{context['news_context']}")

        parts.append("\nUse the available tools to fetch any missing data (such as daily candlestick history or news), "
            "then output the analysis result as Decision Dashboard JSON.")
        return "\n".join(parts)
