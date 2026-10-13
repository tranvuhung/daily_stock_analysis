# -*- coding: utf-8 -*-
"""
===================================
API v1 router aggregation
===================================

Responsibilities:
1. Aggregate all v1 endpoint routers
2. Apply the /api/v1 prefix uniformly
"""

from fastapi import APIRouter

from api.v1.endpoints import (
    agent,
    alerts,
    analysis,
    auth,
    backtest,
    data,
    decision_signals,
    health,
    history,
    intelligence,
    portfolio,
    stocks,
    system_config,
    usage,
)

# Create the main v1 router.
# The /api/v1 prefix is mounted in api.app, so newer FastAPI does not treat the sub-route "" as an empty path.
router = APIRouter()

router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Auth"]
)

router.include_router(
    agent.router,
    prefix="/agent",
    tags=["Agent"]
)

router.include_router(
    analysis.router,
    prefix="/analysis",
    tags=["Analysis"]
)

router.include_router(
    history.router,
    prefix="/history",
    tags=["History"]
)

router.include_router(
    stocks.router,
    prefix="/stocks",
    tags=["Stocks"]
)

router.include_router(
    backtest.router,
    prefix="/backtest",
    tags=["Backtest"]
)

router.include_router(
    system_config.router,
    prefix="/system",
    tags=["SystemConfig"]
)

router.include_router(
    usage.router,
    prefix="/usage",
    tags=["Usage"]
)

router.include_router(
    portfolio.router,
    prefix="/portfolio",
    tags=["Portfolio"]
)

router.include_router(
    alerts.router,
    prefix="/alerts",
    tags=["Alerts"]
)

router.include_router(
    decision_signals.router,
    prefix="/decision-signals",
    tags=["DecisionSignals"]
)

router.include_router(
    data.router,
    prefix="/data",
    tags=["Data"]
)

router.include_router(
    intelligence.router,
    prefix="/intelligence",
    tags=["Intelligence"]
)

router.include_router(
    health.router,
    tags=["Health"]
)
