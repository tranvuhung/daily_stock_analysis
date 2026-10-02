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
        return ["b", "批量", "全部"]
    
    @property
    def description(self) -> str:
        return "批量分析自选股"
    
    @property
    def usage(self) -> str:
        return "/batch [数量]"
    
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
                "自选股列表为空，请先配置 STOCK_LIST"
            )
        
        # Parse the count argument
        limit = None
        if args:
            try:
                limit = int(args[0])
                if limit <= 0:
                    return BotResponse.error_response("数量必须大于0")
            except ValueError:
                return BotResponse.error_response(f"无效的数量: {args[0]}")
        
        # Limit the number of stocks analyzed
        if limit:
            stock_list = stock_list[:limit]
        
        logger.info(f"[BatchCommand] 开始批量分析 {len(stock_list)} 只股票")
        
        # Run the analysis in a background thread
        thread = threading.Thread(
            target=self._run_batch_analysis,
            args=(stock_list, message),
            daemon=True
        )
        thread.start()
        
        return BotResponse.markdown_response(
            f"✅ **批量分析任务已启动**\n\n"
            f"• 分析数量: {len(stock_list)} 只\n"
            f"• 股票列表: {', '.join(stock_list[:5])}"
            f"{'...' if len(stock_list) > 5 else ''}\n\n"
            f"分析完成后将自动推送汇总报告。"
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
            
            logger.info(f"[BatchCommand] 批量分析完成，成功 {len(results)} 只")
            
        except Exception as e:
            logger.error(f"[BatchCommand] 批量分析失败: {e}")
            logger.exception(e)
