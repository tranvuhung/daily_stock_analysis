# -*- coding: utf-8 -*-
"""
===================================
Autocomplete PR0 Unit Tests
===================================

Test backend data contract extensions:
- AnalyzeRequest model extension
- TaskInfo dataclass extension
- Task queue accepts new fields
- Backward compatibility
"""

from api.v1.schemas.analysis import AnalyzeRequest
from concurrent.futures import Future
from src.services.task_queue import TaskInfo, get_task_queue, DuplicateTaskError, AnalysisTaskQueue


class TestAnalyzeRequest:
    """Test AnalyzeRequest model"""

    def test_analyze_request_with_new_fields(self):
        """Test that AnalyzeRequest accepts new fields"""
        request = AnalyzeRequest(
            stock_code="HPG",
            async_mode=True,
            stock_name="Hòa Phát",
            original_query="Hòa Phát",
            selection_source="autocomplete",
        )
        assert request.stock_code == "HPG"
        assert request.stock_name == "Hòa Phát"
        assert request.original_query == "Hòa Phát"
        assert request.selection_source == "autocomplete"

    def test_analyze_request_backward_compatible(self):
        """Test backward compatibility: works fine without new fields"""
        request = AnalyzeRequest(
            stock_code="HPG",
            async_mode=True,
        )
        assert request.stock_code == "HPG"
        assert request.stock_name is None
        assert request.original_query is None
        assert request.selection_source is None

    def test_analyze_request_accepts_strategy_skills_alias(self):
        """Test analysis requests accept both skills and legacy strategies."""
        request = AnalyzeRequest(
            stock_code="HPG",
            skills=["growth_quality"],
        )
        assert request.skills == ["growth_quality"]

        legacy_request = AnalyzeRequest(
            stock_code="HPG",
            strategies=["event_driven"],
        )
        assert legacy_request.skills == ["event_driven"]

    def test_analyze_request_validation_selection_source(self):
        """Test selection_source field validation"""
        # Valid selection_source values
        for source in ["manual", "autocomplete", "import", "image"]:
            request = AnalyzeRequest(
                stock_code="HPG",
                selection_source=source,
            )
            assert request.selection_source == source

    def test_analyze_request_with_multiple_stocks(self):
        """Test support for new fields in batch analysis"""
        request = AnalyzeRequest(
            stock_codes=["HPG", "VCB"],
            async_mode=True,
            stock_name="Danh mục lô",
            original_query="HPG,VCB",
            selection_source="import",
        )
        assert request.stock_codes == ["HPG", "VCB"]
        assert request.stock_name == "Danh mục lô"
        assert request.original_query == "HPG,VCB"
        assert request.selection_source == "import"


class TestTaskInfo:
    """Test TaskInfo dataclass"""

    def test_task_info_with_new_fields(self):
        """Test that TaskInfo contains new fields"""
        task = TaskInfo(
            task_id="test123",
            stock_code="HPG",
            stock_name="Hòa Phát",
            original_query="Hòa Phát",
            selection_source="autocomplete",
        )
        d = task.to_dict()
        assert "original_query" in d
        assert "selection_source" in d
        assert d["original_query"] == "Hòa Phát"
        assert d["selection_source"] == "autocomplete"

    def test_task_info_backward_compatible(self):
        """Test TaskInfo backward compatibility: works fine without new fields"""
        task = TaskInfo(
            task_id="test123",
            stock_code="HPG",
        )
        d = task.to_dict()
        assert d["original_query"] is None
        assert d["selection_source"] is None

    def test_task_info_copy_includes_new_fields(self):
        """Test that TaskInfo.copy() includes new fields"""
        task = TaskInfo(
            task_id="test123",
            stock_code="HPG",
            stock_name="Hòa Phát",
            original_query="Hòa Phát",
            selection_source="autocomplete",
        )
        copied = task.copy()
        assert copied.original_query == "Hòa Phát"
        assert copied.selection_source == "autocomplete"


