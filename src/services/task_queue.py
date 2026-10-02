# -*- coding: utf-8 -*-
"""
===================================
A-share watchlist intelligent analysis system - async task queue
===================================

Responsibilities:
1. Manage the lifecycle of async analysis tasks
2. Prevent duplicate submissions of the same stock code
3. Provide SSE event broadcasting
4. Persist tasks to the database on completion
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, TYPE_CHECKING, Tuple, Literal, Callable

if TYPE_CHECKING:
    from asyncio import Queue as AsyncQueue

from data_provider.base import canonical_stock_code, normalize_stock_code
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    get_current_diagnostic_context,
    reset_run_diagnostic_context,
)
from src.report_language import get_analysis_error_text
from src.utils.analysis_metadata import SELECTION_SOURCES
from src.services.stock_code_utils import resolve_index_stock_code_for_analysis

logger = logging.getLogger(__name__)


def _dedupe_stock_code_key(stock_code: str) -> str:
    """
    Build the internal duplicate-detection key for a stock code.

    The task queue should treat equivalent market code shapes as the same
    underlying stock, e.g. ``600519`` and ``600519.SH``.
    """
    return resolve_index_stock_code_for_analysis(normalize_stock_code(stock_code))


def _dedupe_task_key(
    stock_code: str,
    analysis_target: Optional[Any] = None,
) -> str:
    """
    Build the duplicate-detection key for a submitted task.

    Index targets dedupe by their canonical id (``sh000016`` / ``csi930955``),
    so an index never collapses with a same-digit stock (``000016``) and
    registered CSI aliases converge to one key. Stock targets keep the legacy
    code-based key, preserving existing dedup semantics for
    ``600519``/``600519.SH`` and friends.
    """
    from src.services.stock_list_parser import ParseStatus

    if analysis_target is not None and analysis_target.asset_type == ParseStatus.INDEX:
        return analysis_target.canonical_id
    return _dedupe_stock_code_key(stock_code)


def asset_type_from_analysis_target(analysis_target: Optional[Any]) -> Optional[str]:
    """Derive the optional ``asset_type`` from a submitted analysis target.

    Uses the parser target only (never re-guesses from the code). Registered
    index targets (``ParseStatus.INDEX``) yield ``"index"``, explicit stock
    targets yield ``"stock"``, and targets that were not carried downstream
    (plain stock submissions, market review) yield ``None`` so the field is
    simply omitted from task payloads — keeping legacy clients compatible.
    """
    from src.services.stock_list_parser import ParseStatus

    if analysis_target is None:
        return None
    asset_type = getattr(analysis_target, "asset_type", None)
    if asset_type == ParseStatus.INDEX:
        return "index"
    if asset_type == ParseStatus.STOCK:
        return "stock"
    return None


class TaskStatus(str, Enum):
    """Task status enumeration"""
    PENDING = "pending"        # Waiting for execution
    PROCESSING = "processing"  # In progress
    COMPLETED = "completed"    # Completed
    FAILED = "failed"          # Failed
    CANCEL_REQUESTED = "cancel_requested"  # Cancellation requested
    CANCELLED = "cancelled"    # Cancelled by user/system


@dataclass
class TaskInfo:
    """
    Task information dataclass.

    Used for API responses and internal task management.
    """
    task_id: str
    stock_code: str
    stock_name: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    report_type: str = "detailed"
    analysis_phase: str = "auto"
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    original_query: Optional[str] = None
    selection_source: Optional[str] = None
    query_source: str = "api"
    portfolio_context: Optional[Dict[str, Any]] = None
    skills: Optional[List[str]] = None
    report_language: Optional[str] = None
    trace_id: Optional[str] = None
    region: Optional[str] = None
    flow_events: List[Dict[str, Any]] = field(default_factory=list)
    # Freeze the dedupe key: computed per asset_type branch at submit time and reused on completion/failure removal,
    # so mismatched key generation/removal cannot leave leftovers in _analyzing_stocks.
    dedupe_key: Optional[str] = None
    analysis_target: Optional[Any] = None
    # Optional asset type from the parser (``index``/``stock``/``None``); SSE and the task list
    # pass it through from here and must not re-guess it on the consumer side.
    asset_type: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert task info into an API-friendly dictionary."""
        payload = {
            "task_id": self.task_id,
            "trace_id": self.trace_id or self.task_id,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "report_type": self.report_type,
            "analysis_phase": self.analysis_phase,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "original_query": self.original_query,
            "selection_source": self.selection_source,
            "skills": self.skills,
        }
        if self.region is not None:
            payload["region"] = self.region
        if self.asset_type is not None:
            payload["asset_type"] = self.asset_type
        return payload
    
    def copy(self) -> 'TaskInfo':
        """Create a shallow copy of the task information."""
        return TaskInfo(
            task_id=self.task_id,
            stock_code=self.stock_code,
            stock_name=self.stock_name,
            status=self.status,
            progress=self.progress,
            message=self.message,
            result=self.result,
            error=self.error,
            report_type=self.report_type,
            analysis_phase=self.analysis_phase,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            original_query=self.original_query,
            selection_source=self.selection_source,
            query_source=self.query_source,
            portfolio_context=dict(self.portfolio_context) if isinstance(self.portfolio_context, dict) else None,
            skills=list(self.skills) if self.skills is not None else None,
            report_language=self.report_language,
            trace_id=self.trace_id or self.task_id,
            region=self.region,
            flow_events=copy.deepcopy(self.flow_events),
            dedupe_key=self.dedupe_key,
            analysis_target=self.analysis_target,
            asset_type=self.asset_type,
        )


