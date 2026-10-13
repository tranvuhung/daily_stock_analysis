# -*- coding: utf-8 -*-
"""
AgentOrchestrator — multi-agent pipeline coordinator.

Manages the lifecycle of specialised agents (Technical → Intel → Risk →
Specialist → Decision) for a single stock analysis run.

Modes:
- ``quick``   : Technical only → Decision (fastest, ~2 LLM calls)
- ``standard``: Technical → Intel → Decision (default)
- ``full``    : Technical → Intel → Risk → Decision
- ``specialist``: Technical → Intel → Risk → specialist evaluation → Decision

The orchestrator:
1. Seeds an :class:`AgentContext` with the user query and stock code
2. Runs agents sequentially, passing the shared context
3. Collects :class:`StageResult` from each agent
4. Produces a unified :class:`OrchestratorResult` with the final dashboard

Importantly, this class exposes the same ``run(task, context)`` and
``chat(message, session_id, ...)`` interface as ``AgentExecutor`` so it
can be a drop-in replacement via the factory.
"""

from __future__ import annotations

import json
import inspect
import logging
import time
from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from src.agent.chat_context import build_visible_chat_history
from src.agent.dashboard_payload import sanitize_agent_dashboard_payload
from src.agent.disagreement import build_agent_disagreement_summary
from src.agent.llm_adapter import LLMToolAdapter
from src.agent.protocols import (
    AgentContext,
    AgentRunStats,
    StageFailureReason,
    StageResult,
    StageStatus,
    is_valid_strategy_signal,
    normalize_decision_signal,
    normalize_stage_failure_reason,
)
from src.agent.skills.defaults import is_skill_agent_name
from src.agent.skills.engine import EvidencePartition, StrategyEngine, StrategyResult, StrategyResultStatus
from src.agent.skills.scheduler import AgentSkillScheduler, SkillBatchResult
from src.agent.risk_override import (
    RiskOverrideApplication,
    build_risk_override_application,
    build_risk_override_plan,
)
from src.agent.runtime_facts import (
    AgentRuntimeFacts,
    DegradationBoundary,
    DegradedEvent,
    PipelineTerminationFact,
    build_agent_runtime_facts,
)
from src.agent.runner import parse_dashboard_json
from src.agent.stock_scope import resolve_stock_scope
from src.agent.stream_events import stream_event
from src.agent.tools.registry import ToolRegistry
from src.config import AGENT_MAX_STEPS_DEFAULT, get_config
from src.report_language import normalize_report_language

if TYPE_CHECKING:
    from src.agent.executor import AgentResult

logger = logging.getLogger(__name__)

# Valid orchestrator modes (ordered by cost/depth)
VALID_MODES = ("quick", "standard", "full", "specialist")
NON_CRITICAL_BASE_STAGES = frozenset({"intel", "risk"})


@dataclass
class OrchestratorResult:
    """Unified result from a multi-agent pipeline run."""

    success: bool = False
    content: str = ""
    dashboard: Optional[Dict[str, Any]] = None
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""
    error: Optional[str] = None
    stats: Optional[AgentRunStats] = None
    runtime_facts: Optional[AgentRuntimeFacts] = None


@dataclass(frozen=True)
class PreparedOrchestratorChatTurn:
    """A persisted multi-agent Chat turn ready for pipeline execution."""

    session_id: str
    context: AgentContext


