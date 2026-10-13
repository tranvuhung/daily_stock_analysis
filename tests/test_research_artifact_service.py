# -*- coding: utf-8 -*-
"""Tests for structured ResearchArtifact contract helpers."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from api.v1.schemas.research_artifact import ResearchArtifact
from src.services.research_artifact_service import build_research_artifact


def test_build_research_artifact_from_report_with_evidence_and_invalidation() -> None:
    report = {
        "meta": {
            "id": 12,
            "query_id": "q-12",
            "stock_code": "HPG",
            "stock_name": "Hòa Phát",
            "created_at": "2026-03-19T08:00:00",
        },
        "summary": {
            "analysis_summary": "Xu hướng duy trì nghiêng tăng",
            "operation_advice": "Nắm giữ",
            "action": "hold",
            "action_label": "Nắm giữ",
            "trend_prediction": "Dao động đi lên",
            "sentiment_score": 72,
        },
        "strategy": {
            "stop_loss": "1680",
            "take_profit": "1880",
        },
        "details": {
            "analysis_context_pack_overview": {
                "subject": {"market": "vn"},
                "blocks": [
                    {
                        "key": "daily_price",
                        "label": "Dữ liệu ngày",
                        "status": "available",
                        "source": "tencent",
                        "warnings": [],
                        "missing_reasons": [],
                    },
                    {
                        "key": "news",
                        "label": "Tin tức",
                        "status": "partial",
                        "source": "anspire",
                        "warnings": ["partial"],
                        "missing_reasons": [],
                    },
                ],
                "data_quality": {
                    "overall_score": 83,
                    "level": "good",
                    "limitations": ["Độ phủ tin tức hạn chế"],
                },
            },
            "news_content": "Tóm tắt tin doanh nghiệp",
        },
    }

    artifact = ResearchArtifact.model_validate(build_research_artifact(report))

    assert artifact.schema_version == "research-artifact-v1"
    assert artifact.artifact_id == "report:12"
    assert artifact.subject.stock_code == "HPG"
    assert artifact.subject.market == "vn"
    assert artifact.thesis.direction == "neutral"
    assert artifact.thesis.action == "hold"
    assert artifact.data_quality.level == "good"
    assert artifact.data_quality.source_count == 3
    assert {item.id for item in artifact.evidence} == {
        "context:daily_price",
        "context:news",
        "news:summary",
    }
    assert artifact.evidence[0].freshness == "fresh"
    assert artifact.evidence[0].quality_level == "good"
    condition_ids = {item.id for item in artifact.invalidation_conditions}
    assert "price:stop_loss" in condition_ids
    assert "data_quality:limitations" in condition_ids
    assert artifact.next_actions[-1].action == "monitor_invalidation"


def test_build_research_artifact_always_includes_invalidation_conditions() -> None:
    artifact = ResearchArtifact.model_validate(build_research_artifact({
        "meta": {"query_id": "q-empty", "stock_code": "AAPL"},
        "summary": {"analysis_summary": "Chờ thêm bằng chứng", "sentiment_score": 50},
    }))

    assert artifact.artifact_id == "report:AAPL:q-empty"
    assert artifact.invalidation_conditions[0].id == "manual:thesis_reassessment"
    assert artifact.data_quality.level == "unknown"


def test_research_artifact_requires_invalidation_conditions() -> None:
    with pytest.raises(ValidationError):
        ResearchArtifact.model_validate({
            "artifact_id": "report:bad",
            "subject": {"stock_code": "AAPL"},
            "thesis": {"summary": "missing invalidation"},
            "invalidation_conditions": [],
        })


def test_fallback_artifact_id_is_unique_for_stocks_in_the_same_batch() -> None:
    first = build_research_artifact({
        "meta": {"query_id": "batch-1", "stock_code": "HPG"},
        "summary": {"analysis_summary": "first"},
    })
    second = build_research_artifact({
        "meta": {"query_id": "batch-1", "stock_code": "000001"},
        "summary": {"analysis_summary": "second"},
    })

    assert first["artifact_id"] == "report:HPG:batch-1"
    assert second["artifact_id"] == "report:000001:batch-1"
    assert first["artifact_id"] != second["artifact_id"]


def test_attribute_report_preserves_falsey_values() -> None:
    report = SimpleNamespace(
        meta=SimpleNamespace(query_id="batch-zero", stock_code="AAPL"),
        summary=SimpleNamespace(
            sentiment_score=0,
            analysis_summary="zero is a real score",
            action="",
        ),
        strategy=SimpleNamespace(),
        details=SimpleNamespace(),
    )

    artifact = ResearchArtifact.model_validate(build_research_artifact(report))

    assert artifact.artifact_id == "report:AAPL:batch-zero"
    assert artifact.thesis.score == 0
    assert artifact.thesis.confidence == 1.0
    assert artifact.thesis.direction == "bearish"
    assert artifact.thesis.action is None


def test_unavailable_context_blocks_do_not_inflate_source_count() -> None:
    artifact = ResearchArtifact.model_validate(build_research_artifact({
        "meta": {"query_id": "missing-only", "stock_code": "AAPL"},
        "summary": {"analysis_summary": "waiting for evidence"},
        "details": {
            "analysis_context_pack_overview": {
                "blocks": [
                    {"key": "daily_price", "status": "missing"},
                    {"key": "news", "status": "fetch_failed"},
                ],
            },
            "empty_news_disclosure": "News evidence is unavailable.",
        },
    }))

    assert artifact.data_quality.source_count == 0
    assert {item.id for item in artifact.evidence} == {
        "context:daily_price",
        "context:news",
        "news:summary",
    }

