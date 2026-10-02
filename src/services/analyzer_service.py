# -*- coding: utf-8 -*-
"""
===================================
A-share watchlist intelligent analysis system - analysis service layer
===================================

Responsibilities:
1. Encapsulate core analysis logic for multiple callers (CLI, WebUI, Bot)
2. Provide a clean API that does not depend on command-line arguments
3. Support dependency injection for testing and extension
4. Centrally manage analysis flow and configuration
"""

import uuid
from typing import List, Optional

from src.analyzer import AnalysisResult
from src.core.market_review import run_market_review
from src.core.pipeline import StockAnalysisPipeline
from src.config import Config, get_config
from src.enums import ReportType
from src.notification import NotificationService


def analyze_stock(
    stock_code: str,
    config: Config = None,
    full_report: bool = False,
    notifier: Optional[NotificationService] = None,
) -> Optional[AnalysisResult]:
    """
    Analyze a single stock

    Args:
        stock_code: Stock code
        config: Config object (optional, defaults to the singleton)
        full_report: Whether to generate a full report
        notifier: Notification service (optional)

    Returns:
        Analysis result object
    """
    if config is None:
        config = get_config()

    # Create the analysis pipeline
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=uuid.uuid4().hex,
        query_source="cli"
    )

    # Use the notification service (if provided)
    if notifier:
        pipeline.notifier = notifier

    # Set report type based on full_report
    report_type = ReportType.FULL if full_report else ReportType.SIMPLE

    # Run single-stock analysis
    result = pipeline.process_single_stock(
        code=stock_code,
        skip_analysis=False,
        single_stock_notify=notifier is not None,
        report_type=report_type,
    )

    return result


def analyze_stocks(
    stock_codes: List[str],
    config: Config = None,
    full_report: bool = False,
    notifier: Optional[NotificationService] = None,
) -> List[AnalysisResult]:
    """
    Analyze multiple stocks

    Args:
        stock_codes: List of stock codes
        config: Config object (optional, defaults to the singleton)
        full_report: Whether to generate a full report
        notifier: Notification service (optional)

    Returns:
        List of analysis results
    """
    if config is None:
        config = get_config()

    results = []
    for stock_code in stock_codes:
        result = analyze_stock(stock_code, config, full_report, notifier)
        if result:
            results.append(result)

    return results


def perform_market_review(
    config: Config = None,
    notifier: Optional[NotificationService] = None,
) -> Optional[str]:
    """
    Run the market review

    Args:
        config: Config object (optional, defaults to the singleton)
        notifier: Notification service (optional)

    Returns:
        Market review report content
    """
    if config is None:
        config = get_config()

    # Create the analysis pipeline to obtain the analyzer and search_service
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=uuid.uuid4().hex,
        query_source="cli",
    )

    # Use the provided notification service or create a new one
    review_notifier = notifier or pipeline.notifier

    # Call the market review function
    return run_market_review(
        notifier=review_notifier,
        analyzer=pipeline.analyzer,
        search_service=pipeline.search_service,
        config=config,
        trigger_source="service",
    )
