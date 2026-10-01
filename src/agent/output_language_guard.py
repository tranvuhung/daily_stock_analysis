# -*- coding: utf-8 -*-
"""Bảo đảm câu trả lời Hỏi đáp tiếng Việt không còn chữ Hán.

System prompt và hướng dẫn chiến lược (`strategies/*.yaml`) phần lớn là tiếng Trung
(nhãn công cụ phân tích kỹ thuật đã chuyển sang tiếng Anh), nên model đôi khi vẫn chép
thuật ngữ vào câu trả lời dù đã được dặn trả lời tiếng Việt. Module này chạy trên câu
trả lời cuối:

1. Không có chữ Hán → giữ nguyên.
2. Có chữ Hán → nhờ LLM dịch các đoạn tiếng Trung sang tiếng Việt (một lần gọi).
3. Luôn chạy bước làm sạch tất định: bỏ chú thích tiếng Trung trong ngoặc, dịch thuật
   ngữ quen thuộc theo bảng từ vựng, rồi loại bỏ chữ Hán còn sót. Bước này bảo đảm
   kết quả không còn chữ Hán kể cả khi LLM lỗi hoặc không có LLM (backend Codex).

Ensures Vietnamese chat answers contain no Han characters: optional LLM rewrite,
then a deterministic glossary + strip pass that always runs.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Chữ Hán (CJK Unified Ideographs + Extension A + Compatibility Ideographs).
_HAN_CHARS = "㐀-䶿一-鿿豈-﫿"
_HAN_RE = re.compile(f"[{_HAN_CHARS}]")
# Dấu câu CJK đi kèm chữ Hán (、。〈〉《》「」『』【】〔〕 ...).
_CJK_PUNCT = "　-〿"
_HAN_RUN_RE = re.compile(f"[{_HAN_CHARS}][{_HAN_CHARS}{_CJK_PUNCT}·]*")
# Ngoặc chỉ chứa chữ Hán / dấu câu / khoảng trắng — thường là chú thích của từ tiếng Việt ngay trước.
_HAN_ONLY_PARENS_RE = re.compile(
    f"\\s*[（(][\\s{_HAN_CHARS}{_CJK_PUNCT}·、，,/]*[{_HAN_CHARS}][\\s{_HAN_CHARS}{_CJK_PUNCT}·、，,/]*[）)]"
)

_FULLWIDTH_PUNCT = {
    "，": ", ",
    "。": ". ",
    "；": "; ",
    "：": ": ",
    "！": "! ",
    "？": "? ",
    "（": "(",
    "）": ")",
    "「": "\"",
    "」": "\"",
    "『": "\"",
    "』": "\"",
    "【": "[",
    "】": "]",
    "、": ", ",
    "～": "~",
}

# Thuật ngữ thường gặp từ công cụ phân tích (src/stock_analyzer.py), chiến lược
# (strategies/*.yaml) và văn phong phân tích. Khớp cụm dài trước.
VI_GLOSSARY = {
    # TrendStatus
    "强势多头": "xu hướng tăng mạnh",
    "多头排列": "đường MA xếp tăng",
    "弱势多头": "xu hướng tăng yếu",
    "盘整": "tích luỹ đi ngang",
    "弱势空头": "xu hướng giảm yếu",
    "空头排列": "đường MA xếp giảm",
    "强势空头": "xu hướng giảm mạnh",
    # VolumeStatus
    "放量上涨": "tăng kèm khối lượng lớn",
    "放量下跌": "giảm kèm khối lượng lớn",
    "缩量上涨": "tăng với khối lượng thấp",
    "缩量回调": "điều chỉnh với khối lượng thấp",
    "缩量回踩": "điều chỉnh với khối lượng thấp",
    "量能正常": "khối lượng bình thường",
    "放量突破": "bứt phá kèm khối lượng lớn",
    "底部放量": "khối lượng tăng tại đáy",
    # BuySignal / khuyến nghị
    "强烈买入": "mua mạnh",
    "强烈卖出": "bán mạnh",
    "减仓/卖出": "giảm tỷ trọng/bán",
    "减仓": "giảm tỷ trọng",
    "加仓": "tăng tỷ trọng",
    "买入": "mua",
    "卖出": "bán",
    "持有": "nắm giữ",
    "观望": "quan sát",
    # MACDStatus
    "零轴上金叉": "giao cắt vàng trên trục 0",
    "上穿零轴": "cắt lên trục 0",
    "下穿零轴": "cắt xuống trục 0",
    "金叉": "giao cắt vàng",
    "死叉": "giao cắt tử thần",
    # RSIStatus
    "超买": "quá mua",
    "超卖": "quá bán",
    "强势买入": "vùng mua mạnh",
    "中性": "trung tính",
    "弱势": "yếu",
    # Chiến lược
    "默认多头趋势": "Xu hướng tăng (mặc định)",
    "均线金叉": "giao cắt vàng MA",
    "热点题材": "chủ đề nóng",
    "事件驱动": "theo sự kiện",
    "箱体震荡": "dao động hộp giá",
    "成长质量": "chất lượng tăng trưởng",
    "预期重估": "định giá lại kỳ vọng",
    "缠论": "lý thuyết Chan",
    "波浪理论": "lý thuyết sóng Elliott",
    "龙头策略": "cổ phiếu dẫn dắt",
    "情绪周期": "chu kỳ tâm lý",
    "一阳夹三阴": "mô hình một nến tăng kẹp ba nến giảm",
    # Thuật ngữ chung
    "筹码分布": "phân bổ khối lượng nắm giữ",
    "筹码": "khối lượng nắm giữ",
    "主力资金": "dòng tiền lớn",
    "主力": "dòng tiền lớn",
    "洗盘": "rũ bỏ",
    "支撑位": "vùng hỗ trợ",
    "压力位": "vùng kháng cự",
    "阻力位": "vùng kháng cự",
    "支撑": "hỗ trợ",
    "压力": "kháng cự",
    "阻力": "kháng cự",
    "回踩": "điều chỉnh về",
    "回调": "điều chỉnh",
    "突破": "bứt phá",
    "均线": "đường MA",
    "乖离率": "độ lệch giá so với MA",
    "成交量": "khối lượng giao dịch",
    "量能": "khối lượng",
    "放量": "khối lượng tăng",
    "缩量": "khối lượng giảm",
    "止损": "cắt lỗ",
    "止盈": "chốt lời",
    "仓位": "tỷ trọng",
    "多头": "xu hướng tăng",
    "空头": "xu hướng giảm",
    "趋势": "xu hướng",
    "风险": "rủi ro",
}
_GLOSSARY_RE = re.compile("|".join(re.escape(k) for k in sorted(VI_GLOSSARY, key=len, reverse=True)))

_REWRITE_SYSTEM_PROMPT = (
    "Bạn là biên tập viên tài chính. Văn bản người dùng gửi là câu trả lời phân tích cổ phiếu "
    "bằng tiếng Việt nhưng còn lẫn chữ tiếng Trung. Hãy dịch MỌI đoạn tiếng Trung sang tiếng Việt "
    "có dấu; nếu đoạn tiếng Trung chỉ là chú thích trong ngoặc cho từ tiếng Việt đứng trước thì bỏ hẳn. "
    "Giữ nguyên toàn bộ nội dung khác, số liệu, mã cổ phiếu và định dạng Markdown. "
    "Kết quả không được chứa bất kỳ chữ Hán nào. Chỉ trả về văn bản đã sửa, không giải thích."
)
# Giới hạn thời gian cho lượt dịch lại; quá hạn thì dùng bước làm sạch tất định.
# Time budget for the rewrite call; on timeout the deterministic pass still applies.
_REWRITE_TIMEOUT_SECONDS = 60.0


def contains_han(text: Optional[str]) -> bool:
    """Whether ``text`` contains any Han (Chinese) character."""
    return bool(text) and bool(_HAN_RE.search(text))


def _glossary_replacement(match: "re.Match[str]", source: str) -> str:
    """Translate one glossary hit, adding spaces when it touches Latin words/digits."""
    translated = VI_GLOSSARY[match.group(0)]
    before = source[match.start() - 1:match.start()]
    after = source[match.end():match.end() + 1]
    if before and before.isalnum() and not _HAN_RE.match(before):
        translated = " " + translated
    if after and after.isalnum() and not _HAN_RE.match(after):
        translated = translated + " "
    return translated


_LINE_PREFIX_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)]|>)?\s*)(.*)$", re.DOTALL)


def _collapse_inner_spaces(line: str) -> str:
    match = _LINE_PREFIX_RE.match(line)
    if not match:
        return line
    prefix, body = match.groups()
    return prefix + re.sub(r"[ \t]{2,}", " ", body)


def strip_han_deterministic(text: str) -> str:
    """Remove Han text without an LLM: drop Han-only glosses, translate known terms, strip the rest."""
    if not contains_han(text):
        return text
    result = _HAN_ONLY_PARENS_RE.sub("", text)
    result = _GLOSSARY_RE.sub(lambda m: _glossary_replacement(m, result), result)
    for src, dst in _FULLWIDTH_PUNCT.items():
        result = result.replace(src, dst)
    result = _HAN_RUN_RE.sub("", result)
    # Dọn dấu thừa sau khi xoá: ngoặc/nháy/đậm rỗng, khoảng trắng kép, dấu câu dính khoảng trắng.
    result = re.sub(r"\(\s*\)|\[\s*\]|\"\s*\"|“\s*”|\*\*\s*\*\*", "", result)
    # Chỉ gộp khoảng trắng trong nội dung dòng; giữ thụt lề và ký hiệu danh sách Markdown.
    result = "\n".join(_collapse_inner_spaces(line) for line in result.split("\n"))
    result = re.sub(r"[ \t]+([,.;:!?])", r"\1", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return result


def _rewrite_with_llm(text: str, llm_adapter: Any) -> Optional[str]:
    call_text = getattr(llm_adapter, "call_text", None)
    if not callable(call_text):
        return None
    try:
        response = call_text(
            [
                {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
            timeout=_REWRITE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - lỗi dịch không được làm hỏng lượt chat
        logger.warning("Vietnamese output rewrite failed: %s", exc)
        return None
    if getattr(response, "provider", "") == "error":
        logger.warning("Vietnamese output rewrite failed: %s", getattr(response, "content", ""))
        return None
    rewritten = (getattr(response, "content", None) or "").strip()
    # Bản dịch quá ngắn nhiều khả năng bị cắt/tóm tắt → không dùng.
    if not rewritten or len(rewritten) < len(text.strip()) * 0.6:
        logger.warning("Vietnamese output rewrite rejected (len %d vs %d)", len(rewritten), len(text))
        return None
    return rewritten


def enforce_vietnamese_output(
    text: Optional[str],
    report_language: Optional[str],
    llm_adapter: Any = None,
) -> Optional[str]:
    """Return ``text`` with no Han characters when the report language is Vietnamese."""
    from src.report_language import normalize_report_language

    if not text or normalize_report_language(report_language or "zh") != "vi" or not contains_han(text):
        return text
    han_count = len(_HAN_RE.findall(text))
    candidate = text
    if llm_adapter is not None:
        rewritten = _rewrite_with_llm(text, llm_adapter)
        if rewritten is not None:
            candidate = rewritten
    cleaned = strip_han_deterministic(candidate)
    logger.info(
        "Vietnamese output guard: removed %d Han char(s) (llm_rewrite=%s)",
        han_count,
        candidate is not text,
    )
    return cleaned
