# -*- coding: utf-8 -*-
"""
===================================
Async task service layer
===================================

Responsibilities:
1. Manage async analysis tasks (thread pool)
2. Run stock analysis and push results
3. Query task status and history

Migrated from the AnalysisService class in web/services.py
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Dict, Any, List, Union

from src.enums import ReportType
from src.storage import get_db
from bot.models import BotMessage
from src.services.stock_code_utils import resolve_index_stock_code_for_analysis
from src.services.stock_list_parser import AnalysisTarget, ParseStatus

logger = logging.getLogger(__name__)


class TaskService:
    """
    Async task service

    Responsible for:
    1. Managing async analysis tasks
    2. Running stock analysis
    3. Triggering notification pushes
    """

    _instance: Optional['TaskService'] = None
    _lock = threading.Lock()

    def __init__(self, max_workers: int = 3):
        self._executor: Optional[ThreadPoolExecutor] = None
        self._max_workers = max_workers
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._tasks_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'TaskService':
        """Get the singleton instance"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def executor(self) -> ThreadPoolExecutor:
        """Get or create the thread pool"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="analysis_"
            )
        return self._executor

    def submit_analysis(
        self,
        code: str,
        report_type: Union[ReportType, str] = ReportType.SIMPLE,
        source_message: Optional[BotMessage] = None,
        save_context_snapshot: Optional[bool] = None,
        query_source: str = "bot",
        analysis_target: Optional[AnalysisTarget] = None
    ) -> Dict[str, Any]:
        """
        Submit an async analysis task

        Args:
            code: Stock code
            report_type: Report type enum
            source_message: Source message (used for replies)
            save_context_snapshot: Whether to save a context snapshot
            query_source: Task source identifier (bot/api/cli/system)
            analysis_target: Optional structured analysis target (for INDEX, use
                ``target.canonical_id`` as-is and skip stock code normalization so
                ``sh000016`` is not rewritten to ``SH000016``)

        Returns:
            Task info dict
        """
        # Ensure report_type is an enum
        if isinstance(report_type, str):
            report_type = ReportType.from_str(report_type)

        if (
            analysis_target is not None
            and analysis_target.asset_type != ParseStatus.INDEX
        ):
            raise ValueError("analysis_target must be an INDEX target")
        if analysis_target is not None:
            # When an INDEX target is provided, use canonical_id as-is instead of the stock code
            # normalization path (so ``sh000016`` is not rewritten to ``SH000016``).
            normalized_code = analysis_target.canonical_id
        else:
            normalized_code = resolve_index_stock_code_for_analysis(code)
        if not normalized_code:
            raise ValueError("Mã cổ phiếu không được để trống hoặc chỉ chứa khoảng trắng")

        task_id = f"{normalized_code}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

        # Submit to the thread pool
        self.executor.submit(
            self._run_analysis,
            normalized_code,
            task_id,
            report_type,
            source_message,
            save_context_snapshot,
            query_source,
            analysis_target
        )

        logger.info(
            f"[TaskService] Đã gửi tác vụ phân tích cổ phiếu {normalized_code}, "
            f"task_id={task_id}, report_type={report_type.value}"
        )

        return {
            "success": True,
            "message": "Đã gửi tác vụ phân tích, sẽ chạy bất đồng bộ và gửi thông báo",
            "code": normalized_code,
            "task_id": task_id,
            "report_type": report_type.value
        }

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status"""
        with self._tasks_lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent tasks"""
        with self._tasks_lock:
            tasks = list(self._tasks.values())
        # Sort by start time, newest first
        tasks.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        return tasks[:limit]

    def get_analysis_history(
        self,
        code: Optional[str] = None,
        query_id: Optional[str] = None,
        days: int = 30,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get analysis history records"""
        db = get_db()
        records = db.get_analysis_history(code=code, query_id=query_id, days=days, limit=limit)
        return [r.to_dict() for r in records]

    def _run_analysis(
        self,
        code: str,
        task_id: str,
        report_type: ReportType = ReportType.SIMPLE,
        source_message: Optional[BotMessage] = None,
        save_context_snapshot: Optional[bool] = None,
        query_source: str = "bot",
        analysis_target: Optional[AnalysisTarget] = None
    ) -> Dict[str, Any]:
        """
        Run single-stock analysis

        Internal method, runs in the thread pool
        """
        # Initialize task status
        with self._tasks_lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "code": code,
                "status": "running",
                "start_time": datetime.now().isoformat(),
                "result": None,
                "error": None,
                "report_type": report_type.value
            }

        try:
            # Lazy import to avoid circular dependencies
            from src.config import get_config
            from main import StockAnalysisPipeline

            logger.info(f"[TaskService] Bắt đầu phân tích cổ phiếu: {code}")

            # Create the analysis pipeline
            config = get_config()
            pipeline = StockAnalysisPipeline(
                config=config,
                max_workers=1,
                source_message=source_message,
                query_id=task_id,
                query_source=query_source,
                save_context_snapshot=save_context_snapshot
            )

            # Run single-stock analysis (with per-stock push enabled)
            process_kwargs = {
                "code": code,
                "skip_analysis": False,
                "single_stock_notify": True,
                "report_type": report_type,
            }
            if analysis_target is not None:
                process_kwargs["analysis_target"] = analysis_target
            result = pipeline.process_single_stock(**process_kwargs)

            if result and result.success:
                result_data = {
                    "code": result.code,
                    "name": result.name,
                    "sentiment_score": result.sentiment_score,
                    "operation_advice": result.operation_advice,
                    "trend_prediction": result.trend_prediction,
                    "analysis_summary": result.analysis_summary,
                }

                with self._tasks_lock:
                    self._tasks[task_id].update({
                        "status": "completed",
                        "end_time": datetime.now().isoformat(),
                        "result": result_data
                    })

                logger.info(f"[TaskService] Phân tích cổ phiếu {code} hoàn tất: {result.operation_advice}")
                return {"success": True, "task_id": task_id, "result": result_data}
            else:
                fail_message = "Phân tích trả về kết quả rỗng"
                if result is not None:
                    fail_message = result.error_message or fail_message
                with self._tasks_lock:
                    self._tasks[task_id].update({
                        "status": "failed",
                        "end_time": datetime.now().isoformat(),
                        "error": fail_message
                    })

                logger.warning(f"[TaskService] Phân tích cổ phiếu {code} thất bại: {fail_message}")
                return {"success": False, "task_id": task_id, "error": fail_message}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TaskService] Phân tích cổ phiếu {code} gặp lỗi: {error_msg}")

            with self._tasks_lock:
                self._tasks[task_id].update({
                    "status": "failed",
                    "end_time": datetime.now().isoformat(),
                    "error": error_msg
                })

            return {"success": False, "task_id": task_id, "error": error_msg}


# ============================================================
# Convenience functions
# ============================================================

def get_task_service() -> TaskService:
    """Get the task service singleton"""
    return TaskService.get_instance()
