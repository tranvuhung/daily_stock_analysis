# -*- coding: utf-8 -*-
"""Contract tests for the stock profile aggregate endpoint."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.services.stock_profile_service import InvalidStockProfileCode, StockProfileService
from src.storage import DatabaseManager


def test_stock_profile_service_import_is_independent_of_api_bootstrap() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.services.stock_profile_service import StockProfileService; "
            "assert StockProfileService.__name__ == 'StockProfileService'",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _quote(code: str = "FPT") -> dict:
    return {
        "stock_code": code,
        "stock_name": "FPT",
        "current_price": 120000.0,
        "change": 1000.0,
        "change_percent": 0.84,
        "update_time": "2026-08-29T10:00:00+07:00",
    }


def _history() -> dict:
    return {
        "data": [
            {
                "date": "2026-08-28",
                "open": 118000.0,
                "high": 121000.0,
                "low": 117500.0,
                "close": 120000.0,
                "volume": 1000.0,
            }
        ]
    }


def _report_list() -> dict:
    return {
        "items": [
            {
                "id": 12,
                "query_id": "query-12",
                "stock_code": "FPT",
                "stock_name": "FPT",
                "analysis_summary": "Nhu cầu vẫn vững",
                "sentiment_score": 70,
                "created_at": "2026-08-28T12:00:00+07:00",
            }
        ],
        "total": 1,
    }


def _report_detail() -> dict:
    return {
        "id": 12,
        "query_id": "query-12",
        "stock_code": "FPT",
        "stock_name": "FPT",
        "analysis_summary": "Nhu cầu vẫn vững",
        "operation_advice": "Quan sát",
        "action": "watch",
        "action_label": "Quan sát",
        "trend_prediction": "Đi ngang",
        "sentiment_score": 70,
        "stop_loss": "110000",
        "created_at": "2026-08-28T12:00:00+07:00",
        "context_snapshot": None,
    }


def _intelligence(code: str = "FPT", market: str = "vn") -> dict:
    return {
        "items": [
            {
                "id": 5,
                "source_type": "rss",
                "title": "Company update",
                "url": "https://example.com/update",
                "scope_type": "symbol",
                "scope_value": code,
                "market": market,
            }
        ],
        "total": 1,
    }


def _service(**overrides: object) -> tuple[StockProfileService, dict[str, MagicMock]]:
    dependencies = {
        "stock_service": MagicMock(),
        "history_service": MagicMock(),
        "intelligence_service": MagicMock(),
        "portfolio_repository": MagicMock(),
        "alert_service": MagicMock(),
    }
    dependencies.update(overrides)
    dependencies["stock_service"].get_realtime_quote.return_value = _quote()
    dependencies["stock_service"].get_history_data.return_value = _history()
    dependencies["history_service"].get_history_list.return_value = _report_list()
    dependencies["history_service"].get_history_detail_by_id.return_value = _report_detail()
    dependencies["history_service"].get_latest_fundamental_snapshot.return_value = None
    dependencies["intelligence_service"].list_items.return_value = _intelligence()
    dependencies["portfolio_repository"].list_cached_position_identities.return_value = [("vn", "fpt")]
    dependencies["alert_service"].list_rules.return_value = {
        "items": [{"id": 8, "enabled": True}, {"id": 9, "enabled": False}],
        "total": 2,
    }
    return StockProfileService(**dependencies), dependencies


def test_profile_uses_one_canonical_code_and_returns_structured_research() -> None:
    service, dependencies = _service()

    payload = service.get_profile("fpt", history_days=45)

    assert payload["canonical_code"] == "FPT"
    assert payload["market"] == "vn"
    assert payload["quote"]["status"] == "fresh"
    assert payload["history"]["status"] == "fresh"
    assert payload["research"]["status"] == "fresh"
    assert payload["research"]["data"]["structured_report"]["artifact_id"] == "report:12"
    assert payload["portfolio"]["data"] == {"held": True, "matched_markets": ["vn"]}
    assert payload["monitors"]["data"] == {
        "total_rule_count": 2,
        "enabled_rule_count": 1,
        "rule_ids": [8, 9],
    }
    assert payload["evidence_quality"]["status"] == "partial"
    dependencies["stock_service"].get_realtime_quote.assert_called_once_with("FPT")
    dependencies["stock_service"].get_history_data.assert_called_once_with(
        "FPT", period="daily", days=45
    )
    dependencies["history_service"].get_history_list.assert_called_once_with(
        stock_code="FPT", page=1, limit=5, market_hint="vn"
    )
    assert {call.kwargs["scope_value"] for call in dependencies["intelligence_service"].list_items.call_args_list} == {
        "FPT",
        "FPT.VN",
    }
    assert {call.kwargs["target"] for call in dependencies["alert_service"].list_rules.call_args_list} == {
        "FPT",
        "FPT.VN",
    }
    assert dependencies["intelligence_service"].list_items.call_count == 4
    assert dependencies["alert_service"].list_rules.call_count == 2


def test_profile_alias_queries_are_unique_by_casefolded_identity() -> None:
    aliases = StockProfileService._code_aliases("hpg", market_hint="vn")

    assert len(aliases) == len({alias.casefold() for alias in aliases})
    assert set(aliases) == {"HPG", "HPG.VN"}


@pytest.mark.parametrize("code", ["600519", "AAPL", "00700.HK", "7203.T"])
def test_profile_rejects_non_vietnam_codes_before_downstream_queries(code: str) -> None:
    service, dependencies = _service()

    with pytest.raises(InvalidStockProfileCode):
        service.get_profile(code)

    dependencies["stock_service"].get_realtime_quote.assert_not_called()
    dependencies["history_service"].get_history_list.assert_not_called()


def test_profile_research_preserves_specialized_report_evidence() -> None:
    service, dependencies = _service()
    detail = _report_detail()
    detail["context_snapshot"] = {
        "fundamental_context": {
            "earnings": {
                "data": {
                    "financial_report": {"report_date": "2026-06-30"},
                    "dividend": {"ttm_cash_dividend_per_share": 1200},
                }
            }
        }
    }
    dependencies["history_service"].get_history_detail_by_id.return_value = detail

    payload = service.get_profile("FPT")

    artifact = payload["research"]["data"]["structured_report"]
    evidence_ids = {item["id"] for item in artifact["evidence"]}
    assert {
        "fundamental:financial_report",
        "fundamental:dividend_metrics",
    } <= evidence_ids
    assert artifact["data_quality"]["source_count"] == len(artifact["evidence"])


def test_profile_research_reads_independent_fundamental_snapshot() -> None:
    service, dependencies = _service()
    detail = _report_detail()
    detail["storage_stock_code"] = "FPT.VN"
    dependencies["history_service"].get_history_detail_by_id.return_value = detail
    dependencies["history_service"].get_latest_fundamental_snapshot.return_value = {
        "earnings": {
            "data": {
                "financial_report": {"report_date": "2026-06-30"},
                "dividend": {"ttm_cash_dividend_per_share": 1200},
            }
        }
    }

    payload = service.get_profile("FPT")

    artifact = payload["research"]["data"]["structured_report"]
    evidence_ids = {item["id"] for item in artifact["evidence"]}
    assert {
        "fundamental:financial_report",
        "fundamental:dividend_metrics",
    } <= evidence_ids
    dependencies["history_service"].get_latest_fundamental_snapshot.assert_called_once_with(
        query_id="query-12",
        stock_code="FPT.VN",
    )


def test_vn_suffix_is_canonicalized_before_every_downstream_query() -> None:
    service, dependencies = _service()
    dependencies["stock_service"].get_realtime_quote.return_value = _quote("HPG")
    dependencies["history_service"].get_history_list.return_value = {"items": [], "total": 0}
    dependencies["intelligence_service"].list_items.return_value = _intelligence("HPG.VN")

    payload = service.get_profile("HPG.VN")

    assert payload["canonical_code"] == "HPG"
    assert payload["market"] == "vn"
    dependencies["stock_service"].get_realtime_quote.assert_called_once_with("HPG")
    dependencies["history_service"].get_history_list.assert_called_once_with(
        stock_code="HPG", page=1, limit=5, market_hint="vn"
    )
    assert "HPG.VN" in {
        call.kwargs["scope_value"]
        for call in dependencies["intelligence_service"].list_items.call_args_list
    }
    assert "HPG.VN" in {
        call.kwargs["target"] for call in dependencies["alert_service"].list_rules.call_args_list
    }


def test_fund_and_index_codes_keep_vn_identity() -> None:
    fund_service, _ = _service()
    index_service, index_dependencies = _service()

    fund_payload = fund_service.get_profile("FUEVN100")
    index_payload = index_service.get_profile("vnindex")

    assert fund_payload["canonical_code"] == "FUEVN100"
    assert fund_payload["market"] == "vn"
    assert index_payload["canonical_code"] == "VNINDEX"
    assert {call.kwargs["market"] for call in index_dependencies["intelligence_service"].list_items.call_args_list} == {
        "vn",
        "global",
    }


def test_portfolio_identity_requires_same_market_and_matching_code() -> None:
    held_service, held_dependencies = _service()
    held_dependencies["portfolio_repository"].list_cached_position_identities.return_value = [
        ("vn", "HPG.VN"),
    ]
    legacy_service, legacy_dependencies = _service()
    # Vị thế cũ của thị trường đã loại bỏ không được coi là đang nắm giữ.
    # Positions from removed markets must not count as held.
    legacy_dependencies["portfolio_repository"].list_cached_position_identities.return_value = [
        ("cn", "HPG"),
        ("vn", "VCB"),
    ]

    held_payload = held_service.get_profile("HPG")
    legacy_payload = legacy_service.get_profile("HPG")

    assert held_payload["portfolio"]["data"] == {"held": True, "matched_markets": ["vn"]}
    assert legacy_payload["portfolio"]["data"] == {"held": False, "matched_markets": []}


def test_profile_collects_intelligence_and_monitors_saved_under_legacy_aliases() -> None:
    service, dependencies = _service()

    def intelligence_by_alias(**kwargs: object) -> dict:
        if kwargs.get("scope_value") == "HPG.VN":
            return _intelligence("HPG.VN")
        return {"items": [], "total": 0}

    def rules_by_alias(**kwargs: object) -> dict:
        if kwargs.get("target") == "HPG.VN":
            return {"items": [{"id": 88, "enabled": True}], "total": 1}
        return {"items": [], "total": 0}

    dependencies["intelligence_service"].list_items.side_effect = intelligence_by_alias
    dependencies["alert_service"].list_rules.side_effect = rules_by_alias

    payload = service.get_profile("HPG")

    assert payload["intelligence"]["status"] == "fresh"
    assert payload["intelligence"]["items"][0]["scope_value"] == "HPG.VN"
    assert payload["monitors"]["data"] == {
        "total_rule_count": 1,
        "enabled_rule_count": 1,
        "rule_ids": [88],
    }


def test_profile_includes_global_symbol_intelligence() -> None:
    service, dependencies = _service()

    def intelligence_by_market(**kwargs: object) -> dict:
        if kwargs.get("scope_value") == "FPT" and kwargs.get("market") == "global":
            return _intelligence("FPT", "global")
        return {"items": [], "total": 0}

    dependencies["intelligence_service"].list_items.side_effect = intelligence_by_market

    payload = service.get_profile("FPT")

    assert payload["intelligence"]["status"] == "fresh"
    assert payload["intelligence"]["items"][0]["market"] == "global"


def test_empty_intelligence_keeps_partial_status_when_any_alias_query_fails() -> None:
    service, dependencies = _service()

    def partially_failing_query(**kwargs: object) -> dict:
        if kwargs.get("scope_value") == "FPT.VN" and kwargs.get("market") == "vn":
            raise RuntimeError("transient query failure")
        return {"items": [], "total": 0}

    dependencies["intelligence_service"].list_items.side_effect = partially_failing_query

    payload = service.get_profile("FPT")

    assert payload["intelligence"] == {
        "status": "partial",
        "items": [],
        "limitations": ["intelligence_alias_query_partial"],
    }


def test_optional_block_failures_remain_partial_and_do_not_hide_monitor_data() -> None:
    service, dependencies = _service()
    dependencies["stock_service"].get_realtime_quote.side_effect = RuntimeError("quote failed")
    dependencies["stock_service"].get_history_data.return_value = {"data": []}
    dependencies["history_service"].get_history_detail_by_id.return_value = None
    dependencies["intelligence_service"].list_items.side_effect = RuntimeError("intel failed")
    dependencies["portfolio_repository"].list_cached_position_identities.side_effect = RuntimeError("db failed")

    payload = service.get_profile("FPT")

    assert payload["quote"]["status"] == "unavailable"
    assert payload["history"]["status"] == "unavailable"
    assert payload["research"]["status"] == "partial"
    assert payload["research"]["data"]["recent_reports"][0]["id"] == 12
    assert payload["intelligence"]["status"] == "unavailable"
    assert payload["portfolio"]["status"] == "unavailable"
    assert payload["monitors"]["status"] == "fresh"
    assert payload["evidence_quality"]["status"] == "partial"
    assert "latest_report_detail_unavailable" in payload["evidence_quality"]["limitations"]


def test_all_dependency_failures_return_unavailable_profile_instead_of_raising() -> None:
    service, dependencies = _service()
    for dependency, method in (
        ("stock_service", "get_realtime_quote"),
        ("stock_service", "get_history_data"),
        ("history_service", "get_history_list"),
        ("intelligence_service", "list_items"),
        ("portfolio_repository", "list_cached_position_identities"),
        ("alert_service", "list_rules"),
    ):
        getattr(dependencies[dependency], method).side_effect = RuntimeError("offline")

    payload = service.get_profile("HPG")

    assert payload["market"] == "vn"
    assert payload["evidence_quality"]["status"] == "unavailable"
    assert set(payload["evidence_quality"]["blocks"].values()) == {"unavailable"}


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


def _endpoint_payload() -> dict:
    service, _ = _service()
    return service.get_profile("FPT")


def test_profile_endpoint_validates_code_and_exposes_contract() -> None:
    _reset_auth_globals()
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Dùng file .env cô lập để cấu hình cục bộ (ADMIN_AUTH_ENABLED=true) không rò vào test.
            # Isolated .env so a local ADMIN_AUTH_ENABLED=true config does not leak into the test.
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("ADMIN_AUTH_ENABLED=false\n", encoding="utf-8")
            old_env_file = os.environ.get("ENV_FILE")
            os.environ["ENV_FILE"] = str(env_path)
            os.environ["DATABASE_PATH"] = str(Path(temp_dir) / "profile.db")
            os.environ["ADMIN_AUTH_ENABLED"] = "false"
            Config.reset_instance()
            DatabaseManager.reset_instance()
            app = create_app(static_dir=Path(temp_dir) / "empty-static")
            client = TestClient(app)
            with patch(
                "api.v1.endpoints.stocks.StockProfileService.get_profile",
                return_value=_endpoint_payload(),
            ) as get_profile:
                response = client.get("/api/v1/stocks/FPT/profile", params={"history_days": 90})
                suffixed = client.get("/api/v1/stocks/HPG.VN/profile")
                fund = client.get("/api/v1/stocks/FUEVN100/profile")
            invalid = client.get("/api/v1/stocks/invalid-code/profile")
            foreign = [
                client.get(f"/api/v1/stocks/{code}/profile")
                for code in ("600519", "AAPL", "00700.HK")
            ]

            assert response.status_code == 200, response.text
            assert response.json()["canonical_code"] == "FPT"
            assert [item.args for item in get_profile.call_args_list] == [
                ("FPT",),
                ("HPG.VN",),
                ("FUEVN100",),
            ]
            assert [item.kwargs for item in get_profile.call_args_list] == [
                {"history_days": 90},
                {"history_days": 60},
                {"history_days": 60},
            ]
            assert invalid.status_code == 400
            assert [item.status_code for item in foreign] == [400, 400, 400]
            assert {item.json()["error"] for item in foreign} == {"invalid_stock_code"}
            assert suffixed.status_code == 200
            assert fund.status_code == 200
        finally:
            DatabaseManager.reset_instance()
            Config.reset_instance()
            os.environ.pop("DATABASE_PATH", None)
            os.environ.pop("ADMIN_AUTH_ENABLED", None)
            if old_env_file is None:
                os.environ.pop("ENV_FILE", None)
            else:
                os.environ["ENV_FILE"] = old_env_file
            _reset_auth_globals()


def test_static_openapi_matches_stock_profile_runtime_contract() -> None:
    static_spec = json.loads(
        (Path(__file__).resolve().parents[1] / "docs" / "architecture" / "api_spec.json").read_text(
            encoding="utf-8"
        )
    )
    runtime_spec = create_app().openapi()
    api_path = "/api/v1/stocks/{stock_code}/profile"
    assert static_spec["paths"][api_path] == runtime_spec["paths"][api_path]
    schema_names = [
        name for name in runtime_spec["components"]["schemas"] if name.startswith("StockProfile")
    ]
    assert schema_names
    for schema_name in schema_names:
        assert static_spec["components"]["schemas"][schema_name] == runtime_spec["components"]["schemas"][schema_name]
