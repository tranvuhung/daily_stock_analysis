# -*- coding: utf-8 -*-
"""Tests for #1389 P3 AnalysisContextPack prompt summaries."""

from __future__ import annotations

from src.analysis_context_pack_prompt import format_analysis_context_pack_prompt_section
from src.schemas.analysis_context_pack import (
    AnalysisContextBlock,
    AnalysisContextItem,
    AnalysisContextPack,
    AnalysisSubject,
    ContextFieldStatus,
    DataQuality,
)
from src.services.analysis_context_builder import (
    AnalysisContextBuilder,
    PipelineAnalysisArtifacts,
)


def _pack() -> AnalysisContextPack:
    return AnalysisContextPack(
        subject=AnalysisSubject(code="HPG", stock_name="Hoa Phat Group", market="vn"),
        blocks={
            "quote": AnalysisContextBlock(
                status=ContextFieldStatus.FALLBACK,
                source="fallback",
                warnings=["realtime_provider_fallback"],
                items={
                    "price": AnalysisContextItem(
                        status=ContextFieldStatus.FALLBACK,
                        value=1880.0,
                        source="fallback",
                        fallback_from="primary_realtime_provider",
                    )
                },
            ),
            "technical": AnalysisContextBlock(
                status=ContextFieldStatus.PARTIAL,
                warnings=["intraday_realtime_overlay"],
                items={
                    "trend_result": AnalysisContextItem(
                        status=ContextFieldStatus.AVAILABLE,
                        value={"trend_status": "bullish_alignment_raw", "ma5": 1800.0},
                    ),
                    "intraday_overlay": AnalysisContextItem(
                        status=ContextFieldStatus.ESTIMATED,
                        value={"close": 1880.0},
                    ),
                },
            ),
            "news": AnalysisContextBlock(
                status=ContextFieldStatus.MISSING,
                items={
                    "content": AnalysisContextItem(
                        status=ContextFieldStatus.MISSING,
                        value="Full news body must not enter the summary",
                        missing_reason="news_context_missing",
                    )
                },
            ),
            "fundamentals": AnalysisContextBlock(
                status=ContextFieldStatus.AVAILABLE,
                metadata={
                    "coverage": {
                        "valuation": "ok",
                        "access_token": "secret-token",
                    }
                },
                items={
                    "coverage": AnalysisContextItem(
                        status=ContextFieldStatus.AVAILABLE,
                        value={"valuation": "ok", "access_token": "secret-token"},
                    )
                },
            ),
        },
        data_quality=DataQuality(
            overall_score=76,
            level="usable",
            block_scores={
                "quote": 65,
                "daily_bars": 100,
                "technical": 75,
                "news": 35,
                "fundamentals": 100,
                "chip": 100,
            },
            limitations=["quote: fallback", "technical: partial"],
            warnings=["intraday_realtime_overlay"],
        ),
        metadata={
            "query_id": "q-1",
            "trigger_source": "api",
            "news_result_count": 3,
            "webhook_url": "https://hooks.example.test/secret",
        },
    )


def _pack_with_phase(phase: str) -> AnalysisContextPack:
    return _pack().model_copy(
        update={"phase": {"phase": phase, "is_partial_bar": phase != "premarket"}}
    )


def _core_available_pack(*, phase: str) -> AnalysisContextPack:
    return AnalysisContextPack(
        subject=AnalysisSubject(code="HPG", stock_name="Hoa Phat Group", market="vn"),
        phase={"phase": phase, "is_partial_bar": False},
        blocks={
            "quote": AnalysisContextBlock(status=ContextFieldStatus.AVAILABLE),
            "daily_bars": AnalysisContextBlock(status=ContextFieldStatus.AVAILABLE),
            "technical": AnalysisContextBlock(status=ContextFieldStatus.AVAILABLE),
            "news": AnalysisContextBlock(status=ContextFieldStatus.AVAILABLE),
            "fundamentals": AnalysisContextBlock(status=ContextFieldStatus.AVAILABLE),
            "chip": AnalysisContextBlock(status=ContextFieldStatus.AVAILABLE),
        },
        data_quality=DataQuality(
            overall_score=100,
            level="good",
            block_scores={
                "quote": 100,
                "daily_bars": 100,
                "technical": 100,
                "news": 100,
                "fundamentals": 100,
                "chip": 100,
            },
            limitations=[],
        ),
    )


def _builder_artifacts(*, fundamental_context: dict) -> PipelineAnalysisArtifacts:
    return PipelineAnalysisArtifacts(
        code="HPG",
        stock_name="Hoa Phat Group",
        market="vn",
        phase=None,
        base_context={
            "today": {"close": 1880.0},
            "yesterday": {"close": 1870.0},
            "date": "2026-03-26",
        },
        enhanced_context={},
        realtime_quote={"price": 1880.0, "source": "mock_quote"},
        trend_result={"trend_status": "available"},
        fundamental_context=fundamental_context,
        news_context="News summary",
        news_result_count=1,
        metadata={"trigger_source": "api"},
    )


def test_empty_or_invalid_pack_returns_empty_section() -> None:
    assert format_analysis_context_pack_prompt_section(None) == ""
    assert format_analysis_context_pack_prompt_section({}) == ""
    assert format_analysis_context_pack_prompt_section("not-pack") == ""


