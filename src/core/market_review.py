# -*- coding: utf-8 -*-
"""
===================================
Hệ thống phân tích cổ phiếu - module điểm lại thị trường Việt Nam
===================================

Responsibilities:
1. Run the Vietnam market review (MARKET_REVIEW_REGION is normalized to vn)
2. Run the market review analysis and generate the review report
3. Save and send the review report
"""

import logging
import inspect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import uuid

from src.config import get_config
from src.notification import NotificationService
from src.market_analyzer import MarketAnalyzer
from src.report_language import normalize_report_language
from src.search_service import SearchService
from src.analyzer import AnalysisResult, GeminiAnalyzer
from src.llm.generation_backend import GenerationError
from src.services.run_diagnostics import (
    current_diagnostic_snapshot,
    record_history_run,
    record_notification_run,
)
from src.schemas.market_light import MARKET_LIGHT_REGIONS
from src.utils.market_review_region import (
    enabled_market_review_regions,
    normalize_market_review_region_lenient,
)


logger = logging.getLogger(__name__)

MARKET_REVIEW_HISTORY_CODE = "MARKET"
MARKET_REVIEW_REPORT_TYPE = "market_review"
_MARKET_REVIEW_MARKETS = (
    ('vn', 'vn_title', 'Việt Nam'),
)


@dataclass
class MarketReviewRunResult:
    """Structured result for API/Web consumers while keeping Markdown compatibility."""

    report: str
    market_review_payload: Dict[str, Any] = field(default_factory=dict)


def _refresh_market_review_history_diagnostics(*, query_id: str) -> None:
    """Refresh persisted market-review diagnostics after late flow events are recorded."""
    diagnostic_snapshot = current_diagnostic_snapshot()
    if diagnostic_snapshot is None:
        return

    try:
        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        updater = getattr(db, "update_analysis_history_diagnostics", None)
        if callable(updater):
            updater(
                query_id=query_id,
                code=MARKET_REVIEW_HISTORY_CODE,
                diagnostics=diagnostic_snapshot,
            )
    except Exception as exc:
        logger.warning("Ghi lại chẩn đoán lượt tổng kết thị trường thất bại (fail-open): %s", exc)


def _record_market_review_notification_run(
    *,
    query_id: str,
    channel: str,
    status: str,
    success: bool,
    attempts: int = 1,
    error_message: Optional[Any] = None,
) -> None:
    record_notification_run(
        channel=channel,
        status=status,
        success=success,
        attempts=attempts,
        error_message=error_message,
    )
    _refresh_market_review_history_diagnostics(query_id=query_id)


def _collect_market_light_snapshot(
    snapshots: Dict[str, Dict[str, Any]],
    *,
    region: str,
    review_result: Any,
) -> None:
    if region not in MARKET_LIGHT_REGIONS:
        return
    snapshot = getattr(review_result, "market_light_snapshot", None)
    if isinstance(snapshot, dict) and snapshot:
        snapshots[region] = snapshot


def _get_market_review_text(language: str) -> dict[str, str]:
    normalized = normalize_report_language(language)
    if normalized == "en":
        return {
            "root_title": "# 🎯 Market Review",
            "push_title": "🎯 Market Review",
            "vn_title": "# Vietnam Market Recap",
        }
    # Báo cáo tiếng Việt: tiêu đề bao ngoài cũng dùng tiếng Việt để khớp
    # nội dung do LLM viết bằng tiếng Việt.
    return {
        "root_title": "# 🎯 Điểm lại thị trường",
        "push_title": "🎯 Điểm lại thị trường",
        "vn_title": "# Điểm lại thị trường Việt Nam",
    }


def _market_review_region_metadata(region: Any) -> str:
    normalized = str(region or "").strip().lower()
    if normalized in {market for market, _, _ in _MARKET_REVIEW_MARKETS}:
        return f"[dsa-market-region]: # ({normalized})\n\n"
    return ""


def _resolve_market_review_regions(raw_region: Optional[str]) -> list[str]:
    """Normalize MARKET_REVIEW_REGION into an ordered region list.

    Returns an empty list when no market supports review, and callers skip the review accordingly.
    """

    normalized = normalize_market_review_region_lenient(raw_region)
    if normalized:
        return normalized.split(",")

    enabled = enabled_market_review_regions()
    if not enabled:
        return []
    return [enabled[0]]


