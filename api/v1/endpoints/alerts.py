# -*- coding: utf-8 -*-
"""Alert API endpoints (Issue #1202 P1 MVP)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.v1.schemas.alerts import (
    AlertDeleteResponse,
    AlertLiveQuotesResponse,
    AlertMarketRegionsResponse,
    AlertNotificationListResponse,
    AlertRuleCreateRequest,
    AlertRuleItem,
    AlertRuleListResponse,
    AlertRuleTestResponse,
    AlertRuleUpdateRequest,
    AlertTriggerListResponse,
)
from api.v1.schemas.common import ErrorResponse
from src.services.alert_service import (
    AlertNotFoundError,
    AlertService,
    AlertServiceError,
    UnsupportedAlertTypeError,
)
from src.services.market_light_service import enabled_market_alert_regions

logger = logging.getLogger(__name__)

router = APIRouter()


def _bad_request(exc: Exception, *, error: str = "validation_error") -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": error, "message": str(exc)},
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "not_found", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: {str(exc)}"},
    )


LIVE_QUOTE_FALLBACK_LIMIT = 30
SSE_HEARTBEAT_SECONDS = 30


def _format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get(
    "/stream",
    responses={200: {"description": "SSE event stream", "content": {"text/event-stream": {}}}},
    summary="Realtime alert and live price SSE stream",
)
async def alert_stream():
    """SSE: ``connected``, ``alert_triggered``, ``price_update``, ``feed_status``, ``heartbeat``."""

    from data_provider.realtime_feed import feed_status
    from src.services.event_bus import alert_event_bus

    async def event_generator():
        queue = alert_event_bus.subscribe()
        try:
            yield _format_sse_event("connected", {"message": "Connected to alert stream"})
            yield _format_sse_event("feed_status", _current_feed_status(feed_status))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    yield _format_sse_event(event["type"], event["data"])
                except asyncio.TimeoutError:
                    yield _format_sse_event("heartbeat", {"timestamp": datetime.now().isoformat()})
        except asyncio.CancelledError:
            logger.debug("Alert SSE client disconnected")
            raise
        finally:
            alert_event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _current_feed_status(feed_status_fn) -> Dict[str, Any]:
    from src.services.realtime_alert_engine import get_active_realtime_engine

    engine = get_active_realtime_engine()
    return engine.status() if engine is not None else feed_status_fn()


@router.get(
    "/live-quotes",
    response_model=AlertLiveQuotesResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Live quotes for symbols that have alert rules",
)
def list_live_quotes() -> AlertLiveQuotesResponse:
    """Giá hiện tại của các mã có rule một mã đang bật, kèm trạng thái feed realtime.

    Ưu tiên tick từ feed push; mã chưa có tick thì lấy giá realtime thường (SSI…), tối đa
    ``LIVE_QUOTE_FALLBACK_LIMIT`` mã.
    """

    from data_provider.realtime_feed import feed_status, get_active_feed, vn_bare_symbol

    try:
        service = AlertService()
        rows = service.repo.list_enabled_rules(limit=1000)
        symbols: List[str] = []
        for row in rows:
            if row.target_scope == "single_symbol" and row.target and row.target not in symbols:
                symbols.append(row.target)
        feed = get_active_feed()
        quotes: Dict[str, Dict[str, Any]] = {}
        missing: List[str] = []
        for code in symbols:
            tick = feed.store.get(vn_bare_symbol(code)) if feed is not None else None
            if tick is not None:
                quotes[code] = tick.to_public_dict()
            else:
                missing.append(code)
        if missing:
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager()
            for code in missing[:LIVE_QUOTE_FALLBACK_LIMIT]:
                try:
                    quote = manager.get_realtime_quote(code, log_final_failure=False)
                except Exception:
                    quote = None
                if quote is None or not getattr(quote, "price", None):
                    continue
                quotes[code] = {
                    "symbol": code,
                    "price": getattr(quote, "price", None),
                    "change": getattr(quote, "change_amount", None),
                    "change_pct": getattr(quote, "change_pct", None),
                    "ref": getattr(quote, "pre_close", None),
                    "ceiling": getattr(quote, "vn_ceiling", None),
                    "floor": getattr(quote, "vn_floor", None),
                    "high": getattr(quote, "high", None),
                    "low": getattr(quote, "low", None),
                    "volume": getattr(quote, "volume", None),
                    "market_status": None,
                    "ts": getattr(quote, "provider_timestamp", None),
                    "source": getattr(getattr(quote, "source", None), "value", None) or "realtime_quote",
                }
        return AlertLiveQuotesResponse(
            feed=_current_feed_status(feed_status),
            quotes=[{"target": code, **quote} for code, quote in quotes.items()],
        )
    except Exception as exc:
        raise _internal_error("List live quotes failed", exc)


@router.get(
    "/market-regions",
    response_model=AlertMarketRegionsResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List market regions enabled for market alerts",
)
def list_market_regions() -> AlertMarketRegionsResponse:
    try:
        regions = list(enabled_market_alert_regions())
        return AlertMarketRegionsResponse(regions=regions, default=regions[0] if regions else None)
    except Exception as exc:
        raise _internal_error("List market alert regions failed", exc)


@router.post(
    "/rules",
    response_model=AlertRuleItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Create alert rule",
)
def create_rule(request: AlertRuleCreateRequest) -> AlertRuleItem:
    service = AlertService()
    try:
        return AlertRuleItem(**service.create_rule(request.model_dump()))
    except UnsupportedAlertTypeError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except AlertServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Create alert rule failed", exc)


@router.get(
    "/rules",
    response_model=AlertRuleListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List alert rules",
)
def list_rules(
    enabled: Optional[bool] = Query(None, description="Optional enabled filter"),
    alert_type: Optional[str] = Query(None, description="Optional alert type filter"),
    target_scope: Optional[str] = Query(None, description="Optional target scope filter"),
    target: Optional[str] = Query(None, description="Optional target filter"),
    source: Optional[str] = Query(None, description="Optional source filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AlertRuleListResponse:
    service = AlertService()
    try:
        return AlertRuleListResponse(
            **service.list_rules(
                enabled=enabled,
                alert_type=alert_type,
                target_scope=target_scope,
                target=target,
                source=source,
                page=page,
                page_size=page_size,
            )
        )
    except Exception as exc:
        raise _internal_error("List alert rules failed", exc)


@router.get(
    "/rules/{rule_id}",
    response_model=AlertRuleItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get alert rule",
)
def get_rule(rule_id: int) -> AlertRuleItem:
    service = AlertService()
    try:
        return AlertRuleItem(**service.get_rule(rule_id))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Get alert rule failed", exc)


@router.patch(
    "/rules/{rule_id}",
    response_model=AlertRuleItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Update alert rule",
)
def update_rule(rule_id: int, request: AlertRuleUpdateRequest) -> AlertRuleItem:
    service = AlertService()
    try:
        payload = request.model_dump(exclude_unset=True)
        return AlertRuleItem(**service.update_rule(rule_id, payload))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except UnsupportedAlertTypeError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except AlertServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Update alert rule failed", exc)


@router.delete(
    "/rules/{rule_id}",
    response_model=AlertDeleteResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Delete alert rule",
)
def delete_rule(rule_id: int) -> AlertDeleteResponse:
    service = AlertService()
    try:
        if not service.delete_rule(rule_id):
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")
        return AlertDeleteResponse(deleted=1)
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Delete alert rule failed", exc)


@router.post(
    "/rules/{rule_id}/enable",
    response_model=AlertRuleItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Enable alert rule",
)
def enable_rule(rule_id: int) -> AlertRuleItem:
    service = AlertService()
    try:
        return AlertRuleItem(**service.enable_rule(rule_id, True))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except AlertServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Enable alert rule failed", exc)


@router.post(
    "/rules/{rule_id}/disable",
    response_model=AlertRuleItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Disable alert rule",
)
def disable_rule(rule_id: int) -> AlertRuleItem:
    service = AlertService()
    try:
        return AlertRuleItem(**service.enable_rule(rule_id, False))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Disable alert rule failed", exc)


@router.post(
    "/rules/{rule_id}/test",
    response_model=AlertRuleTestResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Dry-run alert rule",
)
def test_rule(rule_id: int) -> AlertRuleTestResponse:
    service = AlertService()
    try:
        return AlertRuleTestResponse(**service.test_rule(rule_id))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Test alert rule failed", exc)


@router.get(
    "/triggers",
    response_model=AlertTriggerListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List alert trigger history",
)
def list_triggers(
    rule_id: Optional[int] = Query(None, description="Optional rule id filter"),
    target: Optional[str] = Query(None, description="Optional target filter"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AlertTriggerListResponse:
    service = AlertService()
    try:
        return AlertTriggerListResponse(
            **service.list_triggers(
                rule_id=rule_id,
                target=target,
                status=status,
                page=page,
                page_size=page_size,
            )
        )
    except Exception as exc:
        raise _internal_error("List alert triggers failed", exc)


@router.get(
    "/notifications",
    response_model=AlertNotificationListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List alert notification attempts",
)
def list_notifications(
    trigger_id: Optional[int] = Query(None, description="Optional trigger id filter"),
    channel: Optional[str] = Query(None, description="Optional channel filter"),
    success: Optional[bool] = Query(None, description="Optional success filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AlertNotificationListResponse:
    service = AlertService()
    try:
        return AlertNotificationListResponse(
            **service.list_notifications(
                trigger_id=trigger_id,
                channel=channel,
                success=success,
                page=page,
                page_size=page_size,
            )
        )
    except Exception as exc:
        raise _internal_error("List alert notifications failed", exc)