class AgentOrchestrator:
    """Multi-agent pipeline coordinator.

    Drop-in replacement for ``AgentExecutor`` — exposes the same ``run()``
    and ``chat()`` interface.  The factory switches between them via
    ``AGENT_ARCH``.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        technical_skill_policy: str = "",
        max_steps: int = AGENT_MAX_STEPS_DEFAULT,
        mode: str = "standard",
        skill_manager=None,
        config=None,
    ):
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.technical_skill_policy = technical_skill_policy
        self.max_steps = max_steps
        normalized_mode = "specialist" if mode in {"strategy", "skill"} else mode
        self.mode = normalized_mode if normalized_mode in VALID_MODES else "standard"
        self.skill_manager = skill_manager
        self.config = config
        self.strategy_engine = StrategyEngine()

    def _get_timeout_seconds(self) -> int:
        """Return the pipeline timeout in seconds.

        ``0`` means disabled. The timeout is a cooperative budget for the
        whole pipeline rather than a hard interruption of an in-flight stage.
        """
        raw_value = getattr(self.config, "agent_orchestrator_timeout_s", 0)
        try:
            return max(0, int(raw_value or 0))
        except (TypeError, ValueError):
            return 0

    def _get_sub_agent_timeout_map(self) -> Dict[str, float]:
        """Return per-agent timeout clamps from config, skipping disabled (0) entries."""
        config = self.config
        if config is None:
            return {}
        entries = [
            ("technical", "agent_technical_agent_timeout_s"),
            ("intel", "agent_intel_agent_timeout_s"),
            ("risk", "agent_risk_agent_timeout_s"),
            ("decision", "agent_decision_agent_timeout_s"),
            ("portfolio", "agent_portfolio_agent_timeout_s"),
            ("skill", "agent_skill_agent_timeout_s"),
        ]
        return {
            key: float(val)
            for key, attr in entries
            if (val := getattr(config, attr, None)) is not None and val > 0
        }

    def _get_skill_concurrency(self) -> int:
        raw_value = getattr(self.config, "agent_skill_concurrency", 3)
        try:
            parsed = int(raw_value or 3)
        except (TypeError, ValueError):
            parsed = 3
        return max(1, min(4, parsed))

    def _build_timeout_result(
        self,
        stats: AgentRunStats,
        all_tool_calls: List[Dict[str, Any]],
        models_used: List[str],
        elapsed_s: float,
        timeout_s: int,
        ctx: Optional[AgentContext] = None,
        parse_dashboard: bool = True,
    ) -> OrchestratorResult:
        """Build a standard timeout result payload."""
        stats.total_duration_s = round(elapsed_s, 2)
        stats.models_used = list(dict.fromkeys(models_used))
        error = f"Pipeline timed out after {elapsed_s:.2f}s (limit: {timeout_s}s)"
        provider = stats.models_used[0] if stats.models_used else ""
        model = ", ".join(stats.models_used)

        dashboard = None
        content = ""
        if ctx is not None:
            dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)
            if parse_dashboard and dashboard is not None:
                dashboard = self._mark_partial_dashboard(
                    dashboard,
                    note=_fallback_text("timeout_note", _ctx_language(ctx)),
                    language=_ctx_language(ctx),
                )
                ctx.set_data("final_dashboard", dashboard)
                content = json.dumps(dashboard, ensure_ascii=False, indent=2)

        return OrchestratorResult(
            success=bool(content) if (not parse_dashboard or dashboard is not None) else False,
            content=content,
            dashboard=dashboard,
            error=error,
            stats=stats,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            tool_calls_log=all_tool_calls,
            provider=provider,
            model=model,
            runtime_facts=build_agent_runtime_facts(ctx) if ctx is not None else None,
        )

    def _build_budget_skip_result(
        self,
        stats: AgentRunStats,
        all_tool_calls: List[Dict[str, Any]],
        models_used: List[str],
        elapsed_s: float,
        timeout_s: int,
        stage_name: str,
        remaining_budget: float,
        min_stage_budget_s: int,
        ctx: Optional[AgentContext] = None,
        parse_dashboard: bool = True,
    ) -> OrchestratorResult:
        """Build a result for budget-insufficient stage skip (non-timeout semantics)."""
        stats.total_duration_s = round(elapsed_s, 2)
        stats.models_used = list(dict.fromkeys(models_used))
        dashboard = None
        content = ""
        if ctx is not None:
            dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)
            if parse_dashboard and dashboard is not None:
                dashboard = self._mark_partial_dashboard(
                    dashboard,
                    note=_fallback_text("budget_note", _ctx_language(ctx)),
                    language=_ctx_language(ctx),
                )
                ctx.set_data("final_dashboard", dashboard)
                content = json.dumps(dashboard, ensure_ascii=False, indent=2)

        return OrchestratorResult(
            success=bool(content) if (not parse_dashboard or dashboard is not None) else False,
            content=content,
            dashboard=dashboard,
            error=(
                f"Pipeline skipped before stage '{stage_name}' due to insufficient budget "
                f"({remaining_budget:.1f}s remaining, minimum {min_stage_budget_s}s required)"
            ),
            stats=stats,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            tool_calls_log=all_tool_calls,
            provider=stats.models_used[0] if stats.models_used else "",
            model=", ".join(stats.models_used),
            runtime_facts=build_agent_runtime_facts(ctx) if ctx is not None else None,
        )


    def _prepare_agent(self, agent: Any) -> Any:
        """Apply orchestrator-level runtime settings to a child agent.

        When the orchestrator-level ``max_steps`` equals the default
        (``AGENT_MAX_STEPS_DEFAULT``),
        each agent keeps its own per-agent limit — this prevents inflating
        a decision agent (designed for 3 steps) to 10 steps.

        When the user **explicitly** raises the global limit above the
        default, all agents adopt the global value so the user's intent to
        allow more steps is respected.

        When the user **lowers** the global limit below an agent's default,
        the agent is capped at the global value.
        """
        if hasattr(agent, "max_steps"):
            if self.max_steps > AGENT_MAX_STEPS_DEFAULT:
                # User explicitly raised the limit — apply to all agents.
                agent.max_steps = self.max_steps
            else:
                # Default or lowered — keep per-agent limit as ceiling.
                agent.max_steps = min(agent.max_steps, self.max_steps)
        return agent

    def _callable_accepts_timeout_kwarg(self, func: Any) -> Optional[bool]:
        """Return whether a callable accepts ``timeout_seconds`` when inspectable."""
        if not callable(func):
            return None
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return None

        if "timeout_seconds" in signature.parameters:
            return True
        return any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )

    def _agent_run_accepts_timeout(self, run_callable: Any) -> bool:
        """Best-effort compatibility check for legacy test doubles / custom agents."""
        side_effect = getattr(run_callable, "side_effect", None)
        accepts_timeout = self._callable_accepts_timeout_kwarg(side_effect)
        if accepts_timeout is not None:
            return accepts_timeout

        accepts_timeout = self._callable_accepts_timeout_kwarg(run_callable)
        if accepts_timeout is not None:
            return accepts_timeout

        return True

    def _run_stage_agent(
        self,
        agent: Any,
        ctx: AgentContext,
        progress_callback: Optional[Callable] = None,
        timeout_seconds: Optional[float] = None,
    ) -> StageResult:
        """Run a stage agent while preserving compatibility with older call signatures."""
        # Clamp by per-agent limit when configured.
        # When pipeline budget is disabled (timeout_seconds is None),
        # the sub-agent's own limit still applies as a standalone cap.
        sub_agent_timeout_map = self._get_sub_agent_timeout_map()
        if sub_agent_timeout_map:
            agent_limit = sub_agent_timeout_map.get(agent.agent_name)
            if agent_limit is None and agent.agent_name in getattr(self, "_skill_agent_names", set()):
                agent_limit = sub_agent_timeout_map.get("skill")
            if agent_limit is not None:
                if timeout_seconds is not None:
                    timeout_seconds = min(timeout_seconds, agent_limit)
                else:
                    timeout_seconds = agent_limit
        run_kwargs = {"progress_callback": progress_callback}
        if (
            timeout_seconds is not None
            and timeout_seconds > 0
            and self._agent_run_accepts_timeout(agent.run)
        ):
            run_kwargs["timeout_seconds"] = timeout_seconds
        return agent.run(ctx, **run_kwargs)

    # -----------------------------------------------------------------
    # Public interface (mirrors AgentExecutor)
    # -----------------------------------------------------------------

    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> "AgentResult":
        """Run the multi-agent pipeline for a dashboard analysis.

        Returns an ``AgentResult`` (same type as ``AgentExecutor.run``).
        """
        from src.agent.executor import AgentResult

        ctx = self._build_context(task, context)
        ctx.meta["response_mode"] = "dashboard"
        orch_result = self._execute_pipeline(ctx, parse_dashboard=True)

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=orch_result.dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
            runtime_facts=orch_result.runtime_facts,
        )

    def chat(
        self,
        message: str,
        session_id: str,
        progress_callback: Optional[Callable] = None,
        context: Optional[Dict[str, Any]] = None,
        selected_skill_ids: Optional[List[str]] = None,
    ) -> "AgentResult":
        """Run the pipeline in chat mode (free-form answer, no dashboard parse).

        Conversation history is managed externally by the caller (via
        ``conversation_manager``); the orchestrator focuses on multi-agent
        coordination.
        """
        turn = self.prepare_turn(
            message=message,
            session_id=session_id,
            context=context,
            selected_skill_ids=selected_skill_ids,
        )
        return self.execute_turn(
            turn,
            progress_callback=progress_callback,
        )

    def prepare_turn(
        self,
        *,
        message: str,
        session_id: str,
        context: Optional[Dict[str, Any]] = None,
        selected_skill_ids: Optional[List[str]] = None,
    ) -> PreparedOrchestratorChatTurn:
        """Prepare context and persist the user turn before SSE acceptance."""
        from src.agent.conversation import conversation_manager

        scope_resolution = resolve_stock_scope(message, context)
        ctx = self._build_context(message, scope_resolution.effective_context)
        ctx.session_id = session_id
        ctx.meta["response_mode"] = "chat"
        if scope_resolution.stock_scope is not None:
            ctx.meta["stock_scope"] = scope_resolution.stock_scope

        conversation_manager.get_or_create(session_id)
        config = self.config or getattr(self.llm_adapter, "_config", None) or get_config()
        history = build_visible_chat_history(session_id, self.llm_adapter, config)
        if history:
            ctx.meta["conversation_history"] = history

        # Persist user turn
        conversation_manager.add_user_message(
            session_id,
            message,
            selected_skill_ids,
        )

        return PreparedOrchestratorChatTurn(
            session_id=session_id,
            context=ctx,
        )

    def execute_turn(
        self,
        turn: PreparedOrchestratorChatTurn,
        *,
        progress_callback: Optional[Callable] = None,
    ) -> "AgentResult":
        """Execute an accepted multi-agent Chat turn and persist its result."""
        from src.agent.executor import AgentResult, build_chat_failure_note
        from src.agent.conversation import conversation_manager

        orch_result = self._execute_pipeline(
            turn.context,
            parse_dashboard=False,
            progress_callback=progress_callback,
        )

        # Persist assistant response
        if orch_result.success:
            from src.agent.executor import finalize_chat_answer

            orch_result.content = finalize_chat_answer(
                orch_result.content,
                turn.context.meta.get("report_language"),
                self.llm_adapter,
            )
            conversation_manager.add_message(turn.session_id, "assistant", orch_result.content)
        else:
            conversation_manager.add_message(
                turn.session_id, "assistant",
                build_chat_failure_note(
                    "failed",
                    orch_result.error,
                    turn.context.meta.get("report_language"),
                ),
            )

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=orch_result.dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
            runtime_facts=orch_result.runtime_facts,
        )

    # -----------------------------------------------------------------
    # Pipeline execution
    # -----------------------------------------------------------------

    def _execute_pipeline(
        self,
        ctx: AgentContext,
        parse_dashboard: bool = True,
        progress_callback: Optional[Callable] = None,
    ) -> OrchestratorResult:
        """Run the agent pipeline according to ``self.mode``."""
        stats = AgentRunStats()
        all_tool_calls: List[Dict[str, Any]] = []
        models_used: List[str] = []
        t0 = time.time()
        timeout_s = self._get_timeout_seconds()

        agents = self._build_agent_chain(ctx)
        specialist_agents_inserted = False
        index = 0

        # Minimum seconds required for a stage to do useful work.  Starting
        # a stage with less budget virtually guarantees a timeout that wastes
        # an LLM billing cycle.  Only enforced after at least one stage has
        # completed so that the first stage always gets a chance to run
        # even when the total budget is small.
        _MIN_STAGE_BUDGET_S = 15

        while index < len(agents):
            agent = agents[index]
            elapsed_s = time.time() - t0
            remaining_budget = timeout_s - elapsed_s if timeout_s else None
            stage_min_budget_s = (
                _MIN_STAGE_BUDGET_S
            )
            timeout_exhausted = (
                timeout_s
                and remaining_budget is not None
                and remaining_budget <= 0
            )
            budget_guard_triggered = (
                timeout_s
                and remaining_budget is not None
                and index > 0
                and remaining_budget < stage_min_budget_s
            )
            if timeout_exhausted:
                logger.error("[Orchestrator] pipeline timed out before stage '%s'", agent.agent_name)
                self._record_degraded_event(
                    ctx,
                    stage=agent.agent_name,
                    reason=StageFailureReason.TIMEOUT,
                    boundary=DegradationBoundary.BEFORE_STAGE,
                )
                if progress_callback:
                    progress_callback(stream_event(
                        "pipeline_timeout",
                        stage=agent.agent_name,
                        elapsed=round(elapsed_s, 2),
                        timeout=timeout_s,
                    ))
                if ctx is not None:
                    self._apply_partition_fallback(ctx)
                return self._build_timeout_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            if budget_guard_triggered:
                logger.warning(
                    "[Orchestrator] pipeline insufficient budget before stage '%s' (%.1fs remaining, min %ds)",
                    agent.agent_name,
                    remaining_budget,
                    stage_min_budget_s,
                )
                self._record_degraded_event(
                    ctx,
                    stage=agent.agent_name,
                    reason=StageFailureReason.BUDGET_SKIP,
                    boundary=DegradationBoundary.BEFORE_STAGE,
                )
                if progress_callback:
                    progress_callback(stream_event(
                        "pipeline_budget_skipped",
                        stage=agent.agent_name,
                        elapsed=round(elapsed_s, 2),
                        timeout=timeout_s,
                        remaining=round(remaining_budget, 2),
                        minimum=stage_min_budget_s,
                        reason="insufficient_budget",
                        message=(
                            f"Skipped {agent.agent_name} analysis due to insufficient "
                            "remaining budget"
                        ),
                    ))
                if ctx is not None:
                    self._apply_partition_fallback(ctx)
                return self._build_budget_skip_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    agent.agent_name,
                    remaining_budget,
                    stage_min_budget_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            if (
                self.mode == "specialist"
                and agent.agent_name == "decision"
                and not specialist_agents_inserted
            ):
                specialist_agents = self._build_specialist_agents(ctx)
                self._skill_agent_names = {a.agent_name for a in specialist_agents}
                specialist_agents_inserted = True
                if specialist_agents:
                    batch = self._run_specialist_agent_batch(
                        specialist_agents,
                        ctx,
                        progress_callback=progress_callback,
                        timeout_seconds=remaining_budget,
                    )
                    for stage_result in batch.stage_results:
                        stats.record_stage(stage_result)
                        all_tool_calls.extend(
                            tc for tc in (stage_result.meta.get("tool_calls_log") or [])
                        )
                        models_used.extend(stage_result.meta.get("models_used", []))
                        if stage_result.status == StageStatus.FAILED:
                            self._record_degraded_stage(ctx, stage_result.stage_name, stage_result)
                    ctx.opinions.extend(batch.opinions)
                    invalid_bucket = ctx.meta.get("invalid_opinions")
                    if not isinstance(invalid_bucket, list):
                        invalid_bucket = []
                    invalid_bucket.extend(batch.invalid_records)
                    ctx.meta["invalid_opinions"] = invalid_bucket
                    ctx.meta["skill_scheduler"] = {
                        "mode": "thread_pool",
                        "max_concurrency": batch.max_concurrency,
                        "timeout_per_skill": batch.timeout_per_skill,
                        "scheduled_skill_count": len(specialist_agents),
                        "completed_skill_count": sum(1 for item in batch.stage_results if item.success),
                        "invalid_skill_count": len(batch.invalid_records),
                    }
                    continue

            if agent.agent_name == "decision":
                self._run_strategy_engine(ctx)

            if agent.agent_name == "decision":
                self._prepare_decision_context(ctx)

            if progress_callback:
                progress_callback(stream_event(
                    "stage_start",
                    stage=agent.agent_name,
                    message=f"Starting {agent.agent_name} analysis...",
                ))

            remaining_timeout_s = (
                max(0.0, timeout_s - elapsed_s)
                if timeout_s
                else None
            )
            result: StageResult = self._run_stage_agent(
                agent,
                ctx,
                progress_callback=progress_callback,
                timeout_seconds=remaining_timeout_s,
            )
            stats.record_stage(result)
            all_tool_calls.extend(
                tc for tc in (result.meta.get("tool_calls_log") or [])
            )
            models_used.extend(result.meta.get("models_used", []))

            elapsed_s = time.time() - t0
            if progress_callback:
                progress_callback(stream_event(
                    "stage_done",
                    stage=agent.agent_name,
                    status=result.status.value,
                    duration=result.duration_s,
                ))

            if ctx.meta.get("response_mode") == "chat" and agent.agent_name == "decision":
                final_text = result.meta.get("raw_text")
                if isinstance(final_text, str) and final_text.strip():
                    ctx.set_data("final_response_text", final_text.strip())

            # Abort pipeline on critical failure.
            # Non-critical stages that degrade gracefully:
            #   - intel / risk (standard support stages)
            #   - skill agents (specialist evaluation, optional)
            if result.status == StageStatus.FAILED:
                if not self._is_non_critical_stage(agent.agent_name):
                    logger.error("[Orchestrator] critical stage '%s' failed: %s", agent.agent_name, result.error)
                    return OrchestratorResult(
                        success=False,
                        error=f"Stage '{agent.agent_name}' failed: {result.error}",
                        stats=stats,
                        total_tokens=stats.total_tokens,
                        tool_calls_log=all_tool_calls,
                        runtime_facts=build_agent_runtime_facts(ctx),
                    )
                else:
                    self._record_degraded_stage(ctx, agent.agent_name, result)
                    logger.warning(
                        "[Orchestrator] stage '%s' failed (non-critical, degrading): %s",
                        agent.agent_name,
                        result.error,
                    )

            if timeout_s and elapsed_s >= timeout_s:
                logger.error("[Orchestrator] pipeline timed out after stage '%s'", agent.agent_name)
                last_completed_stage = next(
                    (
                        stage.stage_name
                        for stage in reversed(stats.stage_results)
                        if stage.status == StageStatus.COMPLETED
                    ),
                    None,
                )
                self._record_pipeline_termination(
                    ctx,
                    last_completed_stage=last_completed_stage,
                )
                if progress_callback:
                    progress_callback(stream_event(
                        "pipeline_timeout",
                        stage=agent.agent_name,
                        elapsed=round(elapsed_s, 2),
                        timeout=timeout_s,
                    ))
                self._apply_partition_fallback(ctx)
                return self._build_timeout_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            index += 1

        # Assemble final output
        total_duration = round(time.time() - t0, 2)
        stats.total_duration_s = total_duration
        stats.models_used = list(dict.fromkeys(models_used))

        dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)

        model_str = ", ".join(dict.fromkeys(m for m in models_used if m))
        provider = stats.models_used[0] if stats.models_used else ""

        if parse_dashboard and dashboard is None:
            return OrchestratorResult(
                success=False,
                content=content,
                dashboard=None,
                tool_calls_log=all_tool_calls,
                total_steps=stats.total_stages,
                total_tokens=stats.total_tokens,
                provider=provider,
                model=model_str,
                error="Failed to parse dashboard JSON from agent response",
                stats=stats,
                runtime_facts=build_agent_runtime_facts(ctx),
            )

        return OrchestratorResult(
            success=bool(content),
            content=content,
            dashboard=dashboard,
            tool_calls_log=all_tool_calls,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            provider=provider,
            model=model_str,
            stats=stats,
            runtime_facts=build_agent_runtime_facts(ctx),
        )

    # -----------------------------------------------------------------
    # Agent chain construction
    # -----------------------------------------------------------------

    def _build_agent_chain(self, ctx: AgentContext) -> list:
        """Instantiate the ordered agent list based on ``self.mode``."""
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.agents.intel_agent import IntelAgent
        from src.agent.agents.decision_agent import DecisionAgent
        from src.agent.agents.risk_agent import RiskAgent

        self._skill_agent_names = set()

        common_kwargs = dict(
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            skill_instructions=self.skill_instructions,
            technical_skill_policy=self.technical_skill_policy,
        )

        technical = self._prepare_agent(TechnicalAgent(**common_kwargs))
        intel = self._prepare_agent(IntelAgent(**common_kwargs))
        risk = self._prepare_agent(RiskAgent(**common_kwargs))
        decision = self._prepare_agent(DecisionAgent(**common_kwargs))

        if self.mode == "quick":
            return [technical, decision]
        elif self.mode == "standard":
            return [technical, intel, decision]
        elif self.mode == "full":
            return [technical, intel, risk, decision]
        elif self.mode == "specialist":
            # Specialist agents are inserted lazily right before the decision
            # stage so the router can see the finished technical opinion.
            return [technical, intel, risk, decision]
        else:
            return [technical, intel, decision]

    def _build_specialist_agents(self, ctx: AgentContext) -> list:
        """Build specialist sub-agents based on requested skills.

        Uses the skill router to select applicable skills, then creates
        lightweight agent wrappers for each.
        """
        try:
            from src.agent.skills.router import SkillRouter
            common_kwargs = dict(
                tool_registry=self.tool_registry,
                llm_adapter=self.llm_adapter,
                skill_instructions=self.skill_instructions,
                technical_skill_policy=self.technical_skill_policy,
            )
            router = SkillRouter()
            selected = router.select_skills(ctx, max_count=4)
            if not selected:
                return []

            from src.agent.skills.skill_agent import SkillAgent
            agents = []
            for skill_id in selected:
                agent = self._prepare_agent(SkillAgent(
                    skill_id=skill_id,
                    **common_kwargs,
                ))
                agents.append(agent)
            return agents
        except Exception as exc:
            logger.warning("[Orchestrator] failed to build skill agents: %s", exc)
            return []

    def _build_skill_agents(self, ctx: AgentContext) -> list:
        """Compatibility wrapper for legacy imports."""
        return self._build_specialist_agents(ctx)

    def _build_strategy_agents(self, ctx: AgentContext) -> list:
        """Compatibility wrapper for legacy tests/imports."""
        return self._build_specialist_agents(ctx)

    def _run_specialist_agent_batch(
        self,
        agents: list,
        ctx: AgentContext,
        *,
        progress_callback: Optional[Callable] = None,
        timeout_seconds: Optional[float] = None,
    ) -> SkillBatchResult:
        sub_agent_timeout_map = self._get_sub_agent_timeout_map()
        configured_skill_timeout = sub_agent_timeout_map.get("skill", 0.0)
        budget_per_skill = self._skill_batch_timeout_slice(
            len(agents),
            timeout_seconds=timeout_seconds,
        )
        if configured_skill_timeout and budget_per_skill is not None:
            timeout_per_skill = min(configured_skill_timeout, budget_per_skill)
        elif configured_skill_timeout:
            timeout_per_skill = configured_skill_timeout
        elif budget_per_skill is not None:
            timeout_per_skill = budget_per_skill
        else:
            timeout_per_skill = 0.0
        scheduler = AgentSkillScheduler(
            max_concurrency=self._get_skill_concurrency(),
            timeout_per_skill=timeout_per_skill,
        )
        if progress_callback:
            for agent in agents:
                progress_callback(stream_event(
                    "stage_start",
                    stage=agent.agent_name,
                    message=f"Starting {agent.agent_name} analysis...",
                ))

        batch = scheduler.run(
            agents,
            ctx,
            self._run_stage_agent,
            progress_callback=progress_callback,
        )
        if progress_callback:
            for result in batch.stage_results:
                progress_callback(stream_event(
                    "stage_done",
                    stage=result.stage_name,
                    status=result.status.value,
                    duration=result.duration_s,
                ))
        return batch

    def _skill_batch_timeout_slice(
        self,
        agent_count: int,
        *,
        timeout_seconds: Optional[float],
    ) -> Optional[float]:
        """Split remaining specialist budget across queued concurrency waves."""
        if timeout_seconds is None:
            return None
        try:
            remaining = float(timeout_seconds)
        except (TypeError, ValueError):
            return None
        if remaining <= 0:
            return 0.0

        count = max(1, int(agent_count or 1))
        worker_count = min(self._get_skill_concurrency(), count)
        wave_count = max(1, ceil(count / worker_count))
        return remaining / wave_count

    # -----------------------------------------------------------------
    # Skill aggregation
    # -----------------------------------------------------------------

    def _partition_skill_opinions(self, ctx: AgentContext) -> None:
        """Split skill opinions into Evidence Chain (valid) and Diagnostics (invalid).

        Per docs/multi-strategy-contract.md, section "Evidence Chain vs Diagnostics separation":
        this is the ONLY partition point. After this method, ctx.opinions
        contains only valid skill opinions; invalid ones are moved to
        ctx.meta["invalid_opinions"] and never re-enter downstream evidence.
        """
        kept: List = []
        invalid_bucket: List[Dict[str, Any]] = ctx.meta.setdefault("invalid_opinions", [])
        if not isinstance(invalid_bucket, list):
            invalid_bucket = []
            ctx.meta["invalid_opinions"] = invalid_bucket

        for op in ctx.opinions:
            if not is_skill_agent_name(op.agent_name):
                kept.append(op)
                continue

            raw_signal = op.signal if op.signal else (
                op.raw_data.get("signal") if isinstance(op.raw_data, dict) else None
            )

            if raw_signal is None or (isinstance(raw_signal, str) and not raw_signal.strip()):
                reason = "missing_signal"
                raw_display = raw_signal if isinstance(raw_signal, str) else None
                is_valid = False
            elif is_valid_strategy_signal(raw_signal):
                is_valid = True
                reason = ""
                raw_display = str(raw_signal)
            else:
                is_valid = False
                reason = "unrecognized_signal"
                raw_display = str(raw_signal)

            if is_valid:
                kept.append(op)
            else:
                invalid_bucket.append({
                    "agent_name": op.agent_name,
                    "raw_signal": raw_display,
                    "confidence": op.confidence,
                    "reason": reason,
                })
                logger.info(
                    "[Orchestrator] invalid skill opinion moved to diagnostics: agent=%s raw_signal=%r reason=%s",
                    op.agent_name, raw_display, reason,
                )

        ctx.opinions = kept

    def _aggregate_skill_opinions(self, ctx: AgentContext) -> None:
        """Run SkillAggregator to produce a consensus opinion.

        Merges individual skill-agent opinions into a single weighted
        consensus and stores it in context so the decision agent can use it.
        """
        try:
            from src.agent.skills.aggregator import SkillAggregator
            aggregator = SkillAggregator()
            consensus = aggregator.aggregate(ctx)
            if consensus:
                ctx.opinions.append(consensus)
                ctx.set_data("skill_consensus", {
                    "signal": consensus.signal,
                    "confidence": consensus.confidence,
                    "reasoning": consensus.reasoning,
                    "raw_data": consensus.raw_data,
                    "strategy_synthesis": consensus.raw_data.get("strategy_synthesis"),
                    "conflicts": consensus.raw_data.get("conflicts", []),
                })
                logger.info(
                    "[Orchestrator] skill consensus: signal=%s confidence=%.2f",
                    consensus.signal, consensus.confidence,
                )
            else:
                logger.info("[Orchestrator] no skill opinions to aggregate")
        except Exception as exc:
            logger.warning("[Orchestrator] skill aggregation failed: %s", exc)

    def _aggregate_strategy_opinions(self, ctx: AgentContext) -> None:
        """Compatibility wrapper for legacy tests/imports."""
        self._aggregate_skill_opinions(ctx)

    def _run_strategy_engine(self, ctx: AgentContext) -> None:
        """Run the full skill pipeline via StrategyEngine and update ctx.

        Replaces the old two-step _partition_skill_opinions + _aggregate_skill_opinions
        calls. The engine is the single authoritative owner of strategy_synthesis.
        """
        existing_invalid = ctx.meta.get("invalid_opinions")
        if not isinstance(existing_invalid, list):
            existing_invalid = []
        result = self.strategy_engine.process(
            ctx.opinions,
            diagnostic_records=existing_invalid,
        )

        ctx.meta["invalid_opinions"] = list(result.invalid_records)
        ctx.opinions = list(result.non_skill_opinions) + list(result.valid_skill_opinions)
        if result.consensus_opinion is not None:
            ctx.opinions.append(result.consensus_opinion)

        if result.skill_consensus_data is not None:
            ctx.set_data("skill_consensus", result.skill_consensus_data)

        if result.status == StrategyResultStatus.CONSENSUS:
            logger.info(
                "[Orchestrator] strategy engine: signal=%s confidence=%.2f",
                result.consensus_opinion.signal,
                result.consensus_opinion.confidence,
            )
        elif result.status == StrategyResultStatus.NO_CONSENSUS:
            logger.info(
                "[Orchestrator] strategy engine: NO_CONSENSUS invalid_count=%d",
                result.invalid_count,
            )
        else:
            logger.info("[Orchestrator] strategy engine: NO_SKILLS")

    def _apply_partition_fallback(self, ctx: AgentContext) -> None:
        """Partition skill opinions for timeout/budget-skip early-exit paths.

        Does not aggregate — only ensures invalid diagnostics are preserved
        in ctx.meta["invalid_opinions"] before the pipeline bails out.
        Idempotent: skips if the engine already ran fully (skill_consensus present).
        """
        if ctx.get_data("skill_consensus") is not None:
            return

        partition = self.strategy_engine.partition_only(ctx.opinions)
        ctx.opinions = list(partition.non_skill_opinions) + list(partition.valid_skill_opinions)
        invalid_bucket = ctx.meta.get("invalid_opinions")
        if not isinstance(invalid_bucket, list):
            invalid_bucket = []
        invalid_bucket.extend(partition.invalid_records)
        ctx.meta["invalid_opinions"] = invalid_bucket

    def _prepare_decision_context(self, ctx: AgentContext) -> None:
        """Populate low-sensitivity summaries consumed by DecisionAgent."""
        ctx.meta["agent_disagreement_summary"] = build_agent_disagreement_summary(
            ctx,
            risk_override_enabled=getattr(self.config, "agent_risk_override", True),
        )

    def _record_degraded_stage(
        self,
        ctx: AgentContext,
        agent_name: str,
        result: StageResult,
    ) -> None:
        """Record a low-sensitivity degraded stage marker for downstream synthesis."""
        if result.status != StageStatus.FAILED:
            raise ValueError("degraded stage markers are only produced for failed stages")

        degraded_stages = ctx.meta.setdefault("degraded_stages", [])
        if not isinstance(degraded_stages, list):
            degraded_stages = []
            ctx.meta["degraded_stages"] = degraded_stages
        degraded_stages.append({
            "stage_name": agent_name,
            "status": result.status.value,
            "non_critical": self._is_non_critical_stage(agent_name),
        })
        self._record_degraded_event(
            ctx,
            stage=agent_name,
            reason=normalize_stage_failure_reason(result.failure_reason),
            boundary=DegradationBoundary.DURING_STAGE,
        )

    @staticmethod
    def _record_degraded_event(
        ctx: AgentContext,
        *,
        stage: str,
        reason: Any,
        boundary: DegradationBoundary,
    ) -> None:
        """Record one deduplicated fact for an incomplete stage."""
        normalized = DegradedEvent(
            stage=stage,
            reason=reason,
            boundary=boundary,
        )
        event = {
            "stage": normalized.stage,
            "reason": normalized.reason.value,
            "boundary": normalized.boundary.value,
        }
        events = ctx.meta.setdefault("degraded_events", [])
        if not isinstance(events, list):
            events = []
            ctx.meta["degraded_events"] = events
        if event not in events:
            events.append(event)

    @staticmethod
    def _record_pipeline_termination(
        ctx: AgentContext,
        *,
        last_completed_stage: Optional[str],
    ) -> None:
        """Record a pipeline timeout without attributing it to a stage."""
        termination = PipelineTerminationFact(
            reason=StageFailureReason.TIMEOUT,
            last_completed_stage=last_completed_stage,
        )
        ctx.meta["pipeline_termination"] = {
            "reason": termination.reason.value,
            "last_completed_stage": termination.last_completed_stage,
        }

    def _is_non_critical_stage(self, agent_name: str) -> bool:
        """Return whether a failed stage should degrade instead of aborting."""
        normalized_name = str(agent_name or "").strip()
        return (
            normalized_name in NON_CRITICAL_BASE_STAGES
            or normalized_name in getattr(self, "_skill_agent_names", set())
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _build_context(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentContext:
        """Seed an ``AgentContext`` from the user request."""
        ctx = AgentContext(query=task)

        if context:
            ctx.stock_code = context.get("stock_code", "")
            ctx.stock_name = context.get("stock_name", "")
            requested_skills = context.get("skills")
            if requested_skills is None:
                requested_skills = context.get("strategies", [])
            ctx.meta["skills_requested"] = requested_skills or []
            ctx.meta["strategies_requested"] = requested_skills or []
            ctx.meta["report_language"] = normalize_report_language(context.get("report_language", "vi"))
            if context.get("market_phase_context"):
                ctx.meta["market_phase_context"] = context["market_phase_context"]
            daily_market_context = context.get("daily_market_context")
            if isinstance(daily_market_context, dict) and daily_market_context:
                ctx.meta["daily_market_context"] = dict(daily_market_context)
            analysis_context_pack_summary = context.get("analysis_context_pack_summary")
            if isinstance(analysis_context_pack_summary, str) and analysis_context_pack_summary:
                ctx.meta["analysis_context_pack_summary"] = analysis_context_pack_summary

            # Pre-populate data fields that the caller already has
            for data_key in ("realtime_quote", "daily_history",
                             "trend_result", "news_context"):
                if context.get(data_key):
                    ctx.set_data(data_key, context[data_key])

        # Try to extract stock code from the query text
        if not ctx.stock_code:
            ctx.stock_code = _extract_stock_code(task)

        if "report_language" not in ctx.meta:
            ctx.meta["report_language"] = "vi"

        return ctx

    @staticmethod
    def _fallback_summary(ctx: AgentContext) -> str:
        """Build a plaintext summary when dashboard JSON is unavailable."""
        lines = [f"# Analysis Summary: {ctx.stock_code} ({ctx.stock_name})", ""]
        for op in ctx.opinions:
            lines.append(f"## {op.agent_name}")
            lines.append(f"Signal: {op.signal} (confidence: {op.confidence:.0%})")
            lines.append(op.reasoning)
            lines.append("")
        if ctx.risk_flags:
            lines.append("## Risk Flags")
            for rf in ctx.risk_flags:
                lines.append(f"- [{rf['severity']}] {rf['description']}")
        return "\n".join(lines)

    def _resolve_final_output(
        self,
        ctx: AgentContext,
        *,
        parse_dashboard: bool,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        """Resolve the best available final output from context.

        For dashboard mode, prefer:
        1. Parsed/normalized decision dashboard
        2. Parsed raw dashboard text
        3. Synthesised dashboard from completed opinions
        4. Plaintext fallback summary
        """
        final_dashboard = ctx.get_data("final_dashboard")
        final_raw = ctx.get_data("final_dashboard_raw")
        final_text = ctx.get_data("final_response_text")
        chat_mode = ctx.meta.get("response_mode") == "chat"

        if parse_dashboard:
            dashboard = self._resolve_dashboard_payload(ctx, final_dashboard, final_raw)
            if dashboard is not None:
                return dashboard, json.dumps(dashboard, ensure_ascii=False, indent=2)
            if ctx.opinions:
                return None, self._fallback_summary(ctx)
            return None, ""

        if chat_mode and isinstance(final_text, str) and final_text.strip():
            return None, final_text.strip()
        if isinstance(final_raw, str) and final_raw.strip():
            return None, final_raw
        if isinstance(final_dashboard, dict):
            dashboard = self._finalize_dashboard_payload(final_dashboard, ctx)
            if dashboard is not None:
                return dashboard, json.dumps(dashboard, ensure_ascii=False, indent=2)
        if ctx.opinions:
            return None, self._fallback_summary(ctx)
        return None, ""

    def _resolve_dashboard_payload(
        self,
        ctx: AgentContext,
        final_dashboard: Any,
        final_raw: Any,
    ) -> Optional[Dict[str, Any]]:
        """Resolve one dashboard, apply risk once, then derive signal fields."""
        candidate: Optional[Dict[str, Any]] = None

        if isinstance(final_dashboard, dict):
            candidate = final_dashboard
        elif isinstance(final_raw, str) and final_raw.strip():
            parsed = parse_dashboard_json(final_raw)
            if isinstance(parsed, dict):
                candidate = parsed

        prepared = self._prepare_dashboard_payload(candidate or {}, ctx)
        if prepared is None:
            return None

        ctx.set_data("final_dashboard", prepared)
        self._apply_risk_override(ctx)
        post_risk = ctx.get_data("final_dashboard")
        if not isinstance(post_risk, dict):
            return None

        dashboard = self._finalize_dashboard_payload(post_risk, ctx)
        if dashboard is None:
            return None
        ctx.set_data("final_dashboard", dashboard)
        return dashboard

    def _prepare_dashboard_payload(
        self,
        payload: Optional[Dict[str, Any]],
        ctx: AgentContext,
    ) -> Optional[Dict[str, Any]]:
        """Select a safe payload and canonical signal without deriving advice."""
        prepared = sanitize_agent_dashboard_payload(dict(payload or {}))
        meaningful_data_keys = (
            "realtime_quote",
            "daily_history",
            "trend_result",
            "news_context",
            "intel_opinion",
            "fundamental_context",
        )
        has_meaningful_context = any(
            ctx.get_data(key) is not None for key in meaningful_data_keys
        )
        if not prepared and not ctx.opinions and not has_meaningful_context:
            return None

        base_opinion = self._select_base_opinion(ctx)
        prepared["decision_type"] = normalize_decision_signal(
            prepared.get("decision_type")
            or (base_opinion.signal if base_opinion else "hold")
        )
        return prepared

    def _finalize_dashboard_payload(
        self,
        payload: Optional[Dict[str, Any]],
        ctx: AgentContext,
    ) -> Optional[Dict[str, Any]]:
        """Derive the downstream dashboard shape from the post-risk signal."""
        payload = sanitize_agent_dashboard_payload(dict(payload or {}))
        meaningful_data_keys = (
            "realtime_quote",
            "daily_history",
            "trend_result",
            "news_context",
            "intel_opinion",
            "fundamental_context",
        )
        has_meaningful_context = any(ctx.get_data(key) is not None for key in meaningful_data_keys)
        if not payload and not ctx.opinions and not has_meaningful_context:
            return None

        base_opinion = self._select_base_opinion(ctx)
        application = ctx.meta.get("risk_override_application")
        risk_applied = isinstance(application, RiskOverrideApplication) and application.applied
        decision_type = (
            application.post_risk_signal.value
            if risk_applied
            else normalize_decision_signal(
                payload.get("decision_type")
                or (base_opinion.signal if base_opinion else "hold")
            )
        )
        confidence = float(base_opinion.confidence if base_opinion is not None else 0.5)
        # Ngôn ngữ cho các câu mặc định do hệ thống tự điền. / Language of system-filled defaults.
        lang = _ctx_language(ctx)
        sentiment_score = payload.get("sentiment_score")
        try:
            sentiment_score = int(sentiment_score)
        except (TypeError, ValueError):
            sentiment_score = _estimate_sentiment_score(decision_type, confidence)
        if risk_applied:
            sentiment_score = _adjust_sentiment_score(sentiment_score, decision_type)

        dashboard_block = payload.get("dashboard")
        if not isinstance(dashboard_block, dict):
            dashboard_block = {}
        else:
            dashboard_block = dict(dashboard_block)
            # Strip any LLM-written strategy_synthesis — StrategyEngine is the sole writer.
            dashboard_block.pop("strategy_synthesis", None)

        core = dashboard_block.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
        else:
            core = dict(core)

        intelligence = dashboard_block.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
        else:
            intelligence = dict(intelligence)

        battle = dashboard_block.get("battle_plan")
        if not isinstance(battle, dict):
            battle = {}
        else:
            battle = dict(battle)

        analysis_summary = _first_non_empty_text(
            payload.get("analysis_summary"),
            core.get("one_sentence"),
            getattr(base_opinion, "reasoning", ""),
        )
        if not analysis_summary:
            analysis_summary = _fallback_text("incomplete_summary", lang).format(
                operation=_signal_to_operation(decision_type, lang)
            )
        if risk_applied:
            transition_prefix = _fallback_text("risk_transition_prefix", lang).format(
                from_signal=application.from_signal.value,
                to_signal=application.post_risk_signal.value,
            )
            if not analysis_summary.startswith(transition_prefix):
                analysis_summary = f"{transition_prefix} {analysis_summary}"
        analysis_summary = _truncate_text(analysis_summary, 220)

        trend_prediction = _first_non_empty_text(
            payload.get("trend_prediction"),
            (getattr(base_opinion, "raw_data", {}) or {}).get("trend_summary")
            if base_opinion is not None else "",
        )
        if not trend_prediction:
            technical = self._latest_opinion(ctx, {"technical"})
            tech_raw = technical.raw_data if technical and isinstance(technical.raw_data, dict) else {}
            ma_alignment = tech_raw.get("ma_alignment")
            trend_score = tech_raw.get("trend_score")
            if ma_alignment or trend_score is not None:
                trend_prediction = _fallback_text("trend_technical", lang).format(
                    alignment=ma_alignment or "neutral",
                    score=trend_score if trend_score is not None else "N/A",
                )
            else:
                trend_prediction = _fallback_text("trend_pending", lang)

        operation_advice_raw = payload.get("operation_advice")
        if risk_applied:
            pre_risk_advice = _normalize_operation_advice_value(
                operation_advice_raw,
                application.from_signal.value,
                lang,
            )
            operation_advice = _adjust_operation_advice(
                pre_risk_advice,
                decision_type,
                lang,
            )
        else:
            operation_advice = _normalize_operation_advice_value(
                operation_advice_raw,
                decision_type,
                lang,
            )

        existing_position = core.get("position_advice")
        if risk_applied:
            position_advice = _post_risk_position_advice(decision_type, lang)
        else:
            position_advice = (
                dict(existing_position)
                if isinstance(existing_position, dict)
                else {}
            )
            if isinstance(operation_advice_raw, dict):
                no_position = _first_non_empty_text(
                    operation_advice_raw.get("no_position"),
                    operation_advice_raw.get("empty_position"),
                )
                has_position = _first_non_empty_text(
                    operation_advice_raw.get("has_position"),
                    operation_advice_raw.get("holding_position"),
                )
                if no_position and "no_position" not in position_advice:
                    position_advice["no_position"] = no_position
                if has_position and "has_position" not in position_advice:
                    position_advice["has_position"] = has_position
            defaults = _default_position_advice(decision_type, lang)
            position_advice.setdefault("no_position", defaults["no_position"])
            position_advice.setdefault("has_position", defaults["has_position"])

        key_levels = self._collect_key_levels(ctx, payload, dashboard_block)
        sniper = battle.get("sniper_points")
        if not isinstance(sniper, dict):
            sniper = {}
        else:
            sniper = dict(sniper)

        ideal_buy = _pick_first_level(
            sniper.get("ideal_buy"),
            key_levels.get("ideal_buy_if_valuation_improves"),
            key_levels.get("ideal_buy"),
            key_levels.get("support"),
            key_levels.get("immediate_support"),
        )
        sniper["ideal_buy"] = ideal_buy if ideal_buy is not None else "N/A"

        secondary_buy = _coerce_level_value(sniper.get("secondary_buy"))
        if secondary_buy is None:
            secondary_buy = _pick_first_level(
                key_levels.get("secondary_buy"),
                key_levels.get("support"),
                key_levels.get("immediate_support"),
            )
        if _level_values_equal(secondary_buy, sniper.get("ideal_buy")):
            secondary_buy = None
        sniper["secondary_buy"] = secondary_buy if secondary_buy is not None else "N/A"
        sniper.setdefault(
            "stop_loss",
            key_levels.get("stop_loss")
            or key_levels.get("strong_support_stop_loss")
            or _fallback_text("to_be_added", lang),
        )
        sniper.setdefault(
            "take_profit",
            key_levels.get("take_profit")
            or key_levels.get("next_breakout_target")
            or key_levels.get("current_resistance")
            or key_levels.get("resistance")
            or "N/A",
        )

        risk_alerts = self._collect_risk_alerts(ctx, intelligence)
        positive_catalysts = self._collect_positive_catalysts(ctx, intelligence)
        latest_news = _extract_latest_news_title(intelligence)

        if not intelligence.get("risk_alerts"):
            intelligence["risk_alerts"] = risk_alerts
        if positive_catalysts and not intelligence.get("positive_catalysts"):
            intelligence["positive_catalysts"] = positive_catalysts
        if latest_news and not intelligence.get("latest_news"):
            intelligence["latest_news"] = latest_news

        one_sentence = _first_non_empty_text(
            core.get("one_sentence"),
            analysis_summary,
        )
        if risk_applied and not one_sentence.startswith(transition_prefix):
            one_sentence = f"{transition_prefix} {one_sentence}"
        core["one_sentence"] = _truncate_text(one_sentence, 60)
        if not core.get("time_sensitivity"):
            core["time_sensitivity"] = _fallback_text("this_week", lang)
        if risk_applied or not core.get("signal_type"):
            core["signal_type"] = _signal_to_signal_type(decision_type, lang)
        core["position_advice"] = position_advice

        battle["sniper_points"] = sniper
        if "action_checklist" not in battle:
            battle["action_checklist"] = []
        position_strategy = battle.get("position_strategy")
        if risk_applied:
            position_strategy = (
                dict(position_strategy)
                if isinstance(position_strategy, dict)
                else {}
            )
            position_strategy["suggested_position"] = _default_position_size(decision_type, lang)
            position_strategy["entry_plan"] = position_advice["no_position"]
            position_strategy.setdefault(
                "risk_control",
                _fallback_text("stop_loss_ref", lang).format(
                    stop_loss=sniper.get("stop_loss", _fallback_text("to_be_added", lang))
                ),
            )
            battle["position_strategy"] = position_strategy
        elif not isinstance(position_strategy, dict) or not position_strategy:
            battle["position_strategy"] = {
                "suggested_position": _default_position_size(decision_type, lang),
                "entry_plan": position_advice["no_position"],
                "risk_control": _fallback_text("stop_loss_ref", lang).format(
                    stop_loss=sniper.get("stop_loss", _fallback_text("to_be_added", lang))
                ),
            }

        data_perspective = dashboard_block.get("data_perspective")
        if not isinstance(data_perspective, dict):
            data_perspective = {}
        if not data_perspective:
            built_data_perspective = self._build_data_perspective(ctx, key_levels)
            if built_data_perspective:
                data_perspective = built_data_perspective
        if data_perspective:
            dashboard_block["data_perspective"] = data_perspective

        strategy_synthesis = self._collect_strategy_synthesis(ctx, dashboard_block)
        if strategy_synthesis:
            dashboard_block["strategy_synthesis"] = strategy_synthesis

        dashboard_block["core_conclusion"] = core
        dashboard_block["intelligence"] = intelligence
        dashboard_block["battle_plan"] = battle

        key_points = payload.get("key_points")
        if not isinstance(key_points, list) or not key_points:
            key_points = [
                _truncate_text(op.reasoning, 120)
                for op in ctx.opinions
                if isinstance(op.reasoning, str) and op.reasoning.strip()
            ][:5]

        risk_warning = _first_non_empty_text(
            payload.get("risk_warning"),
            "; ".join(risk_alerts[:3]),
            getattr(self._latest_opinion(ctx, {"risk"}), "reasoning", ""),
        )
        if not risk_warning:
            risk_warning = _fallback_text("no_extra_risk", lang)
        if risk_applied:
            risk_opinion = self._latest_opinion(ctx, {"risk"})
            risk_raw = (
                risk_opinion.raw_data
                if risk_opinion and isinstance(risk_opinion.raw_data, dict)
                else {}
            )
            risk_warning = self._merge_risk_warning(
                risk_warning,
                risk_raw,
                ctx.risk_flags,
                decision_type,
                lang,
            )

        payload["stock_name"] = _first_non_empty_text(payload.get("stock_name"), ctx.stock_name, ctx.stock_code)
        payload["sentiment_score"] = sentiment_score
        payload["trend_prediction"] = trend_prediction
        payload["operation_advice"] = operation_advice
        payload["decision_type"] = decision_type
        payload["confidence_level"] = _confidence_label(confidence, lang)
        payload["analysis_summary"] = analysis_summary
        payload["key_points"] = key_points
        payload["risk_warning"] = risk_warning
        payload["dashboard"] = dashboard_block
        if risk_applied:
            for opinion in reversed(ctx.opinions):
                if opinion.agent_name == "decision":
                    opinion.signal = decision_type
                    opinion.reasoning = analysis_summary
                    opinion.raw_data = payload
                    break
        return payload

    def _collect_strategy_synthesis(
        self,
        ctx: AgentContext,
        dashboard_block: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        # Deterministic synthesis from skill_consensus is the authoritative source
        consensus_data = ctx.get_data("skill_consensus")
        if isinstance(consensus_data, dict):
            synthesis = consensus_data.get("strategy_synthesis")
            if isinstance(synthesis, dict) and synthesis:
                return synthesis
            raw_data = consensus_data.get("raw_data")
            if isinstance(raw_data, dict):
                synthesis = raw_data.get("strategy_synthesis")
                if isinstance(synthesis, dict) and synthesis:
                    return synthesis

        # Fallback: scan opinions
        for opinion in reversed(ctx.opinions):
            if getattr(opinion, "agent_name", "") != "skill_consensus":
                continue
            raw_data = opinion.raw_data if isinstance(opinion.raw_data, dict) else {}
            synthesis = raw_data.get("strategy_synthesis")
            if isinstance(synthesis, dict) and synthesis:
                return synthesis
        return None

    def _collect_key_levels(
        self,
        ctx: AgentContext,
        payload: Dict[str, Any],
        dashboard_block: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Collect key price levels from dashboard payloads and agent opinions."""
        levels: Dict[str, Any] = {}

        def absorb(source: Any) -> None:
            if not isinstance(source, dict):
                return
            for key, value in source.items():
                normalized = _coerce_level_value(value)
                if normalized is not None and key not in levels:
                    levels[key] = normalized

        absorb(payload.get("key_levels"))
        absorb(dashboard_block.get("key_levels"))
        for opinion in reversed(ctx.opinions):
            absorb(getattr(opinion, "key_levels", {}))
            raw = opinion.raw_data if isinstance(opinion.raw_data, dict) else {}
            absorb(raw.get("key_levels"))
        return levels

    def _build_data_perspective(
        self,
        ctx: AgentContext,
        key_levels: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a lightweight data_perspective block from cached market data."""
        realtime = ctx.get_data("realtime_quote")
        trend = ctx.get_data("trend_result")
        technical = self._latest_opinion(ctx, {"technical"})
        tech_raw = technical.raw_data if technical and isinstance(technical.raw_data, dict) else {}
        trend_dict = trend if isinstance(trend, dict) else {}

        data_perspective: Dict[str, Any] = {}
        ma_alignment = tech_raw.get("ma_alignment")
        trend_score = tech_raw.get("trend_score")
        if ma_alignment or trend_score is not None:
            data_perspective["trend_status"] = {
                "ma_alignment": ma_alignment or "N/A",
                "trend_score": trend_score if trend_score is not None else "N/A",
                "is_bullish": str(ma_alignment).lower() == "bullish",
            }

        def _bias_label(bias):
            if not isinstance(bias, (int, float)):
                return ""
            lang = _ctx_language(ctx)
            if bias > 5:
                return _fallback_text("bias_overbought", lang)
            elif bias > 2:
                return _fallback_text("bias_high", lang)
            elif bias < -5:
                return _fallback_text("bias_oversold", lang)
            elif bias < -2:
                return _fallback_text("bias_low", lang)
            return _fallback_text("bias_neutral", lang)

        def _r(val, n=2):
            """Round numeric values for display."""
            return round(val, n) if isinstance(val, (int, float)) else val

        def _pick(primary_dict, primary_key, fallback_dict, fallback_key, default="N/A"):
            """Pick first non-None value, avoiding falsy-zero trap."""
            v = primary_dict.get(primary_key)
            if v is not None:
                return v
            v2 = fallback_dict.get(fallback_key, default)
            return v2 if v2 is not None else default

        if isinstance(realtime, dict) or trend_dict:
            data_perspective["price_position"] = {
                "current_price": _r(_pick(trend_dict, "current_price", realtime or {}, "price")),
                "ma5": _r(_pick(trend_dict, "ma5", tech_raw, "ma5")),
                "ma10": _r(_pick(trend_dict, "ma10", tech_raw, "ma10")),
                "ma20": _r(_pick(trend_dict, "ma20", tech_raw, "ma20")),
                "bias_ma5": _r(_pick(trend_dict, "bias_ma5", tech_raw, "bias_ma5")),
                "bias_status": _bias_label(trend_dict.get("bias_ma5")) or tech_raw.get("bias_status", "N/A"),
                "support_level": key_levels.get("support") or key_levels.get("immediate_support") or "N/A",
                "resistance_level": key_levels.get("resistance") or key_levels.get("current_resistance") or "N/A",
            }
            data_perspective["volume_analysis"] = {
                "volume_ratio": (realtime or {}).get("volume_ratio", "N/A"),
                "turnover_rate": (realtime or {}).get("turnover_rate", "N/A"),
                "volume_status": trend_dict.get("volume_status") or tech_raw.get("volume_status", "N/A"),
                "volume_meaning": tech_raw.get("reasoning", "") if tech_raw else "",
            }

        return data_perspective

    def _collect_risk_alerts(
        self,
        ctx: AgentContext,
        intelligence: Dict[str, Any],
    ) -> List[str]:
        alerts: List[str] = []

        def absorb(values: Any) -> None:
            if not isinstance(values, list):
                return
            for item in values:
                text = ""
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict):
                    text = str(item.get("description") or item.get("title") or "").strip()
                if text and text not in alerts:
                    alerts.append(text)

        absorb(intelligence.get("risk_alerts"))
        intel = self._latest_opinion(ctx, {"intel"})
        intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
        absorb(intel_raw.get("risk_alerts"))
        risk = self._latest_opinion(ctx, {"risk"})
        risk_raw = risk.raw_data if risk and isinstance(risk.raw_data, dict) else {}
        absorb(risk_raw.get("flags"))
        for flag in ctx.risk_flags:
            description = str(flag.get("description", "")).strip()
            if description and description not in alerts:
                alerts.append(description)
        return alerts[:8]

    def _collect_positive_catalysts(
        self,
        ctx: AgentContext,
        intelligence: Dict[str, Any],
    ) -> List[str]:
        catalysts: List[str] = []

        def absorb(values: Any) -> None:
            if not isinstance(values, list):
                return
            for item in values:
                text = str(item).strip()
                if text and text not in catalysts:
                    catalysts.append(text)

        absorb(intelligence.get("positive_catalysts"))
        intel = self._latest_opinion(ctx, {"intel"})
        intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
        absorb(intel_raw.get("positive_catalysts"))
        return catalysts[:8]

    @staticmethod
    def _latest_opinion(ctx: AgentContext, names: set[str]) -> Optional[Any]:
        for opinion in reversed(ctx.opinions):
            if opinion.agent_name in names:
                return opinion
        return None

    def _select_base_opinion(self, ctx: AgentContext) -> Optional[Any]:
        preferred_groups = (
            {"decision"},
            {"skill_consensus", "strategy_consensus"},
            {"technical"},
            {"intel"},
            {"risk"},
        )
        for names in preferred_groups:
            opinion = self._latest_opinion(ctx, names)
            if opinion is not None:
                return opinion
        if ctx.opinions:
            return ctx.opinions[-1]
        return None

    @staticmethod
    def _mark_partial_dashboard(
        dashboard: Dict[str, Any],
        *,
        note: str,
        language: str = "vi",
    ) -> Dict[str, Any]:
        tagged = dict(dashboard)
        summary = _first_non_empty_text(tagged.get("analysis_summary"))
        prefix = _fallback_text("degraded_prefix", language)
        if summary and not summary.startswith(prefix):
            tagged["analysis_summary"] = prefix + summary
        elif not summary:
            tagged["analysis_summary"] = prefix + note

        warning = _first_non_empty_text(tagged.get("risk_warning"))
        tagged["risk_warning"] = f"{note} {warning}".strip() if warning else note

        nested = tagged.get("dashboard")
        if isinstance(nested, dict):
            nested = dict(nested)
            core = nested.get("core_conclusion")
            if isinstance(core, dict):
                core = dict(core)
                one_sentence = _first_non_empty_text(core.get("one_sentence"), tagged.get("analysis_summary"))
                if one_sentence and not str(one_sentence).startswith(prefix):
                    core["one_sentence"] = prefix + str(one_sentence)
                nested["core_conclusion"] = core
            tagged["dashboard"] = nested
        return tagged

    def _apply_risk_override(self, ctx: AgentContext) -> Optional[RiskOverrideApplication]:
        """Apply risk rules and retain their validated actual outcome."""
        dashboard = ctx.get_data("final_dashboard")
        if not isinstance(dashboard, dict):
            return None

        current_signal = normalize_decision_signal(dashboard.get("decision_type", "hold"))
        existing = ctx.meta.get("risk_override_application")
        if (
            isinstance(existing, RiskOverrideApplication)
            and existing.post_risk_signal.value == current_signal
        ):
            return existing

        plan = build_risk_override_plan(
            ctx,
            current_signal=current_signal,
            override_enabled=getattr(self.config, "agent_risk_override", True),
        )
        application = build_risk_override_application(plan)
        ctx.meta["risk_override_application"] = application
        if not application.applied:
            return application

        current_signal = application.from_signal.value
        new_signal = application.to_signal.value
        dashboard["decision_type"] = new_signal

        ctx.set_data("final_dashboard", dashboard)
        ctx.set_data("risk_override_applied", {
            "from": current_signal,
            "to": new_signal,
            "adjustment": plan.adjustment or ("veto" if plan.veto_buy else "none"),
            "reason": plan.reason,
        })

        logger.info(
            "[Orchestrator] risk override applied: %s -> %s (adjustment=%s, high_flag=%s)",
            current_signal,
            new_signal,
            plan.adjustment or ("veto" if plan.veto_buy else "none"),
            plan.has_high_flag,
        )
        return application

    @staticmethod
    def _merge_risk_warning(
        existing_warning: Any,
        risk_raw: Dict[str, Any],
        risk_flags: List[Dict[str, Any]],
        signal: str,
        language: str = "vi",
    ) -> str:
        """Build a concise risk warning after a forced downgrade."""
        warnings: List[str] = []
        if isinstance(existing_warning, str) and existing_warning.strip():
            warnings.append(existing_warning.strip())
        if isinstance(risk_raw.get("reasoning"), str) and risk_raw["reasoning"].strip():
            warnings.append(risk_raw["reasoning"].strip())
        for flag in risk_flags[:3]:
            description = str(flag.get("description", "")).strip()
            severity = str(flag.get("severity", "")).lower()
            if description:
                warnings.append(f"[{severity or 'risk'}] {description}")
        prefix = _fallback_text("risk_takeover", language).format(signal=signal)
        merged = " ".join(dict.fromkeys([prefix] + warnings))
        return merged[:500]


# Common English words (2-5 uppercase letters) that should NOT be treated as
# US stock tickers.  This set is checked by _extract_stock_code() and should
# be kept at module level to avoid re-creating it on every call.
_COMMON_WORDS: set[str] = {
    # Pronouns / articles / prepositions / conjunctions
    "AM", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN",
    "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO",
    "UP", "US", "WE",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
    "CAN", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "HAS",
    "HIS", "HOW", "ITS", "LET", "MAY", "NEW", "NOW", "OLD",
    "SEE", "WAY", "WHO", "DID", "GET", "HIM", "USE", "SAY",
    "SHE", "TOO", "ANY", "WITH", "FROM", "THAT", "THAN",
    "THIS", "WHAT", "WHEN", "WILL", "JUST", "ALSO",
    "BEEN", "EACH", "HAVE", "MUCH", "ONLY", "OVER",
    "SOME", "SUCH", "THEM", "THEN", "THEY", "VERY",
    "WERE", "YOUR", "ABOUT", "AFTER", "COULD", "EVERY",
    "OTHER", "THEIR", "THERE", "THESE", "THOSE", "WHICH",
    "WOULD", "BEING", "STILL", "WHERE",
    # Finance/analysis jargon that looks like tickers
    "BUY", "SELL", "HOLD", "LONG", "PUT", "CALL",
    "ETF", "IPO", "RSI", "EPS", "PEG", "ROE", "ROA",
    "USA", "USD", "CNY", "HKD", "EUR", "GBP",
    "STOCK", "TRADE", "PRICE", "INDEX", "FUND",
    "HIGH", "LOW", "OPEN", "CLOSE", "STOP", "LOSS",
    "TREND", "BULL", "BEAR", "RISK", "CASH", "BOND",
    "MACD", "VWAP", "BOLL", "KDJ",
    "TTM", "LTM", "NTM", "FWD", "YOY", "QOQ", "YTD",
    "EBIT", "EBITDA", "DCF", "CAGR", "FCF", "NAV", "AUM",
    "PE", "PB",
    # Greetings / filler words that often appear in chat messages
    "HELLO", "PLEASE", "THANKS", "CHECK", "LOOK", "THINK",
    "MAYBE", "GUESS", "TELL", "SHOW", "WHAT", "WHATS",
    "WHY", "WHEN", "HOWDY", "HEY", "HI",
}

def _is_denied_ticker_candidate(candidate: str) -> bool:
    """Return whether a text token should not be auto-treated as a ticker."""
    return (candidate or "").strip().upper() in _COMMON_WORDS


def _extract_stock_code(text: str) -> str:
    """Trích mã Việt Nam đầu tiên trong câu (best-effort).

    Best-effort extraction of the first Vietnam stock code from free text.
    """
    from src.agent.stock_scope import extract_stock_codes, normalize_vn_stock_code

    candidates = extract_stock_codes(text or "")
    if candidates:
        return candidates[0]
    # Cả câu chỉ là một mã viết thường (``hpg``). / The whole text is one lowercase code.
    stripped = (text or "").strip()
    candidate = normalize_vn_stock_code(stripped)
    if candidate and not _is_denied_ticker_candidate(candidate):
        return candidate
    return ""


def _adjust_sentiment_score(score: int, signal: str) -> int:
    """Clamp sentiment score into the target band for the overridden signal."""
    bands = {
        "buy": (60, 79),
        "hold": (40, 59),
        "sell": (0, 39),
    }
    low, high = bands.get(signal, (0, 100))
    return max(low, min(high, score))


# Câu mặc định do orchestrator tự điền khi model thiếu trường (chỉ vi/en).
# Default texts filled in by the orchestrator when the model omits fields (vi/en only).
_FALLBACK_TEXTS: Dict[str, Dict[str, str]] = {
    "vi": {
        "timeout_note": "Đa Agent quá thời gian, kết luận dưới đây được tự động hạ cấp dựa trên các giai đoạn đã hoàn thành.",
        "budget_note": "Đa Agent không đủ ngân sách thời gian, kết luận dưới đây được tự động hạ cấp dựa trên các giai đoạn đã hoàn thành.",
        "incomplete_summary": "Đa Agent chưa tạo được bảng quyết định đầy đủ, tạm xử lý theo khuyến nghị {operation}.",
        "risk_transition_prefix": "[Hạ bậc do quản trị rủi ro: {from_signal} -> {to_signal}]",
        "trend_technical": "Kỹ thuật {alignment}, điểm xu hướng {score}",
        "trend_pending": "Cần xác nhận thêm từ kết quả các giai đoạn khác",
        "to_be_added": "Chưa rõ",
        "this_week": "Trong tuần này",
        "stop_loss_ref": "Tham chiếu cắt lỗ: {stop_loss}",
        "no_extra_risk": "Chưa có cảnh báo rủi ro bổ sung",
        "bias_overbought": "Quá mua",
        "bias_high": "Hơi cao",
        "bias_oversold": "Quá bán",
        "bias_low": "Hơi thấp",
        "bias_neutral": "Trung tính",
        "degraded_prefix": "[Kết quả hạ cấp] ",
        "risk_takeover": "Quản trị rủi ro can thiệp: tín hiệu cuối cùng đã được hạ xuống {signal}.",
        "advice_downgraded": "{advice} (khuyến nghị ban đầu đã bị quản trị rủi ro hạ bậc)",
    },
    "en": {
        "timeout_note": "Multi-agent run timed out; the conclusion below was auto-degraded from the completed stages.",
        "budget_note": "Multi-agent time budget ran out; the conclusion below was auto-degraded from the completed stages.",
        "incomplete_summary": "Multi-agent run did not produce a complete dashboard; treating it as {operation} for now.",
        "risk_transition_prefix": "[Risk downgrade: {from_signal} -> {to_signal}]",
        "trend_technical": "Technicals {alignment}, trend score {score}",
        "trend_pending": "Pending confirmation from more stage results",
        "to_be_added": "TBD",
        "this_week": "This week",
        "stop_loss_ref": "Stop-loss reference: {stop_loss}",
        "no_extra_risk": "No additional risk alerts",
        "bias_overbought": "Overbought",
        "bias_high": "Elevated",
        "bias_oversold": "Oversold",
        "bias_low": "Depressed",
        "bias_neutral": "Neutral",
        "degraded_prefix": "[Degraded result] ",
        "risk_takeover": "Risk control took over: the final signal was downgraded to {signal}.",
        "advice_downgraded": "{advice} (original advice downgraded by risk control)",
    },
}

_SIGNAL_OPERATION: Dict[str, Dict[str, str]] = {
    "vi": {"buy": "Mua", "hold": "Quan sát", "sell": "Giảm tỷ trọng/Bán"},
    "en": {"buy": "Buy", "hold": "Watch", "sell": "Reduce/Sell"},
}

_SIGNAL_TYPE: Dict[str, Dict[str, str]] = {
    "vi": {"buy": "🟢 Tín hiệu mua", "hold": "🟡 Nắm giữ/Quan sát", "sell": "🔴 Tín hiệu bán", "alert": "⚠️ Cảnh báo rủi ro"},
    "en": {"buy": "🟢 Buy Signal", "hold": "🟡 Hold and Watch", "sell": "🔴 Sell Signal", "alert": "⚠️ Risk Alert"},
}

_DEFAULT_POSITION_ADVICE: Dict[str, Dict[str, Dict[str, str]]] = {
    "vi": {
        "buy": {
            "no_position": "Có thể giải ngân thăm dò từng phần quanh vùng hỗ trợ, tránh mua đuổi một lần.",
            "has_position": "Có thể tiếp tục nắm giữ; chỉ cân nhắc mua thêm khi giá điều chỉnh về vùng then chốt mà không thủng.",
        },
        "hold": {
            "no_position": "Chưa mua đuổi, chờ điều kiện vào lệnh rõ ràng hơn.",
            "has_position": "Chủ yếu quan sát; chỉ thực hiện quản trị rủi ro khi giá thủng mức cắt lỗ.",
        },
        "sell": {
            "no_position": "Tạm thời đứng ngoài, chờ rủi ro được giải toả.",
            "has_position": "Ưu tiên kiểm soát sụt giảm, giảm tỷ trọng hoặc thoát vị thế theo kế hoạch.",
        },
    },
    "en": {
        "buy": {
            "no_position": "Consider scaling in near support; avoid chasing with a full position at once.",
            "has_position": "Keep holding; only consider adding if a pullback to the key level holds.",
        },
        "hold": {
            "no_position": "Do not chase; wait for clearer entry conditions.",
            "has_position": "Mainly watch; apply risk control only if the stop-loss level breaks.",
        },
        "sell": {
            "no_position": "Stay out for now and wait for the risk to clear.",
            "has_position": "Prioritise limiting drawdown; reduce or exit as planned.",
        },
    },
}

_POST_RISK_POSITION_ADVICE: Dict[str, Dict[str, Dict[str, str]]] = {
    "vi": {
        "hold": {
            "no_position": "Đứng ngoài quan sát cho tới khi rủi ro được giải toả, chờ điều kiện vào lệnh rõ ràng hơn.",
            "has_position": "Nắm giữ thận trọng và nâng mức cắt lỗ; chỉ cân nhắc mua thêm khi rủi ro giảm bớt.",
        },
        "sell": {
            "no_position": "Rủi ro đang cao rõ rệt, chưa mở vị thế mới.",
            "has_position": "Ưu tiên kiểm soát sụt giảm, nên giảm tỷ trọng hoặc thoát các vị thế rủi ro cao.",
        },
    },
    "en": {
        "hold": {
            "no_position": "Stay on the sidelines until the risk clears; wait for clearer entry conditions.",
            "has_position": "Hold cautiously with a tighter stop-loss; only consider adding once the risk eases.",
        },
        "sell": {
            "no_position": "Risk is clearly elevated; do not open new positions.",
            "has_position": "Prioritise limiting drawdown; reduce or exit high-risk positions.",
        },
    },
}

_DEFAULT_POSITION_SIZE: Dict[str, Dict[str, str]] = {
    "vi": {"buy": "Thăm dò với tỷ trọng nhỏ", "hold": "Kiểm soát tỷ trọng", "sell": "Hạ tỷ trọng để phòng thủ"},
    "en": {"buy": "Small starter position", "hold": "Keep position size controlled", "sell": "Cut exposure defensively"},
}

_CONFIDENCE_LABELS: Dict[str, Dict[str, str]] = {
    "vi": {"high": "Cao", "medium": "Trung bình", "low": "Thấp"},
    "en": {"high": "High", "medium": "Medium", "low": "Low"},
}


def _lang_key(language: Optional[str]) -> str:
    """Chuẩn hoá ngôn ngữ về 'vi' hoặc 'en'. / Normalize language to 'vi' or 'en'."""
    return "en" if normalize_report_language(language or "vi") == "en" else "vi"


def _ctx_language(ctx: Optional[AgentContext]) -> str:
    """Lấy ngôn ngữ báo cáo từ context. / Read the report language from the context."""
    meta = getattr(ctx, "meta", None) or {}
    return _lang_key(meta.get("report_language"))


def _fallback_text(key: str, language: Optional[str] = "vi") -> str:
    return _FALLBACK_TEXTS[_lang_key(language)][key]


def _adjust_operation_advice(advice: str, signal: str, language: Optional[str] = "vi") -> str:
    """Normalize action wording to the overridden decision signal."""
    mapping = _SIGNAL_OPERATION[_lang_key(language)]
    if signal not in mapping:
        return advice
    if advice == mapping[signal]:
        return advice
    return _fallback_text("advice_downgraded", language).format(advice=mapping[signal])


def _signal_to_operation(signal: str, language: Optional[str] = "vi") -> str:
    mapping = _SIGNAL_OPERATION[_lang_key(language)]
    return mapping.get(signal, mapping["hold"])


def _signal_to_signal_type(signal: str, language: Optional[str] = "vi") -> str:
    mapping = _SIGNAL_TYPE[_lang_key(language)]
    return mapping.get(signal, mapping["alert"])


def _default_position_advice(signal: str, language: Optional[str] = "vi") -> Dict[str, str]:
    mapping = _DEFAULT_POSITION_ADVICE[_lang_key(language)]
    return dict(mapping.get(signal, mapping["hold"]))


def _post_risk_position_advice(signal: str, language: Optional[str] = "vi") -> Dict[str, str]:
    """Return authoritative position advice after an applied risk transition."""
    mapping = _POST_RISK_POSITION_ADVICE[_lang_key(language)]
    if signal in mapping:
        return dict(mapping[signal])
    return _default_position_advice(signal, language)


def _default_position_size(signal: str, language: Optional[str] = "vi") -> str:
    mapping = _DEFAULT_POSITION_SIZE[_lang_key(language)]
    return mapping.get(signal, mapping["hold"])


def _normalize_operation_advice_value(value: Any, signal: str, language: Optional[str] = "vi") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _signal_to_operation(signal, language)


def _confidence_label(confidence: float, language: Optional[str] = "vi") -> str:
    labels = _CONFIDENCE_LABELS[_lang_key(language)]
    if confidence >= 0.75:
        return labels["high"]
    if confidence >= 0.45:
        return labels["medium"]
    return labels["low"]


def _estimate_sentiment_score(signal: str, confidence: float) -> int:
    confidence = max(0.0, min(1.0, float(confidence)))
    bands = {
        "buy": (65, 79),
        "hold": (45, 59),
        "sell": (20, 39),
    }
    low, high = bands.get(signal, (45, 59))
    return int(round(low + (high - low) * confidence))


def _coerce_level_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", "").replace("，", "").strip()
    if not text or text.upper() == "N/A" or text in {"-", "—"}:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return text


def _pick_first_level(*values: Any) -> Any:
    for value in values:
        normalized = _coerce_level_value(value)
        if normalized is not None:
            return normalized
    return None


def _level_values_equal(left: Any, right: Any) -> bool:
    left_normalized = _coerce_level_value(left)
    right_normalized = _coerce_level_value(right)
    return (
        left_normalized is not None
        and right_normalized is not None
        and left_normalized == right_normalized
    )


def _first_non_empty_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _truncate_text(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _extract_latest_news_title(intelligence: Dict[str, Any]) -> str:
    key_news = intelligence.get("key_news")
    if isinstance(key_news, list):
        for item in key_news:
            if isinstance(item, dict):
                title = str(item.get("title", "")).strip()
                if title:
                    return title
    latest_news = intelligence.get("latest_news")
    if isinstance(latest_news, str) and latest_news.strip():
        return latest_news.strip()
    return ""
