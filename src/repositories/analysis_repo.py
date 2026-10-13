# -*- coding: utf-8 -*-
"""
===================================
Analysis history data access layer
===================================

Responsibilities:
1. Encapsulate database operations for analysis history
2. Provide CRUD interfaces
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from src.storage import DatabaseManager, AnalysisHistory

logger = logging.getLogger(__name__)


class AnalysisRepository:
    """
    Analysis history data access layer
    
    Encapsulates database operations on the AnalysisHistory table
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        Initialise the data access layer
        
        Args:
            db_manager: database manager (optional, defaults to the singleton)
        """
        self.db = db_manager or DatabaseManager.get_instance()
    
    def get_by_query_id(self, query_id: str) -> Optional[AnalysisHistory]:
        """
        Get an analysis record by query_id
        
        Args:
            query_id: query ID
            
        Returns:
            AnalysisHistory object, or None if not found
        """
        try:
            records = self.db.get_analysis_history(query_id=query_id, limit=1)
            return records[0] if records else None
        except Exception as e:
            logger.error(f"Truy vấn bản ghi phân tích thất bại: {e}")
            return None
    
    def get_list(
        self,
        code: Optional[str] = None,
        days: int = 30,
        limit: int = 50
    ) -> List[AnalysisHistory]:
        """
        Get a list of analysis records
        
        Args:
            code: stock code filter
            days: time range (days)
            limit: maximum number of results
            
        Returns:
            List of AnalysisHistory objects
        """
        try:
            return self.db.get_analysis_history(
                code=code,
                days=days,
                limit=limit
            )
        except Exception as e:
            logger.error(f"Lấy danh sách phân tích thất bại: {e}")
            return []
    
    def save(
        self,
        result: Any,
        query_id: str,
        report_type: str,
        news_content: Optional[str] = None,
        context_snapshot: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Save an analysis result
        
        Args:
            result: analysis result object
            query_id: query ID
            report_type: report type
            news_content: news content
            context_snapshot: context snapshot
            
        Returns:
            The id of the newly saved AnalysisHistory; 0 if saving fails.
        """
        try:
            return self.db.save_analysis_history(
                result=result,
                query_id=query_id,
                report_type=report_type,
                news_content=news_content,
                context_snapshot=context_snapshot
            )
        except Exception as e:
            logger.error(f"Lưu kết quả phân tích thất bại: {e}")
            return 0
    
    def count_by_code(self, code: str, days: int = 30) -> int:
        """
        Count the analysis records for a given stock
        
        Args:
            code: stock code
            days: time range (days)
            
        Returns:
            Number of records
        """
        try:
            records = self.db.get_analysis_history(code=code, days=days, limit=1000)
            return len(records)
        except Exception as e:
            logger.error(f"Thống kê bản ghi phân tích thất bại: {e}")
            return 0
