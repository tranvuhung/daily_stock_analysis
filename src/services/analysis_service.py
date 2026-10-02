# -*- coding: utf-8 -*-
"""
===================================
Analysis service layer
===================================

Responsibilities:
1. Encapsulate stock analysis logic
2. Call the analyzer and pipeline to run analysis
3. Save analysis results to the database
"""

import logging
import copy
import uuid
from typing import Optional, Dict, Any, Callable, List

from src.repositories.analysis_repo import AnalysisRepository
from src.report_language import (
    get_analysis_error_text,
    get_sentiment_label,
    get_localized_stock_name,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.market_phase_summary import extract_market_phase_summary
from src.schemas.decision_action import build_action_fields
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    build_run_diagnostic_summary,
    get_current_diagnostic_context,
    reset_run_diagnostic_context,
)
from src.services.empty_news import empty_news_disclosure

logger = logging.getLogger(__name__)


def asset_type_from_canonical_code(code: Any) -> Optional[str]:
    """Derive the authoritative ``asset_type`` for a canonical stock/index code.

    Uses :func:`parse_analysis_target` — the single asset-type authority — on
    the *canonical* code, never the display code, so ``sh000016`` (index) and
    bare ``000016`` (stock) are distinguished by the parser rather than by
    display normalization. Returns ``None`` for market review / empty /
    unsupported codes, so legacy clients and market reviews simply omit the
    optional field.
    """
    text = str(code or "").strip()
    if not text:
        return None
    if text.upper() == "MARKET":
        return None

    from src.services.stock_list_parser import ParseStatus, parse_analysis_target

    target = parse_analysis_target(text)
    if target.asset_type == ParseStatus.INDEX:
        return "index"
    if target.asset_type == ParseStatus.STOCK:
        return "stock"
    return None


