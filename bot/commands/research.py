# -*- coding: utf-8 -*-
"""
Research command — deep research on a stock or market topic.

Usage:
    /research HPG                           -> Nghiên cứu sâu Hòa Phát / deep research on HPG
    /research HPG rủi ro lợi nhuận gần đây  -> Câu hỏi cụ thể / focused question
    /research triển vọng ngành ngân hàng     -> Nghiên cứu theo chủ đề / topic-based research
"""

import logging
import time
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from src.config import get_config
from src.services.stock_code_utils import normalize_code

logger = logging.getLogger(__name__)


class ResearchCommand(BotCommand):
    """
    Research command handler — invoke the deep research agent.

    Usage:
        /research HPG                       -> Nghiên cứu sâu một mã / deep research on a stock
        /research HPG rủi ro lợi nhuận      -> Câu hỏi cụ thể / focused question
        /research ngành thép triển vọng     -> Nghiên cứu ngành / sector research
    """

    @property
    def name(self) -> str:
        return "research"

    @property
    def aliases(self) -> List[str]:
        return ["nghiencuu", "deepsearch"]

    @property
    def description(self) -> str:
        return "Nghiên cứu sâu một mã cổ phiếu hoặc chủ đề thị trường"

    @property
    def usage(self) -> str:
        return "/research <mã|chủ đề> [câu hỏi cụ thể]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        if not args:
            return BotResponse.text_response(
                f"Cách dùng: {self.usage}\n"
                "Ví dụ: /research HPG rủi ro gần đây là gì\n"
                "Ví dụ: /research triển vọng ngành ngân hàng"
            )

        config = get_config()

        if not config.agent_mode:
            return BotResponse.text_response(
                "⚠️ Chế độ Agent chưa bật, không thể dùng tính năng nghiên cứu sâu.\nVui lòng đặt `AGENT_MODE=true` trong cấu hình."
            )

        # Parse arguments — first arg may be stock code, rest is the question
        query_parts = list(args)
        stock_code: Optional[str] = None

        # Nhận diện mã Việt Nam ở tham số đầu (HPG, hpg, HPG.VN, FUEVN100, VNINDEX)
        # Try to detect a Vietnam stock/fund/index code in the first argument
        detected = normalize_code(query_parts[0])
        if detected:
            stock_code = detected
            query_parts = query_parts[1:]

        # Build the research query
        if query_parts:
            question = " ".join(query_parts)
        elif stock_code:
            question = f"Comprehensive deep research on stock {stock_code}: fundamentals, technicals, news sentiment, and risk factors"
        else:
            question = " ".join(args)

        if stock_code:
            question = f"[Stock: {stock_code}] {question}"

        # Run the research agent
        try:
            from src.agent.research import ResearchAgent
            from src.agent.factory import get_tool_registry
            from src.agent.llm_adapter import LLMToolAdapter

            registry = get_tool_registry()
            llm_adapter = LLMToolAdapter(config)
            budget = getattr(config, "agent_deep_research_budget", 30000)

            agent = ResearchAgent(
                tool_registry=registry,
                llm_adapter=llm_adapter,
                token_budget=budget,
            )

            research_timeout = getattr(config, "agent_deep_research_timeout", 180)
            logger.info("[ResearchCommand] Starting deep research (timeout=%ds): %s", research_timeout, question[:100])
            t0 = time.time()
            result = agent.research(
                question,
                {"stock_code": stock_code, "stock_name": ""} if stock_code else None,
                timeout_seconds=research_timeout,
            )
            duration = result.duration_s or round(time.time() - t0, 1)

            if getattr(result, "timed_out", False):
                logger.warning("[ResearchCommand] Deep research timed out after %ss", duration)
                return BotResponse.text_response(
                    f"⏳ Nghiên cứu sâu quá thời gian ({duration}s / {research_timeout}s), vui lòng thử lại sau hoặc thu hẹp phạm vi."
                )

            if result.success:
                # Build rich response
                header = "🔬 **Báo cáo nghiên cứu sâu (Deep Research Report)**\n"
                if stock_code:
                    header += f"Mã: {stock_code}\n"
                header += f"Câu hỏi con: {len(result.sub_questions)} | Nguồn: {result.findings_count}\n"
                header += f"Thời gian: {duration}s | Tokens: {result.total_tokens:,}\n"
                header += "─" * 40 + "\n\n"

                report = header + result.report

                # Truncate if too long for bot message
                max_len = 4000
                if len(report) > max_len:
                    report = report[:max_len] + "\n\n... (báo cáo đã rút gọn, xem bản đầy đủ qua API)"

                return BotResponse.markdown_response(report)
            else:
                return BotResponse.text_response(
                    f"⚠️ Nghiên cứu chưa hoàn tất thành công.\n"
                    f"Kết quả một phần: thu thập được {result.findings_count} phát hiện.\n"
                    f"Thời gian: {duration}s"
                )

        except Exception as exc:
            logger.error("[ResearchCommand] Error: %s", exc, exc_info=True)
            return BotResponse.text_response(f"❌ Nghiên cứu thất bại: {exc}")
