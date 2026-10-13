# -*- coding: utf-8 -*-
"""
===================================
Lệnh phân tích cổ phiếu / Stock analysis command
===================================

Phân tích một mã cổ phiếu, chứng chỉ quỹ hoặc chỉ số Việt Nam và gọi AI sinh báo cáo.
Analyzes a Vietnam stock, fund certificate or index and calls AI to generate a report.
"""

import logging
from typing import List, Optional, Tuple

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse
from src.services.stock_list_parser import ParseStatus, parse_analysis_target

logger = logging.getLogger(__name__)

# Từ khoá chuyển sang báo cáo đầy đủ / Keywords that switch to the full report
_FULL_REPORT_KEYWORDS = {"full", "chitiet", "chi-tiet", "daydu", "day-du"}


class AnalyzeCommand(BotCommand):
    """
    Lệnh phân tích cổ phiếu / Stock analysis command

    Phân tích một mã Việt Nam, sinh báo cáo AI và đẩy kết quả.
    Analyzes a Vietnam code, generates an AI analysis report and pushes it.

    Usage:
        /analyze HPG          - Phân tích Hòa Phát (báo cáo ngắn gọn)
        /analyze HPG full     - Phân tích và sinh báo cáo đầy đủ
        /analyze VNINDEX      - Phân tích chỉ số VN-Index
        /analyze FUEVN100     - Phân tích chứng chỉ quỹ ETF
        /analyze Hòa Phát     - Phân tích theo tên công ty
    """

    @property
    def name(self) -> str:
        return "analyze"

    @property
    def aliases(self) -> List[str]:
        return ["a", "phantich", "pt"]

    @property
    def description(self) -> str:
        return "Phân tích mã cổ phiếu, chứng chỉ quỹ hoặc chỉ số"

    @property
    def usage(self) -> str:
        return "/analyze <mã cổ phiếu/quỹ/chỉ số hoặc tên công ty> [full]"

    def validate_args(self, args: List[str]) -> Optional[str]:
        """Kiểm tra tham số (chỉ kiểm tra cấu trúc; kiểm tra ngữ nghĩa nằm trong execute).

        Validate arguments (structural checks only; semantic validation happens in execute).
        """
        if not args:
            return "Vui lòng nhập mã cổ phiếu hoặc tên công ty"
        return None

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """Thực thi lệnh phân tích / Execute the analyze command"""
        # Mặc định báo cáo ngắn gọn; thêm `full` ở cuối để lấy bản đầy đủ.
        # Concise by default; a trailing `full` requests the full report.
        tokens = [a for a in args if a and a.strip()]
        report_type = "simple"
        if len(tokens) > 1 and tokens[-1].lower() in _FULL_REPORT_KEYWORDS:
            report_type = "full"
            tokens = tokens[:-1]

        # Tên công ty có thể gồm nhiều từ (vd: "Hòa Phát") nên ghép lại phần còn lại.
        # Company names may span several words, so join the remaining tokens.
        raw = " ".join(tokens).strip()
        if not raw:
            return BotResponse.error_response("Vui lòng nhập mã cổ phiếu hoặc tên công ty")

        try:
            code, error_msg = self._resolve_analysis_input(raw)
            if code is None:
                return BotResponse.error_response(error_msg or f"Không nhận diện được mã: {raw}")

            logger.info(f"[AnalyzeCommand] Mã phân tích: {code}, loại báo cáo: {report_type}")

            # Gọi dịch vụ phân tích / Call the analysis service
            from src.services.task_service import get_task_service
            from src.enums import ReportType

            service = get_task_service()

            # Gửi tác vụ phân tích bất đồng bộ / Submit an async analysis task
            result = service.submit_analysis(
                code=code,
                report_type=ReportType.from_str(report_type),
                source_message=message,
            )

            if result.get("success"):
                task_id = result.get("task_id", "")
                # ``extra`` chỉ mang danh tính tác vụ nội bộ (cho consumer không phụ thuộc
                # transport như E2E smoke); adapter nền tảng có thể bỏ qua.
                # ``extra`` only carries internal task identity; platform adapters may ignore it.
                response = BotResponse.markdown_response(
                    f"✅ **Đã gửi tác vụ phân tích**\n\n"
                    f"• Mã: `{code}`\n"
                    f"• Loại báo cáo: {ReportType.from_str(report_type).display_name}\n"
                    f"• Task ID: `{task_id[:20]}...`\n\n"
                    f"Kết quả sẽ được gửi tự động khi phân tích hoàn tất."
                )
                response.extra = {
                    "task_id": task_id,
                    "stock_code": code,
                }
                return response
            error = result.get("error", "Lỗi không xác định")
            return BotResponse.error_response(f"Gửi tác vụ phân tích thất bại: {error}")

        except Exception as e:
            logger.error(f"[AnalyzeCommand] Thực thi thất bại: {e}")
            return BotResponse.error_response(f"Phân tích thất bại: {str(e)[:100]}")

    def _resolve_analysis_input(self, raw: str) -> Tuple[Optional[str], Optional[str]]:
        """Quy một tham số ``/analyze`` thành ``(code, error_message)``.

        1. Mã Việt Nam (cổ phiếu ``HPG``/``hpg``/``HPG.VN``/``vnHPG``, quỹ ``FUEVN100``,
           chỉ số ``VNINDEX``/``VN30``/``HNXIndex``) -> trả mã chuẩn hoá.
        2. Ngược lại thử phân giải tên công ty qua :func:`resolve_name_to_code`
           (vd: ``Hòa Phát`` -> ``HPG``).
        3. Không phân giải được -> ``(None, thông báo lỗi tiếng Việt)``.

        Resolve one ``/analyze`` argument into ``(code, error_message)``: VN code first,
        then company-name resolution, otherwise an explicit error.
        """
        target = parse_analysis_target(raw)
        if target.asset_type == ParseStatus.STOCK and target.canonical_id:
            return target.canonical_id, None

        from src.services.name_to_code_resolver import resolve_name_to_code

        code = resolve_name_to_code(raw)
        if code:
            return code, None

        return None, (
            f"Không nhận diện được mã `{raw}`. Vui lòng nhập mã cổ phiếu, chứng chỉ quỹ "
            f"hoặc chỉ số Việt Nam (vd: HPG, FUEVN100, VNINDEX) hoặc tên công ty (vd: Hòa Phát)."
        )