class AnalysisService:
    """
    Analysis service

    Encapsulates stock-analysis business logic
    """
    
    def __init__(self):
        """Initialize the analysis service"""
        self.repo = AnalysisRepository()
        self.last_error: Optional[str] = None
    
    def analyze_stock(
        self,
        stock_code: str,
        report_type: str = "detailed",
        force_refresh: bool = False,
        query_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        send_notification: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        skills: Optional[List[str]] = None,
        analysis_phase: str = "auto",
        query_source: str = "api",
        portfolio_context: Optional[Dict[str, Any]] = None,
        report_language: Optional[str] = None,
        analysis_target: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run stock analysis

        Args:
            stock_code: Stock code
            report_type: Report type (simple/detailed)
            force_refresh: Whether to force a refresh
            query_id: Query ID (optional)
            send_notification: Whether to send notifications (sent by default for API triggers)
            analysis_phase: Requested analysis phase override (auto/premarket/intraday/postmarket)
            analysis_target: Optional structured analysis target (index targets are carried
                through to the pipeline; otherwise indices degrade to stock semantics)

        Returns:
            Analysis result dict containing:
            - stock_code: Stock code
            - stock_name: Stock name
            - report: Analysis report
        """
        try:
            self.last_error = None
            # Import analysis modules
            from src.config import get_config
            from src.core.pipeline import StockAnalysisPipeline
            from src.enums import ReportType
            
            # Generate query_id
            if query_id is None:
                query_id = uuid.uuid4().hex
            effective_trace_id = trace_id or query_id
            diag_token = None
            if get_current_diagnostic_context() is None:
                diag_token = activate_run_diagnostic_context(
                    trace_id=effective_trace_id,
                    query_id=query_id,
                    stock_code=stock_code,
                    trigger_source=query_source or "api",
                )
            
            # Load config
            config = get_config()
            normalized_report_language = normalize_report_language(report_language, default="")
            if normalized_report_language:
                config = copy.copy(config)
                config.report_language = normalized_report_language
            
            # Create the analysis pipeline
            pipeline = StockAnalysisPipeline(
                config=config,
                query_id=query_id,
                trace_id=effective_trace_id,
                query_source=query_source or "api",
                progress_callback=progress_callback,
                analysis_skills=skills,
                analysis_phase=analysis_phase,
                portfolio_context=portfolio_context,
            )
            
            # Determine report type (API: simple/detailed/full/brief -> ReportType)
            rt = ReportType.from_str(report_type)
            
            # Run analysis
            result = pipeline.process_single_stock(
                code=stock_code,
                skip_analysis=False,
                single_stock_notify=send_notification,
                report_type=rt,
                analysis_target=analysis_target,
            )
            
            if result is None:
                logger.warning(f"分析股票 {stock_code} 返回空结果")
                self.last_error = self.last_error or get_analysis_error_text(
                    "empty_result", report_language, code=stock_code
                )
                return None

            if not getattr(result, "success", True):
                self.last_error = getattr(result, "error_message", None) or get_analysis_error_text(
                    "failed", report_language, code=stock_code
                )
                logger.warning(f"分析股票 {stock_code} 未成功完成: {self.last_error}")
                return None
            
            # Build the response
            return self._build_analysis_response(result, query_id, report_type=rt.value)
            
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"分析股票 {stock_code} 失败: {e}", exc_info=True)
            return None
        finally:
            reset_run_diagnostic_context(locals().get("diag_token"))
    
    def _build_analysis_response(
        self, 
        result: Any, 
        query_id: str,
        report_type: str = "detailed",
    ) -> Dict[str, Any]:
        """
        Build the analysis response

        Args:
            result: AnalysisResult object
            query_id: Query ID
            report_type: Normalized report type

        Returns:
            Formatted response dict
        """
        # Get sniper price levels
        sniper_points = {}
        if hasattr(result, 'get_sniper_points'):
            sniper_points = result.get_sniper_points() or {}
        
        # Compute sentiment label
        report_language = normalize_report_language(getattr(result, "report_language", "zh"))
        sentiment_label = get_sentiment_label(result.sentiment_score, report_language)
        stock_name = get_localized_stock_name(getattr(result, "name", None), result.code, report_language)
        action_fields = build_action_fields(
            operation_advice=getattr(result, "operation_advice", None),
            explicit_action=getattr(result, "action", None),
            report_type=report_type,
            report_language=report_language,
            sentiment_score=getattr(result, "sentiment_score", None),
            guardrail_reason=getattr(result, "guardrail_reason", None),
            align_with_score=True,
        )
        diagnostic_context = get_current_diagnostic_context()
        trace_id = diagnostic_context.trace_id if diagnostic_context is not None else query_id
        diagnostic_snapshot = diagnostic_context.snapshot() if diagnostic_context is not None else None
        diagnostic_context_snapshot = getattr(result, "diagnostic_context_snapshot", None)
        market_phase_summary = extract_market_phase_summary(diagnostic_context_snapshot)
        if isinstance(diagnostic_context_snapshot, dict):
            context_snapshot = dict(diagnostic_context_snapshot)
            if diagnostic_snapshot is not None:
                context_snapshot["diagnostics"] = diagnostic_snapshot
        elif diagnostic_snapshot is not None:
            context_snapshot = {"diagnostics": diagnostic_snapshot}
        else:
            context_snapshot = None
        diagnostic_summary = build_run_diagnostic_summary(
            context_snapshot=context_snapshot,
            raw_result=result.to_dict() if hasattr(result, "to_dict") else None,
            report_saved=True,
            query_id=query_id,
            stock_code=result.code,
        )
        
        # Build report structure
        report = {
            "meta": {
                "query_id": query_id,
                "trace_id": trace_id,
                "stock_code": result.code,
                "stock_name": stock_name,
                "report_type": report_type,
                "report_language": report_language,
                "current_price": result.current_price,
                "change_pct": result.change_pct,
                "model_used": getattr(result, "model_used", None),
                "market_phase_summary": market_phase_summary,
                "asset_type": asset_type_from_canonical_code(result.code),
            },
            "summary": {
                "analysis_summary": result.analysis_summary,
                "operation_advice": localize_operation_advice(result.operation_advice, report_language),
                "action": action_fields["action"],
                "action_label": action_fields["action_label"],
                "trend_prediction": localize_trend_prediction(result.trend_prediction, report_language),
                "sentiment_score": result.sentiment_score,
                "sentiment_label": sentiment_label,
            },
            "strategy": {
                "ideal_buy": sniper_points.get("ideal_buy"),
                "secondary_buy": sniper_points.get("secondary_buy"),
                "stop_loss": sniper_points.get("stop_loss"),
                "take_profit": sniper_points.get("take_profit"),
            },
            "details": {
                "news_summary": result.news_summary,
                "empty_news_disclosure": empty_news_disclosure(result, report_language),
                "technical_analysis": result.technical_analysis,
                "fundamental_analysis": result.fundamental_analysis,
                "risk_warning": result.risk_warning,
            }
        }
        if hasattr(result, "to_dict"):
            raw_result_payload = result.to_dict()
            if isinstance(raw_result_payload, dict):
                report["details"]["raw_result"] = raw_result_payload

        return {
            "query_id": query_id,
            "trace_id": trace_id,
            "stock_code": result.code,
            "stock_name": stock_name,
            "report": report,
            "diagnostic_summary": diagnostic_summary,
        }
