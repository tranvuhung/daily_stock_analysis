# -*- coding: utf-8 -*-
"""Unit tests for report language helpers (chỉ vi/en; zh/ko cũ quy về vi)."""

import unittest

from src.report_language import (
    localize_data_limitation,
    SUPPORTED_REPORT_LANGUAGES,
    get_bias_status_emoji,
    get_localized_stock_name,
    get_report_labels,
    get_sentiment_label,
    get_signal_level,
    infer_decision_type_from_advice,
    localize_operation_advice,
    localize_trend_prediction,
    localize_bias_status,
    normalize_report_language,
)


class ReportLanguageTestCase(unittest.TestCase):
    def test_get_signal_level_handles_compound_sell_advice(self) -> None:
        signal_text, emoji, signal_tag = get_signal_level("Bán/Quan sát", 60, "vi")

        self.assertEqual(signal_text, "Bán")
        self.assertEqual(emoji, "🔴")
        self.assertEqual(signal_tag, "sell")

    def test_get_signal_level_handles_compound_buy_advice_in_english(self) -> None:
        signal_text, emoji, signal_tag = get_signal_level("Buy / Watch", 40, "en")

        self.assertEqual(signal_text, "Buy")
        self.assertEqual(emoji, "🟢")
        self.assertEqual(signal_tag, "buy")

    def test_get_signal_level_score_fallback_uses_canonical_scale(self) -> None:
        self.assertEqual(get_signal_level("", 28, "vi"), ("Giảm tỷ trọng", "🟠", "reduce"))
        self.assertEqual(get_signal_level("", 38, "vi"), ("Giảm tỷ trọng", "🟠", "reduce"))
        self.assertEqual(get_signal_level("", 42, "vi"), ("Quan sát", "⚪", "watch"))
        self.assertEqual(get_signal_level("", 55, "vi"), ("Quan sát", "⚪", "watch"))
        self.assertEqual(get_signal_level("", 60, "vi"), ("Mua", "🟢", "buy"))
        self.assertEqual(get_signal_level("", 66, "vi"), ("Mua", "🟢", "buy"))
        self.assertEqual(get_signal_level("", 72, "vi"), ("Mua", "🟢", "buy"))

    def test_get_localized_stock_name_replaces_placeholder(self) -> None:
        # Tên giữ chỗ "Cổ phiếu {code}" được thay bằng nhãn "chưa xác định".
        # Placeholder names are replaced with the localized "unnamed" label.
        self.assertEqual(get_localized_stock_name("Cổ phiếu HPG", "HPG", "en"), "Unnamed Stock")
        self.assertEqual(get_localized_stock_name("Cổ phiếu HPG", "HPG", "vi"), "Cổ phiếu chưa xác định")

    def test_get_sentiment_label_preserves_higher_band_thresholds(self) -> None:
        self.assertEqual(get_sentiment_label(80, "en"), "Very Bullish")
        self.assertEqual(get_sentiment_label(60, "en"), "Bullish")
        self.assertEqual(get_sentiment_label(80, "vi"), "Rất lạc quan")
        self.assertEqual(get_sentiment_label(40, "vi"), "Trung lập")
        self.assertEqual(get_sentiment_label(20, "vi"), "Bi quan")
        self.assertEqual(get_sentiment_label(0, "vi"), "Rất bi quan")

    def test_localize_trend_prediction_translates_english_input_for_vi(self) -> None:
        self.assertEqual(localize_trend_prediction("bullish", "vi"), "Tăng")
        self.assertEqual(localize_trend_prediction("very bearish", "vi"), "Giảm mạnh")

    def test_bias_status_helpers_support_english_values(self) -> None:
        self.assertEqual(localize_bias_status("Safe", "en"), "Safe")
        self.assertEqual(localize_bias_status("Safe", "vi"), "An toàn")
        self.assertEqual(localize_bias_status("Cảnh báo", "en"), "Caution")
        self.assertEqual(localize_bias_status("Danger", "vi"), "Nguy hiểm")
        self.assertEqual(get_bias_status_emoji("Safe"), "✅")
        self.assertEqual(get_bias_status_emoji("Caution"), "⚠️")

    def test_infer_decision_type_from_advice_matches_vietnamese_phrases(self) -> None:
        self.assertEqual(infer_decision_type_from_advice("Nên mua"), "buy")
        self.assertEqual(infer_decision_type_from_advice("Nên nắm giữ"), "hold")
        self.assertEqual(infer_decision_type_from_advice("Nên giảm tỷ trọng"), "sell")
        self.assertEqual(infer_decision_type_from_advice("Tiếp tục nắm giữ"), "hold")
        self.assertEqual(infer_decision_type_from_advice("Quan sát rũ bỏ", default=""), "hold")
        self.assertEqual(infer_decision_type_from_advice("Quan sát", default=""), "hold")
        self.assertEqual(infer_decision_type_from_advice("Không nên mua"), "hold")


