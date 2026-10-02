# -*- coding: utf-8 -*-
"""
===================================
Common response models
===================================

Responsibilities:
1. Define common response models (HealthResponse, ErrorResponse, etc.)
2. Provide a unified response format
"""

from typing import Optional, Any

from pydantic import BaseModel, ConfigDict, Field


class RootResponse(BaseModel):
    """API root route response"""
    
    message: str = Field(..., description="API status message", json_schema_extra={"example": "Daily Stock Analysis API is running"})
    version: Optional[str] = Field(None, description="API version", json_schema_extra={"example": "1.0.0"})
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "message": "Daily Stock Analysis API is running",
            "version": "1.0.0"
        }
    })


class HealthResponse(BaseModel):
    """Health check response"""
    
    status: str = Field(..., description="Service status", json_schema_extra={"example": "ok"})
    timestamp: Optional[str] = Field(None, description="Timestamp")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "status": "ok",
            "timestamp": "2024-01-01T12:00:00"
        }
    })


class ErrorResponse(BaseModel):
    """Error response"""
    
    error: str = Field(..., description="Error type", json_schema_extra={"example": "validation_error"})
    message: str = Field(..., description="Error detail", json_schema_extra={"example": "Invalid request parameters"})
    detail: Optional[Any] = Field(None, description="Additional error information")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "error": "not_found",
            "message": "Resource not found",
            "detail": None
        }
    })


class SuccessResponse(BaseModel):
    """Generic success response"""
    
    success: bool = Field(True, description="Whether the operation succeeded")
    message: Optional[str] = Field(None, description="Success message")
    data: Optional[Any] = Field(None, description="Response data")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "success": True,
            "message": "Operation succeeded",
            "data": None
        }
    })
