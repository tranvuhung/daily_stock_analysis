# -*- coding: utf-8 -*-
"""Tests for low-sensitive DecisionSignal summary helpers."""

from __future__ import annotations

import json

from src.services.decision_signal_summary import (
    format_decision_signal_excerpt,
    summarize_decision_signal,
)


def test_summarize_decision_signal_keeps_only_low_sensitive_fields() -> None:
    summary = summarize_decision_signal({
        "id": 42,
        "stock_code": "HPG",
        "stock_name": "Hòa Phát",
        "market": "vn",
        "action": "sell",
        "action_label": "Bán",
        "confidence": 0.72,
        "score": 68,
        "horizon": "3d",
        "status": "active",
        "source_type": "alert",
        "source_agent": "alert_worker",
        "source_report_id": 88,
        "entry_low": 25000,
        "entry_high": 26000,
        "stop_loss": 24000,
        "target_price": 29000,
        "invalidation": "Đóng cửa dưới 24.000",
        "catalyst_summary": ["Kết quả kinh doanh quý"],
        "reason": "token=secret-value kích hoạt cắt lỗ",
        "watch_conditions": ["Theo dõi khối lượng", "password=hidden"],
        "risk_summary": {"drawdown": "webhook=https://hooks.slack.com/services/T/B/C"},
        "created_at": "2026-06-18T10:00:00+07:00",
        "expires_at": "2026-06-25T10:00:00+07:00",
        "metadata": {"webhook_url": "https://hooks.slack.com/services/T/B/C"},
        "evidence": {"secret": "raw"},
        "diagnostics": "authorization=Bearer raw",
    })

    assert summary is not None
    assert set(summary) == {
        "id",
        "stock_code",
        "stock_name",
        "market",
        "action",
        "action_label",
        "confidence",
        "score",
        "horizon",
        "status",
        "source_type",
        "source_report_id",
        "entry_low",
        "entry_high",
        "stop_loss",
        "target_price",
        "reason",
        "watch_conditions",
        "invalidation",
        "risk_summary",
        "catalyst_summary",
        "created_at",
        "expires_at",
    }
    assert summary["reason"] == "token=[REDACTED] kích hoạt cắt lỗ"
    assert summary["watch_conditions"] == ["Theo dõi khối lượng", "password=[REDACTED]"]
    assert summary["risk_summary"] == {"drawdown": "webhook=[REDACTED_URL]"}


def test_summarize_decision_signal_rejects_non_dict_and_empty_payload() -> None:
    assert summarize_decision_signal(None) is None
    assert summarize_decision_signal(["not", "a", "dict"]) is None
    assert summarize_decision_signal({"metadata": {"token": "secret"}, "evidence": {"raw": True}}) is None
    assert summarize_decision_signal({"stock_code": "", "reason": None}) is None


def test_format_decision_signal_excerpt_formats_vietnamese_list_and_dict_fields() -> None:
    excerpt = format_decision_signal_excerpt({
        "action_label": "Bán",
        "horizon": "3d",
        "source_report_id": 88,
        "reason": "Thủng ngưỡng cắt lỗ",
        "watch_conditions": ["Theo dõi hỗ trợ 26.000", "Chờ khối lượng co lại"],
        "risk_summary": {"drawdown": "Sụt giảm danh mục mở rộng"},
    })

    assert excerpt.startswith("**Tín hiệu quyết định AI**")
    assert "Hành động: Bán | Khung thời gian: 3d | Báo cáo: #88" in excerpt
    assert "- Lý do: Thủng ngưỡng cắt lỗ" in excerpt
    assert "- Điều kiện theo dõi:\n  • Theo dõi hỗ trợ 26.000\n  • Chờ khối lượng co lại" in excerpt
    assert "- Rủi ro: drawdown: Sụt giảm danh mục mở rộng" in excerpt


def test_format_decision_signal_excerpt_falls_back_to_vietnamese_for_legacy_language() -> None:
    # zh/ko đã bị loại bỏ: phải quy về nhãn tiếng Việt thay vì KeyError.
    # zh/ko were removed: they must fall back to Vietnamese labels instead of raising.
    excerpt = format_decision_signal_excerpt({"action": "sell"}, report_language="zh")

    assert excerpt.startswith("**Tín hiệu quyết định AI**")
    assert "Hành động: sell" in excerpt


def test_format_decision_signal_excerpt_formats_english_and_redacts_text() -> None:
    excerpt = format_decision_signal_excerpt({
        "action": "alert",
        "horizon": "5d",
        "reason": "authorization: Bearer raw-token",
        "watch_conditions": "Check price",
        "risk_summary": "token=hidden",
    }, report_language="en")

    assert excerpt.startswith("**AI decision signal**")
    assert "Action: alert | Horizon: 5d" in excerpt
    assert "- Reason: authorization: [REDACTED]" in excerpt
    assert "- Watch: Check price" in excerpt
    assert "- Risk: token=[REDACTED]" in excerpt


