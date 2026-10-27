# -*- coding: utf-8 -*-
"""Helpers for report output language selection and localization."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from src.schemas.decision_scale import signal_key_for_score

# Ngôn ngữ mà bộ render hiểu: tiếng Việt và tiếng Anh. Bản ghi cũ lưu zh/ko được quy về
# tiếng Việt (nhãn hiển thị theo tiếng Việt, nội dung giữ nguyên).
# Languages the renderer understands; legacy zh/ko records normalize to Vietnamese.
SUPPORTED_REPORT_LANGUAGES = ("vi", "en")

# Languages this deployment *allows selecting*. Reports are produced only in Vietnamese, so
# REPORT_LANGUAGE config and API requests accept only vi; SUPPORTED_REPORT_LANGUAGES above also covers English rendering.
SELECTABLE_REPORT_LANGUAGES = ("vi",)

DEFAULT_REPORT_LANGUAGE = "vi"

_REPORT_LANGUAGE_ALIASES = {
    "vi-vn": "vi",
    "vi_vn": "vi",
    "vn": "vi",
    "vie": "vi",
    "vietnamese": "vi",
    "tieng_viet": "vi",
    "tiếng_việt": "vi",
    "zh-cn": "vi",
    "zh_cn": "vi",
    "zh-hans": "vi",
    "zh_hans": "vi",
    "zh-tw": "vi",
    "zh_tw": "vi",
    "cn": "vi",
    "chinese": "vi",
    "english": "en",
    "en-us": "en",
    "en_us": "en",
    "en-gb": "en",
    "en_gb": "en",
    "korean": "vi",
    "kr": "vi",
    "ko-kr": "vi",
    "ko_kr": "vi",
    # Ngôn ngữ đã bị loại bỏ: quy về tiếng Việt. / Removed languages normalize to Vietnamese.
    "zh": "vi",
    "ko": "vi",
}

_OPERATION_ADVICE_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "mua mạnh": "strong_buy",
    "mua": "buy",
    "mua thêm": "buy",
    "nắm giữ": "hold",
    "giữ": "hold",
    "quan sát": "watch",
    "theo dõi": "watch",
    "giảm tỷ trọng": "reduce",
    "bán": "sell",
    "bán mạnh": "strong_sell",
    "strong buy": "strong_buy",
    "strong_buy": "strong_buy",
    "buy": "buy",
    "accumulate": "buy",
    "add position": "buy",
    "hold": "hold",
    "watch": "watch",
    "wait": "watch",
    "wait and see": "watch",
    "reduce": "reduce",
    "trim": "reduce",
    "sell": "sell",
    "strong sell": "strong_sell",
    "strong_sell": "strong_sell",
}

_OPERATION_ADVICE_TRANSLATIONS = {
    "strong_buy": { "vi": "Mua mạnh", "en": "Strong Buy", },
    "buy": { "vi": "Mua", "en": "Buy", },
    "hold": { "vi": "Nắm giữ", "en": "Hold", },
    "watch": { "vi": "Quan sát", "en": "Watch", },
    "reduce": { "vi": "Giảm tỷ trọng", "en": "Reduce", },
    "sell": { "vi": "Bán", "en": "Sell", },
    "strong_sell": { "vi": "Bán mạnh", "en": "Strong Sell", },
}

_TREND_PREDICTION_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "tăng mạnh": "strong_bullish",
    "tăng": "bullish",
    "đi ngang": "sideways",
    "giảm": "bearish",
    "giảm mạnh": "strong_bearish",
    "strong bullish": "strong_bullish",
    "very bullish": "strong_bullish",
    # Nhãn TrendStatus tiếng Anh của src/stock_analyzer.py (chuẩn mới); khoá tiếng Trung
    # phía dưới giữ lại cho bản ghi cũ trong analysis_history.
    # English TrendStatus values from src/stock_analyzer.py; Chinese keys stay for old records.
    "bullish alignment": "bullish",
    "weak bullish": "bullish",
    "consolidation": "sideways",
    "weak bearish": "bearish",
    "bearish alignment": "bearish",
    "bullish": "bullish",
    "uptrend": "bullish",
    "neutral": "sideways",
    "sideways": "sideways",
    "range-bound": "sideways",
    "bearish": "bearish",
    "downtrend": "bearish",
    "strong bearish": "strong_bearish",
    "very bearish": "strong_bearish",
}

_TREND_PREDICTION_TRANSLATIONS = {
    "strong_bullish": { "vi": "Tăng mạnh", "en": "Strong Bullish", },
    "bullish": { "vi": "Tăng", "en": "Bullish", },
    "sideways": { "vi": "Đi ngang", "en": "Sideways", },
    "bearish": { "vi": "Giảm", "en": "Bearish", },
    "strong_bearish": { "vi": "Giảm mạnh", "en": "Strong Bearish", },
}

_CONFIDENCE_LEVEL_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "cao": "high",
    "trung bình": "medium",
    "thấp": "low",
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "low": "low",
}

_CONFIDENCE_LEVEL_TRANSLATIONS = {
    "high": { "vi": "Cao", "en": "High", },
    "medium": { "vi": "Trung bình", "en": "Medium", },
    "low": { "vi": "Thấp", "en": "Low", },
}

_STRATEGY_SIGNAL_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "mua mạnh": "strong_buy",
    "mua": "buy",
    "nắm giữ": "hold",
    "bán": "sell",
    "bán mạnh": "strong_sell",
    "strong buy": "strong_buy",
    "strong_buy": "strong_buy",
    "buy": "buy",
    "hold": "hold",
    "sell": "sell",
    "strong sell": "strong_sell",
    "strong_sell": "strong_sell",
}

_STRATEGY_SIGNAL_TRANSLATIONS = {
    "strong_buy": { "vi": "Mua mạnh", "en": "Strong Buy", },
    "buy": { "vi": "Mua", "en": "Buy", },
    "hold": { "vi": "Nắm giữ", "en": "Hold", },
    "sell": { "vi": "Bán", "en": "Sell", },
    "strong_sell": { "vi": "Bán mạnh", "en": "Strong Sell", },
}

_CONSENSUS_LEVEL_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "cao": "high",
    "trung bình": "medium",
    "thấp": "low",
    "không đủ bằng chứng": "insufficient",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "insufficient": "insufficient",
    "Insufficient": "insufficient",
}

_CONSENSUS_LEVEL_TRANSLATIONS = {
    "high": { "vi": "Cao", "en": "High", },
    "medium": { "vi": "Trung bình", "en": "Medium", },
    "low": { "vi": "Thấp", "en": "Low", },
    "insufficient": { "vi": "Không đủ bằng chứng", "en": "Insufficient", },
}

_CONFLICT_SEVERITY_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "không có": "none",
    "thấp": "low",
    "trung bình": "medium",
    "cao": "high",
    "none": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
}

_CONFLICT_SEVERITY_TRANSLATIONS = {
    "none": { "vi": "Không có", "en": "None", },
    "low": { "vi": "Thấp", "en": "Low", },
    "medium": { "vi": "Trung bình", "en": "Medium", },
    "high": { "vi": "Cao", "en": "High", },
}

_STRATEGY_SKILL_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "xu hướng tăng mặc định": "bull_trend",
    "chủ đề nóng": "hot_theme",
    "bứt phá kèm khối lượng": "volume_breakout",
    "giao cắt vàng đường ma": "ma_golden_cross",
    "chất lượng tăng trưởng": "growth_quality",
    "khối lượng tăng tại đáy": "bottom_volume",
    "dao động trong hộp": "box_oscillation",
    "cấu trúc lý thuyết chan": "chan_theory",
    "chiến pháp cổ phiếu dẫn dắt": "dragon_head",
    "chu kỳ tâm lý": "emotion_cycle",
    "dẫn dắt bởi sự kiện": "event_driven",
    "định giá lại kỳ vọng": "expectation_repricing",
    "một tăng ba giảm": "one_yang_three_yin",
    "điều chỉnh với khối lượng cạn": "shrink_pullback",
    "lý thuyết sóng": "wave_theory",
    "bull trend": "bull_trend",
    "bull_trend": "bull_trend",
    "hot theme": "hot_theme",
    "hot_theme": "hot_theme",
    "volume breakout": "volume_breakout",
    "volume_breakout": "volume_breakout",
    "ma golden cross": "ma_golden_cross",
    "ma_golden_cross": "ma_golden_cross",
    "growth quality": "growth_quality",
    "growth_quality": "growth_quality",
    "bottom volume": "bottom_volume",
    "bottom_volume": "bottom_volume",
    "box oscillation": "box_oscillation",
    "box_oscillation": "box_oscillation",
    "chan theory": "chan_theory",
    "chan_theory": "chan_theory",
    "dragon head": "dragon_head",
    "dragon_head": "dragon_head",
    "emotion cycle": "emotion_cycle",
    "emotion_cycle": "emotion_cycle",
    "event driven": "event_driven",
    "event_driven": "event_driven",
    "expectation repricing": "expectation_repricing",
    "expectation_repricing": "expectation_repricing",
    "one yang three yin": "one_yang_three_yin",
    "one_yang_three_yin": "one_yang_three_yin",
    "shrink pullback": "shrink_pullback",
    "shrink_pullback": "shrink_pullback",
    "wave theory": "wave_theory",
    "wave_theory": "wave_theory",
}

_STRATEGY_SKILL_TRANSLATIONS = {
    "bull_trend": { "vi": "Xu hướng tăng mặc định", "en": "Bull Trend", },
    "hot_theme": { "vi": "Chủ đề nóng", "en": "Hot Theme", },
    "volume_breakout": { "vi": "Bứt phá kèm khối lượng", "en": "Volume Breakout", },
    "ma_golden_cross": { "vi": "Giao cắt vàng đường MA", "en": "MA Golden Cross", },
    "growth_quality": { "vi": "Chất lượng tăng trưởng", "en": "Growth Quality", },
    "bottom_volume": { "vi": "Khối lượng tăng tại đáy", "en": "Bottom Volume", },
    "box_oscillation": { "vi": "Dao động trong hộp", "en": "Box Oscillation", },
    "chan_theory": { "vi": "Cấu trúc lý thuyết Chan", "en": "Chan Theory", },
    "dragon_head": { "vi": "Chiến pháp cổ phiếu dẫn dắt", "en": "Dragon Head", },
    "emotion_cycle": { "vi": "Chu kỳ tâm lý", "en": "Emotion Cycle", },
    "event_driven": { "vi": "Dẫn dắt bởi sự kiện", "en": "Event Driven", },
    "expectation_repricing": { "vi": "Định giá lại kỳ vọng", "en": "Expectation Repricing", },
    "one_yang_three_yin": { "vi": "Một tăng ba giảm", "en": "One Yang Three Yin", },
    "shrink_pullback": { "vi": "Điều chỉnh với khối lượng cạn", "en": "Shrink Pullback", },
    "wave_theory": { "vi": "Lý thuyết sóng", "en": "Wave Theory", },
}

_VOLUME_STATUS_CANONICAL_MAP = {
    # The LLM copies example values from the prompt schema (Chinese "expanding / shrinking / flat volume"),
    # even when the output-language instruction asks for Vietnamese. Normalize and translate them here at
    # the render boundary instead of relying on the model to follow instructions.
    "expanding": "expanding",
    "expanding volume": "expanding",
    "high volume": "expanding",
    "surging": "surging",
    "shrinking": "shrinking",
    "shrinking volume": "shrinking",
    "low volume": "shrinking",
    "normal": "normal",
    "normal volume": "normal",
    # Enum volume_status của TechnicalAgent (multi-agent): heavy|normal|light.
    # TechnicalAgent volume_status enum (multi-agent): heavy|normal|light.
    "heavy": "expanding",
    "light": "shrinking",
    # Nhãn VolumeStatus tiếng Anh của src/stock_analyzer.py.
    # English VolumeStatus values from src/stock_analyzer.py.
    "heavy volume up": "expanding",
    "heavy volume down": "expanding",
    "low volume up": "shrinking",
    "low volume pullback": "shrinking",
    "tăng khối lượng": "expanding",
    "khối lượng tăng": "expanding",
    "bùng nổ khối lượng": "surging",
    "cạn khối lượng": "shrinking",
    "khối lượng thấp": "shrinking",
    "khối lượng bình thường": "normal",
    "khối lượng cạn": "shrinking",
    "khối lượng tăng": "expanding",
}

_VOLUME_STATUS_TRANSLATIONS = {
    "expanding": {"vi": "Khối lượng tăng",  "en": "Expanding volume", },
    "surging": {"vi": "Bùng nổ khối lượng",  "en": "Surging volume", },
    "shrinking": {"vi": "Khối lượng cạn",  "en": "Shrinking volume", },
    "normal": {"vi": "Khối lượng bình thường",  "en": "Normal volume", },
}

_BIAS_STATUS_CANONICAL_MAP = {
    # Tiếng Việt — nhận lại thuật ngữ do LLM trả về khi báo cáo là tiếng Việt.
    # Thiếu phần này, giá trị như "Mua" sẽ không quy được về canonical và
    # decision_type sẽ rơi về mặc định.
    "an toàn": "safe",
    "cảnh báo": "caution",
    "nguy hiểm": "danger",
    "safe": "safe",
    "caution": "caution",
    "risk": "danger",
    "danger": "danger",
}

_BIAS_STATUS_TRANSLATIONS = {
    "safe": { "vi": "An toàn", "en": "Safe", },
    "caution": { "vi": "Cảnh báo", "en": "Caution", },
    "danger": { "vi": "Nguy hiểm", "en": "Danger", },
}

_PLACEHOLDER_BY_LANGUAGE = {
    "vi": "Chưa có",
    "en": "TBD",
}

_UNKNOWN_BY_LANGUAGE = {
    "vi": "Không rõ",
    "en": "Unknown",
}

_NO_DATA_BY_LANGUAGE = {
    "vi": "Thiếu dữ liệu",
    "en": "Data unavailable",
}

# Thông báo tiến độ của pipeline. Chuỗi này KHÔNG chỉ nằm trong log: nó đi qua
# ``progress_callback`` -> ``task_queue.update_task_progress`` -> ``task.message`` và được
# ``src/services/run_flow.py`` render thẳng vào panel Run Flow cũng như luồng SSE. Vì là văn
# xuôi tự do, frontend không thể tự dịch như với nhãn có định danh ổn định — nên phải dịch
# ngay tại nguồn theo ``REPORT_LANGUAGE`` của lượt chạy.
# Pipeline progress messages. These are not log-only: they reach task.message and are rendered
# verbatim in the Run Flow panel and the SSE stream, so they must be localized at the source.
_PIPELINE_PROGRESS_MESSAGES: Dict[str, Dict[str, str]] = {
    "prepare": {
        "vi": "{name}: đang chuẩn bị tác vụ phân tích",
        "en": "{name}: preparing the analysis task",
    },
    "market_ready": {
        "vi": "{name}: đã chuẩn bị xong dữ liệu giá",
        "en": "{name}: market data ready",
    },
    "fetch_market": {
        "vi": "{name}: đang lấy dữ liệu giá",
        "en": "{name}: fetching quote data",
    },
    "aggregate": {
        "vi": "{name}: đang tổng hợp dữ liệu cơ bản và xu hướng",
        "en": "{name}: aggregating fundamental and trend data",
    },
    "news": {
        "vi": "{name}: đang tìm tin tức và tâm lý thị trường",
        "en": "{name}: searching news and sentiment",
    },
    "agent_switch": {
        "vi": "{name}: đang chuyển sang luồng phân tích Agent",
        "en": "{name}: switching to the Agent analysis path",
    },
    "context": {
        "vi": "{name}: đang sắp xếp ngữ cảnh phân tích",
        "en": "{name}: assembling the analysis context",
    },
    "llm_request": {
        "vi": "{name}: đang yêu cầu LLM sinh báo cáo",
        "en": "{name}: requesting the report from the LLM",
    },
    "llm_streaming": {
        "vi": "{name}: LLM đang sinh kết quả phân tích (đã nhận {chars} ký tự)",
        "en": "{name}: the LLM is generating the analysis ({chars} characters received)",
    },
    # Bốn giai đoạn dưới đây do ``src/analyzer.py`` phát ra, đi chung
    # ``progress_callback`` với pipeline (``pipeline.py:932``) nên cùng lọt ra Run Flow.
    # Emitted by ``src/analyzer.py`` through the same progress callback.
    "llm_wait": {
        "vi": "{name}: chờ {seconds} giây trước khi gọi LLM",
        "en": "{name}: waiting {seconds}s before the LLM request",
    },
    "llm_accepted": {
        "vi": "{name}: LLM đã nhận yêu cầu, đang chờ phản hồi",
        "en": "{name}: the LLM accepted the request, awaiting the response",
    },
    "llm_parsing": {
        "vi": "{name}: LLM đã trả kết quả, đang phân tích JSON",
        "en": "{name}: the LLM responded, parsing JSON",
    },
    "llm_retry": {
        "vi": "{name}: báo cáo thiếu trường bắt buộc, đang thử bổ sung ({attempt}/{total})",
        "en": "{name}: the report is missing required fields, retrying completion ({attempt}/{total})",
    },
    "validate": {
        "vi": "{name}: đang kiểm tra và sắp xếp kết quả phân tích",
        "en": "{name}: validating and tidying the analysis result",
    },
    "save_report": {
        "vi": "{name}: đang lưu báo cáo phân tích",
        "en": "{name}: saving the analysis report",
    },
}

_PLACEHOLDER_EXACT = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "tbd",
    "không có dữ liệu",
    "chưa có dữ liệu",
    "chưa rõ",
    "không rõ",
}

_PLACEHOLDER_HINTS = (
    "thiếu dữ liệu",
    "không có dữ liệu",
    "không thể đánh giá",
    "data unavailable",
    "unavailable",
    "not available",
    "missing",
    "not supported",
)

_GENERIC_STOCK_NAME_BY_LANGUAGE = {
    "vi": "Cổ phiếu chưa xác định",
    "en": "Unnamed Stock",
}

_REPORT_LABELS: Dict[str, Dict[str, str]] = {
    "vi": {
        "dashboard_title": "Bảng điều khiển quyết định",
        "brief_title": "Bản tin quyết định",
        "analyzed_prefix": "Đã phân tích",
        "stock_unit": "mã cổ phiếu",
        "stock_unit_compact": "mã",
        "buy_label": "Mua",
        "watch_label": "Quan sát",
        "sell_label": "Bán",
        "summary_heading": "Tóm tắt kết quả phân tích",
        "info_heading": "Thông tin quan trọng",
        "sentiment_summary_label": "Tâm lý thị trường",
        "earnings_outlook_label": "Triển vọng lợi nhuận",
        "risk_alerts_label": "Cảnh báo rủi ro",
        "positive_catalysts_label": "Yếu tố hỗ trợ",
        "latest_news_label": "Tin mới nhất",
        "core_conclusion_heading": "Kết luận cốt lõi",
        "one_sentence_label": "Quyết định một câu",
        "time_sensitivity_label": "Tính thời điểm",
        "default_time_sensitivity": "Trong tuần này",
        "position_status_label": "Tình trạng nắm giữ",
        "action_advice_label": "Khuyến nghị hành động",
        "no_position_label": "Chưa có vị thế",
        "has_position_label": "Đang nắm giữ",
        "continue_holding": "Tiếp tục nắm giữ",
        "market_snapshot_heading": "Diễn biến phiên",
        "close_label": "Đóng cửa",
        "prev_close_label": "Tham chiếu",
        "open_label": "Mở cửa",
        "high_label": "Cao nhất",
        "low_label": "Thấp nhất",
        "change_pct_label": "Biến động (%)",
        "change_amount_label": "Mức biến động",
        "amplitude_label": "Biên độ",
        "volume_label": "Khối lượng",
        "amount_label": "Giá trị giao dịch",
        "current_price_label": "Giá hiện tại",
        "volume_ratio_label": "Tỷ lệ khối lượng",
        "turnover_rate_label": "Tỷ lệ chuyển nhượng",
        "source_label": "Nguồn giá",
        "data_sources_label": "Nguồn dữ liệu",
        "data_perspective_heading": "Phân tích dữ liệu",
        "ma_alignment_label": "Sắp xếp đường MA",
        "bullish_alignment_label": "Sắp xếp tăng giá",
        "yes_label": "Có",
        "no_label": "Không",
        "none_label": "Không có",
        "trend_strength_label": "Độ mạnh xu hướng",
        "price_metrics_label": "Chỉ số giá",
        "ma5_label": "MA5",
        "ma10_label": "MA10",
        "ma20_label": "MA20",
        "bias_ma5_label": "Độ lệch (MA5)",
        "support_level_label": "Hỗ trợ",
        "resistance_level_label": "Kháng cự",
        "phase_decision_heading": "Hàng rào quyết định theo phiên",
        "action_window_label": "Khung thời gian hành động",
        "immediate_action_label": "Hành động hiện tại",
        "watch_conditions_label": "Điều kiện theo dõi",
        "next_check_time_label": "Lần kiểm tra tới",
        "confidence_reason_label": "Lý do độ tin cậy",
        "data_limitations_label": "Hạn chế dữ liệu",
        # Mục "Phân tích chi tiết" của báo cáo chi tiết lịch sử (Web).
        # "Detailed Analysis" section of the history detailed report (Web).
        "detailed_analysis_heading": "Phân tích chi tiết",
        "analysis_summary_label": "Tổng quan",
        "key_points_label": "Điểm chính",
        "buy_reason_label": "Lý do khuyến nghị",
        "risk_warning_label": "Lưu ý rủi ro",
        "outlook_heading": "Triển vọng",
        "short_term_outlook_label": "Ngắn hạn (1-3 phiên)",
        "medium_term_outlook_label": "Trung hạn (1-2 tuần)",
        "technical_heading": "Kỹ thuật",
        "trend_analysis_label": "Cấu trúc giá",
        "technical_analysis_label": "Tổng hợp",
        "ma_analysis_label": "Đường trung bình",
        "volume_analysis_label": "Khối lượng",
        "pattern_analysis_label": "Mô hình nến",
        "fundamental_heading": "Cơ bản",
        "fundamental_analysis_label": "Phân tích cơ bản",
        "sector_position_label": "Vị thế ngành",
        "company_highlights_label": "Điểm nhấn doanh nghiệp",
        "news_heading": "Tin tức & tâm lý",
        "news_summary_label": "Tóm tắt tin",
        "market_sentiment_label": "Tâm lý thị trường",
        "hot_topics_label": "Chủ đề liên quan",
        "battle_plan_heading": "Kế hoạch giao dịch",
        "ideal_buy_label": "Điểm mua lý tưởng",
        "secondary_buy_label": "Điểm mua thứ hai",
        "stop_loss_label": "Cắt lỗ",
        "take_profit_label": "Mục tiêu",
        "suggested_position_label": "Tỷ trọng đề xuất",
        "entry_plan_label": "Chiến lược vào lệnh",
        "risk_control_label": "Chiến lược quản trị rủi ro",
        "checklist_heading": "Danh mục kiểm tra",
        "failed_checks_heading": "Mục chưa đạt",
        "history_compare_heading": "So sánh tín hiệu lịch sử",
        "time_label": "Thời gian",
        "score_label": "Điểm",
        "advice_label": "Khuyến nghị",
        "trend_label": "Xu hướng",
        "generated_at_label": "Thời điểm tạo báo cáo",
        "report_time_label": "Thời điểm tạo",
        "no_results": "Không có kết quả phân tích",
        "report_title": "Báo cáo phân tích cổ phiếu",
        "avg_score_label": "Điểm trung bình",
        "action_points_heading": "Vùng giá hành động",
        "position_advice_heading": "Khuyến nghị nắm giữ",
        "analysis_model_label": "Mô hình phân tích",
        "not_investment_advice": "Nội dung do AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư",
        "details_report_hint": "Xem báo cáo chi tiết tại",
        "financial_summary_heading": "Tóm tắt tài chính",
        "report_date_label": "Kỳ báo cáo",
        "revenue_label": "Doanh thu",
        "net_profit_label": "Lợi nhuận sau thuế công ty mẹ",
        "operating_cash_flow_label": "Dòng tiền kinh doanh",
        "roe_label": "ROE",
        "revenue_yoy_label": "Doanh thu so cùng kỳ",
        "net_profit_yoy_label": "Lợi nhuận so cùng kỳ",
        "gross_margin_label": "Biên lợi nhuận gộp",
        "shareholder_return_heading": "Lợi ích cổ đông",
        "ttm_cash_dividend_label": "Cổ tức tiền mặt/cp 12 tháng (trước thuế)",
        "ttm_event_count_label": "Số lần trả cổ tức 12 tháng",
        "ttm_dividend_yield_label": "Tỷ suất cổ tức TTM",
        "latest_ex_dividend_label": "Ngày GDKHQ gần nhất",
        "institutional_flow_heading": "Giao dịch khối tổ chức",
        "institutional_flow_note": "Số dương = mua ròng, số âm = bán ròng; đơn vị: cổ phiếu.",
        "inst_foreign_label": "Khối ngoại",
        "inst_trust_label": "Quỹ đầu tư",
        "inst_dealer_label": "Tự doanh",
        "inst_total_label": "Tổng khối tổ chức",
        "related_boards_heading": "Nhóm ngành liên quan",
        "industry_boards_heading": "Nhóm ngành",
        "concept_boards_heading": "Nhóm chủ đề",
        "board_name_label": "Nhóm",
        "board_type_label": "Loại",
        "board_status_label": "Diễn biến nhóm",
        "board_change_pct_label": "Biến động nhóm (%)",
        "leading_board_label": "Dẫn dắt",
        "lagging_board_label": "Tụt lại",
        "signal_attribution_heading": "Phân rã tín hiệu",
        "attribution_weights_label": "Trọng số phân rã",
        "technical_indicators_label": "Chỉ báo kỹ thuật",
        "news_sentiment_label": "Tâm lý tin tức",
        "fundamentals_label": "Yếu tố cơ bản",
        "market_conditions_label": "Bối cảnh thị trường",
        "strongest_bullish_signal_label": "Tín hiệu tăng mạnh nhất",
        "strongest_bearish_signal_label": "Tín hiệu giảm mạnh nhất",
        "strategy_synthesis_heading": "Tổng hợp chiến lược",
        "strategy_final_signal_label": "Tín hiệu tổng hợp",
        "strategy_consensus_level_label": "Mức đồng thuận",
        "strategy_conflict_label": "Xung đột",
        "strategy_confidence_label": "Độ tin cậy",
        "strategy_summary_label": "Diễn giải tổng hợp",
        "strategy_supporting_skills_label": "Chiến lược ủng hộ",
        "strategy_opposing_skills_label": "Chiến lược phản đối",
        "strategy_invalid_opinions_label": "Có {count} chiến lược khác không tạo được tín hiệu hợp lệ",
    },
    "en": {
        "dashboard_title": "Decision Dashboard",
        "brief_title": "Decision Brief",
        "analyzed_prefix": "Analyzed",
        "stock_unit": "stocks",
        "stock_unit_compact": "stocks",
        "buy_label": "Buy",
        "watch_label": "Watch",
        "sell_label": "Sell",
        "summary_heading": "Summary",
        "info_heading": "Key Updates",
        "sentiment_summary_label": "Sentiment",
        "earnings_outlook_label": "Earnings Outlook",
        "risk_alerts_label": "Risk Alerts",
        "positive_catalysts_label": "Positive Catalysts",
        "latest_news_label": "Latest News",
        "core_conclusion_heading": "Core Conclusion",
        "one_sentence_label": "One-line Decision",
        "time_sensitivity_label": "Time Sensitivity",
        "default_time_sensitivity": "This week",
        "position_status_label": "Position",
        "action_advice_label": "Action",
        "no_position_label": "No Position",
        "has_position_label": "Holding",
        "continue_holding": "Continue holding",
        "market_snapshot_heading": "Market Snapshot",
        "close_label": "Close",
        "prev_close_label": "Prev Close",
        "open_label": "Open",
        "high_label": "High",
        "low_label": "Low",
        "change_pct_label": "Change %",
        "change_amount_label": "Change",
        "amplitude_label": "Amplitude",
        "volume_label": "Volume",
        "amount_label": "Turnover",
        "current_price_label": "Price",
        "volume_ratio_label": "Volume Ratio",
        "turnover_rate_label": "Turnover Rate",
        "source_label": "Source",
        "data_sources_label": "Data Sources",
        "data_perspective_heading": "Data View",
        "ma_alignment_label": "MA Alignment",
        "bullish_alignment_label": "Bullish Alignment",
        "yes_label": "Yes",
        "no_label": "No",
        "none_label": "None",
        "trend_strength_label": "Trend Strength",
        "price_metrics_label": "Price Metrics",
        "ma5_label": "MA5",
        "ma10_label": "MA10",
        "ma20_label": "MA20",
        "bias_ma5_label": "Bias (MA5)",
        "support_level_label": "Support",
        "resistance_level_label": "Resistance",
        "phase_decision_heading": "Phase Decision Guardrail",
        "action_window_label": "Action Window",
        "immediate_action_label": "Current Action",
        "watch_conditions_label": "Watch Conditions",
        "next_check_time_label": "Next Check",
        "confidence_reason_label": "Confidence Reason",
        "data_limitations_label": "Data Limitations",
        "detailed_analysis_heading": "Detailed Analysis",
        "analysis_summary_label": "Overview",
        "key_points_label": "Key Points",
        "buy_reason_label": "Rationale",
        "risk_warning_label": "Risk Warning",
        "outlook_heading": "Outlook",
        "short_term_outlook_label": "Short term (1-3 sessions)",
        "medium_term_outlook_label": "Medium term (1-2 weeks)",
        "technical_heading": "Technicals",
        "trend_analysis_label": "Price Structure",
        "technical_analysis_label": "Summary",
        "ma_analysis_label": "Moving Averages",
        "volume_analysis_label": "Volume",
        "pattern_analysis_label": "Candlestick Patterns",
        "fundamental_heading": "Fundamentals",
        "fundamental_analysis_label": "Fundamental Analysis",
        "sector_position_label": "Sector Position",
        "company_highlights_label": "Company Highlights",
        "news_heading": "News Flow",
        "news_summary_label": "News Summary",
        "market_sentiment_label": "Market Sentiment",
        "hot_topics_label": "Related Topics",
        "battle_plan_heading": "Battle Plan",
        "ideal_buy_label": "Ideal Entry",
        "secondary_buy_label": "Secondary Entry",
        "stop_loss_label": "Stop Loss",
        "take_profit_label": "Target",
        "suggested_position_label": "Position Size",
        "entry_plan_label": "Entry Plan",
        "risk_control_label": "Risk Control",
        "checklist_heading": "Checklist",
        "failed_checks_heading": "Failed Checks",
        "history_compare_heading": "Historical Signal Comparison",
        "time_label": "Time",
        "score_label": "Score",
        "advice_label": "Advice",
        "trend_label": "Trend",
        "generated_at_label": "Generated At",
        "report_time_label": "Generated",
        "no_results": "No analysis results",
        "report_title": "Stock Analysis Report",
        "avg_score_label": "Avg Score",
        "action_points_heading": "Action Levels",
        "position_advice_heading": "Position Advice",
        "analysis_model_label": "Model",
        "not_investment_advice": "AI-generated content for reference only. Not investment advice.",
        "details_report_hint": "See detailed report:",
        "financial_summary_heading": "Financial Summary",
        "report_date_label": "Report Date",
        "revenue_label": "Revenue",
        "net_profit_label": "Net Profit (Parent)",
        "operating_cash_flow_label": "Operating Cash Flow",
        "roe_label": "ROE",
        "revenue_yoy_label": "Revenue YoY",
        "net_profit_yoy_label": "Net Profit YoY",
        "gross_margin_label": "Gross Margin",
        "shareholder_return_heading": "Shareholder Return",
        "ttm_cash_dividend_label": "TTM Cash Dividend / Share (Pre-tax)",
        "ttm_event_count_label": "TTM Dividend Events",
        "ttm_dividend_yield_label": "TTM Dividend Yield",
        "latest_ex_dividend_label": "Latest Ex-dividend Date",
        "institutional_flow_heading": "Institutional Flows (3 Majors)",
        "institutional_flow_note": "Positive = net buy, negative = net sell; unit: shares.",
        "inst_foreign_label": "Foreign",
        "inst_trust_label": "Inv. Trust",
        "inst_dealer_label": "Dealer",
        "inst_total_label": "Total (3 Majors)",
        "related_boards_heading": "Related Boards",
        "industry_boards_heading": "Industry Sectors",
        "concept_boards_heading": "Concept Themes",
        "board_name_label": "Board",
        "board_type_label": "Type",
        "board_status_label": "Status",
        "board_change_pct_label": "Change %",
        "leading_board_label": "Leading",
        "lagging_board_label": "Lagging",
        "signal_attribution_heading": "Signal Attribution",
        "attribution_weights_label": "Attribution Weights",
        "technical_indicators_label": "Technical Indicators",
        "news_sentiment_label": "News Sentiment",
        "fundamentals_label": "Fundamentals",
        "market_conditions_label": "Market Conditions",
        "strongest_bullish_signal_label": "Strongest Bullish Signal",
        "strongest_bearish_signal_label": "Strongest Bearish Signal",
        "strategy_synthesis_heading": "Strategy Synthesis",
        "strategy_final_signal_label": "Final Signal",
        "strategy_consensus_level_label": "Consensus",
        "strategy_conflict_label": "Conflict",
        "strategy_confidence_label": "Confidence",
        "strategy_summary_label": "Summary",
        "strategy_supporting_skills_label": "Supporting Strategies",
        "strategy_opposing_skills_label": "Opposing Strategies",
        "strategy_invalid_opinions_label": "{count} additional strategies failed to produce valid signals",
    },
}

_DECISION_INTENT_NEGATIONS = (
    # Tiếng Việt. Có khoảng trắng ở cuối để không khớp nhầm vào giữa từ khác
    # (ví dụ "không" trong "khống chế" — tiếng Việt viết rời nên ranh giới từ
    # là khoảng trắng).
    "không ",
    "chưa ",
    "đừng ",
    "chớ ",
    "hạn chế ",
    "no ",
    "not ",
    " never",
)

_DECISION_INTENT_NEGATION_SCOPE_BREAK_CHARS = ",;:!?."
_DECISION_INTENT_NEGATION_CONNECTORS = (
    # Tiếng Việt: các từ nối thường đứng giữa từ phủ định và động từ quyết định
    # ("không nên mua", "chưa vội bán").
    "nên",
    "vội",
    "cần",
    "phải",
    "được",
    "tiếp tục",
)


def _strip_decision_negation_connectors(text: str) -> str:
    """Remove common advisory connectors between a negation token and decision word."""
    suffix = text.strip()
    changed = True
    while changed:
        changed = False
        for connector in _DECISION_INTENT_NEGATION_CONNECTORS:
            if suffix.startswith(connector):
                suffix = suffix[len(connector):].strip()
                changed = True
                break
    return suffix


def normalize_report_language(
    value: Optional[str], default: str = DEFAULT_REPORT_LANGUAGE
) -> str:
    """Normalize report language to a supported short code."""
    candidate = (value or default).strip().lower().replace(" ", "_")
    candidate = _REPORT_LANGUAGE_ALIASES.get(candidate, candidate)
    if candidate in SUPPORTED_REPORT_LANGUAGES:
        return candidate
    return default


def is_supported_report_language_value(value: Optional[str]) -> bool:
    """Return whether the raw value is a language the renderer understands.

    Includes legacy languages (zh/en/ko) so old records can still be rendered;
    to check whether a language can be *configured*, use
    :func:`is_selectable_report_language_value`.
    """
    candidate = (value or "").strip().lower().replace(" ", "_")
    if not candidate:
        return False
    return candidate in SUPPORTED_REPORT_LANGUAGES or candidate in _REPORT_LANGUAGE_ALIASES


def is_selectable_report_language_value(value: Optional[str]) -> bool:
    """Return whether the raw value may be selected as the output language."""
    candidate = (value or "").strip().lower().replace(" ", "_")
    if not candidate:
        return False
    candidate = _REPORT_LANGUAGE_ALIASES.get(candidate, candidate)
    return candidate in SELECTABLE_REPORT_LANGUAGES


# Chinese / Korean typography uses a full-width colon; Vietnamese and English use a half-width colon plus a space.
_LABEL_SEPARATOR_BY_LANGUAGE = {
    "vi": ": ",
    "en": ": ",
}


def get_label_separator(language: Optional[str]) -> str:
    """Return the label/value separator for the given report language."""
    return _LABEL_SEPARATOR_BY_LANGUAGE.get(normalize_report_language(language), ": ")


def get_report_labels(language: Optional[str]) -> Dict[str, str]:
    """Return UI copy for the selected report language."""
    normalized = normalize_report_language(language)
    return _REPORT_LABELS[normalized]


def get_placeholder_text(language: Optional[str]) -> str:
    """Return placeholder text for missing localized content."""
    return _PLACEHOLDER_BY_LANGUAGE[normalize_report_language(language)]


def get_unknown_text(language: Optional[str]) -> str:
    """Return localized unknown text."""
    return _UNKNOWN_BY_LANGUAGE[normalize_report_language(language)]


def get_no_data_text(language: Optional[str]) -> str:
    """Return localized data unavailable text."""
    return _NO_DATA_BY_LANGUAGE[normalize_report_language(language)]


# Chuỗi lỗi phân tích cũng đi ra UI qua ``task.error`` -> sự kiện ``task_failed`` của
# Run Flow (``src/services/run_flow.py:918``), nên cũng phải theo ngôn ngữ báo cáo.
# Analysis error strings reach the Run Flow ``task_failed`` event via ``task.error``.
_ANALYSIS_ERROR_MESSAGES: Dict[str, Dict[str, str]] = {
    "empty_result": {
        "vi": "Phân tích mã {code} trả về kết quả rỗng",
        "en": "Analysis of {code} returned an empty result",
    },
    "failed": {
        "vi": "Phân tích mã {code} thất bại",
        "en": "Analysis of {code} failed",
    },
    "empty_result_generic": {
        "vi": "Phân tích trả về kết quả rỗng",
        "en": "Analysis returned an empty result",
    },
}


def get_analysis_error_text(key: str, language: Optional[str], **params: Any) -> str:
    """Trả chuỗi lỗi phân tích theo ngôn ngữ báo cáo. / Localized analysis error text."""
    template = _ANALYSIS_ERROR_MESSAGES.get(key)
    if not template:
        return key
    text = template[normalize_report_language(language)]
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text


def get_pipeline_progress_text(stage: str, language: Optional[str], **params: Any) -> str:
    """Trả thông báo tiến độ của pipeline theo ngôn ngữ báo cáo.

    Return the pipeline progress message for the given stage and report language.
    ``stage`` lạ thì trả chính nó, để một giai đoạn mới thêm sau không làm sập luồng chạy.
    An unknown stage returns the stage name itself so a newly added stage never breaks a run.
    """
    template = _PIPELINE_PROGRESS_MESSAGES.get(stage)
    if not template:
        return stage
    text = template[normalize_report_language(language)]
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        # Thiếu tham số thì vẫn phải trả chuỗi dùng được, không được ném lỗi ra luồng phân tích.
        # A missing parameter must not raise into the analysis flow.
        return text


def _normalize_lookup_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _iter_lookup_candidates(value: Any) -> list[str]:
    raw_text = str(value or "").strip()
    if not raw_text:
        return []

    candidates = [raw_text]
    for part in re.split(r"[/|,，、]+", raw_text):
        normalized = part.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _canonicalize_lookup_value(value: Any, canonical_map: Dict[str, str]) -> Optional[str]:
    for candidate in _iter_lookup_candidates(value):
        canonical = canonical_map.get(_normalize_lookup_key(candidate))
        if canonical:
            return canonical
    return None


def _first_non_negated_position(text: str, token: str) -> Optional[int]:
    if not text or not token:
        return None

    normalized_text = text.lower().strip()
    if any(ch in normalized_text for ch in "abcdefghijklmnopqrstuvwxyz"):
        matches = list(re.finditer(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", normalized_text))
    else:
        matches = list(re.finditer(re.escape(token), normalized_text))

    for match in matches:
        prefix = normalized_text[: match.start()]
        if any(prefix.rstrip().endswith(neg) for neg in _DECISION_INTENT_NEGATIONS):
            continue
        lookback = prefix[-12:]
        negated = False
        for neg in _DECISION_INTENT_NEGATIONS:
            if not neg:
                continue
            neg_idx = lookback.rfind(neg)
            if neg_idx < 0:
                continue
            suffix = lookback[neg_idx + len(neg):]
            if not suffix:
                negated = True
                break
            if any(ch in suffix for ch in _DECISION_INTENT_NEGATION_SCOPE_BREAK_CHARS):
                continue
            normalized_suffix = _strip_decision_negation_connectors(suffix)
            if not normalized_suffix:
                negated = True
                break
            if any(ch in normalized_suffix for ch in _DECISION_INTENT_NEGATION_SCOPE_BREAK_CHARS):
                continue
            if len(normalized_suffix) > 6 and token not in normalized_suffix:
                continue
            if normalized_suffix.startswith(token):
                negated = True
                break
        if negated:
            continue
        else:
            return match.start()
    return None


def _is_placeholder_stock_name(value: Any, code: Any = None) -> bool:
    text = str(value or "").strip()
    if not text:
        return True

    lowered = text.lower()
    if lowered in {"n/a", "na", "none", "null", "unknown"}:
        return True
    if text in {"-", "—"}:
        return True

    code_text = str(code or "").strip()
    if code_text and lowered == code_text.lower():
        return True

    from src.data.stock_mapping import PLACEHOLDER_NAME_PREFIXES

    return text.startswith(PLACEHOLDER_NAME_PREFIXES)


def _translate_from_map(
    value: Any,
    language: Optional[str],
    *,
    canonical_map: Dict[str, str],
    translations: Dict[str, Dict[str, str]],
) -> str:
    normalized_language = normalize_report_language(language)
    raw_text = str(value or "").strip()
    if not raw_text:
        return raw_text

    canonical = _canonicalize_lookup_value(raw_text, canonical_map)
    if canonical:
        return translations[canonical][normalized_language]
    return raw_text


def localize_operation_advice(value: Any, language: Optional[str]) -> str:
    """Translate operation advice between Chinese and English when recognized."""
    return _translate_from_map(
        value,
        language,
        canonical_map=_OPERATION_ADVICE_CANONICAL_MAP,
        translations=_OPERATION_ADVICE_TRANSLATIONS,
    )


def localize_trend_prediction(value: Any, language: Optional[str]) -> str:
    """Translate trend prediction between Chinese and English when recognized."""
    normalized_language = normalize_report_language(language)
    raw_text = str(value or "").strip()
    if not raw_text:
        return raw_text
    return _translate_from_map(
        value,
        normalized_language,
        canonical_map=_TREND_PREDICTION_CANONICAL_MAP,
        translations=_TREND_PREDICTION_TRANSLATIONS,
    )


def localize_confidence_level(value: Any, language: Optional[str]) -> str:
    """Translate confidence level between Chinese and English when recognized."""
    return _translate_from_map(
        value,
        language,
        canonical_map=_CONFIDENCE_LEVEL_CANONICAL_MAP,
        translations=_CONFIDENCE_LEVEL_TRANSLATIONS,
    )


def localize_strategy_signal(value: Any, language: Optional[str]) -> str:
    """Translate strategy signal labels when recognized."""
    return _translate_from_map(
        value,
        language,
        canonical_map=_STRATEGY_SIGNAL_CANONICAL_MAP,
        translations=_STRATEGY_SIGNAL_TRANSLATIONS,
    )


def localize_consensus_level(value: Any, language: Optional[str]) -> str:
    """Translate strategy consensus levels when recognized."""
    return _translate_from_map(
        value,
        language,
        canonical_map=_CONSENSUS_LEVEL_CANONICAL_MAP,
        translations=_CONSENSUS_LEVEL_TRANSLATIONS,
    )


def localize_conflict_severity(value: Any, language: Optional[str]) -> str:
    """Translate strategy conflict severity when recognized."""
    return _translate_from_map(
        value,
        language,
        canonical_map=_CONFLICT_SEVERITY_CANONICAL_MAP,
        translations=_CONFLICT_SEVERITY_TRANSLATIONS,
    )


def localize_strategy_skill(value: Any, language: Optional[str]) -> str:
    """Translate strategy skill names when recognized."""
    return _translate_from_map(
        value,
        language,
        canonical_map=_STRATEGY_SKILL_CANONICAL_MAP,
        translations=_STRATEGY_SKILL_TRANSLATIONS,
    )


def localize_volume_status(value: Any, language: Optional[str]) -> str:
    """Translate the LLM-provided volume status at the render boundary.

    The model often returns compound values (observed example: a Chinese
    "shrinking volume" label followed by ``(Khối lượng thấp)``) that are neither
    pure Chinese nor pure Vietnamese, so an exact match misses them. After the
    exact match fails, a "contains" match is tried: keys are scanned from longest
    to shortest and the first known value found in the text wins.
    """

    exact = _translate_from_map(
        value,
        language,
        canonical_map=_VOLUME_STATUS_CANONICAL_MAP,
        translations=_VOLUME_STATUS_TRANSLATIONS,
    )
    raw_text = str(value or "").strip()
    if not raw_text or exact != raw_text:
        return exact

    lowered = _normalize_lookup_key(raw_text)
    for key in sorted(_VOLUME_STATUS_CANONICAL_MAP, key=len, reverse=True):
        if _normalize_lookup_key(key) in lowered:
            canonical = _VOLUME_STATUS_CANONICAL_MAP[key]
            return _VOLUME_STATUS_TRANSLATIONS[canonical][
                normalize_report_language(language)
            ]
    return raw_text


def is_placeholder_value(value: Any) -> bool:
    """Trả True khi trường bị điền rỗng hoặc placeholder "không có dữ liệu".

    Return True for fields filled with empty or no-data placeholders.
    """
    if value is None:
        return True
    if isinstance(value, (int, float)) and value == 0:
        return True
    text = str(value).strip()
    lowered = text.lower()
    if lowered in _PLACEHOLDER_EXACT:
        return True
    return any(hint in lowered for hint in _PLACEHOLDER_HINTS)


def localize_strategy_conflict_description(conflict_type: Any, language: Optional[str]) -> str:
    """Translate strategy conflict type into a display sentence at render boundaries."""
    lang = normalize_report_language(language)
    key = str(conflict_type or "").strip()
    translations = {
        "directional_opposition": {
            "vi": "Các chiến lược cho tín hiệu trái chiều: một số nghiêng về tăng, một số nghiêng về giảm, nên hạ mức độ chắc chắn của kết luận tổng hợp.",
            "en": "Strategy directions diverge: some strategies are bullish while others are bearish, so conviction should be reduced.",
        },
        "wide_score_dispersion": {
            "vi": "Điểm tín hiệu giữa các chiến lược phân tán rộng, cho thấy bất đồng đáng kể về cấu trúc thị trường.",
            "en": "Strategy signal scores are widely dispersed, indicating meaningful disagreement on market structure.",
        },
        "high_confidence_dissent": {
            "vi": "Có chiến lược thiểu số với độ tin cậy cao lệch rõ khỏi tín hiệu tổng hợp; cần giữ lại quan điểm phản biện này.",
            "en": "A high-confidence minority strategy materially disagrees with the final signal and should be kept as a dissenting view.",
        },
        "adjustment_contradiction": {
            "vi": "Hướng cộng/trừ điểm giữa các chiến lược mâu thuẫn nhau, cho thấy khác biệt lớn khi chấm điểm biên cho cùng một mã.",
            "en": "Strategy score adjustments contradict each other, showing large disagreement in marginal scoring.",
        },
    }
    localized = translations.get(key, {})
    return localized.get(lang) or localized.get("vi") or key


def normalize_strategy_synthesis_payload(value: Any) -> Dict[str, Any]:
    """Return a renderer-safe copy of a strategy synthesis payload.

    Historical records and external callers may contain pre-contract values.
    Renderers must treat a malformed top-level payload as absent and must not
    iterate malformed collection fields as strategy/conflict entries.
    """
    if not isinstance(value, dict) or not value:
        return {}

    payload = dict(value)
    for key in ("supporting_skills", "opposing_skills", "conflicts"):
        items = payload.get(key)
        payload[key] = (
            [item for item in items if isinstance(item, dict)]
            if isinstance(items, list)
            else []
        )
    return payload


def strategy_invalid_opinion_count(strategy_synthesis: Any) -> int:
    """Safely extract invalid_opinion_count from a possibly-malformed synthesis payload.

    Guards against `summary_params` being absent OR present-but-not-a-dict
    (e.g. a legacy string value).  `d.get(k, {})` only uses the default when
    the key is missing; if the key exists with a bad value it returns that value
    and the subsequent `.get()` crashes.  This helper eliminates that footgun
    for all renderers.
    """
    strategy_synthesis = normalize_strategy_synthesis_payload(strategy_synthesis)
    if not strategy_synthesis:
        return 0
    summary_params = strategy_synthesis.get("summary_params")
    if not isinstance(summary_params, dict):
        return 0
    count = summary_params.get("invalid_opinion_count")
    if isinstance(count, bool):
        return 0
    if isinstance(count, int):
        return count if count > 0 else 0
    if isinstance(count, str):
        normalized = count.strip()
        if normalized.isascii() and normalized.isdecimal():
            parsed = int(normalized)
            return parsed if parsed > 0 else 0
    return 0


def localize_strategy_synthesis_summary(strategy_synthesis: Any, language: Optional[str]) -> str:
    """Render a language-specific summary from the structured synthesis payload."""
    strategy_synthesis = normalize_strategy_synthesis_payload(strategy_synthesis)
    if not strategy_synthesis:
        return ""
    lang = normalize_report_language(language)
    summary_params = strategy_synthesis.get("summary_params")
    if not isinstance(summary_params, dict):
        summary_params = {}
    opinion_count = summary_params.get("opinion_count")
    if not isinstance(opinion_count, int):
        opinion_count = len(strategy_synthesis.get("supporting_skills") or []) + len(strategy_synthesis.get("opposing_skills") or [])
    final_signal = localize_strategy_signal(strategy_synthesis.get("final_signal"), lang)
    consensus_level = localize_consensus_level(strategy_synthesis.get("consensus_level"), lang)
    conflict_severity = localize_conflict_severity(strategy_synthesis.get("conflict_severity"), lang)
    conflict_count = strategy_synthesis.get("conflict_count", 0)
    if lang != "en":
        if conflict_count:
            base = (
                f"Tổng hợp từ {opinion_count} chiến lược: tín hiệu tổng hợp là {final_signal}, "
                f"mức đồng thuận {consensus_level}, mức độ xung đột {conflict_severity}."
            )
        else:
            base = (
                f"Tổng hợp từ {opinion_count} chiến lược: tín hiệu tổng hợp là {final_signal}, "
                f"mức đồng thuận {consensus_level}, không phát hiện xung đột."
            )
        return base
    if lang == "en":
        if conflict_count:
            base = f"Strategy synthesis from {opinion_count} strategies: final signal is {final_signal}, consensus level is {consensus_level}, conflict severity is {conflict_severity}."
        else:
            base = f"Strategy synthesis from {opinion_count} strategies: final signal is {final_signal}, consensus level is {consensus_level}, with no detected conflicts."
        return base
    return base


def localize_bias_status(value: Any, language: Optional[str]) -> str:
    """Translate price bias status labels between Chinese and English when recognized."""
    return _translate_from_map(
        value,
        language,
        canonical_map=_BIAS_STATUS_CANONICAL_MAP,
        translations=_BIAS_STATUS_TRANSLATIONS,
    )


# Nhãn tiếng Việt cho mã hạn chế dữ liệu dạng "quote: stale" do analysis_context_builder
# sinh ra; mã chuẩn vẫn dùng trong prompt/overview và giữ nguyên với báo cáo tiếng Anh.
# Vietnamese labels for "quote: stale"-style data-limitation codes; English keeps the code.
_DATA_LIMITATION_BLOCK_LABELS_VI = {
    "quote": "Báo giá",
    "daily_bars": "Nến ngày",
    "technical": "Kỹ thuật",
    "news": "Tin tức",
    "fundamentals": "Cơ bản",
}
_DATA_LIMITATION_STATUS_LABELS_VI = {
    "available": "đầy đủ",
    "missing": "thiếu",
    "not_supported": "không hỗ trợ",
    "fallback": "dùng nguồn dự phòng",
    "stale": "dữ liệu trễ",
    "estimated": "ước lượng",
    "partial": "một phần",
    "fetch_failed": "lấy dữ liệu lỗi",
}


def localize_data_limitation(value: Any, language: Optional[str]) -> str:
    """Translate a ``"<block>: <status>"`` data-limitation code; other text is returned unchanged."""
    text = str(value or "").strip()
    if normalize_report_language(language) == "en" or ":" not in text:
        return text
    block, _, status = text.partition(":")
    block_label = _DATA_LIMITATION_BLOCK_LABELS_VI.get(block.strip())
    status_label = _DATA_LIMITATION_STATUS_LABELS_VI.get(status.strip())
    if not block_label or not status_label:
        return text
    return f"{block_label}: {status_label}"


def get_bias_status_emoji(value: Any) -> str:
    """Return the stable alert emoji for a localized or canonical bias status."""
    canonical = _canonicalize_lookup_value(value, _BIAS_STATUS_CANONICAL_MAP)
    if canonical == "safe":
        return "✅"
    if canonical == "caution":
        return "⚠️"
    return "🚨"


def infer_decision_type_from_advice(value: Any, default: str = "hold") -> str:
    """Infer buy/hold/sell from human-readable operation advice."""
    canonical = _canonicalize_lookup_value(value, _OPERATION_ADVICE_CANONICAL_MAP)
    if canonical in {"strong_buy", "buy"}:
        return "buy"
    if canonical in {"reduce", "sell", "strong_sell"}:
        return "sell"
    if canonical in {"hold", "watch"}:
        return "hold"

    normalized_text = _normalize_lookup_key(value)
    best_position: Optional[int] = None
    best_canonical: Optional[str] = None
    for option, canonical in _OPERATION_ADVICE_CANONICAL_MAP.items():
        option_norm = _normalize_lookup_key(option)
        pos = _first_non_negated_position(normalized_text, option_norm)
        if pos is None:
            continue
        if best_position is None or pos < best_position:
            best_position = pos
            best_canonical = canonical

    if best_canonical in {"strong_buy", "buy"}:
        return "buy"
    if best_canonical in {"reduce", "sell", "strong_sell"}:
        return "sell"
    if best_canonical in {"hold", "watch"}:
        return "hold"

    return default


def get_signal_level(advice: Any, score: Any, language: Optional[str]) -> tuple[str, str, str]:
    """Return localized signal text, emoji, and stable color tag."""
    normalized_language = normalize_report_language(language)
    canonical = _canonicalize_lookup_value(advice, _OPERATION_ADVICE_CANONICAL_MAP)
    if canonical == "strong_buy":
        return (_OPERATION_ADVICE_TRANSLATIONS["strong_buy"][normalized_language], "💚", "strong_buy")
    if canonical == "buy":
        return (_OPERATION_ADVICE_TRANSLATIONS["buy"][normalized_language], "🟢", "buy")
    if canonical == "hold":
        return (_OPERATION_ADVICE_TRANSLATIONS["hold"][normalized_language], "🟡", "hold")
    if canonical == "watch":
        return (_OPERATION_ADVICE_TRANSLATIONS["watch"][normalized_language], "⚪", "watch")
    if canonical == "reduce":
        return (_OPERATION_ADVICE_TRANSLATIONS["reduce"][normalized_language], "🟠", "reduce")
    if canonical in {"sell", "strong_sell"}:
        return (_OPERATION_ADVICE_TRANSLATIONS["sell"][normalized_language], "🔴", "sell")

    try:
        numeric_score = int(float(score))
    except (TypeError, ValueError):
        numeric_score = 50

    score_signal = signal_key_for_score(numeric_score)
    if score_signal == "strong_buy":
        return (_OPERATION_ADVICE_TRANSLATIONS["strong_buy"][normalized_language], "💚", "strong_buy")
    if score_signal == "buy":
        return (_OPERATION_ADVICE_TRANSLATIONS["buy"][normalized_language], "🟢", "buy")
    if score_signal == "watch":
        return (_OPERATION_ADVICE_TRANSLATIONS["watch"][normalized_language], "⚪", "watch")
    if score_signal == "reduce":
        return (_OPERATION_ADVICE_TRANSLATIONS["reduce"][normalized_language], "🟠", "reduce")
    return (_OPERATION_ADVICE_TRANSLATIONS["sell"][normalized_language], "🔴", "sell")


def get_localized_stock_name(value: Any, code: Any, language: Optional[str]) -> str:
    """Return a localized stock name placeholder when the original name is missing."""
    raw_text = str(value or "").strip()
    if not _is_placeholder_stock_name(raw_text, code):
        return raw_text
    return _GENERIC_STOCK_NAME_BY_LANGUAGE[normalize_report_language(language)]


def get_sentiment_label(score: int, language: Optional[str]) -> str:
    """Return localized sentiment label by score band."""
    normalized = normalize_report_language(language)
    if normalized == "vi":
        if score >= 80:
            return "Rất lạc quan"
        if score >= 60:
            return "Lạc quan"
        if score >= 40:
            return "Trung lập"
        if score >= 20:
            return "Bi quan"
        return "Rất bi quan"

    if score >= 80:
        return "Very Bullish"
    if score >= 60:
        return "Bullish"
    if score >= 40:
        return "Neutral"
    if score >= 20:
        return "Bearish"
    return "Very Bearish"