def test_default_summary_renders_low_sensitivity_pack_statuses_in_english() -> None:
    # Mặc định (vi) và giá trị cũ (zh) đều dùng nhãn cấu trúc tiếng Anh.
    # Default (vi) and legacy (zh) languages both use the English structural labels.
    for language in ("vi", "zh"):
        section = format_analysis_context_pack_prompt_section(_pack(), report_language=language)

        assert section == format_analysis_context_pack_prompt_section(_pack(), report_language="en")
    section = format_analysis_context_pack_prompt_section(_pack())

    assert "Analysis Context Pack Summary" in section
    assert "HPG" in section
    assert "Hoa Phat Group" in section
    assert "quote: fallback" in section
    assert "technical: partial" in section
    assert "warnings=realtime_provider_fallback" in section
    assert "news: missing" in section
    assert "news_context_missing" in section
    assert "News result count: 3" in section
    assert "intraday_realtime_overlay" in section
    assert "confidence_level must not be High" in section
    assert "Phase/data rule" not in section


def test_english_summary_renders_readable_statuses() -> None:
    section = format_analysis_context_pack_prompt_section(
        _pack(),
        report_language="en",
    )

    assert "Analysis Context Pack Summary" in section
    assert "Subject: HPG (Hoa Phat Group); market=vn" in section
    assert "quote: fallback" in section
    assert "news: missing" in section
    assert "News result count: 3" in section
    assert "Data Limitations" in section
    assert "Data quality score: 76/100 (usable)" in section
    assert "Known limitations: quote: fallback, technical: partial" in section
    assert "confidence_level must not be High" in section
    assert "Phase/data rule" not in section


def test_intraday_phase_data_quality_guard_renders_in_english() -> None:
    section = format_analysis_context_pack_prompt_section(
        _pack_with_phase("intraday"),
        report_language="en",
    )

    assert "Phase/data rule" in section
    assert "intraday judgment is limited" in section
    assert "data quality" in section
    assert "confidence_level must not be High" in section
    assert "This is not a post-market recap" not in section


def test_lunch_break_and_closing_auction_degraded_core_add_data_guard_only() -> None:
    for phase in ("lunch_break", "closing_auction"):
        section = format_analysis_context_pack_prompt_section(_pack_with_phase(phase))

        assert "Phase/data rule" in section
        assert "intraday judgment is limited" in section


def test_premarket_degraded_quote_limits_opening_plan_without_phase_repetition() -> None:
    section = format_analysis_context_pack_prompt_section(_pack_with_phase("premarket"))

    assert "the opening plan is limited by data freshness" in section
    assert "do not describe degraded quote data as" in section


def test_non_trading_and_unknown_degraded_core_are_conservative() -> None:
    for phase in ("non_trading", "unknown"):
        section = format_analysis_context_pack_prompt_section(_pack_with_phase(phase))

        assert "use only available data conservatively" in section
        assert "not fill in nonexistent intraday facts" in section


def test_phase_cross_guard_is_skipped_for_postmarket_invalid_or_non_dict_phase() -> None:
    postmarket_degraded = format_analysis_context_pack_prompt_section(
        _pack_with_phase("postmarket")
    )
    postmarket_available = format_analysis_context_pack_prompt_section(
        _core_available_pack(phase="postmarket")
    )
    invalid = format_analysis_context_pack_prompt_section(
        _pack().model_copy(update={"phase": {"phase": "not_a_phase"}})
    )
    non_dict = _pack().model_dump(mode="json")
    non_dict["phase"] = "intraday"
    non_dict_section = format_analysis_context_pack_prompt_section(non_dict)

    assert "Phase/data rule" not in postmarket_degraded
    assert "confidence_level must not be High" in postmarket_degraded
    assert "Phase/data rule" not in postmarket_available
    assert "Phase/data rule" not in invalid
    assert "confidence_level must not be High" in invalid
    assert "Phase/data rule" not in non_dict_section
    assert "confidence_level must not be High" in non_dict_section


def test_summary_does_not_dump_values_or_sensitive_payloads() -> None:
    section = format_analysis_context_pack_prompt_section(_pack())

    assert "analysis_context_pack" not in section
    assert "Full news body must not enter the summary" not in section
    assert "bullish_alignment_raw" not in section
    assert "secret-token" not in section
    assert "hooks.example.test" not in section
    assert "webhook_url" not in section
    assert "access_token" not in section
    assert "N/A" not in section
    assert "None" not in section


def test_builder_to_prompt_renders_aux_fetch_failed_without_confidence_cap() -> None:
    pack = AnalysisContextBuilder.build(
        _builder_artifacts(
            fundamental_context={
                "status": "failed",
                "coverage": {"valuation": "failed"},
                "source_chain": [
                    {"provider": "fundamental_pipeline", "result": "failed"}
                ],
            }
        )
    )

    section = format_analysis_context_pack_prompt_section(pack)

    assert pack.data_quality.limitations == ["fundamentals: fetch_failed"]
    assert "Data Limitations" in section
    assert "Data quality score: 89/100 (good)" in section
    assert "Known limitations: fundamentals: fetch failed" in section
    assert "Confidence rule" not in section
    assert "confidence_level" not in section
