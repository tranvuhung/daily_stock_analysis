# -*- coding: utf-8 -*-
"""Tests for Market Light alert evaluation."""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.market_light_alerts import (
    MarketLightAlert,
    evaluate_market_light_alert,
    make_market_light_payload,
    normalize_market_alert_parameters,
)
from src.services.market_light_service import (
    enabled_market_alert_regions,
    ensure_market_alert_region_enabled,
)


def _snapshot(
    *,
    trade_date: str = "2026-03-07",
    score: int = 45,
    status: str = "yellow",
    data_quality: str = "ok",
    breadth_available: bool = True,
    index_available: bool = True,
    limit_available: bool = True,
) -> dict:
    return {
        "region": "vn",
        "trade_date": trade_date,
        "status": status,
        "score": score,
        "label": "balanced",
        "temperature_label": "mixed",
        "reasons": ["test"],
        "guidance": "test",
        "dimensions": {
            "breadth": {"score": 50, "available": breadth_available},
            "index": {"score": 50, "available": index_available},
            "limit": {"score": 50, "available": limit_available},
        },
        "data_quality": data_quality,
    }


class MarketLightAlertsTestCase(unittest.TestCase):
    def _rule(self, alert_type: str, parameters: dict) -> MarketLightAlert:
        return MarketLightAlert(
            target_scope="market",
            target="vn",
            alert_type=alert_type,
            parameters=parameters,
            metadata={"persisted_rule_id": 7, "trading_day_check_enabled": False},
        )

    def test_normalize_status_rejects_green(self) -> None:
        with self.assertRaisesRegex(ValueError, "red or yellow"):
            normalize_market_alert_parameters("market_light_status", {"statuses": ["green"]})

    def test_market_light_alert_rejects_non_vn_targets(self) -> None:
        for region in ("cn", "hk", "us", "jp", "kr"):
            with self.subTest(region=region):
                with self.assertRaisesRegex(ValueError, "must be one of vn"):
                    MarketLightAlert(
                        target_scope="market",
                        target=region,
                        alert_type="market_light_status",
                        parameters={"statuses": ["red"]},
                    )

    def test_market_light_alert_accepts_vn_target(self) -> None:
        rule = MarketLightAlert(
            target_scope="market",
            target="VN",
            alert_type="market_light_status",
            parameters={"statuses": ["red"]},
        )
        self.assertEqual(rule.target, "vn")

    def test_enabled_regions_follow_supported_markets_vn_first(self) -> None:
        with patch.dict(os.environ, {"SUPPORTED_MARKETS": "vn"}):
            self.assertEqual(enabled_market_alert_regions(), ("vn",))
        # Chỉ còn thị trường Việt Nam, mọi cấu hình đều quy về vn.
        # Only Vietnam remains; every configuration resolves to vn.
        with patch.dict(os.environ, {"SUPPORTED_MARKETS": "us,vn,cn,jp"}):
            self.assertEqual(enabled_market_alert_regions(), ("vn",))
        with patch.dict(os.environ, {"SUPPORTED_MARKETS": "all"}):
            self.assertEqual(enabled_market_alert_regions(), ("vn",))

    def test_ensure_region_enabled_rejects_disabled_market(self) -> None:
        with patch.dict(os.environ, {"SUPPORTED_MARKETS": "vn"}):
            self.assertEqual(ensure_market_alert_region_enabled("vn"), "vn")
            with self.assertRaisesRegex(ValueError, "must be one of vn"):
                ensure_market_alert_region_enabled("cn")

    def test_disabled_market_payload_is_skipped_with_explicit_reason(self) -> None:
        data = {
            "id": 9,
            "target_scope": "market",
            "target": "vn",
            "alert_type": "market_light_status",
            "parameters": {"statuses": ["red"]},
            "name": "vn risk-off",
        }
        config = SimpleNamespace(trading_day_check_enabled=True, report_language="vi")
        # vn luôn được bật; giả lập trạng thái tắt để kiểm tra nhánh bỏ qua.
        # vn is always enabled; simulate a disabled market to cover the skip path.
        with patch(
            "src.services.market_light_alerts.is_market_alert_region_enabled", return_value=False
        ), patch(
            "src.services.market_light_alerts.get_open_markets_today"
        ) as open_markets, patch("src.services.market_light_alerts.build_current_snapshot") as build_snapshot:
            payload = make_market_light_payload(parent_key="rule:9", data=data, config=config)
            result = evaluate_market_light_alert(payload.rule)

        open_markets.assert_not_called()
        build_snapshot.assert_not_called()
        self.assertEqual(result["record_status"], "skipped")
        self.assertIn("not enabled in SUPPORTED_MARKETS", result["message"])
        self.assertEqual(payload.display_target, "Việt Nam")

    def test_vn_payload_uses_trading_calendar_and_vietnamese_label(self) -> None:
        data = {
            "id": 10,
            "target_scope": "market",
            "target": "vn",
            "alert_type": "market_light_status",
            "parameters": {"statuses": ["red"]},
            "name": "",
        }
        config = SimpleNamespace(trading_day_check_enabled=True, report_language="vi")
        with patch.dict(os.environ, {"SUPPORTED_MARKETS": "vn"}), patch(
            "src.services.market_light_alerts.get_open_markets_today", return_value={"vn"}
        ):
            payload = make_market_light_payload(parent_key="rule:10", data=data, config=config)

        self.assertEqual(payload.display_target, "Việt Nam")
        self.assertTrue(payload.rule.metadata["market_enabled"])
        self.assertTrue(payload.rule.metadata["market_is_open"])

    def test_status_unavailable_is_skipped(self) -> None:
        result = evaluate_market_light_alert(
            self._rule("market_light_status", {"statuses": ["red", "yellow"]}),
            current_snapshot=_snapshot(data_quality="unavailable", index_available=False),
        )

        self.assertFalse(result["triggered"])
        self.assertEqual(result["record_status"], "skipped")
        self.assertEqual(result["data_source"], "market_light")

    def test_status_partial_triggers_with_missing_dimensions_diagnostics(self) -> None:
        result = evaluate_market_light_alert(
            self._rule("market_light_status", {"statuses": ["yellow"]}),
            current_snapshot=_snapshot(data_quality="partial", breadth_available=False, limit_available=False),
        )

        self.assertTrue(result["triggered"])
        diagnostics = json.loads(result["diagnostics"])
        self.assertEqual(diagnostics["data_quality"], "partial")
        self.assertEqual(diagnostics["missing_dimensions"], ["breadth", "limit"])
        self.assertEqual(result["data_timestamp"].isoformat(), "2026-03-07T00:00:00")

    def test_score_drop_uses_previous_trade_date_and_allows_partial_comparison(self) -> None:
        previous = _snapshot(
            trade_date="2026-03-06",
            score=75,
            status="green",
            data_quality="partial",
            limit_available=False,
        )
        current = _snapshot(trade_date="2026-03-07", score=55, data_quality="partial", breadth_available=False)

        with patch("src.services.market_light_alerts.load_previous_snapshot", return_value=previous):
            result = evaluate_market_light_alert(
                self._rule("market_light_score_drop", {"min_drop": 10.0}),
                current_snapshot=current,
            )

        self.assertTrue(result["triggered"])
        self.assertEqual(result["observed_value"], 55.0)
        self.assertEqual(result["threshold"], 10.0)
        diagnostics = json.loads(result["diagnostics"])
        self.assertTrue(diagnostics["partial_comparison"])
        self.assertEqual(diagnostics["prev_score"], 75)
        self.assertEqual(diagnostics["prev_trade_date"], "2026-03-06")
        self.assertEqual(diagnostics["missing_dimensions"], ["breadth", "limit"])

    def test_score_drop_without_previous_snapshot_is_skipped(self) -> None:
        with patch("src.services.market_light_alerts.load_previous_snapshot", return_value=None):
            result = evaluate_market_light_alert(
                self._rule("market_light_score_drop", {"min_drop": 10.0}),
                current_snapshot=_snapshot(),
            )

        self.assertFalse(result["triggered"])
        self.assertEqual(result["record_status"], "skipped")

    def test_score_drop_previous_snapshot_parse_error_is_degraded(self) -> None:
        with patch(
            "src.services.market_light_alerts.load_previous_snapshot",
            side_effect=ValueError("invalid persisted market light snapshot"),
        ):
            result = evaluate_market_light_alert(
                self._rule("market_light_score_drop", {"min_drop": 10.0}),
                current_snapshot=_snapshot(),
            )

        self.assertFalse(result["triggered"])
        self.assertEqual(result["record_status"], "degraded")
        diagnostics = json.loads(result["diagnostics"])
        self.assertIn("invalid persisted market light snapshot", diagnostics["error"])


if __name__ == "__main__":
    unittest.main()
