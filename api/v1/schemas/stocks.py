# -*- coding: utf-8 -*-
"""
===================================
股票数据相关模型
===================================

职责：
1. 定义Realtime stock quote模型
2. 定义历史 K 线数据模型
"""

from typing import Dict, Literal, Optional, List

from pydantic import BaseModel, ConfigDict, Field

from api.v1.schemas.history import HistoryItem
from api.v1.schemas.intelligence import IntelligenceItem
from api.v1.schemas.research_artifact import ResearchArtifact

StockProfileStatus = Literal["fresh", "partial", "unavailable"]


class StockQuote(BaseModel):
    """Realtime stock quote"""
    
    stock_code: str = Field(..., description="Stock code")
    stock_name: Optional[str] = Field(None, description="Stock name")
    current_price: float = Field(..., description="Current price")
    change: Optional[float] = Field(None, description="Price change")
    change_percent: Optional[float] = Field(None, description="Change percent (%)")
    open: Optional[float] = Field(None, description="Open")
    high: Optional[float] = Field(None, description="High")
    low: Optional[float] = Field(None, description="Low")
    prev_close: Optional[float] = Field(None, description="Previous close")
    volume: Optional[float] = Field(None, description="Volume (shares)")
    amount: Optional[float] = Field(None, description="Turnover (CNY)")
    update_time: Optional[str] = Field(None, description="Updated at")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "current_price": 1800.00,
            "change": 15.00,
            "change_percent": 0.84,
            "open": 1785.00,
            "high": 1810.00,
            "low": 1780.00,
            "prev_close": 1785.00,
            "volume": 10000000,
            "amount": 18000000000,
            "update_time": "2024-01-01T15:00:00"
        }
    })


class KLineData(BaseModel):
    """K-line data point"""
    
    date: str = Field(..., description="Date")
    open: float = Field(..., description="Open")
    high: float = Field(..., description="High")
    low: float = Field(..., description="Low")
    close: float = Field(..., description="Close")
    volume: Optional[float] = Field(None, description="Volume")
    amount: Optional[float] = Field(None, description="Turnover")
    change_percent: Optional[float] = Field(None, description="Change percent (%)")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "date": "2024-01-01",
            "open": 1785.00,
            "high": 1810.00,
            "low": 1780.00,
            "close": 1800.00,
            "volume": 10000000,
            "amount": 18000000000,
            "change_percent": 0.84
        }
    })


class ExtractItem(BaseModel):
    """A single extraction result (code, name, confidence)"""

    code: Optional[str] = Field(None, description="Stock code; None means parsing failed")
    name: Optional[str] = Field(None, description="Stock name (if available)")
    confidence: str = Field("medium", description="Confidence: high/medium/low")


class ExtractFromImageResponse(BaseModel):
    """Image stock code extraction response"""

    codes: List[str] = Field(..., description="Extracted stock codes (deduplicated, kept for backward compatibility)")
    items: List[ExtractItem] = Field(default_factory=list, description="Extraction details (code, name and confidence)")
    raw_text: Optional[str] = Field(None, description="Raw LLM response (for debugging)")


class StockHistoryResponse(BaseModel):
    """Historical stock quote response"""
    
    stock_code: str = Field(..., description="Stock code")
    stock_name: Optional[str] = Field(None, description="Stock name")
    period: str = Field(..., description="K-line interval")
    data: List[KLineData] = Field(default_factory=list, description="K-line data list")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "period": "daily",
            "data": []
        }
    })


class StockProfileQuoteBlock(BaseModel):
    status: StockProfileStatus
    data: Optional[StockQuote] = None
    limitations: List[str] = Field(default_factory=list)


class StockProfileHistoryBlock(BaseModel):
    status: StockProfileStatus
    period: Literal["daily"] = "daily"
    data: List[KLineData] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


class StockProfileResearchData(BaseModel):
    latest_report: Optional[HistoryItem] = None
    recent_reports: List[HistoryItem] = Field(default_factory=list)
    structured_report: Optional[ResearchArtifact] = None


class StockProfileResearchBlock(BaseModel):
    status: StockProfileStatus
    data: StockProfileResearchData = Field(default_factory=StockProfileResearchData)
    limitations: List[str] = Field(default_factory=list)


class StockProfileIntelligenceBlock(BaseModel):
    status: StockProfileStatus
    items: List[IntelligenceItem] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


class StockProfilePortfolioRelation(BaseModel):
    held: bool = False
    matched_markets: List[str] = Field(default_factory=list)


class StockProfilePortfolioBlock(BaseModel):
    status: StockProfileStatus
    data: StockProfilePortfolioRelation = Field(default_factory=StockProfilePortfolioRelation)
    limitations: List[str] = Field(default_factory=list)


class StockProfileMonitorData(BaseModel):
    total_rule_count: int = 0
    enabled_rule_count: int = 0
    rule_ids: List[int] = Field(default_factory=list)


class StockProfileMonitorBlock(BaseModel):
    status: StockProfileStatus
    data: StockProfileMonitorData = Field(default_factory=StockProfileMonitorData)
    limitations: List[str] = Field(default_factory=list)


class StockProfileEvidenceQuality(BaseModel):
    status: StockProfileStatus
    blocks: Dict[str, StockProfileStatus] = Field(default_factory=dict)
    limitations: List[str] = Field(default_factory=list)


class StockProfileResponse(BaseModel):
    requested_code: str
    canonical_code: str
    market: Literal["cn", "hk", "us", "jp", "kr", "tw"]
    as_of: str
    quote: StockProfileQuoteBlock
    history: StockProfileHistoryBlock
    research: StockProfileResearchBlock
    intelligence: StockProfileIntelligenceBlock
    portfolio: StockProfilePortfolioBlock
    monitors: StockProfileMonitorBlock
    evidence_quality: StockProfileEvidenceQuality
