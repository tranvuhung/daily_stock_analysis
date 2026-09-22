# -*- coding: utf-8 -*-
"""Thông báo tiến độ của pipeline phải theo ngôn ngữ báo cáo.

Bối cảnh: các chuỗi này KHÔNG chỉ nằm trong log. Chúng đi theo đường
``StockAnalysisPipeline._emit_progress`` -> ``progress_callback`` ->
``task_queue.update_task_progress`` -> ``task.message``, rồi được
``src/services/run_flow.py`` render thẳng vào panel Run Flow và luồng SSE
(``run_flow.py:134, 151, 898, 908, 938``). Vì là văn xuôi tự do không kèm định danh
ổn định, frontend không thể tự dịch như với nhãn lane/node — nên phải dịch tại nguồn.

Pipeline progress strings reach the Run Flow panel verbatim through ``task.message``,
so they must be localized at the source rather than on the client.
"""

import re
import unittest

from src.report_language import (
    get_analysis_error_text,
    get_localized_stock_name,
    get_pipeline_progress_text,
)

CJK = re.compile(r"[一-鿿]")

# Mọi giai đoạn mà ``_progress_text`` có thể được gọi tới. Thêm giai đoạn mới mà quên
# bổ sung bản dịch thì ca kiểm thử này phải đỏ.
ALL_STAGES = (
    # 11 giai đoạn do src/core/pipeline.py phát ra
    "prepare",
    "market_ready",
    "fetch_market",
    "aggregate",
    "news",
    "agent_switch",
    "context",
    "llm_request",
    "llm_streaming",
    "validate",
    "save_report",
    # 4 giai đoạn do src/analyzer.py phát ra, đi chung progress_callback
    "llm_wait",
    "llm_accepted",
    "llm_parsing",
    "llm_retry",
)


class PipelineProgressLanguageTestCase(unittest.TestCase):
    def test_vietnamese_progress_has_no_chinese(self):
        for stage in ALL_STAGES:
            text = get_pipeline_progress_text(
                stage, "vi", name="HPG", chars=128, seconds="1.5", attempt=1, total=2
            )
            self.assertFalse(CJK.search(text), f"{stage}: {text}")
            self.assertIn("HPG", text)

    def test_english_progress_has_no_chinese(self):
        for stage in ALL_STAGES:
            text = get_pipeline_progress_text(
                stage, "en", name="HPG", chars=128, seconds="1.5", attempt=1, total=2
            )
            self.assertFalse(CJK.search(text), f"{stage}: {text}")

    def test_chinese_run_still_gets_chinese(self):
        """Chống hồi quy cho người dùng ``zh`` của upstream."""

        text = get_pipeline_progress_text("fetch_market", "zh", name="600519")
        self.assertTrue(CJK.search(text))

    def test_every_stage_defines_every_language(self):
        # Thiếu một ngôn ngữ sẽ ném KeyError ngay giữa luồng phân tích thật.
        for stage in ALL_STAGES:
            for language in ("vi", "en", "zh", "ko"):
                self.assertTrue(
                    get_pipeline_progress_text(
                        stage, language, name="X", chars=1, seconds="1.0", attempt=1, total=2
                    )
                )

    def test_streaming_stage_interpolates_char_count(self):
        text = get_pipeline_progress_text("llm_streaming", "vi", name="HPG", chars=512)
        self.assertIn("512", text)

    def test_unknown_stage_does_not_raise(self):
        # Một giai đoạn mới chưa kịp khai báo không được làm sập lượt phân tích.
        self.assertEqual(get_pipeline_progress_text("brand_new_stage", "vi", name="X"), "brand_new_stage")

    def test_missing_parameter_does_not_raise(self):
        text = get_pipeline_progress_text("llm_streaming", "vi", name="HPG")
        self.assertTrue(text)


class PipelineProgressHelperTestCase(unittest.TestCase):
    """``_progress_text`` phải chuẩn hoá cả tên giữ chỗ ``股票{code}``."""

    def _pipeline(self, language):
        from src.core.pipeline import StockAnalysisPipeline

        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)

        class _Cfg:
            report_language = language

        pipeline.config = _Cfg()
        return pipeline

    def test_placeholder_stock_name_is_localized(self):
        # ``src/core/pipeline.py`` đặt tên giữ chỗ là ``股票{code}`` khi không tra được tên.
        # Nếu không chuẩn hoá, tiếng Trung vẫn lọt ra UI qua chính thông báo tiến độ.
        pipeline = self._pipeline("vi")
        text = pipeline._progress_text("fetch_market", code="HPG", name="股票HPG")
        self.assertFalse(CJK.search(text), text)
        self.assertIn(get_localized_stock_name("股票HPG", "HPG", "vi"), text)

    def test_real_stock_name_is_preserved(self):
        pipeline = self._pipeline("vi")
        text = pipeline._progress_text("aggregate", code="HPG", name="Hòa Phát")
        self.assertIn("Hòa Phát", text)

    def test_missing_config_does_not_break_the_run(self):
        """Hồi quy: ``_progress_text`` chạy TRƯỚC lớp try/except của ``_emit_progress``.

        Pipeline dựng một phần (``__new__``, chưa gắn ``config``) từng làm
        ``process_single_stock`` ném ``AttributeError`` và trả ``None``.
        """

        from src.core.pipeline import StockAnalysisPipeline

        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        self.assertFalse(hasattr(pipeline, "config"))
        text = pipeline._progress_text("prepare", code="HPG")
        self.assertIn("HPG", text)

    def test_code_only_call_sites_work(self):
        # Hai call site dùng ``code`` mà không có ``stock_name``.
        pipeline = self._pipeline("vi")
        text = pipeline._progress_text("prepare", code="HPG")
        self.assertFalse(CJK.search(text), text)
        self.assertIn("HPG", text)


class AnalysisErrorLanguageTestCase(unittest.TestCase):
    """Chuỗi lỗi đi ra sự kiện ``task_failed`` của Run Flow qua ``task.error``."""

    def test_error_messages_follow_report_language(self):
        for key in ("empty_result", "failed", "empty_result_generic"):
            vi = get_analysis_error_text(key, "vi", code="HPG")
            en = get_analysis_error_text(key, "en", code="HPG")
            self.assertFalse(CJK.search(vi), f"{key}: {vi}")
            self.assertFalse(CJK.search(en), f"{key}: {en}")
            self.assertTrue(CJK.search(get_analysis_error_text(key, "zh", code="HPG")))

    def test_unknown_key_does_not_raise(self):
        self.assertEqual(get_analysis_error_text("nope", "vi"), "nope")


if __name__ == "__main__":
    unittest.main()
