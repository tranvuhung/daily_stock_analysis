# -*- coding: utf-8 -*-
"""Câu trả lời Hỏi đáp tiếng Việt không được còn chữ Hán."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.output_language_guard import (  # noqa: E402
    contains_han,
    enforce_vietnamese_output,
    strip_han_deterministic,
)

_SAMPLE = (
    "*   **Khối lượng:** tín hiệu **\"thu hẹp khối lượng\" (缩量回调)** gần đây.\n"
    "- Xu hướng: 多头排列，MACD 金叉。\n"
    "- Cấu trúc筹码 (chip) ổn định\n"
    "- Ghi chú: 这是一个测试句子"
)


class _Adapter:
    def __init__(self, content: str, provider: str = "gemini") -> None:
        self.response = SimpleNamespace(content=content, provider=provider)
        self.calls = []

    def call_text(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.response


def test_deterministic_pass_removes_all_han_and_keeps_markdown() -> None:
    cleaned = strip_han_deterministic(_SAMPLE)

    assert not contains_han(cleaned)
    assert cleaned.startswith("*   **Khối lượng:** tín hiệu **\"thu hẹp khối lượng\"** gần đây.")
    assert "đường MA xếp tăng, MACD giao cắt vàng." in cleaned
    assert "Cấu trúc khối lượng nắm giữ (chip) ổn định" in cleaned


def test_non_vietnamese_or_clean_text_is_untouched() -> None:
    adapter = _Adapter("unused")

    assert enforce_vietnamese_output(_SAMPLE, "en", adapter) == _SAMPLE
    assert enforce_vietnamese_output("Không có chữ Hán", "vi", adapter) == "Không có chữ Hán"
    assert adapter.calls == []


def test_llm_rewrite_is_used_then_cleaned() -> None:
    rewritten = _SAMPLE.replace("(缩量回调)", "").replace("多头排列，MACD 金叉。", "MA xếp tăng, MACD cắt lên.")
    adapter = _Adapter(rewritten)

    result = enforce_vietnamese_output(_SAMPLE, "vi", adapter)

    assert len(adapter.calls) == 1
    assert "MA xếp tăng, MACD cắt lên." in result
    assert not contains_han(result)


def test_rejected_or_failed_rewrite_falls_back_to_deterministic_pass() -> None:
    for adapter in (_Adapter("quá ngắn"), _Adapter("All LLM models failed", provider="error")):
        result = enforce_vietnamese_output(_SAMPLE, "vi", adapter)
        assert not contains_han(result)
        assert "đường MA xếp tăng" in result

    assert not contains_han(enforce_vietnamese_output(_SAMPLE, "vi", None))
