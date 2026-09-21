# -*- coding: utf-8 -*-
"""Unit tests for report language helpers."""

import unittest

from src.report_language import (
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
        signal_text, emoji, signal_tag = get_signal_level("卖出/观望", 60, "zh")

        self.assertEqual(signal_text, "卖出")
        self.assertEqual(emoji, "🔴")
        self.assertEqual(signal_tag, "sell")

    def test_get_signal_level_handles_compound_buy_advice_in_english(self) -> None:
        signal_text, emoji, signal_tag = get_signal_level("Buy / Watch", 40, "en")

        self.assertEqual(signal_text, "Buy")
        self.assertEqual(emoji, "🟢")
        self.assertEqual(signal_tag, "buy")

    def test_get_signal_level_score_fallback_uses_canonical_scale(self) -> None:
        self.assertEqual(get_signal_level("", 28, "zh"), ("减仓", "🟠", "reduce"))
        self.assertEqual(get_signal_level("", 38, "zh"), ("减仓", "🟠", "reduce"))
        self.assertEqual(get_signal_level("", 42, "zh"), ("观望", "⚪", "watch"))
        self.assertEqual(get_signal_level("", 55, "zh"), ("观望", "⚪", "watch"))
        self.assertEqual(get_signal_level("", 60, "zh"), ("买入", "🟢", "buy"))
        self.assertEqual(get_signal_level("", 66, "zh"), ("买入", "🟢", "buy"))
        self.assertEqual(get_signal_level("", 72, "zh"), ("买入", "🟢", "buy"))

    def test_get_localized_stock_name_replaces_placeholder_for_english(self) -> None:
        self.assertEqual(
            get_localized_stock_name("股票AAPL", "AAPL", "en"),
            "Unnamed Stock",
        )

    def test_get_sentiment_label_preserves_higher_band_thresholds(self) -> None:
        self.assertEqual(get_sentiment_label(80, "en"), "Very Bullish")
        self.assertEqual(get_sentiment_label(60, "en"), "Bullish")
        self.assertEqual(get_sentiment_label(40, "zh"), "中性")
        self.assertEqual(get_sentiment_label(20, "zh"), "悲观")

    def test_localize_trend_prediction_preserves_fine_grain_zh_states(self) -> None:
        self.assertEqual(localize_trend_prediction("多头排列", "zh"), "多头排列")
        self.assertEqual(localize_trend_prediction("弱势空头", "zh"), "弱势空头")

    def test_localize_trend_prediction_still_translates_english_input_for_zh(self) -> None:
        self.assertEqual(localize_trend_prediction("bullish", "zh"), "看多")
        self.assertEqual(localize_trend_prediction("very bearish", "zh"), "强烈看空")

    def test_bias_status_helpers_support_english_values(self) -> None:
        self.assertEqual(localize_bias_status("Safe", "en"), "Safe")
        self.assertEqual(localize_bias_status("警戒", "en"), "Caution")
        self.assertEqual(get_bias_status_emoji("Safe"), "✅")
        self.assertEqual(get_bias_status_emoji("Caution"), "⚠️")

    def test_infer_decision_type_from_advice_matches_chinese_phrases(self) -> None:
        self.assertEqual(infer_decision_type_from_advice("建议买入"), "buy")
        self.assertEqual(infer_decision_type_from_advice("建议持有"), "hold")
        self.assertEqual(infer_decision_type_from_advice("建议减仓"), "sell")
        self.assertEqual(infer_decision_type_from_advice("继续持有"), "hold")
        self.assertEqual(infer_decision_type_from_advice("建议洗盘观察"), "hold")
        self.assertEqual(infer_decision_type_from_advice("洗盘观察", default=""), "hold")
        self.assertEqual(infer_decision_type_from_advice("观察", default=""), "hold")
        self.assertEqual(infer_decision_type_from_advice("不建议买入"), "hold")
        self.assertEqual(
            infer_decision_type_from_advice("当前不跌破支撑位继续持有"),
            "hold",
        )
        self.assertEqual(
            infer_decision_type_from_advice("不破支撑后仍可持有"),
            "hold",
        )


class KoreanReportLanguageTestCase(unittest.TestCase):
    def test_korean_is_supported(self) -> None:
        self.assertIn("ko", SUPPORTED_REPORT_LANGUAGES)
        self.assertIn("vi", SUPPORTED_REPORT_LANGUAGES)

    def test_normalize_korean_aliases(self) -> None:
        self.assertEqual(normalize_report_language("ko"), "ko")
        self.assertEqual(normalize_report_language("korean"), "ko")
        self.assertEqual(normalize_report_language("ko-KR"), "ko")
        self.assertEqual(normalize_report_language("kr"), "ko")

    def test_unknown_language_falls_back_to_default(self) -> None:
        # 默认输出语言已切换为越南语；zh/en/ko 仍可渲染历史记录。
        self.assertEqual(normalize_report_language("fr"), "vi")
        self.assertEqual(normalize_report_language(None), "vi")
        self.assertEqual(normalize_report_language("fr", default="zh"), "zh")

    def test_vietnamese_aliases(self) -> None:
        for alias in ("vi", "VI", "vn", "vie", "vietnamese", "Tiếng Việt", "vi-VN"):
            self.assertEqual(normalize_report_language(alias), "vi", alias)

    def test_only_vietnamese_is_selectable(self) -> None:
        from src.report_language import is_selectable_report_language_value

        self.assertTrue(is_selectable_report_language_value("vi"))
        self.assertTrue(is_selectable_report_language_value("vietnamese"))
        for legacy in ("zh", "en", "ko"):
            self.assertFalse(is_selectable_report_language_value(legacy), legacy)

    def test_vietnamese_labels_cover_full_english_key_set(self) -> None:
        from src.report_language import get_report_labels

        self.assertEqual(
            set(get_report_labels("vi")), set(get_report_labels("en"))
        )

    def test_vietnamese_terms_round_trip_to_canonical(self) -> None:
        """LLM 现在输出越南语，系统必须能把它归一回 canonical 值。"""
        from src.report_language import (
            infer_decision_type_from_advice,
            localize_operation_advice,
            localize_trend_prediction,
        )

        self.assertEqual(localize_operation_advice("Mua", "en"), "Buy")
        self.assertEqual(localize_operation_advice("Bán mạnh", "en"), "Strong Sell")
        self.assertEqual(localize_trend_prediction("Đi ngang", "en"), "Sideways")
        self.assertEqual(infer_decision_type_from_advice("Mua"), "buy")
        self.assertEqual(infer_decision_type_from_advice("Bán"), "sell")

    def test_volume_status_placeholders_are_localized(self) -> None:
        """LLM 常照抄 prompt schema 里的中文示例值（放量/缩量/平量）。

        渲染边界必须做归一翻译，否则越南语报告里会出现中文。真实运行中已观察到
        这个泄漏。
        """
        from src.report_language import localize_volume_status

        self.assertEqual(localize_volume_status("缩量", "vi"), "Khối lượng cạn")
        self.assertEqual(localize_volume_status("放量", "vi"), "Khối lượng tăng")
        self.assertEqual(localize_volume_status("巨量", "vi"), "Bùng nổ khối lượng")
        self.assertEqual(localize_volume_status("平量", "vi"), "Khối lượng bình thường")
        # 英文与越南语输入也要能归一
        self.assertEqual(localize_volume_status("shrinking", "vi"), "Khối lượng cạn")
        self.assertEqual(localize_volume_status("Khối lượng cạn", "zh"), "缩量")
        # 模型常返回复合值（真实运行中观察到 "缩量 (Khối lượng thấp)"）
        self.assertEqual(
            localize_volume_status("缩量 (Khối lượng thấp)", "vi"), "Khối lượng cạn"
        )
        self.assertEqual(localize_volume_status("放量 / expanding", "vi"), "Khối lượng tăng")
        # 空值与未知值原样返回，不抛异常
        self.assertEqual(localize_volume_status("", "vi"), "")
        self.assertEqual(localize_volume_status("không rõ", "vi"), "không rõ")

    def test_vietnamese_negation_is_not_read_as_a_buy_signal(self) -> None:
        """"Không nên mua" 必须解析为 hold，绝不能被当成买入信号。"""
        from src.report_language import infer_decision_type_from_advice

        for advice in (
            "Không nên mua",
            "Chưa nên mua",
            "Không mua vào lúc này",
            "Hạn chế mua đuổi",
            "Đừng bán",
            "Chưa vội bán",
        ):
            self.assertEqual(infer_decision_type_from_advice(advice), "hold", advice)

    def test_korean_labels_cover_full_english_key_set(self) -> None:
        ko_labels = get_report_labels("ko")
        en_labels = get_report_labels("en")
        zh_labels = get_report_labels("zh")
        self.assertEqual(set(ko_labels.keys()), set(en_labels.keys()))
        self.assertEqual(set(zh_labels.keys()), set(en_labels.keys()))
        self.assertEqual(ko_labels["dashboard_title"], "결정 대시보드")
        self.assertEqual(ko_labels["risk_alerts_label"], "리스크 경보")

    def test_data_sources_label_is_localized(self) -> None:
        self.assertEqual(get_report_labels("zh")["data_sources_label"], "数据来源")
        self.assertEqual(get_report_labels("en")["data_sources_label"], "Data Sources")
        self.assertEqual(get_report_labels("ko")["data_sources_label"], "데이터 출처")

    def test_korean_sentiment_label_bands(self) -> None:
        self.assertEqual(get_sentiment_label(80, "ko"), "매우 낙관")
        self.assertEqual(get_sentiment_label(40, "ko"), "중립")
        self.assertEqual(get_sentiment_label(0, "ko"), "매우 비관")

    def test_korean_operation_advice_and_trend(self) -> None:
        self.assertEqual(localize_operation_advice("买入", "ko"), "매수")
        self.assertEqual(localize_operation_advice("strong sell", "ko"), "적극 매도")
        self.assertEqual(localize_trend_prediction("bullish", "ko"), "상승")

    def test_korean_localized_stock_name_placeholder(self) -> None:
        self.assertEqual(
            get_localized_stock_name("股票AAPL", "AAPL", "ko"),
            "미확인 종목",
        )

    def test_existing_languages_unchanged(self) -> None:
        self.assertEqual(get_sentiment_label(80, "en"), "Very Bullish")
        self.assertEqual(get_sentiment_label(40, "zh"), "中性")

    def test_korean_advice_canonicalizes_to_decision_type(self) -> None:
        self.assertEqual(infer_decision_type_from_advice("매수"), "buy")
        self.assertEqual(infer_decision_type_from_advice("매도"), "sell")
        self.assertEqual(infer_decision_type_from_advice("보유"), "hold")
        self.assertEqual(infer_decision_type_from_advice("관망"), "hold")

    def test_korean_advice_resolves_signal_level(self) -> None:
        self.assertEqual(get_signal_level("매수", 72, "ko"), ("매수", "🟢", "buy"))
        self.assertEqual(get_signal_level("매도", 30, "ko"), ("매도", "🔴", "sell"))

    def test_korean_values_canonicalize_back_for_other_languages(self) -> None:
        self.assertEqual(localize_trend_prediction("상승", "en"), "Bullish")
        self.assertEqual(localize_operation_advice("적극 매도", "zh"), "强烈卖出")


if __name__ == "__main__":
    unittest.main()