class TestTaskQueue:
    """Test task queue"""

    def setup_method(self):
        self._original_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None

    def teardown_method(self):
        queue = AnalysisTaskQueue._instance
        if queue is not None and queue is not self._original_instance:
            executor = getattr(queue, "_executor", None)
            if executor is not None and hasattr(executor, "shutdown"):
                executor.shutdown(wait=False, cancel_futures=True)
        AnalysisTaskQueue._instance = self._original_instance

    @staticmethod
    def _build_queue():
        queue = AnalysisTaskQueue(max_workers=1)
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()
        return queue

    def test_task_queue_accepts_new_fields(self):
        """Test task queue accepts new fields"""
        queue = self._build_queue()
        tasks, _duplicates = queue.submit_tasks_batch(
            stock_codes=["HPG"],
            stock_name="Hòa Phát",
            original_query="Hòa Phát",
            selection_source="autocomplete",
        )
        assert len(tasks) == 1
        assert tasks[0].stock_name == "Hòa Phát"
        assert tasks[0].original_query == "Hòa Phát"
        assert tasks[0].selection_source == "autocomplete"

    def test_task_queue_backward_compatible(self):
        """Test task queue backward compatibility: works fine without new fields"""
        queue = self._build_queue()
        tasks, _duplicates = queue.submit_tasks_batch(
            stock_codes=["HPG"],
        )
        assert len(tasks) == 1
        assert tasks[0].original_query is None
        assert tasks[0].selection_source is None

    def test_task_queue_batch_with_new_fields(self):
        """Test support for new fields during batch submission"""
        queue = self._build_queue()
        tasks, _duplicates = queue.submit_tasks_batch(
            stock_codes=["HPG", "VCB"],
            stock_name="Danh mục lô",
            original_query="HPG,VCB",
            selection_source="import",
        )
        assert len(tasks) == 2
        for task in tasks:
            assert task.stock_name == "Danh mục lô"
            assert task.original_query == "HPG,VCB"
            assert task.selection_source == "import"

    def test_task_queue_duplicate_detection_with_new_fields(self):
        """Test that new fields do not affect duplicate submission detection logic"""
        queue = self._build_queue()
        stock_code = "HPG"

        # First submission
        tasks1, dups1 = queue.submit_tasks_batch(
            stock_codes=[stock_code],
            stock_name="Hòa Phát",
            original_query="Hòa Phát",
            selection_source="autocomplete",
        )
        assert len(tasks1) == 1
        assert len(dups1) == 0

        # Second submission (should be rejected)
        tasks2, dups2 = queue.submit_tasks_batch(
            stock_codes=[stock_code],
            stock_name="Hòa Phát",
            original_query="Hòa Phát",
            selection_source="manual",  # Rejection still applies even if selection_source differs
        )
        assert len(tasks2) == 0
        assert len(dups2) == 1
        assert isinstance(dups2[0], DuplicateTaskError)

class TestIntegration:
    """Integration Tests"""

    def setup_method(self):
        self._original_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None

    def teardown_method(self):
        queue = AnalysisTaskQueue._instance
        if queue is not None and queue is not self._original_instance:
            executor = getattr(queue, "_executor", None)
            if executor is not None and hasattr(executor, "shutdown"):
                executor.shutdown(wait=False, cancel_futures=True)
        AnalysisTaskQueue._instance = self._original_instance

    def test_end_to_end_flow_with_autocomplete(self):
        """Test end-to-end flow: autocomplete -> analysis request -> task creation"""
        # Simulate request after autocomplete
        request = AnalyzeRequest(
            stock_code="HPG.VN",
            async_mode=True,
            stock_name="Hòa Phát",
            original_query="Hòa Phát",
            selection_source="autocomplete",
            report_type="detailed",
        )

        # Submit to task queue
        queue = get_task_queue()
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()
        tasks, _duplicates = queue.submit_tasks_batch(
            stock_codes=[request.stock_code],
            stock_name=request.stock_name,
            original_query=request.original_query,
            selection_source=request.selection_source,
            report_type=request.report_type,
        )

        assert len(tasks) == 1
        task = tasks[0]
        assert task.stock_code == "HPG.VN"
        assert task.stock_name == "Hòa Phát"
        assert task.original_query == "Hòa Phát"
        assert task.selection_source == "autocomplete"
        assert task.report_type == "detailed"

    def test_end_to_end_flow_manual_input(self):
        """Test end-to-end flow: manual input -> analysis request -> task creation"""
        # Simulate manual input request
        request = AnalyzeRequest(
            stock_code="HPG",
            async_mode=True,
            selection_source="manual",
        )

        # Submit to task queue
        queue = get_task_queue()
        queue._executor = type("ExecutorStub", (), {"submit": lambda self, *args, **kwargs: Future()})()
        tasks, _duplicates = queue.submit_tasks_batch(
            stock_codes=[request.stock_code],
            selection_source=request.selection_source,
            report_type=request.report_type,
        )

        assert len(tasks) == 1
        task = tasks[0]
        assert task.stock_code == "HPG"
        assert task.selection_source == "manual"
