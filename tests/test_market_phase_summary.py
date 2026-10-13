# -*- coding: utf-8 -*-
"""Regression tests for Issue #1386 P1b market phase summary."""

import json

from src.market_phase_summary import (
    MARKET_PHASE_SUMMARY_KEY,
    extract_market_phase_summary,
    format_public_market_status_line,
    format_public_phase_pack_excerpt,
    normalize_analysis_phase_bucket,
    rebuild_market_phase_summary_for_stock_code,
    render_market_phase_summary,
)


SUMMARY_KEYS = {
    "market",
    "phase",
    "market_local_time",
    "session_date",
    "effective_daily_bar_date",
    "is_trading_day",
    "is_market_open_now",
    "is_partial_bar",
    "minutes_to_open",
    "minutes_to_close",
    "trigger_source",
    "analysis_intent",
    "warnings",
}


def _phase_context() -> dict:
    return {
        "market": "vn",
        "phase": "intraday",
        "market_local_time": "2026-03-27T10:00:00+07:00",
        "session_date": "2026-03-27",
        "effective_daily_bar_date": "2026-03-26",
        "is_trading_day": True,
        "is_market_open_now": True,
        "is_partial_bar": True,
        "minutes_to_open": None,
        "minutes_to_close": 300,
        "trigger_source": "api",
        "analysis_intent": "auto",
        "warnings": ["partial_bar", "partial_bar"],
    }


def test_render_market_phase_summary_outputs_only_public_whitelist() -> None:
    payload = {
        **_phase_context(),
        "market_phase_context": {"raw": True},
        "analysis_context_pack_summary": "prompt text should not be exposed",
        "analysis_context_pack": {"blocks": {"quote": {"items": {"value": 1}}}},
        "quote_freshness": "fresh",
        "data_quality_score": 100,
    }

    summary = render_market_phase_summary(payload)

    assert summary is not None
    assert set(summary) == SUMMARY_KEYS
    assert summary["phase"] == "intraday"
    assert summary["minutes_to_close"] == 300
    assert summary["warnings"] == ["partial_bar"]
    rendered = json.dumps(summary, ensure_ascii=False)
    assert "market_phase_context" not in rendered
    assert "analysis_context_pack_summary" not in rendered
    assert "prompt text" not in rendered
    assert "quote_freshness" not in rendered
    assert "data_quality_score" not in rendered


def test_extract_market_phase_summary_re_sanitizes_persisted_snapshot() -> None:
    summary = {
        **_phase_context(),
        "prompt": "raw prompt should not pass through",
        "market_phase_context": {"raw": True},
        "quote_timestamp": "2026-03-27T10:00:00+07:00",
        "fallback_provider": "secret-provider",
        "trigger_source": {"prompt": "raw prompt should not pass through"},
        "analysis_intent": "api_key=secret",
    }
    snapshot = json.dumps(
        {
            MARKET_PHASE_SUMMARY_KEY: summary,
            "enhanced_context": {"code": "FPT"},
        },
        ensure_ascii=False,
    )

    extracted = extract_market_phase_summary(snapshot)

    assert extracted is not None
    assert set(extracted) == SUMMARY_KEYS
    assert extracted["phase"] == "intraday"
    assert extracted["trigger_source"] is None
    assert extracted["analysis_intent"] == "[REDACTED]"
    assert "prompt" not in extracted
    assert "market_phase_context" not in extracted
    assert "quote_timestamp" not in extracted
    assert "fallback_provider" not in extracted


def test_extract_market_phase_summary_returns_none_for_missing_or_malformed_snapshot() -> None:
    assert extract_market_phase_summary(None) is None
    assert extract_market_phase_summary({"enhanced_context": {"code": "FPT"}}) is None
    assert extract_market_phase_summary({MARKET_PHASE_SUMMARY_KEY: "not-a-dict"}) is None
    assert extract_market_phase_summary(
        {MARKET_PHASE_SUMMARY_KEY: {**_phase_context(), "phase": "bad_phase"}}
    ) is None
    assert extract_market_phase_summary("not-json") is None


def test_normalize_analysis_phase_bucket_maps_public_statistics_buckets() -> None:
    assert normalize_analysis_phase_bucket("premarket") == "premarket"
    assert normalize_analysis_phase_bucket("intraday") == "intraday"
    assert normalize_analysis_phase_bucket("lunch_break") == "intraday"
    assert normalize_analysis_phase_bucket("closing_auction") == "intraday"
    assert normalize_analysis_phase_bucket("postmarket") == "postmarket"
    assert normalize_analysis_phase_bucket("non_trading") == "unknown"
    assert normalize_analysis_phase_bucket(None) == "unknown"
    assert normalize_analysis_phase_bucket("bad_phase") == "unknown"


