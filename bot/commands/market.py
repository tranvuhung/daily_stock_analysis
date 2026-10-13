# -*- coding: utf-8 -*-
"""
===================================
Lệnh điểm lại thị trường / Market review command
===================================

Chạy phân tích điểm lại thị trường Việt Nam và sinh báo cáo tổng quan.
Runs a Vietnam market review analysis and generates a market overview report.
"""

import logging
import threading
from typing import Any, List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)


class MarketCommand(BotCommand):
    """
    Lệnh điểm lại thị trường / Market review command

    Chạy điểm lại thị trường Việt Nam, gồm:
    - Diễn biến các chỉ số chính (VN-Index, VN30, HNX-Index, UPCoM)
    - Nhóm ngành nổi bật
    - Tâm lý thị trường
    - Triển vọng

    Usage:
        /market - chạy điểm lại thị trường
    """

    @property
    def name(self) -> str:
        return "market"

    @property
    def aliases(self) -> List[str]:
        return ["m", "thitruong", "diemlai", "tongquan"]

    @property
    def description(self) -> str:
        return "Điểm lại thị trường Việt Nam"

    @property
    def usage(self) -> str:
        return "/market"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """Thực thi lệnh điểm lại thị trường / Execute the market review command"""
        config = self._get_config()
        lock_token = self._try_acquire_market_review_lock(config)
        if lock_token is None:
            return BotResponse.markdown_response("⚠️ Tác vụ điểm lại thị trường đang chạy, vui lòng thử lại sau.")

        thread = threading.Thread(
            target=self._run_market_review,
            args=(message, config, lock_token),
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "[MarketCommand] Không khởi động được luồng nền điểm lại thị trường: %s",
                exc,
            )
            self._release_market_review_lock(lock_token)
            return BotResponse.error_response(
                "Khởi động điểm lại thị trường thất bại, đã giải phóng khoá chạy; vui lòng thử lại sau"
            )

        return BotResponse.markdown_response(
            "✅ **Đã khởi động điểm lại thị trường**\n\n"
            "Đang phân tích:\n"
            "• Diễn biến các chỉ số chính\n"
            "• Nhóm ngành nổi bật\n"
            "• Tâm lý thị trường\n"
            "• Triển vọng phiên tới\n\n"
            "Kết quả sẽ được gửi tự động khi phân tích hoàn tất."
        )

    def _get_config(self):
        from src.config import get_config
        return get_config()

    def _try_acquire_market_review_lock(self, config):
        from src.core.market_review_lock import try_acquire_market_review_lock
        return try_acquire_market_review_lock(config)

    def _release_market_review_lock(self, lock_token: Optional[Any]) -> None:
        from src.core.market_review_lock import release_market_review_lock
        release_market_review_lock(lock_token)

    def _compute_market_review_override_region(self, config) -> Optional[str]:
        if not getattr(config, "trading_day_check_enabled", True):
            return None

        try:
            from src.core.trading_calendar import (
                get_open_markets_today,
                compute_effective_region,
            )

            open_markets = get_open_markets_today()
            # Chỉ còn vùng ``vn``; giá trị cấu hình cũ được chuẩn hoá ở tầng dưới.
            # Only the ``vn`` region remains; legacy config values are normalized downstream.
            return compute_effective_region(
                getattr(config, "market_review_region", "vn") or "vn",
                open_markets,
            )
        except Exception as exc:
            logger.warning("Lọc ngày giao dịch thất bại, vẫn tiếp tục điểm lại thị trường theo cấu hình: %s", exc)
            return None

    def _run_market_review(
        self,
        message: BotMessage,
        config,
        lock_token: Optional[Any],
    ) -> None:
        """Chạy điểm lại thị trường ở nền / Run the market review in the background"""
        try:
            override_region = self._compute_market_review_override_region(config)
            if override_region == "":
                from src.notification import NotificationService
                notifier = NotificationService(source_message=message)
                logger.info("[MarketCommand] Hôm nay thị trường nghỉ giao dịch, bỏ qua điểm lại thị trường")
                if notifier.is_available():
                    notifier.send(
                        "🎯 Điểm lại thị trường\n\nHôm nay thị trường Việt Nam nghỉ giao dịch, đã bỏ qua điểm lại thị trường.",
                        email_send_to_all=True,
                        route_type="report",
                    )
                return

            from src.core.market_review_runtime import build_market_review_runtime
            from src.core.market_review import run_market_review

            notifier, analyzer, search_service = build_market_review_runtime(
                config,
                source_message=message,
            )
            review_report = run_market_review(
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=True,
                override_region=override_region,
                trigger_source="bot",
            )
            if review_report:
                logger.info("[MarketCommand] Điểm lại thị trường hoàn tất và đã gửi")
            else:
                logger.warning("[MarketCommand] Điểm lại thị trường trả về kết quả rỗng")
        except Exception as e:
            logger.error("[MarketCommand] Điểm lại thị trường thất bại: %s", e)
            logger.exception(e)
        finally:
            self._release_market_review_lock(lock_token)
