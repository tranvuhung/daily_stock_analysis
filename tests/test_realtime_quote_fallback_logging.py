# -*- coding: utf-8 -*-
"""Kiểm thử hồi quy ngữ nghĩa log khi chuyển nguồn giá realtime (Việt Nam).

Regression tests for realtime quote fallback logging semantics (Vietnam).
"""

import asyncio
import importlib.util
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType


class _DummyFetcher:
    def __init__(self, name: str, priority: int, result=None, error: Exception | None = None):
        self.name = name
        self.priority = priority
        self._result = result
        self._error = error

    def get_realtime_quote(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._result


def _make_quote(
    code: str = "HPG",
    name: str = "Hòa Phát",
    source: RealtimeSource = RealtimeSource.FALLBACK,
    **overrides,
) -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(
        code=code,
        name=name,
        source=source,
        price=27500.0,
        change_pct=1.2,
        **overrides,
    )


def _make_pipeline(enable_realtime_quote: bool, realtime_quote=None) -> StockAnalysisPipeline:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        enable_realtime_quote=enable_realtime_quote,
        enable_chip_distribution=True,
        agent_mode=False,
        agent_skills=[],
        fundamental_stage_timeout_seconds=1.5,
        report_language="vi",
    )
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "Hòa Phát"
    pipeline.fetcher_manager.get_realtime_quote.return_value = realtime_quote
    pipeline.fetcher_manager.get_chip_distribution.return_value = None
    pipeline.fetcher_manager.get_fundamental_context.return_value = {
        "source_chain": [],
        "coverage": {},
    }
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {
        "source_chain": [],
        "coverage": {},
    }
    pipeline.db = MagicMock()
    pipeline.db.save_fundamental_snapshot.return_value = None
    pipeline.db.get_data_range.return_value = []
    pipeline.db.get_analysis_context.return_value = {}
    pipeline.search_service = SimpleNamespace(is_available=False)
    pipeline.social_sentiment_service = SimpleNamespace(is_available=False)
    pipeline.query_source = "system"
    pipeline.trend_analyzer = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.analyzer.analyze.return_value = None
    pipeline._attach_belong_boards_to_fundamental_context = MagicMock(side_effect=lambda code, ctx: ctx)
    pipeline._enhance_context = MagicMock(return_value={"realtime": {}})
    pipeline.save_context_snapshot = False
    return pipeline


@patch("src.config.get_config")
def test_manager_does_not_warn_when_fallback_source_succeeds(mock_get_config, caplog):
    # SSI hỏng thì VCI phục vụ, không được log cảnh báo.
    # SSI fails and VCI serves the quote without warning logs.
    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="ssi,vci",
    )
    manager = DataFetcherManager(
        fetchers=[
            _DummyFetcher("SsiFetcher", 0, error=RuntimeError("ssi timeout")),
            _DummyFetcher("VciFetcher", 1, result=_make_quote()),
        ]
    )

    with caplog.at_level(logging.INFO):
        quote = manager.get_realtime_quote("HPG")

    assert quote is not None
    assert quote.name == "Hòa Phát"
    assert quote.fetched_at is not None
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@patch("src.config.get_config")
def test_manager_respects_configured_vn_source_order(mock_get_config):
    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="vci,ssi",
    )
    vci_quote = _make_quote(name="VCI")
    ssi_quote = _make_quote(name="SSI")
    manager = DataFetcherManager(
        fetchers=[
            _DummyFetcher("SsiFetcher", 0, result=ssi_quote),
            _DummyFetcher("VciFetcher", 1, result=vci_quote),
        ]
    )

    assert manager.get_realtime_quote("HPG") is vci_quote


@patch("src.config.get_config")
def test_manager_returns_none_for_non_vn_code_without_fetcher_calls(mock_get_config):
    mock_get_config.return_value = SimpleNamespace(enable_realtime_quote=True, realtime_source_priority="")
    ssi = _DummyFetcher("SsiFetcher", 0, error=AssertionError("must not be called"))
    manager = DataFetcherManager(fetchers=[ssi])

    assert manager.get_realtime_quote("AAPL") is None
    assert manager.get_realtime_quote("600519") is None


@patch("src.config.get_config")
def test_manager_drops_invalid_provider_timestamp_before_return(mock_get_config):
    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="ssi",
        realtime_cache_ttl=600,
    )
    raw_quote = _make_quote(provider_timestamp="not-a-date")
    manager = DataFetcherManager(fetchers=[_DummyFetcher("SsiFetcher", 0, result=raw_quote)])

    quote = manager.get_realtime_quote("HPG")

    assert quote is raw_quote
    assert quote.fetched_at is not None
    assert quote.provider_timestamp is None
    assert quote.stale_seconds is None
    assert quote.is_stale is None


def test_pipeline_warns_once_when_all_realtime_sources_fail(caplog):
    pipeline = _make_pipeline(enable_realtime_quote=True, realtime_quote=None)

    with caplog.at_level(logging.INFO):
        result = pipeline.analyze_stock("HPG", ReportType.SIMPLE, "q1")

    assert result is None
    pipeline.fetcher_manager.get_stock_name.assert_called_once_with("HPG", allow_realtime=False)
    pipeline.fetcher_manager.get_realtime_quote.assert_called_once_with("HPG", log_final_failure=False)
    downgrade_logs = [
        record.message
        for record in caplog.records
        if "giá đóng cửa lịch sử để tiếp tục phân tích" in record.message
    ]
    assert downgrade_logs == ["Hòa Phát(HPG) mọi nguồn báo giá realtime đều không khả dụng, đã hạ cấp sang giá đóng cửa lịch sử để tiếp tục phân tích"]


@patch("src.config.get_config")
def test_event_monitor_keeps_manager_failure_summary_for_direct_quote_call(mock_get_config, caplog):
    from src.agent.events import EventMonitor, PriceAlert

    mock_get_config.return_value = SimpleNamespace(
        enable_realtime_quote=True,
        realtime_source_priority="ssi",
    )
    manager = DataFetcherManager(
        fetchers=[
            _DummyFetcher("SsiFetcher", 0, error=RuntimeError("ssi timeout")),
        ]
    )
    monitor = EventMonitor()
    rule = PriceAlert(stock_code="HPG", direction="above", price=30000.0)

    async def _run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch("data_provider.DataFetcherManager", return_value=manager), patch(
        "src.agent.events.asyncio.to_thread", new=_run_inline
    ), caplog.at_level(logging.INFO):
        result = asyncio.run(monitor._check_price(rule))

    assert result is None
    assert "[Giá realtime] HPG không có nguồn dữ liệu khả dụng" in caplog.text


def test_pipeline_logs_disabled_realtime_once_without_fetching_quote(caplog):
    pipeline = _make_pipeline(enable_realtime_quote=False, realtime_quote=_make_quote())

    with caplog.at_level(logging.INFO):
        result = pipeline.analyze_stock("HPG", ReportType.SIMPLE, "q1")

    assert result is None
    pipeline.fetcher_manager.get_stock_name.assert_called_once_with("HPG", allow_realtime=False)
    pipeline.fetcher_manager.get_realtime_quote.assert_not_called()
    downgrade_logs = [
        record.message
        for record in caplog.records
        if "giá đóng cửa lịch sử để tiếp tục phân tích" in record.message
    ]
    assert downgrade_logs == ["Hòa Phát(HPG) báo giá realtime đã tắt, dùng giá đóng cửa lịch sử để tiếp tục phân tích"]
