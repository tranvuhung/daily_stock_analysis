# -*- coding: utf-8 -*-
"""
===================================
Batch analysis command
===================================

Batch-analyzes all stocks in the watchlist.
"""

import logging
import threading
import uuid
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class BatchCommand(BotCommand):
    """
    Batch analysis command
    
    Batch-analyzes the configured watchlist and produces a summary report.
    
    Usage:
        /batch      - analyze the whole watchlist
        /batch 3    - analyze only the first 3
    """
    
    @property
    def name(self) -> str:
        return "batch"
    
    @property
    def aliases(self) -> List[str]:
        return ["b", "hangloat", "tatca"]
    
    @property
    def description(self) -> str:
        return "Phân tích hàng loạt danh mục theo dõi"
    
    @property
    def usage(self) -> str:
        return "/batch [số lượng]"
    
    @property
    def admin_only(self) -> bool:
        """Batch analysis requires admin permission (to prevent abuse)"""
        return False  # Can be set to True if needed
    
    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """Run the batch analysis command"""
        from src.config import get_config
        
        config = get_config()
        config.refresh_stock_list()
        
        stock_list = config.stock_list
        
        if not stock_list:
            return BotResponse.error_response(
                "Danh mục theo dõi đang trống, vui lòng cấu hình STOCK_LIST trước"
            )
        
        # Parse the count argument
        limit = None
        if args:
            try:
                limit = int(args[0])
                if limit <= 0:
                    return BotResponse.error_response("Số lượng phải lớn hơn 0")
            except ValueError:
                return BotResponse.error_response(f"Số lượng không hợp lệ: {args[0]}")
        
        # Limit the number of stocks analyzed
        if limit:
            stock_list = stock_list[:limit]
        
        logger.info(f"[BatchCommand] Bắt đầu phân tích hàng loạt {len(stock_list)} mã")
        
        # Run the analysis in a background thread
        thread = threading.Thread(
            target=self._run_batch_analysis,
            args=(stock_list, message),
            daemon=True
        )
        thread.start()
        
        return BotResponse.markdown_response(
            f"✅ **Đã khởi động phân tích hàng loạt**\n\n"
            f"• Số lượng: {len(stock_list)} mã\n"
            f"• Danh sách mã: {', '.join(stock_list[:5])}"
            f"{'...' if len(stock_list) > 5 else ''}\n\n"
            f"Báo cáo tổng hợp sẽ được gửi tự động khi phân tích hoàn tất."
        )
    
    def _run_batch_analysis(self, stock_list: List[str], message: BotMessage) -> None:
        """Run batch analysis in the background"""
        try:
            from src.config import get_config
            from main import StockAnalysisPipeline
            
            config = get_config()
            
            # Create the analysis pipeline
            pipeline = StockAnalysisPipeline(
                config=config,
                source_message=message,
                query_id=uuid.uuid4().hex,
                query_source="bot"
            )
            
            # Run the analysis (the summary report is pushed automatically)
            results = pipeline.run(
                stock_codes=stock_list,
                dry_run=False,
                send_notification=True
            )
            
            logger.info(f"[BatchCommand] Phân tích hàng loạt hoàn tất, thành công {len(results)} mã")
            
        except Exception as e:
            logger.error(f"[BatchCommand] Phân tích hàng loạt thất bại: {e}")
            logger.exception(e)
