# -*- coding: utf-8 -*-
"""Kiểm thử dịch vụ tổng quan năng lực dữ liệu (ma trận nguồn Việt Nam).

Tests for the data capability overview service (Vietnam provider matrix).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.data_capability_service import DataCapabilityService


class _Fetcher:
    def __init__(
        self,
        name: str,
        priority: int,
        *,
        available=None,
        last_error: str = "",
        is_available=None,
        is_available_for_request=None,
    ) -> None:
        self.name = name
        self.priority = priority
        if available is not None:
            self._available = available
        if last_error:
            self.last_error = last_error
        if is_available is not None:
            self.is_available = lambda: is_available
        if is_available_for_request is not None:
            self.is_available_for_request = lambda _capability="": is_available_for_request


class _FetcherManager:
    def __init__(self, fetchers) -> None:
        self._fetchers = fetchers

    def _get_fetchers_snapshot(self):
        return list(self._fetchers)


class _RuntimeScheduler:
    def __init__(self, active: bool) -> None:
        self.active = active

    def is_background_task_active(self, name: str) -> bool:
        return self.active and name == "agent_event_monitor"


def _config(**overrides):
    values = {
        "enable_realtime_quote": True,
        "enable_fundamental_pipeline": True,
        "realtime_source_priority": "ssi,vci",
        "agent_event_monitor_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _default_manager(**availability):
    """VCI(P0) -> SSI(P1) -> YFinance(P4), mặc định đều khả dụng / all available by default."""

    return _FetcherManager([
        _Fetcher("VciFetcher", 0, available=availability.get("vci", True)),
        _Fetcher("SsiFetcher", 1, available=availability.get("ssi", True)),
        _Fetcher("YfinanceFetcher", 4, available=availability.get("yfinance", True)),
    ])


def _dataset(overview, name: str):
    return next(item for item in overview["datasets"] if item["dataset"] == name)


def _provider(overview, name: str):
    return next(item for item in overview["providers"] if item["name"] == name)


def _priorities(overview):
    return {item["scenario"]: item for item in overview["priorities"]}


@pytest.fixture(autouse=True)
def _reset_daily_health(monkeypatch):
    # Trạng thái ngắt mạch nến ngày là cấp class, reset để test độc lập
    # Daily circuit state is class-level; reset it so tests stay independent
    from data_provider.base import DataFetcherManager

    monkeypatch.delenv("VNSTOCK_ENABLED", raising=False)
    monkeypatch.delenv("FIINTRADE_ENABLED", raising=False)
    monkeypatch.delenv("FIINTRADE_ORIGIN", raising=False)
    DataFetcherManager.reset_daily_source_health()
    yield
    DataFetcherManager.reset_daily_source_health()


def test_provider_matrix_is_vietnam_only() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(),
    ).get_overview()

    assert [item["name"] for item in overview["providers"]] == ["fiintrade", "vci", "ssi", "vnstock", "yfinance"]
    for provider in overview["providers"]:
        assert provider["markets"] == ["vn"]
        for markets in provider["dataset_markets"].values():
            assert markets == ["vn"]

    assert set(_provider(overview, "vci")["datasets"]) == {
        "quote.realtime",
        "kline.daily",
        "index.daily",
        "market.overview",
    }
    assert set(_provider(overview, "ssi")["datasets"]) == {
        "quote.realtime",
        "kline.daily",
        "market.overview",
    }
    assert set(_provider(overview, "fiintrade")["datasets"]) == {
        "quote.realtime",
        "kline.daily",
        "index.daily",
    }
    assert _provider(overview, "vnstock")["datasets"] == ["kline.daily"]
    # Yahoo chỉ là dự phòng nến ngày / Yahoo is a daily-only fallback
    assert _provider(overview, "yfinance")["datasets"] == ["kline.daily"]

    assert [item["dataset"] for item in overview["datasets"]] == [
        "quote.realtime",
        "kline.daily",
        "index.daily",
        "market.overview",
        "financial.snapshot",
        "news.events",
        "alert.monitor",
        "portfolio.account",
    ]
    assert [item["scenario"] for item in overview["priorities"]] == [
        "vn.realtime",
        "daily.generic",
        "vn.index.daily",
        "market.overview",
        "news.events",
    ]


def test_builtin_providers_report_runtime_status_and_priority() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(ssi=False),
    ).get_overview()

    vci = _provider(overview, "vci")
    ssi = _provider(overview, "ssi")
    assert vci["enabled"] is True and vci["configured"] is True
    assert vci["status"] == "ok"
    assert vci["priority"] == 0
    assert ssi["status"] == "unavailable"
    assert ssi["warnings"] == ["provider_marked_unavailable"]


def test_provider_runtime_probe_preserves_unknown_until_checked() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([
            _Fetcher("VciFetcher", 0),
            _Fetcher("SsiFetcher", 1, is_available=False),
        ]),
    ).get_overview()

    assert _provider(overview, "vci")["status"] == "unknown"
    assert _provider(overview, "vci")["warnings"] == ["runtime_probe_not_performed"]
    assert _provider(overview, "ssi")["status"] == "unavailable"


def test_provider_runtime_probe_honors_request_time_unavailable_over_cached_flag() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([
            _Fetcher("SsiFetcher", 1, available=True, is_available_for_request=False),
        ]),
    ).get_overview()

    assert _provider(overview, "ssi")["status"] == "unavailable"
    assert _provider(overview, "ssi")["warnings"] == ["provider_marked_unavailable"]


def test_builtin_provider_missing_from_manager_is_reported_not_initialized() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([]),
    ).get_overview()

    assert _provider(overview, "vci")["status"] == "unavailable"
    assert _provider(overview, "vci")["warnings"] == ["provider_not_initialized"]


def test_vnstock_is_unconfigured_unless_opted_in(monkeypatch) -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(),
    ).get_overview()
    vnstock = _provider(overview, "vnstock")
    assert vnstock["configured"] is False
    assert vnstock["enabled"] is False
    assert vnstock["status"] == "unconfigured"

    monkeypatch.setenv("VNSTOCK_ENABLED", "true")
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([_Fetcher("VnstockFetcher", 2, available=True)]),
    ).get_overview()
    vnstock = _provider(overview, "vnstock")
    assert vnstock["configured"] is True
    assert vnstock["enabled"] is True
    assert vnstock["status"] == "ok"
    assert vnstock["priority"] == 2


def test_fiintrade_requires_origin(monkeypatch) -> None:
    # FiinTrade mặc định bật nhưng chỉ được coi là đã cấu hình khi có origin.
    # FiinTrade is on by default but counts as configured only with an origin.
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(),
    ).get_overview()
    assert _provider(overview, "fiintrade")["status"] == "unconfigured"

    monkeypatch.setenv("FIINTRADE_ORIGIN", "https://partner.example")
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([_Fetcher("FiinTradeFetcher", -1, available=True)]),
    ).get_overview()
    fiintrade = _provider(overview, "fiintrade")
    assert fiintrade["configured"] is True
    assert fiintrade["status"] == "ok"
    assert fiintrade["priority"] == -1


def test_realtime_priority_puts_fiintrade_first_only_when_enabled(monkeypatch) -> None:
    # Khớp DataFetcherManager._vn_realtime_source_order: FiinTrade bật mà không ghi
    # trong cấu hình thì đứng đầu; tắt thì token bị bỏ qua kèm cảnh báo.
    service = DataCapabilityService(config=_config(), fetcher_manager=_default_manager())
    providers, warnings = service._vn_realtime_priority()
    assert providers == ["ssi", "vci"]

    monkeypatch.setenv("FIINTRADE_ORIGIN", "https://partner.example")
    providers, warnings = service._vn_realtime_priority()
    assert providers == ["fiintrade", "ssi", "vci"]
    assert warnings == []


def test_market_disabled_marks_providers_unconfigured() -> None:
    with patch("src.core.markets.is_market_enabled", return_value=False):
        overview = DataCapabilityService(
            config=_config(),
            fetcher_manager=_default_manager(),
        ).get_overview()

    for provider in overview["providers"]:
        assert provider["enabled"] is False
        assert provider["status"] == "unconfigured"
        assert provider["warnings"] == ["market_disabled"]


def test_realtime_priority_follows_config_and_appends_missing_defaults() -> None:
    overview = DataCapabilityService(
        config=_config(realtime_source_priority="vci"),
        fetcher_manager=_default_manager(),
    ).get_overview()

    realtime = _priorities(overview)["vn.realtime"]
    assert realtime["providers"] == ["vci", "ssi"]
    assert realtime["source"] == "Config.realtime_source_priority"
    assert realtime["warnings"] == []

    quality = _dataset(overview, "quote.realtime")
    assert quality["status"] == "ok"
    assert quality["source"] == "vci"
    assert quality["coverage"]["markets"]["vn"]["source"] == "vci"


def test_realtime_priority_flags_ignored_legacy_tokens() -> None:
    overview = DataCapabilityService(
        config=_config(realtime_source_priority="tencent,ssi,efinance"),
        fetcher_manager=_default_manager(),
    ).get_overview()

    realtime = _priorities(overview)["vn.realtime"]
    assert realtime["providers"] == ["ssi", "vci"]
    assert realtime["warnings"] == ["ignored_source:tencent", "ignored_source:efinance"]
    assert "vn.realtime:ignored_source:tencent" in overview["warnings"]


def test_realtime_dataset_degrades_when_ssi_is_unavailable() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(ssi=False),
    ).get_overview()

    quality = _dataset(overview, "quote.realtime")
    assert quality["status"] == "degraded"
    assert quality["source"] == "vci"
    assert quality["fallback_from"] == ["vn:ssi"]
    assert "vn:source_status:ssi:unavailable" in quality["warnings"]


def test_daily_priority_follows_fetcher_order_and_excludes_request_unavailable() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([
            _Fetcher("YfinanceFetcher", 4, available=True),
            _Fetcher("SsiFetcher", 1, available=True),
            _Fetcher("VciFetcher", 0, available=True, is_available_for_request=False),
        ]),
    ).get_overview()

    priorities = _priorities(overview)
    assert priorities["daily.generic"]["providers"] == ["ssi", "yfinance"]
    daily = _dataset(overview, "kline.daily")
    assert daily["status"] == "ok"
    assert daily["source"] == "ssi"
    assert daily["fallback_from"] == []
    # VCI là nguồn duy nhất cho chỉ số / VCI is the only index source
    assert priorities["vn.index.daily"]["providers"] == []
    index_daily = _dataset(overview, "index.daily")
    assert index_daily["status"] == "unavailable"
    assert "vn:request_available_priority_empty" in index_daily["warnings"]


def test_daily_circuit_open_falls_back_to_next_source() -> None:
    service = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(),
    )

    with patch(
        "data_provider.base.DataFetcherManager._is_daily_source_available",
        side_effect=lambda fetcher, market: not (fetcher.name == "VciFetcher" and market == "vn"),
    ):
        overview = service.get_overview()

    daily = _dataset(overview, "kline.daily")["coverage"]["markets"]["vn"]
    assert daily["status"] == "degraded"
    assert daily["source"] == "ssi"
    assert daily["fallback_from"] == ["vci"]
    assert daily["warnings"] == ["source_status:vci:cooldown"]

    index_daily = _dataset(overview, "index.daily")
    assert index_daily["status"] == "unavailable"
    assert "vn:source_status:vci:cooldown" in index_daily["warnings"]


def test_daily_route_stops_at_unprobed_preferred_source() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([
            _Fetcher("VciFetcher", 0),
            _Fetcher("SsiFetcher", 1, available=True),
        ]),
    ).get_overview()

    daily = _dataset(overview, "kline.daily")["coverage"]["markets"]["vn"]
    assert daily["status"] == "unknown"
    assert daily["source"] is None
    assert daily["fallback_from"] == []
    assert daily["warnings"] == ["source_status:vci:unknown"]


def test_market_overview_uses_vci_then_ssi() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(vci=False),
    ).get_overview()

    assert _priorities(overview)["market.overview"]["providers"] == ["vci", "ssi"]
    quality = _dataset(overview, "market.overview")
    assert quality["status"] == "degraded"
    assert quality["source"] == "ssi"
    assert quality["fallback_from"] == ["vn:vci"]


def test_fundamental_dataset_is_valuation_only_from_realtime_chain() -> None:
    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_default_manager(),
    ).get_overview()

    quality = _dataset(overview, "financial.snapshot")
    assert quality["status"] == "ok"
    assert quality["source"] == "ssi"
    assert "vn:valuation_only" in quality["warnings"]


def test_disabled_runtime_features_surface_dataset_quality_warnings() -> None:
    overview = DataCapabilityService(
        config=_config(enable_realtime_quote=False, enable_fundamental_pipeline=False),
        fetcher_manager=_default_manager(),
    ).get_overview()

    assert _dataset(overview, "quote.realtime")["status"] == "unavailable"
    assert _dataset(overview, "quote.realtime")["warnings"] == ["realtime_quote_disabled"]
    assert _dataset(overview, "financial.snapshot")["status"] == "unavailable"
    assert _dataset(overview, "financial.snapshot")["warnings"] == ["fundamental_pipeline_disabled"]
    assert _dataset(overview, "alert.monitor")["status"] == "unavailable"
    assert _dataset(overview, "alert.monitor")["source"] is None
    assert _dataset(overview, "alert.monitor")["warnings"] == ["agent_event_monitor_disabled"]
    assert _dataset(overview, "news.events")["source"] is None
    assert _dataset(overview, "portfolio.account")["status"] == "ok"


def test_fundamental_dataset_unavailable_when_only_realtime_is_disabled() -> None:
    overview = DataCapabilityService(
        config=_config(enable_realtime_quote=False),
        fetcher_manager=_default_manager(),
    ).get_overview()

    quality = _dataset(overview, "financial.snapshot")
    assert quality["status"] == "unavailable"
    assert quality["warnings"] == ["realtime_quote_disabled"]


def test_enabled_alert_monitor_requires_a_registered_live_scheduler_task() -> None:
    quality = _dataset(
        DataCapabilityService(
            config=_config(agent_event_monitor_enabled=True),
            fetcher_manager=_FetcherManager([]),
            runtime_scheduler=_RuntimeScheduler(active=False),
        ).get_overview(),
        "alert.monitor",
    )

    assert quality["status"] == "unavailable"
    assert quality["source"] is None
    assert quality["warnings"] == ["agent_event_monitor_not_running"]


def test_running_alert_monitor_surfaces_local_dataset_as_available() -> None:
    quality = _dataset(
        DataCapabilityService(
            config=_config(agent_event_monitor_enabled=True),
            fetcher_manager=_FetcherManager([]),
            runtime_scheduler=_RuntimeScheduler(active=True),
        ).get_overview(),
        "alert.monitor",
    )

    assert quality["status"] == "ok"
    assert quality["source"] == "alerts"
    assert quality["warnings"] == []


def test_overview_does_not_leak_last_error_whitespace_and_validates_schema() -> None:
    from api.v1.schemas.data_capability import DataCapabilityOverviewResponse

    overview = DataCapabilityService(
        config=_config(),
        fetcher_manager=_FetcherManager([
            _Fetcher("VciFetcher", 0, available=False, last_error="timeout\n  upstream"),
        ]),
    ).get_overview()

    assert _provider(overview, "vci")["last_error"] == "timeout upstream"
    DataCapabilityOverviewResponse.model_validate(overview)
    json.dumps(overview, ensure_ascii=False)


def test_data_capability_api_paths_return_valid_contract() -> None:
    from fastapi.testclient import TestClient

    from api.app import create_app

    overview_payload = {
        "as_of": "2026-08-26T15:05:00+07:00",
        "providers": [
            {
                "name": "ssi",
                "label": "SSI iBoard",
                "enabled": True,
                "configured": True,
                "status": "ok",
                "priority": 1,
                "markets": ["vn"],
                "datasets": ["quote.realtime"],
                "dataset_markets": {"quote.realtime": ["vn"]},
                "warnings": [],
                "last_error": None,
                "cooldown": None,
            }
        ],
        "datasets": [
            {
                "dataset": "quote.realtime",
                "status": "ok",
                "source": "ssi",
                "stale": False,
                "last_success": None,
                "last_error": None,
                "fallback_from": [],
                "coverage": None,
                "warnings": [],
            }
        ],
        "priorities": [
            {
                "scenario": "vn.realtime",
                "providers": ["ssi", "vci"],
                "source": "test",
                "warnings": [],
            }
        ],
        "warnings": [],
    }

    class _Service:
        def __init__(self, *, config, runtime_scheduler=None) -> None:
            self.config = config
            self.runtime_scheduler = runtime_scheduler

        def get_overview(self):
            return overview_payload

    with tempfile.TemporaryDirectory() as temp_dir:
        client = TestClient(create_app(static_dir=Path(temp_dir)))
        with patch("api.v1.endpoints.data.DataCapabilityService", _Service):
            overview_response = client.get("/api/v1/data/overview")
            capabilities_response = client.get("/api/v1/data/capabilities")

    assert overview_response.status_code == 200
    assert capabilities_response.status_code == 200
    assert overview_response.json() == overview_payload
    assert capabilities_response.json() == overview_payload
