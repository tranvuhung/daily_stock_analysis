# -*- coding: utf-8 -*-
"""
PortfolioAgent — đánh giá một *nhóm* cổ phiếu như một danh mục thống nhất (thị trường Việt Nam).
Analyses a set of Vietnamese stocks as one portfolio rather than one by one.

Trách nhiệm / Responsibilities:
- So sánh tỷ trọng hiện tại với tỷ trọng đề xuất, theo lô 100 cp và chu kỳ T+2
- Cảnh báo tập trung theo mã / ngành (ICB) và cụm tương quan điển hình của VN
- Tỷ lệ tiền mặt, mức gần ngưỡng cắt lỗ, điểm rủi ro danh mục 1-10
- Gợi ý tái cân bằng

Hai nguồn đầu vào / Two input modes:
1. Đánh giá danh mục thật (Web ``/portfolio``): ``ctx.data`` có ``portfolio_summary``,
   ``portfolio_holdings``, ``portfolio_risk`` do server tính sẵn (xem
   ``src/services/portfolio_review_service.py``).
2. Overlay nhiều mã của bot ``/ask``: chỉ có ``stock_opinions`` / ``stock_list``.

Typical usage::

    from src.agent.agents.portfolio_agent import PortfolioAgent
    agent = PortfolioAgent(tool_registry=registry, llm_adapter=adapter)
    result = agent.run(ctx, timeout_seconds=120)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json

logger = logging.getLogger(__name__)

# Khoá ctx.data được nạp dạng JSON (nhỏ, có cấu trúc). Danh mục nắm giữ và ý kiến từng mã
# được hiển thị gọn trong user message để không bị cắt ở 8000 ký tự.
# ctx.data keys injected as JSON; holdings/opinions are rendered compactly in the user message.
_INJECTED_DATA_KEYS = ("portfolio_summary", "portfolio_risk")
_MAX_RENDERED_HOLDINGS = 40
_MAX_RENDERED_RISK_FLAGS = 10


def _format_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _format_risk_flag(flag: Any) -> str:
    """Hiển thị risk flag dạng ``[severity] category: description`` thay vì dict thô."""
    if isinstance(flag, dict):
        severity = flag.get("severity") or "medium"
        category = flag.get("category") or ""
        description = flag.get("description") or ""
        label = f"{category}: {description}" if category else str(description)
        return f"- [{severity}] {label}".rstrip()
    return f"- {flag}"


class PortfolioAgent(BaseAgent):
    """Portfolio-level analysis agent.

    Đọc dữ liệu danh mục / ý kiến từng mã từ ``ctx.data`` và trả JSON đánh giá danh mục,
    lưu vào ``ctx.data["portfolio_assessment"]``.
    """

    agent_name = "portfolio"
    description = "Portfolio-level risk and allocation analysis (Vietnam market)"

    # Chỉ dùng để lấp chỗ trống (giá cũ, ngành chưa rõ); dữ liệu chính đã được nạp sẵn.
    # Tools only fill gaps (stale price, unknown sector); core data is pre-injected.
    tool_names = [
        "get_realtime_quote",
        "get_stock_info",
    ]
    max_steps = 4

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            "You are a professional **portfolio analyst for the Vietnamese stock market** "
            "(HOSE, HNX, UPCoM; prices and values in VND).\n\n"
            "## Market rules to respect\n"
            "- Settlement is T+2: shares bought today cannot be sold before T+2, so do not "
            "suggest selling a position opened in the last two sessions.\n"
            "- Board lot is 100 shares on HOSE/HNX; express share-count suggestions in multiples of 100.\n"
            "- Daily price bands: ±7% HOSE, ±10% HNX, ±15% UPCoM.\n"
            "- Sector data may be `UNCLASSIFIED`. You may estimate ICB sectors (banks, securities, "
            "real estate, steel, oil & gas, retail, ...) from your knowledge, but label them as estimates.\n"
            "- Typical correlated clusters in Vietnam: banks / securities / real estate; "
            "steel / construction; oil & gas.\n\n"
            "## Inputs\n"
            "- `portfolio_summary` and `portfolio_holdings` (if present) are computed by the server: "
            "`current_weight` and `cash_ratio` are the source of truth, do NOT recompute them.\n"
            "- `portfolio_risk.thresholds` gives the configured alert thresholds; use "
            "`concentration_alert_pct` as the single-stock concentration limit instead of a fixed number.\n"
            "- Per-stock AI signals can be missing or stale. When a holding has no signal, "
            "do not invent conviction; say the data is missing and list it in `data_gaps`.\n\n"
            "## Your task\n"
            "1. **Position sizing** — compare `current_weight` with a `suggested_weight` per holding.\n"
            "2. **Concentration** — by stock and by (estimated) sector.\n"
            "3. **Correlation risk** — flag clusters that move together.\n"
            "4. **Cash ratio** — suggest a target cash ratio for the current market phase.\n"
            "5. **Stop-loss proximity** — holdings near or past the stop-loss threshold.\n"
            "6. **Portfolio risk score** — 1 (low) to 10 (high).\n"
            "7. **Rebalance suggestions** — concrete trim/add actions respecting T+2 and 100-share lots.\n\n"
            "## Output format\n"
            "Return a single JSON object:\n"
            "```json\n"
            "{\n"
            '  "portfolio_risk_score": 6,\n'
            '  "total_stocks": 5,\n'
            '  "positions": [\n'
            '    {"code": "HPG", "current_weight": 0.32, "suggested_weight": 0.20, '
            '"signal": "reduce", "priority": "high", "note": "..."},\n'
            "    ...\n"
            "  ],\n"
            '  "cash_ratio": 0.12,\n'
            '  "suggested_cash_ratio": 0.25,\n'
            '  "sector_warnings": ["..."],\n'
            '  "correlation_warnings": ["..."],\n'
            '  "cross_market_notes": ["VN-Index / macro notes"],\n'
            '  "rebalance_suggestions": ["..."],\n'
            '  "data_gaps": ["..."],\n'
            '  "summary": "..."\n'
            "}\n"
            "```\n"
            "- `signal` is one of: buy, add, hold, reduce, sell, watch.\n"
            "- `priority` is one of: high, medium, low.\n"
            "- Weights and ratios are fractions between 0 and 1.\n"
        )

    def _inject_cached_data(self, ctx: AgentContext) -> str:
        """Chỉ nạp tóm tắt + rủi ro dạng JSON; tránh nạp trùng ý kiến từng mã.

        Only inject summary and risk JSON; holdings and opinions are rendered in the
        user message so they are not duplicated nor truncated.
        """
        parts: List[str] = []
        for key in _INJECTED_DATA_KEYS:
            value = ctx.data.get(key)
            if value is None:
                continue
            try:
                serialised = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                serialised = str(value)
            parts.append(f"[Pre-fetched: {key}]\n{serialised}")
        memory_context = self._build_memory_context(ctx)
        if memory_context:
            parts.append(memory_context)
        return "\n\n".join(parts)

    def build_user_message(self, ctx: AgentContext) -> str:
        holdings = ctx.data.get("portfolio_holdings") or []
        stock_opinions: Dict[str, Any] = ctx.data.get("stock_opinions", {}) or {}
        stock_list = ctx.data.get("stock_list", []) or []

        parts: List[str] = []
        if holdings:
            parts.append(f"Assess this portfolio of {len(holdings)} holdings (sorted by weight):\n")
            rendered = sorted(holdings, key=lambda item: float(item.get("current_weight") or 0.0), reverse=True)
            for item in rendered[:_MAX_RENDERED_HOLDINGS]:
                code = item.get("code")
                line = (
                    f"- **{code}**: qty={item.get('quantity')}, avg_cost={item.get('avg_cost')}, "
                    f"last_price={item.get('last_price')}, current_weight={_format_pct(item.get('current_weight'))}, "
                    f"pnl_pct={item.get('unrealized_pnl_pct')}"
                )
                if item.get("price_stale"):
                    line += ", price_stale=true"
                if item.get("price_available") is False:
                    line += ", price_missing=true"
                opinion = stock_opinions.get(code)
                line += f"; signal: {self._format_opinion(opinion) if opinion else 'none'}"
                parts.append(line)
            if len(rendered) > _MAX_RENDERED_HOLDINGS:
                parts.append(f"- ... {len(rendered) - _MAX_RENDERED_HOLDINGS} smaller holdings omitted")
        else:
            parts.append(f"Analyze the following portfolio of {len(stock_list) or len(stock_opinions)} stocks:\n")
            if stock_opinions:
                for code, opinion in stock_opinions.items():
                    parts.append(f"- **{code}**: {self._format_opinion(opinion)}")
            elif stock_list:
                for code in stock_list:
                    parts.append(f"- {code}")

        if ctx.risk_flags:
            parts.append("\n### Risk flags:")
            for flag in ctx.risk_flags[:_MAX_RENDERED_RISK_FLAGS]:
                parts.append(_format_risk_flag(flag))

        if ctx.query:
            parts.append(f"\nUser request: {ctx.query}")

        return "\n".join(parts)

    @staticmethod
    def _format_opinion(opinion: Any) -> str:
        if isinstance(opinion, AgentOpinion):
            return (
                f"signal={opinion.signal}, confidence={opinion.confidence:.0%}, "
                f"summary={opinion.reasoning[:200]}"
            )
        if isinstance(opinion, dict):
            text = f"signal={opinion.get('signal', 'unknown')}, confidence={opinion.get('confidence', 'N/A')}"
            for key in ("horizon", "stop_loss", "target_price", "created_at"):
                if opinion.get(key) not in (None, ""):
                    text += f", {key}={opinion.get(key)}"
            return text + f", summary={str(opinion.get('summary', ''))[:200]}"
        return str(opinion)

    def post_process(self, ctx: AgentContext, raw_response: str) -> Optional[AgentOpinion]:
        """Extract portfolio assessment and store in context."""
        data = try_parse_json(raw_response)
        if data is None:
            logger.debug("[PortfolioAgent] post_process: failed to parse JSON")
            return AgentOpinion(
                agent_name="portfolio",
                signal="hold",
                confidence=0.3,
                reasoning=raw_response[:500],
                raw_data={"raw": raw_response[:1000]},
            )

        # Store portfolio assessment in context
        ctx.data["portfolio_assessment"] = data

        risk_score = data.get("portfolio_risk_score", 5)
        signal = "hold"
        if risk_score <= 3:
            signal = "buy"
        elif risk_score >= 7:
            signal = "sell"

        return AgentOpinion(
            agent_name="portfolio",
            signal=signal,
            confidence=0.6,
            reasoning=data.get("summary", raw_response[:300]),
            raw_data=data,
        )