def test_rebuild_market_phase_summary_returns_sanitized_summary_without_rebuild() -> None:
    summary = {
        **_phase_context(),
        "market": "vn",
        "phase": "non_trading",
        "analysis_intent": "non_trading",
    }

    rebuilt = rebuild_market_phase_summary_for_stock_code(
        "FPT",
        {MARKET_PHASE_SUMMARY_KEY: summary},
    )

    assert rebuilt == extract_market_phase_summary({MARKET_PHASE_SUMMARY_KEY: summary})
    assert rebuild_market_phase_summary_for_stock_code("FPT", None) is None


def test_format_public_phase_pack_excerpt_limits_and_redacts_public_fields() -> None:
    excerpt = format_public_phase_pack_excerpt(
        {
            "phase": "intraday",
            "market": "vn",
            "trigger_source": "portfolio",
            "is_partial_bar": True,
        },
        {
            "data_quality": {
                "level": "limited",
                "limitations": [
                    "quote stale",
                    "api_key=secret should not leak",
                    "news missing",
                ],
            }
        },
        source="analysis_history_snapshot",
        report_language="zh",
    )

    # zh không còn được hỗ trợ: quy về tiếng Việt. / zh is no longer supported: falls back to Vietnamese.
    assert "giai đoạn: intraday" in excerpt
    assert "nguồn kích hoạt: portfolio" in excerpt
    assert "nguồn tóm tắt: snapshot phân tích gần nhất" in excerpt
    assert "cảnh báo nến chưa đóng" in excerpt
    assert "chất lượng dữ liệu: limited" in excerpt
    assert "hạn chế: quote stale" in excerpt
    assert "hạn chế: [REDACTED]" in excerpt
    assert "news missing" not in excerpt
    assert "api_key=secret" not in excerpt
    assert not any("一" <= ch <= "鿿" for ch in excerpt)


def test_format_public_phase_pack_excerpt_returns_empty_without_summary_or_pack() -> None:
    assert format_public_phase_pack_excerpt(None, None, source="evaluator_snapshot") == ""


def test_format_public_market_status_line_localizes_compact_summary() -> None:
    assert (
        format_public_market_status_line(
            {"market": "vn", "phase": "postmarket"},
            report_language="zh",
        )
        == "Trạng thái thị trường: Việt Nam · Sau phiên"
    )
    assert (
        format_public_market_status_line(
            {"market": "vn", "phase": "premarket"},
            report_language="en",
        )
        == "Market status: Vietnam · Pre-market"
    )


def test_format_public_market_status_line_returns_empty_without_valid_phase() -> None:
    assert format_public_market_status_line(None, report_language="zh") == ""
    assert format_public_market_status_line({"market": "vn"}, report_language="zh") == ""
    assert (
        format_public_market_status_line(
            {"market": "vn", "phase": "bad_phase"},
            report_language="zh",
        )
        == ""
    )


def _alert_excerpt_inputs() -> tuple[dict, dict]:
    # Tái hiện tin cảnh báo NLG thực tế: phase unknown, thị trường vn, nguồn "analysis_history_snapshot".
    return (
        {"phase": "unknown", "market": "vn", "trigger_source": "alert"},
        {"data_quality": {"level": "usable", "limitations": ["quote: fallback", "technical: partial"]}},
    )


def test_format_public_phase_pack_excerpt_vietnamese_has_no_chinese() -> None:
    phase, pack = _alert_excerpt_inputs()
    excerpt = format_public_phase_pack_excerpt(
        phase, pack, source="analysis_history_snapshot", report_language="vi"
    )

    assert excerpt == (
        "- giai đoạn: unknown | thị trường: vn | nguồn kích hoạt: alert"
        " | nguồn tóm tắt: snapshot phân tích gần nhất\n"
        "- chất lượng dữ liệu: usable\n"
        "- hạn chế: quote: fallback\n"
        "- hạn chế: technical: partial"
    )
    assert not any("一" <= ch <= "鿿" for ch in excerpt)


def test_format_public_phase_pack_excerpt_english_labels() -> None:
    phase, pack = _alert_excerpt_inputs()
    excerpt = format_public_phase_pack_excerpt(
        {**phase, "is_partial_bar": True}, pack, source="analysis_history_snapshot", report_language="en"
    )

    assert "- phase: unknown | market: vn | trigger: alert | source: recent analysis snapshot" in excerpt
    assert "- partial-bar warning: intraday data may be incomplete" in excerpt
    assert "- data quality: usable" in excerpt
    assert "- limitation: quote: fallback" in excerpt
    assert not any("一" <= ch <= "鿿" for ch in excerpt)


def test_format_public_market_status_line_vietnamese() -> None:
    assert (
        format_public_market_status_line({"market": "vn", "phase": "intraday"}, report_language="vi")
        == "Trạng thái thị trường: Việt Nam · Trong phiên"
    )
