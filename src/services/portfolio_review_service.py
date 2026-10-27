# -*- coding: utf-8 -*-
"""Đánh giá danh mục bằng AI (PortfolioAgent) trên vị thế thật.

Luồng / Flow:
1. Snapshot danh mục (``PortfolioService``) + báo cáo rủi ro (``PortfolioRiskService``, lỗi
   thì giảm cấp) + tín hiệu AI active mới nhất của từng mã (``DecisionSignalService``).
2. Server tự tính tỷ trọng hiện tại và tỷ lệ tiền mặt, nạp vào ``AgentContext``.
3. Chạy ``PortfolioAgent`` với hard timeout; chuẩn hoá kết quả (ghi đè số liệu server tính).

Không chạy lại phân tích từng mã và không lưu kết quả vào DB.
AI portfolio review on real holdings; no per-stock re-analysis and no persistence.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.agent.protocols import AgentContext, StageFailureReason
from src.config import Config, get_config
from src.services.decision_signal_service import DecisionSignalService
from src.services.decision_signal_summary import summarize_decision_signal
from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

# Giới hạn mặc định cho Web khi không cấu hình AGENT_PORTFOLIO_AGENT_TIMEOUT_S
# (AGENT_ORCHESTRATOR_TIMEOUT_S mặc định 600s, quá dài cho một request HTTP đồng bộ).
# Default web cap when AGENT_PORTFOLIO_AGENT_TIMEOUT_S is unset.
DEFAULT_REVIEW_TIMEOUT_S = 240.0
# Thời gian chờ thêm cho vòng agent tự dừng trước khi hard timeout cắt ngang.
_HARD_TIMEOUT_GRACE_S = 5.0
_SIGNAL_TEXT_LIMIT = 200
_VALID_SIGNALS = {"buy", "add", "hold", "reduce", "sell", "watch"}
_VALID_PRIORITIES = {"high", "medium", "low"}


@dataclass
class PortfolioReviewResult:
    success: bool
    assessment: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timed_out: bool = False
    duration_s: float = 0.0
    tokens_used: int = 0
    context_meta: Dict[str, Any] = field(default_factory=dict)


def resolve_portfolio_agent_timeout(config: Config, budget_s: Optional[float] = None) -> float:
    """Thời gian tối đa cho một lượt PortfolioAgent.

    ``AGENT_PORTFOLIO_AGENT_TIMEOUT_S`` > 0 được dùng nguyên; nếu không, lấy
    ``min(AGENT_ORCHESTRATOR_TIMEOUT_S, 240s)``. ``budget_s`` (thời gian còn lại của nơi gọi)
    luôn là trần.
    """
    explicit = float(getattr(config, "agent_portfolio_agent_timeout_s", 0) or 0)
    if explicit > 0:
        timeout = explicit
    else:
        orchestrator = float(getattr(config, "agent_orchestrator_timeout_s", 0) or 0)
        timeout = min(orchestrator, DEFAULT_REVIEW_TIMEOUT_S) if orchestrator > 0 else DEFAULT_REVIEW_TIMEOUT_S
    if budget_s is not None:
        timeout = min(timeout, max(0.0, float(budget_s)))
    return timeout


def run_portfolio_agent(config: Config, ctx: AgentContext, *, timeout_s: float) -> Tuple[Any, bool]:
    """Chạy PortfolioAgent với hard timeout. Trả ``(StageResult | None, timed_out)``.

    Vòng agent chỉ kiểm tra thời gian giữa các bước, nên cần thêm hard timeout bằng thread.
    The agent loop only checks its budget between steps, so a thread wait enforces it.
    """
    from src.agent.agents.portfolio_agent import PortfolioAgent
    from src.agent.factory import get_tool_registry
    from src.agent.llm_adapter import LLMToolAdapter

    agent = PortfolioAgent(tool_registry=get_tool_registry(), llm_adapter=LLMToolAdapter(config))
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(agent.run, ctx, timeout_seconds=timeout_s)
    try:
        return future.result(timeout=timeout_s + _HARD_TIMEOUT_GRACE_S), False
    except FutureTimeoutError:
        logger.warning("[PortfolioReview] PortfolioAgent timed out after %.1fs", timeout_s)
        return None, True
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trim(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _SIGNAL_TEXT_LIMIT:
        return value[:_SIGNAL_TEXT_LIMIT] + "…"
    return value


class PortfolioReviewService:
    """Dựng ngữ cảnh danh mục thật và chạy PortfolioAgent."""

    def __init__(
        self,
        *,
        portfolio_service: Optional[PortfolioService] = None,
        risk_service: Optional[PortfolioRiskService] = None,
        decision_signal_service: Optional[DecisionSignalService] = None,
        config: Optional[Config] = None,
    ):
        self.config = config or get_config()
        self.portfolio_service = portfolio_service or PortfolioService()
        self.risk_service = risk_service or PortfolioRiskService(portfolio_service=self.portfolio_service)
        self.decision_signal_service = decision_signal_service or DecisionSignalService()

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    def build_context(
        self,
        *,
        account_id: Optional[int] = None,
        cost_method: str = "fifo",
        query: Optional[str] = None,
    ) -> Tuple[AgentContext, Dict[str, Any]]:
        snapshot = self.portfolio_service.get_portfolio_snapshot(
            account_id=account_id,
            cost_method=cost_method,
            include_realtime=True,
        )

        risk: Optional[Dict[str, Any]] = None
        try:
            risk = self.risk_service.get_risk_report(account_id=account_id, cost_method=cost_method)
        except Exception:
            # Rủi ro chỉ bổ trợ; lỗi thì vẫn đánh giá trên snapshot.
            logger.exception("[PortfolioReview] risk report unavailable, continuing without it")

        holdings = self._merge_holdings(snapshot)
        total_equity = float(snapshot.get("total_equity") or 0.0)
        total_cash = float(snapshot.get("total_cash") or 0.0)
        for item in holdings:
            item["current_weight"] = (
                round(item["market_value"] / total_equity, 6) if total_equity > 0 else None
            )
        cash_ratio = round(total_cash / total_equity, 6) if total_equity > 0 else None

        opinions = self._latest_signals(snapshot)

        ctx = AgentContext(query=(query or "").strip() or "Đánh giá danh mục hiện tại")
        ctx.meta["report_language"] = getattr(self.config, "report_language", "vi")
        ctx.data["portfolio_summary"] = {
            "as_of": snapshot.get("as_of"),
            "currency": snapshot.get("currency"),
            "cost_method": snapshot.get("cost_method"),
            "total_equity": snapshot.get("total_equity"),
            "total_market_value": snapshot.get("total_market_value"),
            "total_cash": snapshot.get("total_cash"),
            "cash_ratio": cash_ratio,
            "realized_pnl": snapshot.get("realized_pnl"),
            "unrealized_pnl": snapshot.get("unrealized_pnl"),
            "data_quality": snapshot.get("data_quality"),
            "limitations": snapshot.get("limitations"),
        }
        ctx.data["portfolio_holdings"] = holdings
        ctx.data["stock_opinions"] = opinions
        ctx.data["stock_list"] = [item["code"] for item in holdings]
        if risk is not None:
            from src.agent.tools.data_tools import _compact_portfolio_risk

            ctx.data["portfolio_risk"] = _compact_portfolio_risk(risk)
            self._add_risk_flags(ctx, risk)

        missing_signal = [item["code"] for item in holdings if item["code"] not in opinions]
        meta = {
            "as_of": snapshot.get("as_of"),
            "account_id": account_id,
            "holding_count": len(holdings),
            "cash_ratio": cash_ratio,
            "with_signal": len(holdings) - len(missing_signal),
            "without_signal": missing_signal,
            "risk_available": risk is not None,
        }
        return ctx, meta

    @staticmethod
    def _merge_holdings(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Gộp vị thế cùng mã ở nhiều tài khoản thành một dòng."""
        merged: Dict[str, Dict[str, Any]] = {}
        for account in snapshot.get("accounts", []) or []:
            for pos in account.get("positions", []) or []:
                code = str(pos.get("symbol") or "").strip().upper()
                qty = float(pos.get("quantity") or 0.0)
                if not code or qty <= 0:
                    continue
                item = merged.setdefault(code, {
                    "code": code,
                    "quantity": 0.0,
                    "total_cost": 0.0,
                    "market_value": 0.0,
                    "unrealized_pnl": 0.0,
                    "last_price": pos.get("last_price"),
                    "price_stale": False,
                    "price_available": True,
                })
                item["quantity"] += qty
                item["total_cost"] += float(pos.get("total_cost") or 0.0)
                item["market_value"] += float(pos.get("market_value_base") or 0.0)
                item["unrealized_pnl"] += float(pos.get("unrealized_pnl_base") or 0.0)
                item["price_stale"] = item["price_stale"] or bool(pos.get("price_stale"))
                item["price_available"] = item["price_available"] and bool(pos.get("price_available", True))

        holdings = []
        for item in merged.values():
            qty = item["quantity"]
            cost_basis = item["total_cost"]
            item["avg_cost"] = round(cost_basis / qty, 4) if qty > 0 else None
            cost_of_valued = cost_basis if item["price_available"] else 0.0
            item["unrealized_pnl_pct"] = (
                round(item["unrealized_pnl"] / cost_of_valued * 100.0, 2) if cost_of_valued > 0 else None
            )
            item["quantity"] = round(qty, 4)
            item["market_value"] = round(item["market_value"], 2)
            item.pop("total_cost")
            item.pop("unrealized_pnl")
            holdings.append(item)
        holdings.sort(key=lambda item: item["market_value"], reverse=True)
        return holdings

    def _latest_signals(self, snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Tín hiệu active mới nhất (mọi action) cho từng mã đang nắm giữ.

        Khác ``PortfolioRiskService`` (chỉ lấy sell/reduce/alert): ở đây tín hiệu mua mới hơn
        phải thắng tín hiệu giảm cũ hơn. Lỗi thì trả rỗng (mọi mã vào data_gaps).
        """
        identities = PortfolioRiskService._held_position_identities(snapshot)
        if not identities:
            return {}
        stock_identities = sorted({(item["market"], item["signal_stock_code"]) for item in identities})
        latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
        try:
            page = 1
            while True:
                response = self.decision_signal_service.list_signals(
                    stock_identities=stock_identities,
                    status="active",
                    page=page,
                    page_size=100,
                )
                items = response.get("items", []) if isinstance(response, dict) else []
                for item in items:
                    key = (
                        str(item.get("market") or "").strip().lower(),
                        str(item.get("stock_code") or "").strip().upper(),
                    )
                    # list_signals trả mới nhất trước; giữ dòng đầu tiên cho mỗi mã.
                    if key[0] and key[1] and key not in latest:
                        latest[key] = item
                total = int(response.get("total", 0) or 0) if isinstance(response, dict) else 0
                if page * 100 >= total or not items:
                    break
                page += 1
        except Exception:
            logger.exception("[PortfolioReview] decision signals unavailable")
            return {}

        opinions: Dict[str, Dict[str, Any]] = {}
        for identity in identities:
            summary = summarize_decision_signal(latest.get((identity["market"], identity["signal_stock_code"])))
            if not summary or identity["symbol"] in opinions:
                continue
            opinions[identity["symbol"]] = {
                "signal": summary.get("action"),
                "confidence": summary.get("confidence"),
                "horizon": summary.get("horizon"),
                "stop_loss": summary.get("stop_loss"),
                "target_price": summary.get("target_price"),
                "created_at": summary.get("created_at"),
                "summary": _trim(summary.get("reason") or summary.get("risk_summary") or ""),
            }
        return opinions

    @staticmethod
    def _add_risk_flags(ctx: AgentContext, risk: Dict[str, Any]) -> None:
        concentration = risk.get("concentration") or {}
        if concentration.get("alert"):
            ctx.add_risk_flag(
                "concentration",
                f"Mã lớn nhất chiếm {concentration.get('top_weight_pct')}% giá trị cổ phiếu",
                severity="high",
            )
        drawdown = risk.get("drawdown") or {}
        if drawdown.get("alert"):
            ctx.add_risk_flag(
                "drawdown",
                f"Drawdown tối đa {drawdown.get('max_drawdown_pct')}%, hiện tại {drawdown.get('current_drawdown_pct')}%",
                severity="high",
            )
        for item in (risk.get("stop_loss") or {}).get("items", [])[:5]:
            ctx.add_risk_flag(
                "stop_loss",
                f"{item.get('symbol')} lỗ {item.get('loss_pct')}%"
                + (" (đã chạm ngưỡng cắt lỗ)" if item.get("is_triggered") else " (gần ngưỡng cắt lỗ)"),
                severity="high" if item.get("is_triggered") else "medium",
            )

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------

    def review(
        self,
        *,
        account_id: Optional[int] = None,
        cost_method: str = "fifo",
        query: Optional[str] = None,
    ) -> PortfolioReviewResult:
        started = time.time()
        ctx, meta = self.build_context(account_id=account_id, cost_method=cost_method, query=query)
        if meta["holding_count"] == 0:
            # Không có vị thế: không gọi LLM.
            return PortfolioReviewResult(success=False, error="no_positions", context_meta=meta)

        stage_result, timed_out = run_portfolio_agent(
            self.config,
            ctx,
            timeout_s=resolve_portfolio_agent_timeout(self.config),
        )
        duration = round(time.time() - started, 2)
        if timed_out:
            return PortfolioReviewResult(
                success=False, error="timeout", timed_out=True, duration_s=duration, context_meta=meta
            )
        assessment = ctx.data.get("portfolio_assessment")
        if stage_result is None or not stage_result.success or not isinstance(assessment, dict):
            error = getattr(stage_result, "error", None) or "invalid_agent_output"
            timed_out = getattr(stage_result, "failure_reason", None) == StageFailureReason.TIMEOUT
            return PortfolioReviewResult(
                success=False,
                error=error,
                timed_out=timed_out,
                duration_s=duration,
                tokens_used=int(getattr(stage_result, "tokens_used", 0) or 0),
                context_meta=meta,
            )
        return PortfolioReviewResult(
            success=True,
            assessment=normalize_assessment(assessment, ctx.data["portfolio_holdings"], meta),
            duration_s=duration,
            tokens_used=int(stage_result.tokens_used or 0),
            context_meta=meta,
        )


def normalize_assessment(
    assessment: Dict[str, Any],
    holdings: List[Dict[str, Any]],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Chuẩn hoá output LLM: số liệu server tính thắng, bỏ mã không nắm giữ, clamp tỷ trọng.

    Server-computed weights win; non-held codes are dropped; weights clamped to [0, 1].
    """
    weights = {item["code"]: item.get("current_weight") for item in holdings}
    positions: List[Dict[str, Any]] = []
    for raw in assessment.get("positions") or []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip().upper()
        if code not in weights:
            continue
        suggested = _to_float(raw.get("suggested_weight"))
        signal = str(raw.get("signal") or "").strip().lower()
        priority = str(raw.get("priority") or "").strip().lower()
        positions.append({
            "code": code,
            "current_weight": weights[code],
            "suggested_weight": min(max(suggested, 0.0), 1.0) if suggested is not None else None,
            "signal": signal if signal in _VALID_SIGNALS else None,
            "priority": priority if priority in _VALID_PRIORITIES else None,
            "note": str(raw.get("note") or raw.get("action_note") or "").strip(),
        })

    def _str_list(key: str) -> List[str]:
        value = assessment.get(key) or []
        if isinstance(value, str):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []

    risk_score = _to_float(assessment.get("portfolio_risk_score"))
    suggested_cash = _to_float(assessment.get("suggested_cash_ratio"))
    data_gaps = _str_list("data_gaps")
    if meta.get("without_signal"):
        gap = "Chưa có tín hiệu AI: " + ", ".join(meta["without_signal"])
        if gap not in data_gaps:
            data_gaps.append(gap)
    return {
        "portfolio_risk_score": min(max(risk_score, 1.0), 10.0) if risk_score is not None else None,
        "total_stocks": len(holdings),
        "summary": str(assessment.get("summary") or "").strip(),
        "positions": positions,
        "cash_ratio": meta.get("cash_ratio"),
        "suggested_cash_ratio": min(max(suggested_cash, 0.0), 1.0) if suggested_cash is not None else None,
        "sector_warnings": _str_list("sector_warnings"),
        "correlation_warnings": _str_list("correlation_warnings"),
        "cross_market_notes": _str_list("cross_market_notes"),
        "rebalance_suggestions": _str_list("rebalance_suggestions"),
        "data_gaps": data_gaps,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