def test_format_decision_signal_excerpt_preserves_complete_sanitized_reason() -> None:
    reason = (
        "HPG đang trong giai đoạn dao động trung hạn, điều chỉnh ngắn hạn. Về kỹ thuật, giá giảm "
        "với khối lượng thấp về MA5, độ lệch chỉ 1,29%, có dấu hiệu kết thúc rũ bỏ. Tuy nhiên, hệ "
        "thống đường trung bình chưa xếp tăng chuẩn và rủi ro thị trường chung ở mức cao, hạn chế "
        "dư địa hồi phục. Nhà đầu tư nên thận trọng, theo dõi hỗ trợ MA5, tránh mua đuổi."
    )

    excerpt = format_decision_signal_excerpt({
        "reason": f"{reason} token=secret-value",
        "watch_conditions": "theo dõi " * 40,
        "risk_summary": "rủi ro " * 40,
    })

    assert f"- Lý do: {reason} token=[REDACTED]" in excerpt
    assert "tránh mua đuổi." in excerpt
    watch_line = next(line for line in excerpt.splitlines() if line.startswith("- Điều kiện theo dõi: "))
    risk_line = next(line for line in excerpt.splitlines() if line.startswith("- Rủi ro: "))
    # Rủi ro / điều kiện theo dõi dài không còn bị cắt.
    # Long risk / watch text is no longer truncated.
    assert watch_line.removeprefix("- Điều kiện theo dõi: ") == ("theo dõi " * 40).strip()
    assert risk_line.removeprefix("- Rủi ro: ") == ("rủi ro " * 40).strip()


def test_format_decision_signal_excerpt_returns_empty_for_invalid_input() -> None:
    assert format_decision_signal_excerpt(None) == ""
    assert format_decision_signal_excerpt({}) == ""
    assert format_decision_signal_excerpt(["not", "a", "dict"]) == ""


def test_format_decision_signal_excerpt_decodes_json_list_from_db() -> None:
    # DB lưu list dưới dạng chuỗi JSON; tin gửi phải liệt kê đủ từng rủi ro, không còn ký tự JSON thô.
    # The DB stores lists as JSON strings; every risk must be listed, without raw JSON characters.
    risks = [f"Rủi ro số {index}: áp lực bán ròng khối ngoại kéo dài nhiều phiên liên tiếp" for index in range(1, 6)]
    excerpt = format_decision_signal_excerpt({
        "action_label": "Theo dõi",
        "risk_summary": json.dumps(risks, ensure_ascii=False, sort_keys=True),
        "watch_conditions": json.dumps(["Giữ hỗ trợ MA20"], ensure_ascii=False),
    })

    assert "- Rủi ro:" in excerpt
    for risk in risks:
        assert f"  • {risk}" in excerpt
    assert "- Điều kiện theo dõi: Giữ hỗ trợ MA20" in excerpt
    assert '["' not in excerpt


def test_format_decision_signal_excerpt_keeps_invalid_json_text() -> None:
    excerpt = format_decision_signal_excerpt({"risk_summary": "[thiếu dấu đóng"})

    assert "- Rủi ro: [thiếu dấu đóng" in excerpt


def test_format_decision_signal_excerpt_includes_prices_quality_and_disclaimer_vi() -> None:
    excerpt = format_decision_signal_excerpt({
        "action_label": "Mua",
        "market": "vn",
        "confidence": 0.725,
        "score": 68,
        "entry_low": 25000,
        "entry_high": 26000,
        "stop_loss": 24000,
        "target_price": 29500.5,
        "invalidation": ["Đóng cửa dưới 24.000"],
        "catalyst_summary": ["Kết quả kinh doanh quý", "Dòng tiền ETF"],
        "expires_at": "2026-10-16T03:30:00",
    })

    assert "Độ tin cậy: 72% | Điểm: 68" in excerpt
    assert "Vùng mua: 25.000–26.000 | Cắt lỗ: 24.000 | Mục tiêu: 29.500,5" in excerpt
    assert "- Điều kiện vô hiệu: Đóng cửa dưới 24.000" in excerpt
    assert "- Chất xúc tác:\n  • Kết quả kinh doanh quý\n  • Dòng tiền ETF" in excerpt
    # expires_at naive được hiểu là UTC, hiển thị theo giờ Việt Nam (UTC+7).
    # Naive expires_at is UTC and is shown in Vietnam time (UTC+7).
    assert "Hiệu lực đến: 16/10/2026 10:30" in excerpt
    assert excerpt.splitlines()[-1] == "Nội dung do AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư"


def test_format_decision_signal_excerpt_includes_prices_and_disclaimer_en() -> None:
    excerpt = format_decision_signal_excerpt({
        "action": "buy",
        "entry_low": 25000,
        "stop_loss": 24000,
        "expires_at": "2026-10-16T10:30:00+07:00",
    }, report_language="en")

    assert "Entry: 25,000 | Stop loss: 24,000" in excerpt
    assert "Valid until: 16/10/2026 10:30" in excerpt
    assert excerpt.splitlines()[-1] == "AI-generated content for reference only. Not investment advice."


def test_format_decision_signal_excerpt_hides_empty_optional_fields() -> None:
    excerpt = format_decision_signal_excerpt({"action": "alert", "confidence": "n/a", "stop_loss": None})

    assert "Độ tin cậy" not in excerpt
    assert "Cắt lỗ" not in excerpt
    assert "Vùng mua" not in excerpt
    assert "Hiệu lực đến" not in excerpt