class DuplicateTaskError(Exception):
    """
    Duplicate submission error

    Raised when the stock is already being analyzed
    """
    def __init__(self, stock_code: str, existing_task_id: str):
        self.stock_code = stock_code
        self.existing_task_id = existing_task_id
        super().__init__(f"股票 {stock_code} 正在分析中 (task_id: {existing_task_id})")


class AnalysisTaskQueue:
    """
    Async analysis task queue

    Singleton, one global instance

    Features:
    1. Prevents duplicate submissions of the same stock code
    2. Runs analysis tasks in a thread pool
    3. SSE event broadcasting
    4. Automatic persistence on task completion
    """
    
    _instance: Optional['AnalysisTaskQueue'] = None
    _instance_lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, max_workers: int = 3):
        # Prevent re-initialization
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self._max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        
        # Core data structures
        self._tasks: Dict[str, TaskInfo] = {}           # task_id -> TaskInfo
        self._analyzing_stocks: Dict[str, str] = {}     # dedupe_key -> task_id
        self._futures: Dict[str, Future] = {}           # task_id -> Future
        
        # SSE subscriber list (asyncio.Queue instances)
        self._subscribers: List['AsyncQueue'] = []
        self._subscribers_lock = threading.Lock()
        
        # Main event loop reference (for cross-thread broadcasting)
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Thread-safety lock
        self._data_lock = threading.RLock()
        
        # Number of tasks kept in history (in memory)
        self._max_history = 100
        self._max_flow_events_per_task = 200
        
        self._initialized = True
        logger.info(f"[TaskQueue] 初始化完成，最大并发: {max_workers}")
    
    @property
    def executor(self) -> ThreadPoolExecutor:
        """Lazily create the thread pool"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="analysis_task_"
            )
        return self._executor

    @property
    def max_workers(self) -> int:
        """Return current executor max worker setting."""
        return self._max_workers

    def _has_inflight_tasks_locked(self) -> bool:
        """Check whether queue has any pending/processing tasks."""
        if self._analyzing_stocks:
            return True
        return any(
            task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
            for task in self._tasks.values()
        )

    def sync_max_workers(
        self,
        max_workers: int,
        *,
        log: bool = True,
    ) -> Literal["applied", "unchanged", "deferred_busy"]:
        """
        Try to sync queue concurrency without replacing singleton instance.

        Returns:
            - "applied": new value applied immediately (idle queue only)
            - "unchanged": target equals current value or invalid target
            - "deferred_busy": queue is busy, apply is deferred
        """
        try:
            target = max(1, int(max_workers))
        except (TypeError, ValueError):
            if log:
                logger.warning("[TaskQueue] 忽略非法 MAX_WORKERS 值: %r", max_workers)
            return "unchanged"

        executor_to_shutdown: Optional[ThreadPoolExecutor] = None
        previous: int
        with self._data_lock:
            previous = self._max_workers
            if target == previous:
                return "unchanged"

            if self._has_inflight_tasks_locked():
                if log:
                    logger.info(
                        "[TaskQueue] 最大并发调整延后: 当前繁忙 (%s -> %s)",
                        previous,
                        target,
                    )
                return "deferred_busy"

            self._max_workers = target
            executor_to_shutdown = self._executor
            self._executor = None

        if executor_to_shutdown is not None:
            executor_to_shutdown.shutdown(wait=False)

        if log:
            logger.info("[TaskQueue] 最大并发已更新: %s -> %s", previous, target)
        return "applied"
    
    # ========== Task submission & query ==========
    
    def is_analyzing(self, stock_code: str) -> bool:
        """
        Check whether a stock is currently being analyzed

        Args:
            stock_code: Stock code

        Returns:
            True if it is being analyzed
        """
        dedupe_key = _dedupe_stock_code_key(stock_code)
        with self._data_lock:
            return dedupe_key in self._analyzing_stocks
    
    def get_analyzing_task_id(self, stock_code: str) -> Optional[str]:
        """
        Get the ID of the task currently analyzing this stock

        Args:
            stock_code: Stock code

        Returns:
            Task ID, or None if there is none
        """
        dedupe_key = _dedupe_stock_code_key(stock_code)
        with self._data_lock:
            return self._analyzing_stocks.get(dedupe_key)

    def validate_selection_source(self, selection_source: Optional[str]) -> None:
        """
        Validate the selection source parameter.

        Args:
            selection_source: Selection source label.

        Raises:
            ValueError: Raised when the selection source is invalid.
        """
        if selection_source is not None and selection_source not in SELECTION_SOURCES:
            raise ValueError(
                f"Invalid selection_source: {selection_source}. "
                f"Must be one of {SELECTION_SOURCES}"
            )
    
    def submit_task(
        self,
        stock_code: str,
        stock_name: Optional[str] = None,
        original_query: Optional[str] = None,
        selection_source: Optional[str] = None,
        query_source: str = "api",
        portfolio_context: Optional[Dict[str, Any]] = None,
        report_type: str = "detailed",
        analysis_phase: str = "auto",
        force_refresh: bool = False,
        skills: Optional[List[str]] = None,
        report_language: Optional[str] = None,
        analysis_target: Optional[Any] = None,
    ) -> TaskInfo:
        """
        Submit a single analysis task.

        Args:
            stock_code: Stock code
            stock_name: Optional stock name
            original_query: Optional raw user input
            selection_source: Optional source label
            report_type: Report type
            analysis_phase: Requested analysis phase override
            force_refresh: Whether to bypass cache
            analysis_target: Optional structured analysis target (index targets
                dedupe by canonical id and flow through to the pipeline)

        Returns:
            TaskInfo: Accepted task information

        Raises:
            DuplicateTaskError: Raised when the stock is already being analyzed
        """
        stock_code = resolve_index_stock_code_for_analysis(stock_code)
        if not stock_code:
            raise ValueError("Stock code must not be empty or whitespace only")

        accepted, duplicates = self.submit_tasks_batch(
            [stock_code],
            stock_name=stock_name,
            original_query=original_query,
            selection_source=selection_source,
            query_source=query_source,
            portfolio_context=portfolio_context,
            report_type=report_type,
            analysis_phase=analysis_phase,
            force_refresh=force_refresh,
            skills=skills,
            report_language=report_language,
            analysis_targets=([analysis_target] if analysis_target is not None else None),
        )
        if duplicates:
            raise duplicates[0]
        return accepted[0]

    def submit_tasks_batch(
        self,
        stock_codes: List[str],
        stock_name: Optional[str] = None,
        original_query: Optional[str] = None,
        selection_source: Optional[str] = None,
        query_source: str = "api",
        portfolio_context: Optional[Dict[str, Any]] = None,
        report_type: str = "detailed",
        analysis_phase: str = "auto",
        force_refresh: bool = False,
        notify: bool = True,
        skills: Optional[List[str]] = None,
        report_language: Optional[str] = None,
        analysis_targets: Optional[List[Any]] = None,
    ) -> Tuple[List[TaskInfo], List[DuplicateTaskError]]:
        """
        Submit analysis tasks in batch.

        - Duplicate stocks are skipped and recorded in duplicates.
        - If executor submission fails, the current batch is rolled back.
        - ``analysis_targets`` is an optional per-code structured target list.
          Index targets dedupe by ``canonical_id`` (never collapsing with a
          same-digit stock); stock targets keep the legacy code-based key.
        """
        self.validate_selection_source(selection_source)

        accepted: List[TaskInfo] = []
        duplicates: List[DuplicateTaskError] = []
        created_task_ids: List[str] = []

        # Align per-code targets with the canonicalized code list. Index targets
        # keep their parser canonical id (lowercase ``sh000016`` / ``csi930955``)
        # so the pipeline and fetchers see the exact identity the API resolved;
        # stock codes keep the legacy canonicalization path.
        targets_by_index: Dict[int, Any] = {}
        for idx, code in enumerate(stock_codes):
            target = (
                analysis_targets[idx]
                if analysis_targets is not None and idx < len(analysis_targets)
                else None
            )
            if target is not None and getattr(target, "asset_type", None) == "index":
                targets_by_index[idx] = target

        canonical_codes = []
        for idx, code in enumerate(stock_codes):
            target = targets_by_index.get(idx)
            if target is not None:
                canonical_codes.append(target.canonical_id)
            else:
                normalized = resolve_index_stock_code_for_analysis(code)
                if normalized:
                    canonical_codes.append(normalized)

        with self._data_lock:
            for idx, stock_code in enumerate(canonical_codes):
                analysis_target = targets_by_index.get(idx)
                dedupe_key = _dedupe_task_key(stock_code, analysis_target)
                if dedupe_key in self._analyzing_stocks:
                    existing_task_id = self._analyzing_stocks[dedupe_key]
                    duplicates.append(DuplicateTaskError(stock_code, existing_task_id))
                    continue

                task_id = uuid.uuid4().hex
                task_skills = list(skills) if skills is not None else None
                task_info = TaskInfo(
                    task_id=task_id,
                    trace_id=task_id,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    status=TaskStatus.PENDING,
                    message="Task queued",
                    report_type=report_type,
                    analysis_phase=analysis_phase or "auto",
                    original_query=original_query,
                    selection_source=selection_source,
                    query_source=query_source or "api",
                    portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
                    skills=task_skills,
                    report_language=report_language,
                    dedupe_key=dedupe_key,
                    analysis_target=analysis_target,
                    asset_type=asset_type_from_analysis_target(analysis_target),
                )
                self._tasks[task_id] = task_info
                self._analyzing_stocks[dedupe_key] = task_id

                try:
                    future = self.executor.submit(
                        self._execute_task,
                        task_id,
                        stock_code,
                        report_type,
                        force_refresh,
                        notify,
                        task_skills,
                        report_language,
                        analysis_target,
                    )
                except Exception:
                    # Roll back the current batch to avoid partial submission.
                    self._rollback_submitted_tasks_locked(created_task_ids + [task_id])
                    raise

                self._futures[task_id] = future
                accepted.append(task_info)
                created_task_ids.append(task_id)
                logger.info(f"[TaskQueue] 任务已提交: {stock_code} -> {task_id}")

            # Keep task_created ordered before worker-emitted task_started/task_completed.
            # Broadcasting here also preserves batch rollback semantics because we only
            # reach this point after every submit in the batch has succeeded.
            for task_info in accepted:
                self._broadcast_event("task_created", task_info.to_dict())

        return accepted, duplicates

    def submit_background_task(
        self,
        run_task: Callable[[], Optional[Any]],
        *,
        stock_code: str,
        stock_name: Optional[str] = None,
        report_type: str = "detailed",
        message: Optional[str] = "任务已加入队列",
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        region: Optional[str] = None,
    ) -> TaskInfo:
        """
        Submit a generic background callable with task lifecycle tracking.

        This is used by callers that need task status visibility but do not
        map to standard per-stock async analysis flow.
        """
        task_id = task_id or uuid.uuid4().hex
        task_info = TaskInfo(
            task_id=task_id,
            trace_id=trace_id or task_id,
            stock_code=stock_code,
            stock_name=stock_name,
            status=TaskStatus.PENDING,
            message=message,
            report_type=report_type,
            region=region,
        )

        with self._data_lock:
            if task_id in self._tasks:
                raise ValueError(f"Task ID already exists: {task_id}")
            self._tasks[task_id] = task_info
            try:
                future = self.executor.submit(self._execute_background_task, task_id, run_task)
            except Exception:
                del self._tasks[task_id]
                raise

            self._futures[task_id] = future
            self._broadcast_event("task_created", task_info.to_dict())

        return task_info.copy()

    def _rollback_submitted_tasks_locked(self, task_ids: List[str]) -> None:
        """Roll back tasks created in the current batch that have not yet been reliably returned to the caller."""
        for task_id in task_ids:
            future = self._futures.pop(task_id, None)
            if future is not None:
                future.cancel()

            task = self._tasks.pop(task_id, None)
            if task:
                # Use the dedupe_key frozen at submit time (if present) instead of recomputing from code,
                # which could diverge from the submit path and leave leftovers in _analyzing_stocks.
                dedupe_key = task.dedupe_key or _dedupe_task_key(
                    task.stock_code,
                    getattr(task, "analysis_target", None),
                )
                if self._analyzing_stocks.get(dedupe_key) == task_id:
                    del self._analyzing_stocks[dedupe_key]
    
    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """
        Get task info

        Args:
            task_id: Task ID

        Returns:
            TaskInfo or None
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            return task.copy() if task else None

    def append_task_flow_event(
        self,
        task_id: str,
        flow_event: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Append a recent run-flow event to an active task and broadcast it.

        The event cache is deliberately bounded and fail-open; diagnostics must
        never affect the analysis pipeline.
        """
        try:
            event_payload = copy.deepcopy(flow_event)
        except Exception:
            logger.debug("[TaskQueue] 忽略不可复制的运行流事件: task_id=%s", task_id)
            return None

        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.flow_events.append(event_payload)
            if len(task.flow_events) > self._max_flow_events_per_task:
                task.flow_events = task.flow_events[-self._max_flow_events_per_task:]
            task_snapshot = task.copy()

        payload = task_snapshot.to_dict()
        payload["flow_event"] = event_payload
        self._broadcast_event("task_progress", payload)
        return event_payload

    def get_task_flow_events(self, task_id: str) -> List[Dict[str, Any]]:
        """Return a copy of the recent run-flow events for a task."""
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return []
            return copy.deepcopy(task.flow_events)
    
    def list_pending_tasks(self) -> List[TaskInfo]:
        """
        Get all in-progress tasks (pending + processing)

        Returns:
            Task list (copy)
        """
        with self._data_lock:
            return [
                task.copy() for task in self._tasks.values()
                if task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.CANCEL_REQUESTED)
            ]
    
    def list_all_tasks(self, limit: int = 50) -> List[TaskInfo]:
        """
        Get all tasks (newest created first)

        Args:
            limit: Maximum number to return

        Returns:
            Task list (copy)
        """
        with self._data_lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True
            )
            return [t.copy() for t in tasks[:limit]]
    
    def get_task_stats(self) -> Dict[str, int]:
        """
        Get task statistics

        Returns:
            Statistics dict
        """
        with self._data_lock:
            stats = {
                "total": len(self._tasks),
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
            }
            for task in self._tasks.values():
                stats[task.status.value] = stats.get(task.status.value, 0) + 1
            return stats

    def update_task_progress(
        self,
        task_id: str,
        progress: int,
        message: Optional[str] = None,
        *,
        event_type: str = "task_progress",
    ) -> Optional[TaskInfo]:
        """
        Update in-flight task progress and broadcast an SSE event.

        Only pending/processing tasks are updated. Progress is clamped to
        [0, 99] so terminal states remain controlled by completion/failure.
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                return None

            next_progress = max(task.progress, max(0, min(99, int(progress))))
            changed = False
            if next_progress != task.progress:
                task.progress = next_progress
                changed = True
            if message is not None and message != task.message:
                task.message = message
                changed = True

            if not changed:
                return task.copy()

            task_snapshot = task.copy()

        self._broadcast_event(event_type, task_snapshot.to_dict())
        return task_snapshot
    
    # ========== Task execution ==========
    
    def _execute_task(
        self,
        task_id: str,
        stock_code: str,
        report_type: str,
        force_refresh: bool,
        notify: bool = True,
        skills: Optional[List[str]] = None,
        report_language: Optional[str] = None,
        analysis_target: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run an analysis task (in the thread pool)

        Args:
            task_id: Task ID
            stock_code: Stock code
            report_type: Report type
            force_refresh: Whether to force a refresh
            analysis_target: Optional structured analysis target (index targets are passed through to the pipeline)

        Returns:
            Analysis result dict
        """
        # Set status to processing
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            trace_id = task.trace_id or task_id
            analysis_phase = task.analysis_phase
            query_source = task.query_source or "api"
            portfolio_context = dict(task.portfolio_context) if isinstance(task.portfolio_context, dict) else None
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "Analyzing..."
            task.progress = 10
        
        self._broadcast_event("task_started", task.to_dict())
        
        try:
            # Import the analysis service (lazy import to avoid circular dependencies)
            from src.services.analysis_service import AnalysisService
            
            # Run analysis
            service = AnalysisService()

            def _on_progress(progress: int, message: str) -> None:
                self.update_task_progress(task_id, progress, message)

            diag_token = None
            if get_current_diagnostic_context() is None:
                diag_token = activate_run_diagnostic_context(
                    trace_id=trace_id,
                    task_id=task_id,
                    query_id=task_id,
                    stock_code=stock_code,
                    trigger_source=query_source,
                    event_sink=lambda event: self.append_task_flow_event(task_id, event),
                )
            result = service.analyze_stock(
                stock_code=stock_code,
                report_type=report_type,
                force_refresh=force_refresh,
                query_id=task_id,
                trace_id=trace_id,
                send_notification=notify,
                progress_callback=_on_progress,
                skills=skills,
                analysis_phase=analysis_phase,
                query_source=query_source,
                portfolio_context=portfolio_context,
                report_language=report_language,
                analysis_target=analysis_target,
            )
            reset_run_diagnostic_context(diag_token)
            diag_token = None
            
            if result:
                # Set task status to completed
                with self._data_lock:
                    task = self._tasks.get(task_id)
                    if task:
                        task.status = TaskStatus.COMPLETED
                        task.progress = 100
                        task.completed_at = datetime.now()
                        task.result = result
                        task.message = "Analysis completed"
                        task.stock_name = result.get("stock_name", task.stock_name)
                        
                        # Remove from the in-analysis set (using the key frozen at submit time to avoid inconsistent leftovers)
                        dedupe_key = task.dedupe_key or _dedupe_task_key(
                            task.stock_code,
                            getattr(task, "analysis_target", None),
                        )
                        if dedupe_key in self._analyzing_stocks:
                            del self._analyzing_stocks[dedupe_key]
                
                self._broadcast_event("task_completed", task.to_dict())
                logger.info(f"[TaskQueue] 任务完成: {task_id} ({stock_code})")
                
                # Clean up expired tasks
                self._cleanup_old_tasks()
                
                return result
            else:
                # Chuỗi này đi ra UI Run Flow qua ``task.error`` -> sự kiện ``task_failed``.
                # This string reaches the Run Flow ``task_failed`` event via ``task.error``.
                raise Exception(
                    service.last_error
                    or get_analysis_error_text("empty_result_generic", report_language)
                )
                
        except Exception as e:
            if "diag_token" in locals():
                reset_run_diagnostic_context(diag_token)
            error_msg = str(e)
            logger.error(f"[TaskQueue] 任务失败: {task_id} ({stock_code}), 错误: {error_msg}")
            
            with self._data_lock:
                task = self._tasks.get(task_id)
                if task:
                    task.status = TaskStatus.FAILED
                    task.completed_at = datetime.now()
                    task.error = error_msg[:200]  # Truncate the error message
                    task.message = f"Analysis failed: {error_msg[:50]}"
                    
                    # Remove from the in-analysis set (using the key frozen at submit time to avoid inconsistent leftovers)
                    dedupe_key = task.dedupe_key or _dedupe_task_key(
                        task.stock_code,
                        getattr(task, "analysis_target", None),
                    )
                    if dedupe_key in self._analyzing_stocks:
                        del self._analyzing_stocks[dedupe_key]
            
            self._broadcast_event("task_failed", task.to_dict())
            
            # Clean up expired tasks
            self._cleanup_old_tasks()
            
            return None

    def _execute_background_task(
        self,
        task_id: str,
        run_task: Callable[[], Optional[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """
        Run a generic background task (supports custom run logic)

        Args:
            task_id: Task ID
            run_task: Task execution function

        Returns:
            Task execution result dict (optional)
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            trace_id = task.trace_id or task_id
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "Task running"
            task.progress = 10
            self._broadcast_event("task_started", task.to_dict())

        try:
            diag_token = None
            if get_current_diagnostic_context() is None:
                diag_token = activate_run_diagnostic_context(
                    trace_id=trace_id,
                    task_id=task_id,
                    query_id=task_id,
                    stock_code=task.stock_code,
                    trigger_source="api",
                    event_sink=lambda event: self.append_task_flow_event(task_id, event),
                )
            try:
                result = run_task()
            finally:
                reset_run_diagnostic_context(diag_token)
            if result is None:
                raise RuntimeError("任务返回空结果，未生成可持久化内容")

            with self._data_lock:
                task = self._tasks.get(task_id)
                if task:
                    task.status = TaskStatus.COMPLETED
                    task.progress = 100
                    task.completed_at = datetime.now()
                    task.result = result
                    task.message = "Task execution completed"

            self._broadcast_event("task_completed", task.to_dict())
            logger.info(f"[TaskQueue] 自定义任务完成: {task_id}")

            self._cleanup_old_tasks()
            return result

        except Exception as e:  # pragma: no cover - behavior verified in downstream tests
            error_msg = str(e)
            logger.error(
                f"[TaskQueue] 自定义任务失败: {task_id}, 错误: {error_msg}"
            )

            with self._data_lock:
                task = self._tasks.get(task_id)
                if task:
                    task.status = TaskStatus.FAILED
                    task.completed_at = datetime.now()
                    task.error = error_msg[:200]
                    task.message = f"Task failed: {error_msg[:80]}"

            if task:
                self._broadcast_event("task_failed", task.to_dict())

            self._cleanup_old_tasks()
            return None
    
    def _cleanup_old_tasks(self) -> int:
        """
        Clean up expired completed tasks

        Keeps the most recent _max_history tasks

        Returns:
            Number of tasks cleaned up
        """
        with self._data_lock:
            if len(self._tasks) <= self._max_history:
                return 0
            
            # Sort by time and delete old completed tasks
            completed_tasks = sorted(
                [t for t in self._tasks.values()
                 if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)],
                key=lambda t: t.created_at
            )
            
            to_remove = len(self._tasks) - self._max_history
            removed = 0
            
            for task in completed_tasks[:to_remove]:
                del self._tasks[task.task_id]
                if task.task_id in self._futures:
                    del self._futures[task.task_id]
                removed += 1
            
            if removed > 0:
                logger.debug(f"[TaskQueue] 清理了 {removed} 个过期任务")
            
            return removed
    
    # ========== SSE event broadcasting ==========
    
    def subscribe(self, queue: 'AsyncQueue') -> None:
        """
        Subscribe to task events

        Args:
            queue: asyncio.Queue instance that receives events
        """
        with self._subscribers_lock:
            self._subscribers.append(queue)
            # Capture the current event loop (should be called from an async context on the main thread)
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                # If not in an async context, try to get the event loop
                try:
                    self._main_loop = asyncio.get_event_loop()
                except RuntimeError:
                    pass
            logger.debug(f"[TaskQueue] 新订阅者加入，当前订阅者数: {len(self._subscribers)}")
    
    def unsubscribe(self, queue: 'AsyncQueue') -> None:
        """
        Unsubscribe from task events

        Args:
            queue: The asyncio.Queue instance to unsubscribe
        """
        with self._subscribers_lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)
                logger.debug(f"[TaskQueue] 订阅者离开，当前订阅者数: {len(self._subscribers)}")
    
    def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Broadcast an event to all subscribers

        Uses call_soon_threadsafe for cross-thread safety

        Args:
            event_type: Event type
            data: Event data
        """
        event = {"type": event_type, "data": data}
        
        with self._subscribers_lock:
            subscribers = self._subscribers.copy()
            loop = self._main_loop
        
        if not subscribers:
            return
        
        if loop is None:
            logger.warning("[TaskQueue] 无法广播事件：主事件循环未设置")
            return
        
        for queue in subscribers:
            try:
                # Use call_soon_threadsafe to put the event into the asyncio queue
                # This is the safe way to send messages from a worker thread to the main event loop
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError as e:
                # Event loop is closed
                logger.debug(f"[TaskQueue] 广播事件跳过（循环已关闭）: {e}")
            except Exception as e:
                logger.warning(f"[TaskQueue] 广播事件失败: {e}")
    
    # ========== Cleanup ==========
    
    def shutdown(self) -> None:
        """Shut down the task queue"""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
            logger.info("[TaskQueue] 线程池已关闭")


# ========== Convenience functions ==========

def get_task_queue() -> AnalysisTaskQueue:
    """
    Get the task queue singleton

    Returns:
        AnalysisTaskQueue instance
    """
    queue = AnalysisTaskQueue()
    try:
        from src.config import get_config

        config = get_config()
        target_workers = max(1, int(getattr(config, "max_workers", queue.max_workers)))
        queue.sync_max_workers(target_workers, log=False)
    except Exception as exc:
        logger.debug("[TaskQueue] 读取 MAX_WORKERS 失败，使用当前并发设置: %s", exc)

    return queue