class SupportedReportLanguageTestCase(unittest.TestCase):
    def test_only_vi_and_en_are_supported(self) -> None:
        self.assertEqual(tuple(SUPPORTED_REPORT_LANGUAGES), ("vi", "en"))

    def test_removed_languages_normalize_to_vietnamese(self) -> None:
        # zh/ko đã bị gỡ: mọi alias cũ đều quy về tiếng Việt.
        # zh/ko were removed: legacy aliases normalize to Vietnamese.
        for legacy in ("zh", "cn", "ko", "kr", "korean", "chinese", "zh-CN"):
            self.assertEqual(normalize_report_language(legacy), "vi", legacy)
        self.assertEqual(get_report_labels("zh"), get_report_labels("vi"))
        self.assertEqual(get_report_labels("ko"), get_report_labels("vi"))

    def test_unknown_language_falls_back_to_default(self) -> None:
        self.assertEqual(normalize_report_language("fr"), "vi")
        self.assertEqual(normalize_report_language(None), "vi")
        self.assertEqual(normalize_report_language("fr", default="en"), "en")

    def test_vietnamese_aliases(self) -> None:
        for alias in ("vi", "VI", "vn", "vie", "vietnamese", "Tiếng Việt", "vi-VN"):
            self.assertEqual(normalize_report_language(alias), "vi", alias)

    def test_only_vietnamese_is_selectable(self) -> None:
        from src.report_language import is_selectable_report_language_value

        self.assertTrue(is_selectable_report_language_value("vi"))
        self.assertTrue(is_selectable_report_language_value("vietnamese"))
        # Tiếng Anh chỉ dùng để render, không chọn được qua cấu hình.
        # English is render-only, not selectable through config.
        self.assertFalse(is_selectable_report_language_value("en"))
        self.assertFalse(is_selectable_report_language_value("fr"))

    def test_vietnamese_labels_cover_full_english_key_set(self) -> None:
        self.assertEqual(set(get_report_labels("vi")), set(get_report_labels("en")))

    def test_data_sources_label_is_localized(self) -> None:
        self.assertEqual(get_report_labels("vi")["data_sources_label"], "Nguồn dữ liệu")
        self.assertEqual(get_report_labels("en")["data_sources_label"], "Data Sources")

    def test_vietnamese_terms_round_trip_to_canonical(self) -> None:
        """LLM xuất tiếng Việt; hệ thống phải quy được về giá trị canonical."""
        self.assertEqual(localize_operation_advice("Mua", "en"), "Buy")
        self.assertEqual(localize_operation_advice("Bán mạnh", "en"), "Strong Sell")
        self.assertEqual(localize_operation_advice("strong sell", "vi"), "Bán mạnh")
        self.assertEqual(localize_trend_prediction("Đi ngang", "en"), "Sideways")
        self.assertEqual(infer_decision_type_from_advice("Mua"), "buy")
        self.assertEqual(infer_decision_type_from_advice("Bán"), "sell")

    def test_volume_status_values_are_localized(self) -> None:
        from src.report_language import localize_volume_status

        self.assertEqual(localize_volume_status("shrinking", "vi"), "Khối lượng cạn")
        self.assertEqual(localize_volume_status("Khối lượng cạn", "en"), "Shrinking volume")
        # Giá trị rỗng / không biết trả nguyên, không ném lỗi.
        # Empty and unknown values are returned unchanged without raising.
        self.assertEqual(localize_volume_status("", "vi"), "")
        self.assertEqual(localize_volume_status("không rõ", "vi"), "không rõ")

    def test_vietnamese_negation_is_not_read_as_a_buy_signal(self) -> None:
        """"Không nên mua" phải ra hold, không bao giờ là tín hiệu mua."""
        for advice in (
            "Không nên mua",
            "Chưa nên mua",
            "Không mua vào lúc này",
            "Hạn chế mua đuổi",
            "Đừng bán",
            "Chưa vội bán",
        ):
            self.assertEqual(infer_decision_type_from_advice(advice), "hold", advice)

    def test_data_limitation_codes_are_localized_for_vietnamese(self) -> None:
        self.assertEqual(localize_data_limitation("quote: stale", "vi"), "Báo giá: dữ liệu trễ")
        self.assertEqual(
            localize_data_limitation("technical: fallback", "vi"),
            "Kỹ thuật: dùng nguồn dự phòng",
        )
        self.assertEqual(localize_data_limitation("quote: stale", "en"), "quote: stale")
        # Câu tự do hoặc mã lạ giữ nguyên. / Free text and unknown codes stay unchanged.
        self.assertEqual(localize_data_limitation("Báo giá trễ 15 phút", "vi"), "Báo giá trễ 15 phút")
        self.assertEqual(localize_data_limitation("portfolio: hidden", "vi"), "portfolio: hidden")


if __name__ == "__main__":
    unittest.main()
