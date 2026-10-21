# -*- coding: utf-8 -*-
"""Dịch vụ tổng quan năng lực nguồn dữ liệu và chất lượng dataset (chỉ đọc).

Hệ thống chỉ còn phục vụ thị trường Việt Nam (``vn``), nên ma trận nguồn chỉ gồm
VCI, SSI, vnstock (tuỳ chọn) và Yahoo Finance (dự phòng nến ngày HOSE).

Read-only data capability and dataset quality overview service. Only the Vietnam
market (``vn``) is supported, so the provider matrix is limited to VCI, SSI,
optional vnstock and Yahoo Finance (HOSE daily fallback).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from src.config import get_config

logger = logging.getLogger(__name__)

# Thị trường duy nhất còn được hỗ trợ / The only supported market
_VN = "vn"


@dataclass(frozen=True)
class _ProviderDefinition:
    name: str
    label: str
    fetcher_name: Optional[str]
    dataset_markets: Mapping[str, Sequence[str]]
    builtin: bool = False

    @property
    def datasets(self) -> Sequence[str]:
        return tuple(self.dataset_markets)

    @property
    def markets(self) -> Sequence[str]:
        supported = {
            market
            for markets in self.dataset_markets.values()
            for market in markets
        }
        # Thứ tự lấy từ registry thị trường / Order comes from the market registry
        from src.core.markets import ALL_MARKET_CODES

        return tuple(market for market in ALL_MARKET_CODES if market in supported)


_PROVIDER_DEFINITIONS: Sequence[_ProviderDefinition] = (
    _ProviderDefinition(
        name="fiintrade",
        label="FiinTrade (optional)",
        fetcher_name="FiinTradeFetcher",
        dataset_markets={
            # Mặc định bật khi có FIINTRADE_ORIGIN (tắt bằng FIINTRADE_ENABLED=false); phủ HOSE,
            # HNX và chỉ số trừ UPCoM (UPCOM để VCI), giá cùng cơ sở điều chỉnh với VCI.
            # On by default once FIINTRADE_ORIGIN is set (FIINTRADE_ENABLED=false disables);
            # covers HOSE, HNX and non-UPCoM indices, same adjustment basis as VCI.
            # PriceDepth: giá trong phiên + định giá + khối ngoại (cổ phiếu HOSE/HNX)
            # PriceDepth: intraday quote + valuation + foreign flow (HOSE/HNX stocks)
            "quote.realtime": (_VN,),
            "kline.daily": (_VN,),
            "index.daily": (_VN,),
            # GetLatestIndices + MoneyFlow: chỉ số, độ rộng, dòng tiền cho điểm lại thị trường
            # GetLatestIndices + MoneyFlow: indices, breadth and flows for the market review
            "market.overview": (_VN,),
        },
    ),
    _ProviderDefinition(
        name="vci",
        label="VCI (Vietcap)",
        fetcher_name="VciFetcher",
        dataset_markets={
            # Giá realtime cho chỉ số đã đăng ký (suy ra từ nến ngày mới nhất), đứng sau SSI
            # Realtime quotes for registered indices (derived from latest daily bar), after SSI
            "quote.realtime": (_VN,),
            "kline.daily": (_VN,),
            # VNINDEX / VN30 / HNXIndex / HNX30 / HNXUpcomIndex dùng chung endpoint nến ngày
            # VNINDEX / VN30 / HNXIndex / HNX30 / HNXUpcomIndex share the same daily endpoint
            "index.daily": (_VN,),
            # Chỉ số chính cho điểm lại thị trường (get_main_indices)
            # Main indices for the market review (get_main_indices)
            "market.overview": (_VN,),
        },
        builtin=True,
    ),
    _ProviderDefinition(
        name="ssi",
        label="SSI iBoard",
        fetcher_name="SsiFetcher",
        dataset_markets={
            # Snapshot toàn thị trường (mỗi sàn HOSE/HNX/UPCOM một request)
            # Full-market snapshot (one request each for HOSE/HNX/UPCOM)
            "quote.realtime": (_VN,),
            "kline.daily": (_VN,),
            # Độ rộng thị trường (tăng/giảm/trần/sàn) và chỉ số chính dự phòng sau VCI
            # Market breadth (up/down/ceiling/floor) plus main-index fallback after VCI
            "market.overview": (_VN,),
        },
        builtin=True,
    ),
    _ProviderDefinition(
        name="vnstock",
        label="vnstock (optional)",
        fetcher_name="VnstockFetcher",
        dataset_markets={
            # Chỉ khởi tạo khi VNSTOCK_ENABLED=true và đã cài vnstock; không khai báo giá
            # realtime để tránh thành nguồn dự phòng giả.
            # Only instantiated when VNSTOCK_ENABLED=true and vnstock is installed; no
            # realtime capability is declared, to avoid a pseudo-fallback.
            "kline.daily": (_VN,),
        },
    ),
    _ProviderDefinition(
        name="yfinance",
        label="YFinance",
        fetcher_name="YfinanceFetcher",
        dataset_markets={
            # Dự phòng cấp cuối cho nến ngày, chỉ phủ sàn HOSE và không ghi vào DB.
            # Yahoo không có lịch sử chỉ số Việt Nam nên không khai index.daily.
            # Last-resort daily fallback, HOSE only and never persisted. Yahoo has no
            # Vietnam index history, so index.daily is not declared.
            "kline.daily": (_VN,),
        },
        builtin=True,
    ),
)

_PROVIDER_DEFINITION_MAP = {
    definition.name: definition
    for definition in _PROVIDER_DEFINITIONS
}

_FETCHER_TO_PROVIDER = {
    definition.fetcher_name: definition.name
    for definition in _PROVIDER_DEFINITIONS
    if definition.fetcher_name
}

# Token cấu hình REALTIME_SOURCE_PROVIDER mà DataFetcherManager thực sự xử lý, theo
# thứ tự mặc định (khớp ``DataFetcherManager._vn_realtime_source_order``).
# Realtime tokens that DataFetcherManager actually handles, in default order
# (mirrors ``DataFetcherManager._vn_realtime_source_order``).
_VN_REALTIME_SOURCES: Sequence[str] = ("ssi", "vci")
# Token tuỳ chọn: chỉ có hiệu lực khi FiinTrade đang chạy; khi chạy mà không ghi trong
# cấu hình thì đứng đầu. / Optional token, effective only when FiinTrade is enabled.
_VN_OPTIONAL_REALTIME_SOURCES: Sequence[str] = ("fiintrade",)


def _truthy(value: Any) -> bool:
    return bool(str(value or "").strip())


def _split_priority(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = str(value or "").split(",")
    return [str(item).strip().lower() for item in raw_items if str(item).strip()]


class DataCapabilityService:
    """Build side-effect-light provider capability and dataset quality snapshots."""

    def __init__(
        self,
        *,
        config: Any = None,
        fetcher_manager: Any = None,
        runtime_scheduler: Any = None,
    ) -> None:
        self.config = config or get_config()
        self.fetcher_manager = fetcher_manager
        self.runtime_scheduler = runtime_scheduler

    def get_overview(self) -> Dict[str, Any]:
        """Return a read-only data capability overview."""

        fetchers = self._fetchers_snapshot()
        providers = self._build_provider_capabilities(fetchers)
        provider_map = {item["name"]: item for item in providers}
        priorities = self._build_priority_views(fetchers)
        datasets = self._build_dataset_quality(provider_map, priorities, fetchers)
        warnings = self._build_global_warnings(priorities)

        return {
            "as_of": datetime.now(timezone.utc).astimezone().isoformat(),
            "providers": providers,
            "datasets": datasets,
            "priorities": priorities,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Fetcher snapshot
    # ------------------------------------------------------------------
    def _fetchers_snapshot(self) -> List[Any]:
        manager = self.fetcher_manager
        if manager is None:
            try:
                from data_provider import DataFetcherManager

                manager = DataFetcherManager()
            except Exception as exc:  # noqa: BLE001 - diagnostics must fail open.
                logger.warning("Failed to initialize data fetcher manager for capability overview: %s", exc)
                return []

        snapshot = getattr(manager, "_get_fetchers_snapshot", None)
        if callable(snapshot):
            try:
                return list(snapshot())
            except Exception as exc:  # noqa: BLE001 - diagnostics must fail open.
                logger.warning("Failed to read data fetcher snapshot: %s", exc)
                return []
        return list(getattr(manager, "_fetchers", []) or [])

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------
    def _build_provider_capabilities(self, fetchers: Sequence[Any]) -> List[Dict[str, Any]]:
        fetcher_map = {str(getattr(fetcher, "name", "")): fetcher for fetcher in fetchers}
        providers: List[Dict[str, Any]] = []

        from src.core.markets import is_market_enabled

        for definition in _PROVIDER_DEFINITIONS:
            fetcher = fetcher_map.get(definition.fetcher_name or "")
            configured = self._is_provider_configured(definition)
            # Nguồn mà mọi thị trường phục vụ đều bị tắt sẽ không được khởi tạo; báo
            # "unavailable" sẽ khiến dashboard hiểu nhầm là nguồn hỏng.
            # Providers whose markets are all disabled are never instantiated; reporting
            # "unavailable" would make dashboards misread it as a broken source.
            market_enabled = any(
                is_market_enabled(market) for market in definition.markets
            ) if definition.markets else True
            enabled = bool(definition.builtin or configured) and market_enabled
            warnings: List[str] = []

            if not market_enabled:
                # Dùng lại trạng thái unconfigured thay vì thêm enum mới (hợp đồng ngoài).
                # Reuse unconfigured instead of a new enum value (external contract).
                status = "unconfigured"
                warnings.append("market_disabled")
            elif not configured and not definition.builtin:
                status = "unconfigured"
            elif fetcher is None and enabled:
                status = "unavailable"
                warnings.append("provider_not_initialized")
            else:
                status = self._provider_runtime_status(fetcher)
                if status == "unavailable":
                    warnings.append("provider_marked_unavailable")
                elif status == "unknown":
                    warnings.append("runtime_probe_not_performed")

            providers.append(
                {
                    "name": definition.name,
                    "label": definition.label,
                    "enabled": enabled,
                    "configured": configured,
                    "status": status,
                    "priority": self._provider_priority(fetcher),
                    "markets": list(definition.markets),
                    "datasets": list(definition.datasets),
                    "dataset_markets": {
                        dataset: list(markets)
                        for dataset, markets in definition.dataset_markets.items()
                    },
                    "warnings": warnings,
                    "last_error": self._safe_last_error(fetcher),
                    "cooldown": None,
                }
            )

        return providers

    @staticmethod
    def _provider_runtime_status(fetcher: Any) -> str:
        probe_result = DataCapabilityService._probe_fetcher_available(fetcher)
        if probe_result is True:
            return "ok"
        if probe_result is False:
            return "unavailable"

        known_available = getattr(fetcher, "_available", None)
        if known_available is True:
            return "ok"
        if known_available is False:
            return "unavailable"
        return "unknown"

    @staticmethod
    def _probe_fetcher_available(fetcher: Any, capability: str = "") -> Optional[bool]:
        try:
            from data_provider.base import DataFetcherManager

            for probe_name in ("is_available_for_request", "is_available", "_is_available"):
                result = DataFetcherManager._call_availability_probe(fetcher, probe_name, capability)
                if result is not None:
                    return result
        except Exception as exc:  # noqa: BLE001 - diagnostics must fail open.
            logger.debug("Failed to probe fetcher availability for capability overview: %s", exc)
        return None

    @staticmethod
    def _is_provider_configured(definition: _ProviderDefinition) -> bool:
        if definition.builtin:
            return True
        if definition.name == "fiintrade":
            # Khớp điều kiện khởi tạo trong DataFetcherManager._init_default_fetchers
            # Mirrors the opt-in check in DataFetcherManager._init_default_fetchers
            from data_provider.fiintrade_fetcher import is_fiintrade_enabled

            return is_fiintrade_enabled()
        if definition.name == "vnstock":
            # Khớp điều kiện khởi tạo trong DataFetcherManager._init_default_fetchers
            # Mirrors the opt-in check in DataFetcherManager._init_default_fetchers
            return str(os.getenv("VNSTOCK_ENABLED", "false")).strip().lower() == "true"
        return False

    @staticmethod
    def _provider_priority(fetcher: Any) -> Optional[int]:
        value = getattr(fetcher, "priority", None)
        return value if isinstance(value, int) else None

    @staticmethod
    def _safe_last_error(fetcher: Any) -> Optional[str]:
        if fetcher is None:
            return None
        for attr in ("last_error", "_last_error"):
            value = getattr(fetcher, attr, None)
            if value:
                return " ".join(str(value).split())
        return None

    # ------------------------------------------------------------------
    # Priorities
    # ------------------------------------------------------------------
    def _build_priority_views(self, fetchers: Sequence[Any]) -> List[Dict[str, Any]]:
        ordered_fetchers = sorted(fetchers, key=lambda item: getattr(item, "priority", 99))
        generic_daily = [
            _FETCHER_TO_PROVIDER.get(str(getattr(fetcher, "name", "")), str(getattr(fetcher, "name", "")).lower())
            for fetcher in ordered_fetchers
            if getattr(fetcher, "name", None)
            and self._fetcher_available_for_capability(fetcher, capability="daily_data")
        ]
        # Chỉ số đi qua cùng chuỗi nến ngày, nhưng chỉ nguồn khai báo index.daily phục vụ được
        # Indices share the daily chain, but only providers declaring index.daily can serve them
        index_daily = [
            token
            for token in generic_daily
            if self._provider_supports_market_dataset(token, dataset="index.daily", market=_VN)
        ]
        # get_main_indices / get_market_stats lặp fetcher theo thứ tự ưu tiên
        # get_main_indices / get_market_stats iterate fetchers in priority order
        market_overview = [
            provider
            for fetcher in ordered_fetchers
            if (provider := _FETCHER_TO_PROVIDER.get(str(getattr(fetcher, "name", ""))))
            and self._provider_supports_market_dataset(provider, dataset="market.overview", market=_VN)
        ]

        realtime_providers, realtime_warnings = self._vn_realtime_priority()
        realtime_view = self._priority_view(
            "vn.realtime",
            realtime_providers,
            "Config.realtime_source_priority",
            known_sources=(*_VN_OPTIONAL_REALTIME_SOURCES, *_VN_REALTIME_SOURCES),
        )
        realtime_view["warnings"].extend(realtime_warnings)

        return [
            realtime_view,
            self._priority_view(
                "daily.generic",
                generic_daily,
                "DataFetcherManager.fetchers",
                known_sources=set(_FETCHER_TO_PROVIDER.values()),
            ),
            self._priority_view(
                "vn.index.daily",
                index_daily,
                "DataFetcherManager.fetchers (index.daily)",
                known_sources=set(_FETCHER_TO_PROVIDER.values()),
            ),
            self._priority_view(
                "market.overview",
                market_overview,
                "DataFetcherManager market overview route",
                known_sources=set(_FETCHER_TO_PROVIDER.values()),
            ),
            self._priority_view(
                "news.events",
                ["intelligence", "search"],
                "IntelligenceSource/SearchService",
                known_sources={"intelligence", "search"},
            ),
        ]

    def _vn_realtime_priority(self) -> tuple[List[str], List[str]]:
        """Thứ tự realtime thực thi được, khớp ``_vn_realtime_source_order``.

        Token không xử lý được bị bỏ qua lúc chạy nên chỉ báo cảnh báo; các nguồn mặc
        định còn thiếu được nối vào cuối.

        Effective realtime order mirroring ``_vn_realtime_source_order``: unhandled
        tokens are ignored at runtime (surfaced as warnings), missing defaults are appended.
        """

        from data_provider.fiintrade_fetcher import is_fiintrade_enabled

        fiintrade_enabled = is_fiintrade_enabled()
        configured = _split_priority(getattr(self.config, "realtime_source_priority", ""))
        ordered: List[str] = []
        warnings: List[str] = []
        for token in configured:
            if token in _VN_REALTIME_SOURCES or (token == "fiintrade" and fiintrade_enabled):
                if token not in ordered:
                    ordered.append(token)
            else:
                warnings.append(f"ignored_source:{token}")
        if fiintrade_enabled and "fiintrade" not in ordered:
            ordered.insert(0, "fiintrade")
        for token in _VN_REALTIME_SOURCES:
            if token not in ordered:
                ordered.append(token)
        return ordered, warnings

    @staticmethod
    def _priority_view(
        scenario: str,
        providers: Sequence[str],
        source: str,
        *,
        known_sources: Iterable[str],
    ) -> Dict[str, Any]:
        known = set(known_sources)
        warnings = [f"unknown_source:{provider}" for provider in providers if provider not in known]
        return {
            "scenario": scenario,
            "providers": list(providers),
            "source": source,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------
    def _build_dataset_quality(
        self,
        provider_map: Dict[str, Dict[str, Any]],
        priorities: Sequence[Dict[str, Any]],
        fetchers: Sequence[Any],
    ) -> List[Dict[str, Any]]:
        priority_map = {item["scenario"]: item for item in priorities}
        fetcher_map = {
            str(getattr(fetcher, "name", "")): fetcher
            for fetcher in fetchers
            if getattr(fetcher, "name", None)
        }
        daily_resolver = self._daily_status_resolver(provider_map=provider_map, fetcher_map=fetcher_map)

        def scoped(scenario: str, *, empty_unavailable: bool = False) -> Dict[str, Any]:
            priority = priority_map.get(scenario, {})
            result = {
                "providers": list(priority.get("providers") or []),
                "warnings": list(priority.get("warnings") or []),
            }
            if empty_unavailable and not result["providers"]:
                result["empty_status"] = "unavailable"
            return result

        realtime_disabled = not bool(getattr(self.config, "enable_realtime_quote", True))
        fundamental_priority = scoped("vn.realtime")
        # Việt Nam chỉ có khối định giá (PE/PB/vốn hoá) lấy từ snapshot realtime
        # Vietnam only has the valuation block (PE/PB/market cap) from the realtime snapshot
        fundamental_priority["warnings"].append("valuation_only")

        return [
            self._aggregate_market_dataset(
                dataset="quote.realtime",
                market_priorities={_VN: scoped("vn.realtime")},
                provider_map=provider_map,
                disabled=realtime_disabled,
                disabled_warning="realtime_quote_disabled",
            ),
            self._aggregate_market_dataset(
                dataset="kline.daily",
                market_priorities={_VN: scoped("daily.generic", empty_unavailable=True)},
                provider_map=provider_map,
                status_resolvers={_VN: daily_resolver},
            ),
            self._aggregate_market_dataset(
                dataset="index.daily",
                market_priorities={_VN: scoped("vn.index.daily", empty_unavailable=True)},
                provider_map=provider_map,
                status_resolvers={_VN: daily_resolver},
            ),
            self._aggregate_market_dataset(
                dataset="market.overview",
                market_priorities={_VN: scoped("market.overview")},
                provider_map=provider_map,
            ),
            self._aggregate_market_dataset(
                dataset="financial.snapshot",
                market_priorities={_VN: fundamental_priority},
                provider_map=provider_map,
                disabled=(
                    not bool(getattr(self.config, "enable_fundamental_pipeline", True))
                    or realtime_disabled
                ),
                disabled_warning=(
                    "fundamental_pipeline_disabled"
                    if not bool(getattr(self.config, "enable_fundamental_pipeline", True))
                    else "realtime_quote_disabled"
                ),
            ),
            self._news_events_dataset(),
            self._alert_monitor_dataset(),
            self._local_dataset("portfolio.account", "portfolio"),
        ]

    def _daily_status_resolver(
        self,
        *,
        provider_map: Dict[str, Dict[str, Any]],
        fetcher_map: Dict[str, Any],
    ) -> Callable[[str], str]:
        def resolve(token: str) -> str:
            provider_status = self._source_token_status(token, provider_map)
            definition = _PROVIDER_DEFINITION_MAP.get(token)
            fetcher_name = definition.fetcher_name if definition is not None else None
            fetcher = fetcher_map.get(fetcher_name or "")
            if fetcher is not None and not self._daily_source_available(fetcher, _VN):
                return "cooldown"
            return provider_status

        return resolve

    @staticmethod
    def _daily_source_available(fetcher: Any, market: str) -> bool:
        try:
            from data_provider.base import DataFetcherManager

            return bool(DataFetcherManager._is_daily_source_available(fetcher, market))
        except Exception as exc:  # noqa: BLE001 - diagnostics must fail open.
            logger.debug("Failed to probe daily source health for capability overview: %s", exc)
            return True

    @staticmethod
    def _provider_supports_market_dataset(
        provider: str,
        *,
        dataset: str,
        market: str,
    ) -> bool:
        definition = _PROVIDER_DEFINITION_MAP.get(provider)
        if definition is None:
            return False
        return market in definition.dataset_markets.get(dataset, ())

    @staticmethod
    def _fetcher_available_for_capability(fetcher: Any, *, capability: str) -> bool:
        result = DataCapabilityService._probe_fetcher_available(fetcher, capability)
        if result is not None:
            return result
        known_available = getattr(fetcher, "_available", None)
        if known_available is not None:
            return bool(known_available)
        return True

    def _aggregate_market_dataset(
        self,
        *,
        dataset: str,
        market_priorities: Dict[str, Dict[str, Any]],
        provider_map: Dict[str, Dict[str, Any]],
        status_resolvers: Optional[Dict[str, Callable[[str], str]]] = None,
        disabled: bool = False,
        disabled_warning: str = "",
    ) -> Dict[str, Any]:
        if disabled:
            return self._dataset_from_priority(
                dataset=dataset,
                priority={},
                provider_map=provider_map,
                disabled=True,
                disabled_warning=disabled_warning,
            )

        market_results = {
            market: self._dataset_from_priority(
                dataset=dataset,
                priority=priority,
                provider_map=provider_map,
                status_resolver=(status_resolvers or {}).get(market),
            )
            for market, priority in market_priorities.items()
        }
        statuses = [str(result["status"]) for result in market_results.values()]
        selected_sources = [
            str(result["source"])
            for result in market_results.values()
            if result.get("source")
        ]
        unique_sources = sorted(set(selected_sources))
        warnings: List[str] = []
        fallback_from: List[str] = []
        coverage: Dict[str, Any] = {"markets": {}}

        for market, result in market_results.items():
            coverage["markets"][market] = {
                "status": result["status"],
                "source": result["source"],
                "fallback_from": list(result.get("fallback_from") or []),
                "warnings": list(result.get("warnings") or []),
            }
            fallback_from.extend(f"{market}:{token}" for token in result.get("fallback_from") or [])
            warnings.extend(f"{market}:{warning}" for warning in result.get("warnings") or [])

        return {
            "dataset": dataset,
            "status": self._aggregate_market_status(statuses),
            "source": unique_sources[0] if len(unique_sources) == 1 else None,
            "stale": None,
            "last_success": None,
            "last_error": None,
            "fallback_from": fallback_from,
            "coverage": coverage,
            "warnings": warnings,
        }

    @staticmethod
    def _aggregate_market_status(statuses: Sequence[str]) -> str:
        available_statuses = {"ok", "degraded"}
        if statuses and all(status == "ok" for status in statuses):
            return "ok"
        if statuses and all(status in available_statuses for status in statuses):
            return "degraded"
        if any(status in available_statuses for status in statuses):
            return "partial"
        if statuses and all(status == "unknown" for status in statuses):
            return "unknown"
        if statuses and all(status == "unconfigured" for status in statuses):
            return "unconfigured"
        if any(status == "unknown" for status in statuses):
            return "unknown"
        return "unavailable"

    @staticmethod
    def _empty_dataset(dataset: str, status: str, warnings: Sequence[str], fallback_from: Sequence[str] = ()) -> Dict[str, Any]:
        return {
            "dataset": dataset,
            "status": status,
            "source": None,
            "stale": None,
            "last_success": None,
            "last_error": None,
            "fallback_from": list(fallback_from),
            "coverage": None,
            "warnings": list(warnings),
        }

    def _dataset_from_priority(
        self,
        *,
        dataset: str,
        priority: Dict[str, Any],
        provider_map: Dict[str, Dict[str, Any]],
        status_resolver: Optional[Callable[[str], str]] = None,
        disabled: bool = False,
        disabled_warning: str = "",
    ) -> Dict[str, Any]:
        if disabled:
            return self._empty_dataset(
                dataset,
                "unavailable",
                [disabled_warning] if disabled_warning else [],
            )

        providers = list(priority.get("providers") or [])
        warnings = list(priority.get("warnings") or [])
        if not providers:
            if priority.get("empty_status") == "unavailable":
                return self._empty_dataset(
                    dataset,
                    "unavailable",
                    ["request_available_priority_empty", *warnings],
                )
            return self._empty_dataset(dataset, "unknown", ["priority_empty", *warnings])

        selected: Optional[str] = None
        fallback_from: List[str] = []
        token_statuses: List[str] = []
        for token in providers:
            token_status = (
                status_resolver(token)
                if callable(status_resolver)
                else self._source_token_status(token, provider_map)
            )
            token_statuses.append(token_status)
            if token_status == "ok":
                selected = token
                break
            warnings.append(f"source_status:{token}:{token_status}")
            if token_status == "unknown":
                # Không bỏ qua nguồn ưu tiên chưa được kiểm tra / never skip an unprobed preferred source
                return self._empty_dataset(dataset, "unknown", warnings, fallback_from)
            fallback_from.append(token)

        if selected is None:
            status = "unknown" if "unknown" in token_statuses else "unavailable"
            return self._empty_dataset(dataset, status, warnings, fallback_from)

        return {
            "dataset": dataset,
            "status": "ok" if not fallback_from else "degraded",
            "source": selected,
            "stale": None,
            "last_success": None,
            "last_error": None,
            "fallback_from": fallback_from,
            "coverage": None,
            "warnings": warnings,
        }

    @staticmethod
    def _source_token_status(token: str, provider_map: Dict[str, Dict[str, Any]]) -> str:
        provider = provider_map.get(token)
        if provider is None:
            if token in {"intelligence", "search"}:
                return "ok"
            return "unknown"
        return str(provider.get("status") or "unknown")

    @classmethod
    def _local_dataset(
        cls,
        dataset: str,
        source: str,
        *,
        enabled: bool = True,
        disabled_warning: str = "",
    ) -> Dict[str, Any]:
        if not enabled:
            return cls._empty_dataset(
                dataset,
                "unavailable",
                [disabled_warning] if disabled_warning else [],
            )
        return {
            "dataset": dataset,
            "status": "ok",
            "source": source,
            "stale": False,
            "last_success": None,
            "last_error": None,
            "fallback_from": [],
            "coverage": None,
            "warnings": [],
        }

    def _alert_monitor_dataset(self) -> Dict[str, Any]:
        if not bool(getattr(self.config, "agent_event_monitor_enabled", False)):
            return self._local_dataset(
                "alert.monitor",
                "alerts",
                enabled=False,
                disabled_warning="agent_event_monitor_disabled",
            )
        active = False
        checker = getattr(self.runtime_scheduler, "is_background_task_active", None)
        if callable(checker):
            try:
                active = bool(checker("agent_event_monitor"))
            except Exception as exc:  # noqa: BLE001 - diagnostics must fail open.
                logger.debug("Failed to probe Event Monitor scheduler state: %s", exc)
        return self._local_dataset(
            "alert.monitor",
            "alerts",
            enabled=active,
            disabled_warning="agent_event_monitor_not_running",
        )

    @classmethod
    def _news_events_dataset(cls) -> Dict[str, Any]:
        return cls._empty_dataset("news.events", "unknown", ["runtime_probe_not_performed"])

    @staticmethod
    def _build_global_warnings(priorities: Sequence[Dict[str, Any]]) -> List[str]:
        warnings: List[str] = []
        for priority in priorities:
            for warning in priority.get("warnings") or []:
                scoped = f"{priority['scenario']}:{warning}"
                if scoped not in warnings:
                    warnings.append(scoped)
        return warnings
