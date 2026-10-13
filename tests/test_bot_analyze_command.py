# -*- coding: utf-8 -*-
"""Kiểm thử lệnh ``/analyze`` (chỉ mã Việt Nam) / Tests for the shared ``/analyze`` command (Vietnam only).

Covers the real ``CommandDispatcher`` gate (``validate_args`` before
``execute``) with a stubbed ``TaskService`` so the tests assert the
code/target handed to ``submit_analysis`` without touching the network or
the real pipeline.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

# Keep tests runnable when optional deps are missing.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from tests.litellm_stub import ensure_litellm_stub
    ensure_litellm_stub()

from bot.commands.analyze import AnalyzeCommand
from bot.dispatcher import CommandDispatcher
from bot.models import BotMessage, BotResponse, ChatType
from src.enums import ReportType


class _StubTaskService:
    """Records the last ``submit_analysis`` call; never touches the network.

    Supports two canned outcomes (success by default, or failure) so tests
    can assert both the success ``extra`` contract and the no-identity error
    contract without touching the network or the real pipeline.
    """

    def __init__(self, fail: bool = False):
        self.calls = []
        self._fail = fail

    def submit_analysis(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            return {"success": False, "task_id": "", "error": "boom"}
        return {
            "success": True,
            "task_id": "task-1234567890abcdef",
            "code": kwargs.get("code", ""),
        }


def _make_message(content: str) -> BotMessage:
    return BotMessage(
        platform="telegram",
        message_id="m1",
        user_id="u1",
        user_name="tester",
        chat_id="c1",
        chat_type=ChatType.PRIVATE,
        content=content,
        raw_content=content,
        mentioned=False,
        timestamp=datetime.now(),
    )


class TestAnalyzeCommandDispatcherGate(unittest.TestCase):
    """Real dispatcher gate: ``validate_args`` runs before ``execute`` and the
    command submits through the shared ``AnalyzeCommand``."""

    def _dispatch(self, content: str):
        dispatcher = CommandDispatcher()
        command = AnalyzeCommand()
        dispatcher.register(command)
        stub = _StubTaskService()
        with patch("src.services.task_service.get_task_service", return_value=stub):
            response = dispatcher.dispatch(_make_message(content))
        return response, stub

    def test_vn_code_variants_submit_normalized_code(self):
        """Mã VN ở mọi dạng nhập được chuẩn hoá trước khi gửi tác vụ."""
        cases = (
            ("HPG", "HPG"),
            ("hpg", "HPG"),
            ("HPG.VN", "HPG"),
            ("vnHPG", "HPG"),
            ("FUEVN100", "FUEVN100"),
            ("VNINDEX", "VNINDEX"),
            ("hnxindex", "HNXIndex"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                response, stub = self._dispatch(f"/analyze {raw}")
                self.assertIn("Đã gửi tác vụ phân tích", response.text)
                self.assertEqual(len(stub.calls), 1)
                call = stub.calls[0]
                self.assertEqual(call["code"], expected)
                # Không còn truyền analysis_target cho mã cổ phiếu.
                self.assertNotIn("analysis_target", call)

    def test_vn_code_does_not_hit_name_resolver(self):
        with patch(
            "src.services.name_to_code_resolver.resolve_name_to_code"
        ) as mock_resolve:
            response, stub = self._dispatch("/analyze FPT")
        self.assertEqual(stub.calls[0]["code"], "FPT")
        mock_resolve.assert_not_called()

    def test_company_name_falls_back_to_name_resolver(self):
        """Tên công ty nhiều từ được ghép lại và tra qua resolve_name_to_code."""
        with patch(
            "src.services.name_to_code_resolver.resolve_name_to_code",
            return_value="HPG",
        ) as mock_resolve:
            response, stub = self._dispatch("/analyze Hòa Phát")
        self.assertIn("Đã gửi tác vụ phân tích", response.text)
        self.assertEqual(stub.calls[0]["code"], "HPG")
        mock_resolve.assert_called_once_with("Hòa Phát")

    def test_company_name_with_full_flag(self):
        with patch(
            "src.services.name_to_code_resolver.resolve_name_to_code",
            return_value="HPG",
        ) as mock_resolve:
            response, stub = self._dispatch("/analyze Hòa Phát full")
        mock_resolve.assert_called_once_with("Hòa Phát")
        self.assertEqual(stub.calls[0]["code"], "HPG")
        self.assertEqual(stub.calls[0]["report_type"].value, "full")

    def test_non_vn_codes_return_error_without_submission(self):
        """Mã thị trường khác (A-share/HK/US) không còn được hỗ trợ."""
        for raw in ("600519", "hk00700", "AAPL", "sh000016", "12345"):
            with self.subTest(raw=raw):
                with patch(
                    "src.services.name_to_code_resolver.resolve_name_to_code",
                    return_value=None,
                ):
                    response, stub = self._dispatch(f"/analyze {raw}")
                self.assertIn("Không nhận diện được mã", response.text)
                self.assertEqual(len(stub.calls), 0)

    def test_unknown_name_returns_error_and_does_not_submit(self):
        with patch(
            "src.services.name_to_code_resolver.resolve_name_to_code",
            return_value=None,
        ):
            response, stub = self._dispatch("/analyze công ty không tồn tại")
        self.assertIn("Không nhận diện được mã", response.text)
        self.assertEqual(len(stub.calls), 0)

    def test_empty_args_rejected_by_validate_args_gate(self):
        response, stub = self._dispatch("/analyze")
        self.assertIn("Vui lòng nhập", response.text)
        self.assertEqual(len(stub.calls), 0)

    def test_full_report_flag_still_works(self):
        response, stub = self._dispatch("/analyze HPG full")
        self.assertIn("Đã gửi tác vụ phân tích", response.text)
        call = stub.calls[0]
        self.assertEqual(call["code"], "HPG")
        self.assertEqual(call["report_type"].value, "full")

    def test_vietnamese_alias_routes_to_analyze(self):
        response, stub = self._dispatch("/phantich VCB")
        self.assertEqual(stub.calls[0]["code"], "VCB")

    def test_success_text_and_task_identity_extra(self):
        """Khi gửi thành công, ``extra`` mang danh tính tác vụ nội bộ."""
        response, stub = self._dispatch("/analyze HPG")
        expected_text = (
            "✅ **Đã gửi tác vụ phân tích**\n\n"
            "• Mã: `HPG`\n"
            f"• Loại báo cáo: {ReportType.SIMPLE.display_name}\n"
            "• Task ID: `task-1234567890abcde...`\n\n"
            "Kết quả sẽ được gửi tự động khi phân tích hoàn tất."
        )
        self.assertEqual(response.text, expected_text)
        self.assertEqual(response.extra, {
            "task_id": "task-1234567890abcdef",
            "stock_code": "HPG",
        })

    def test_success_extra_uses_normalized_code(self):
        response, stub = self._dispatch("/analyze hpg.vn")
        self.assertEqual(response.extra["task_id"], "task-1234567890abcdef")
        self.assertEqual(response.extra["stock_code"], "HPG")

    def test_error_response_has_no_task_identity_extra(self):
        """Gửi thất bại thì không có tác vụ nên ``extra`` phải rỗng."""
        dispatcher = CommandDispatcher()
        dispatcher.register(AnalyzeCommand())
        stub = _StubTaskService(fail=True)
        with patch("src.services.task_service.get_task_service", return_value=stub):
            response = dispatcher.dispatch(_make_message("/analyze HPG"))
        self.assertIn("Gửi tác vụ phân tích thất bại", response.text)
        self.assertEqual(response.extra, {})


if __name__ == "__main__":
    unittest.main()
