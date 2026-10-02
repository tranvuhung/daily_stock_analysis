# -*- coding: utf-8 -*-
"""
===================================
Health check endpoint
===================================

Responsibilities:
1. Provide the /api/v1/health Health check endpoint
2. Used by load balancers and monitoring systems
"""

from datetime import datetime

from fastapi import APIRouter

from api.v1.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint
    
    Used by load balancers or monitoring systems to check service status
    
    Returns:
        HealthResponse: Service status and timestamp
    """
    return HealthResponse(
        status="ok",
        timestamp=datetime.now().isoformat()
    )