def run_market_review(
    notifier: NotificationService,
    analyzer: Optional[GeminiAnalyzer] = None,
    search_service: Optional[SearchService] = None,
    config: Optional[object] = None,
    send_notification: bool = True,
    merge_notification: bool = False,
    override_region: Optional[str] = None,
    query_id: Optional[str] = None,
    return_structured: bool = False,
    save_report_file: bool = True,
    persist_history: bool = True,
    trigger_source: str = "cli",
) -> Optional[str] | Optional[MarketReviewRunResult]:
    """
    Run the market review analysis

    Args:
        notifier: notification service
        analyzer: AI analyzer (optional)
        search_service: search service (optional)
        config: configuration for this review (optional; the global config is used when omitted)
        send_notification: whether to send notifications
        merge_notification: whether to merge the push (skip this push; the main layer merges stocks + market and sends once, Issue #190)
        override_region: overrides config's market_review_region (the valid subset after Issue #373 trading-day filtering)
        query_id: history record correlation ID; API background tasks pass task_id, and it is generated automatically when empty for CLI/Bot
        save_report_file: whether to save the Markdown file; context-generation paths can disable it so temporary multi-region reviews do not overwrite each other
        persist_history: whether to write analysis_history; warm-up paths can disable it to avoid overwriting the user-visible same-day market review record
        trigger_source: trigger source, used for log troubleshooting (cli/schedule/api/bot/service, etc.)

    Returns:
        Review report text
    """
    runtime_config = config or get_config()
    history_query_id = query_id or f"market_review_{uuid.uuid4().hex}"
    review_text = _get_market_review_text(getattr(runtime_config, "report_language", "zh"))
    raw_region = (
        override_region
        if override_region is not None
        else (getattr(runtime_config, 'market_review_region', 'vn') or 'vn')
    )
    run_markets = _resolve_market_review_regions(raw_region)
    if not run_markets:
        # Không có thị trường nào hỗ trợ điểm lại thị trường thì bỏ qua.
        # No market supports market review; skip.
        logger.info(
            "[MarketReview] component=market_review action=skip "
            "reason=no_enabled_review_market trigger_source=%s query_id=%s",
            trigger_source,
            history_query_id,
        )
        return None
    persist_region = run_markets[0]
    logger.info(
        "[MarketReview] component=market_review action=start trigger_source=%s query_id=%s region=%s",
        trigger_source,
        history_query_id,
        persist_region,
    )

    try:
        run_region = run_markets[0]
        label = next(
            (market_label for mkt, _, market_label in _MARKET_REVIEW_MARKETS if mkt == run_region),
            run_region,
        )
        logger.info(
            "[MarketReview] component=market_review action=build_report "
            "trigger_source=%s query_id=%s region=%s label=%s",
            trigger_source,
            history_query_id,
            run_region,
            label,
        )
        market_analyzer = MarketAnalyzer(
            search_service=search_service,
            analyzer=analyzer,
            region=run_region,
            config=runtime_config,
        )
        review_result = market_analyzer.run_daily_review_with_snapshot()
        review_report = review_result.report
        market_light_snapshots = {}
        _collect_market_light_snapshot(
            market_light_snapshots,
            region=run_region,
            review_result=review_result,
        )
        market_review_payloads = {
            run_region: _coerce_market_review_payload(
                review_result,
                region=run_region,
                report=review_report,
            )
        }
        
        if review_report:
            market_review_payload = _build_combined_market_review_payload(
                review_report=review_report,
                payloads=market_review_payloads,
                region=persist_region,
                language=getattr(runtime_config, "report_language", "zh"),
                root_title=review_text["root_title"],
            )
            markdown_report = _render_market_review_payload_markdown(
                market_review_payload,
                wrapper_title=review_text["root_title"],
            )
            merge_markdown_report = _render_market_review_merge_markdown(
                market_review_payload,
                review_report=review_report,
            )
            if save_report_file:
                # Save the report to a file
                date_str = datetime.now().strftime('%Y%m%d')
                report_filename = f"market_review_{date_str}.md"
                filepath = notifier.save_report_to_file(
                    markdown_report,
                    report_filename
                )
                logger.info(
                    "[MarketReview] component=market_review action=save_report "
                    "trigger_source=%s query_id=%s region=%s path=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                    filepath,
                )

            if persist_history:
                _persist_market_review_history(
                    review_report=review_report,
                    markdown_report=markdown_report,
                    region=persist_region,
                    config=runtime_config,
                    query_id=history_query_id,
                    market_light_snapshots=market_light_snapshots,
                    market_review_payload=market_review_payload,
                )
            
            # Push notifications (skipped in merge mode; the main layer sends them together)
            if merge_notification and send_notification:
                logger.info(
                    "[MarketReview] component=market_review action=skip_standalone_notification "
                    "trigger_source=%s query_id=%s region=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                )
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
            elif send_notification and notifier.is_available():
                # Add the title
                report_content = _render_market_review_payload_markdown(
                    market_review_payload,
                    wrapper_title=review_text["push_title"],
                )

                send_kwargs: Dict[str, Any] = {
                    "email_send_to_all": True,
                    "route_type": "report",
                }
                try:
                    supports_payload = (
                        "structured_payload" in inspect.signature(notifier.send).parameters
                    )
                except (TypeError, ValueError):
                    supports_payload = False
                if supports_payload:
                    send_kwargs["structured_payload"] = market_review_payload
                success = notifier.send(report_content, **send_kwargs)
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="success" if success else "failed",
                    success=success,
                )
                if success:
                    logger.info(
                        "[MarketReview] component=market_review action=send_notification "
                        "status=success trigger_source=%s query_id=%s region=%s",
                        trigger_source,
                        history_query_id,
                        persist_region,
                    )
                else:
                    logger.warning(
                        "[MarketReview] component=market_review action=send_notification "
                        "status=failed trigger_source=%s query_id=%s region=%s",
                        trigger_source,
                        history_query_id,
                        persist_region,
                    )
            elif not send_notification:
                logger.info(
                    "[MarketReview] component=market_review action=skip_notification "
                    "reason=no_notify trigger_source=%s query_id=%s region=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                )
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
            else:
                logger.info(
                    "[MarketReview] component=market_review action=skip_notification "
                    "reason=not_configured trigger_source=%s query_id=%s region=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                )
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
            
            if return_structured:
                return MarketReviewRunResult(
                    report=review_report,
                    market_review_payload=market_review_payload,
                )
            if merge_notification:
                return merge_markdown_report
            return review_report
        
    except GenerationError:
        logger.exception(
            "[MarketReview] component=market_review action=failed "
            "reason=generation_backend_config trigger_source=%s query_id=%s region=%s",
            trigger_source,
            history_query_id,
            persist_region,
        )
        raise
    except Exception:
        logger.exception(
            "[MarketReview] component=market_review action=failed "
            "trigger_source=%s query_id=%s region=%s",
            trigger_source,
            history_query_id,
            persist_region,
        )
    
    return None


def _coerce_market_review_payload(
    review_result: Any,
    *,
    region: str,
    report: Optional[str],
) -> Dict[str, Any]:
    payload = getattr(review_result, "structured_payload", None)
    if isinstance(payload, dict) and payload:
        return payload
    return {
        "version": 1,
        "kind": MARKET_REVIEW_REPORT_TYPE,
        "region": region,
        "title": "",
        "sections": [{"key": "full_review", "title": "Review", "markdown": report or ""}],
        "markdown_report": report or "",
    }


def _build_combined_market_review_payload(
    *,
    review_report: str,
    payloads: Dict[str, Dict[str, Any]],
    region: str,
    language: str,
    root_title: str,
) -> Dict[str, Any]:
    normalized_language = normalize_report_language(language)
    title = root_title.lstrip("#").strip()
    payload = dict(next(iter(payloads.values()))) if payloads else {}
    payload["version"] = payload.get("version") or 1
    payload["kind"] = MARKET_REVIEW_REPORT_TYPE
    payload["region"] = region
    payload["language"] = payload.get("language") or normalized_language
    payload["root_title"] = title
    payload["markdown_report"] = review_report
    return payload


def _render_market_review_payload_markdown(
    payload: Dict[str, Any],
    *,
    wrapper_title: Optional[str] = None,
) -> str:
    """Render Markdown from the structured market-review payload for file/push compatibility."""
    metadata = _market_review_region_metadata(payload.get("region"))
    body = _render_market_review_payload_body(payload)
    if wrapper_title:
        return f"{metadata}{wrapper_title}\n\n{body}".strip()
    return f"{metadata}{body}".strip()


def _render_market_review_merge_markdown(
    payload: Dict[str, Any],
    *,
    review_report: str,
) -> str:
    """Render market-review body for the outer combined notification wrapper."""
    return review_report.strip()


def _render_market_review_payload_body(payload: Dict[str, Any]) -> str:
    return _render_single_market_review_payload(payload)


def _render_single_market_review_payload(payload: Dict[str, Any]) -> str:
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        markdown = payload.get("markdown_report")
        rendered = markdown if isinstance(markdown, str) else ""
        return rendered.strip()

    title = payload.get("title")
    normalized_title = _normalize_market_review_heading(title)
    lines = []
    if isinstance(title, str) and title.strip():
        lines.extend([f"## {title.strip()}", ""])
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_title = str(section.get("title") or "").strip()
        markdown = str(section.get("markdown") or "").strip()
        if not markdown:
            continue
        should_render_section_title = (
            section_title
            and section.get("key") != "overview"
            and _normalize_market_review_heading(section_title) != normalized_title
        )
        if should_render_section_title:
            lines.extend([f"### {section_title}", ""])
        lines.extend([markdown, ""])
    return "\n".join(lines).strip()


def _normalize_market_review_heading(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.lstrip("#").strip().lower().split())


def _persist_market_review_history(
    *,
    review_report: str,
    markdown_report: str,
    region: str,
    config: object,
    query_id: Optional[str] = None,
    market_light_snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
    market_review_payload: Optional[Dict[str, Any]] = None,
) -> int:
    """Persist market review output into the existing analysis history table."""
    try:
        from src.storage import DatabaseManager

        report_language = normalize_report_language(getattr(config, "report_language", "vi"))
        summary = _summarize_market_review(review_report, report_language)
        # Cả vi và en đều dùng nhãn tiếng Anh cố định cho bản ghi lịch sử.
        # Both vi and en use fixed English labels for the history record.
        stock_name = "Market Review"
        operation_advice = "View review"
        trend_prediction = "Market review"

        result = AnalysisResult(
            code=MARKET_REVIEW_HISTORY_CODE,
            name=stock_name,
            sentiment_score=50,
            trend_prediction=trend_prediction,
            operation_advice=operation_advice,
            analysis_summary=summary,
            report_language=report_language,
            news_summary=review_report,
            raw_response=markdown_report,
            data_sources="market_review",
        )

        history_query_id = query_id or f"market_review_{uuid.uuid4().hex}"
        context_snapshot = {
            "report_kind": MARKET_REVIEW_REPORT_TYPE,
            "market_review_region": region,
            "report_language": report_language,
        }
        if market_light_snapshots:
            context_snapshot["market_light_snapshots"] = market_light_snapshots
        if market_review_payload:
            context_snapshot["market_review_payload"] = market_review_payload
        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            context_snapshot["diagnostics"] = diagnostic_snapshot
        context_snapshot["analysis_context_pack_overview"] = _build_market_review_context_overview(
            region=region,
            report_language=report_language,
            diagnostic_snapshot=diagnostic_snapshot,
        )

        db = DatabaseManager.get_instance()
        saved_history_id = db.save_analysis_history(
            result=result,
            query_id=history_query_id,
            report_type=MARKET_REVIEW_REPORT_TYPE,
            news_content=review_report,
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        valid_saved_history_id = (
            saved_history_id
            if (
                isinstance(saved_history_id, int)
                and not isinstance(saved_history_id, bool)
                and saved_history_id > 0
            )
            else None
        )
        record_history_run(
            report_saved=bool(saved_history_id),
            metadata_saved=bool(saved_history_id),
            analysis_history_id=valid_saved_history_id,
        )
        _refresh_market_review_history_diagnostics(query_id=history_query_id)
        if saved_history_id:
            logger.info("Đã lưu bản ghi lịch sử tổng kết thị trường: query_id=%s", history_query_id)
        else:
            logger.warning("Lưu bản ghi lịch sử tổng kết thị trường thất bại: query_id=%s", history_query_id)
        return saved_history_id
    except Exception as exc:
        record_history_run(
            report_saved=False,
            metadata_saved=False,
            error_message=exc,
        )
        logger.warning("Lỗi khi lưu lịch sử tổng kết thị trường, file báo cáo và luồng gửi thông báo vẫn tiếp tục: %s", exc, exc_info=True)
        return 0


def _build_market_review_context_overview(
    *,
    region: str,
    report_language: str,
    diagnostic_snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a low-sensitivity overview block for market-review run-flow rendering."""
    warnings: list[str] = []
    counts = {
        "available": 1,
        "missing": 0,
        "not_supported": 0,
        "fallback": 0,
        "stale": 0,
        "estimated": 0,
        "partial": 0,
        "fetch_failed": 0,
    }
    metadata: Dict[str, Any] = {
        "trigger_source": "market_review",
        "scope": "market_review",
        "report_type": MARKET_REVIEW_REPORT_TYPE,
    }
    if isinstance(diagnostic_snapshot, dict):
        metadata["trigger_source"] = diagnostic_snapshot.get("trigger_source") or metadata["trigger_source"]
        metadata["scope"] = diagnostic_snapshot.get("scope") or metadata["scope"]

    label = "Market review"
    return {
        "pack_version": "market_review/1.0",
        "created_at": datetime.now().isoformat(),
        "subject": {
            "code": MARKET_REVIEW_HISTORY_CODE,
            "stock_name": label,
            "market": region,
        },
        "blocks": [
            {
                "key": MARKET_REVIEW_REPORT_TYPE,
                "label": label,
                "status": "available",
                "source": MARKET_REVIEW_REPORT_TYPE,
                "warnings": warnings,
                "missing_reasons": [],
            }
        ],
        "counts": counts,
        "warnings": warnings,
        "metadata": metadata,
        "data_quality": {
            "level": "good",
            "overall_score": 100,
            "available": 1,
            "total": 1,
            "missing": 0,
        },
    }


def _summarize_market_review(review_report: str, report_language: str) -> str:
    for line in (review_report or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text and not text.startswith("---") and not text.startswith(">"):
            return text[:200]
    if report_language == "vi":
        return "Đã tạo báo cáo tổng kết thị trường."
    return "Market review report generated."
