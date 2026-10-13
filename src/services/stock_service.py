# -*- coding: utf-8 -*-
"""
===================================
Stock data service layer
===================================

Responsibilities:
1. Encapsulate stock data fetching logic
2. Provide realtime quote and historical data interfaces
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)


class StockService:
    """
    Stock data service

    Encapsulates the business logic for fetching stock data
    """
    
    def __init__(self):
        """Initialize the stock data service"""
        self.repo = StockRepository()
    
    def get_realtime_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        Get the realtime quote for a stock

        Args:
            stock_code: Stock code

        Returns:
            Realtime quote data dict
        """
        try:
            # Call the data fetcher to get the realtime quote
            from data_provider.base import DataFetcherManager
            
            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(stock_code)
            
            if quote is None:
                logger.warning(f"Lấy báo giá realtime của {stock_code} thất bại")
                return None
            
            # UnifiedRealtimeQuote is a dataclass; use getattr for safe field access
            # Field mapping: UnifiedRealtimeQuote -> API response
            # - code -> stock_code
            # - name -> stock_name
            # - price -> current_price
            # - change_amount -> change
            # - change_pct -> change_percent
            # - open_price -> open
            # - high -> high
            # - low -> low
            # - pre_close -> prev_close
            # - volume -> volume
            # - amount -> amount
            return {
                "stock_code": getattr(quote, "code", stock_code),
                "stock_name": getattr(quote, "name", None),
                "current_price": getattr(quote, "price", 0.0) or 0.0,
                "change": getattr(quote, "change_amount", None),
                "change_percent": getattr(quote, "change_pct", None),
                "open": getattr(quote, "open_price", None),
                "high": getattr(quote, "high", None),
                "low": getattr(quote, "low", None),
                "prev_close": getattr(quote, "pre_close", None),
                "volume": getattr(quote, "volume", None),
                "amount": getattr(quote, "amount", None),
                "update_time": datetime.now().isoformat(),
            }
            
        except ImportError:
            logger.warning("Không tìm thấy DataFetcherManager, dùng dữ liệu giữ chỗ")
            return self._get_placeholder_quote(stock_code)
        except Exception as e:
            logger.error(f"Lấy báo giá realtime thất bại: {e}", exc_info=True)
            return None
    
    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get historical quotes for a stock

        Args:
            stock_code: Stock code
            period: K-line period (daily/weekly/monthly)
            days: Number of days to fetch

        Returns:
            Historical quote data dict

        Raises:
            ValueError: When period is not daily (weekly/monthly not implemented yet)
        """
        # Validate period; only daily is supported
        if period != "daily":
            raise ValueError(
                f"Chưa hỗ trợ chu kỳ '{period}', hiện chỉ hỗ trợ 'daily'. "
                "Tổng hợp weekly/monthly sẽ có ở phiên bản sau."
            )
        
        try:
            # Call the data fetcher to get historical data
            from data_provider.base import DataFetcherManager
            
            manager = DataFetcherManager()
            df, source = manager.get_daily_data(stock_code, days=days)
            
            if df is None or df.empty:
                logger.warning(f"Lấy dữ liệu lịch sử của {stock_code} thất bại")
                return {"stock_code": stock_code, "period": period, "data": []}
            
            # Get the stock name
            stock_name = manager.get_stock_name(stock_code)
            
            # Convert to response format
            data = []
            for _, row in df.iterrows():
                date_val = row.get("date")
                if hasattr(date_val, "strftime"):
                    date_str = date_val.strftime("%Y-%m-%d")
                else:
                    date_str = str(date_val)
                
                data.append({
                    "date": date_str,
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)) if row.get("volume") else None,
                    "amount": float(row.get("amount", 0)) if row.get("amount") else None,
                    "change_percent": float(row.get("pct_chg", 0)) if row.get("pct_chg") else None,
                })
            
            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "period": period,
                "data": data,
            }
            
        except ImportError:
            logger.warning("Không tìm thấy DataFetcherManager, trả về dữ liệu rỗng")
            return {"stock_code": stock_code, "period": period, "data": []}
        except Exception as e:
            logger.error(f"Lấy dữ liệu lịch sử thất bại: {e}", exc_info=True)
            return {"stock_code": stock_code, "period": period, "data": []}
    
    def _get_placeholder_quote(self, stock_code: str) -> Dict[str, Any]:
        """
        Get placeholder quote data (for testing)

        Args:
            stock_code: Stock code

        Returns:
            Placeholder quote data
        """
        return {
            "stock_code": stock_code,
            "stock_name": f"Cổ phiếu {stock_code}",
            "current_price": 0.0,
            "change": None,
            "change_percent": None,
            "open": None,
            "high": None,
            "low": None,
            "prev_close": None,
            "volume": None,
            "amount": None,
            "update_time": datetime.now().isoformat(),
        }
