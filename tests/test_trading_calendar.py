# -*- coding: utf-8 -*-
"""
Kiểm thử lịch giao dịch Việt Nam (giờ cố định, không dùng exchange-calendars).

Tests for the Vietnam trading calendar (fixed session hours).
"""

import json
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.core import trading_calendar

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _vn(day: int, hour: int, minute: int, month: int = 9) -> datetime:
    # 2026-09-29 là thứ Ba, 2026-09-27 là Chủ nhật. / 2026-09-29 is a Tuesday.
    return datetime(2026, month, day, hour, minute, tzinfo=VN_TZ)


class MarketLookupTestCase(unittest.TestCase):
    def test_get_market_for_stock_vn_and_unknown(self):
        self.assertEqual(trading_calendar.get_market_for_stock("HPG"), "vn")
        self.assertEqual(trading_calendar.get_market_for_stock("VNINDEX"), "vn")
        self.assertIsNone(trading_calendar.get_market_for_stock("AAPL"))
        self.assertIsNone(trading_calendar.get_market_for_stock(""))

    def test_is_market_open_excludes_weekends_only(self):
        self.assertTrue(trading_calendar.is_market_open("vn", date(2026, 9, 29)))
        self.assertFalse(trading_calendar.is_market_open("vn", date(2026, 9, 27)))
        # Thị trường không rõ: fail-open. / Unknown market: fail-open.
        self.assertTrue(trading_calendar.is_market_open("xx", date(2026, 9, 27)))

    def test_vn_has_no_exchange_calendar_mapping(self):
        self.assertNotIn("vn", trading_calendar.MARKET_EXCHANGE)
        self.assertEqual(trading_calendar.MARKET_TIMEZONE["vn"], "Asia/Ho_Chi_Minh")


class EffectiveTradingDateTestCase(unittest.TestCase):
    def test_returns_market_local_natural_date(self):
        self.assertEqual(
            trading_calendar.get_effective_trading_date("vn", _vn(29, 10, 0)),
            date(2026, 9, 29),
        )

    def test_market_timezone_controls_cross_timezone_resolution(self):
        # 2026-09-28 18:00 UTC = 2026-09-29 01:00 giờ VN.
        utc_time = datetime(2026, 9, 28, 18, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(trading_calendar.get_effective_trading_date("vn", utc_time), date(2026, 9, 29))

    def test_naive_time_is_treated_as_market_local(self):
        now = trading_calendar.get_market_now("vn", datetime(2026, 9, 29, 10, 0))
        self.assertEqual(now.utcoffset().total_seconds(), 7 * 3600)
        self.assertEqual(now.hour, 10)


class HistoricalDailyBarDateTestCase(unittest.TestCase):
    def test_vn_weekday_calendar_resolves_completed_bar(self):
        resolve = trading_calendar.resolve_historical_daily_bar_date
        # 2026-09-29 là thứ Ba, 2026-09-28 là thứ Hai, 2026-09-27 là Chủ nhật.
        self.assertEqual(resolve("vn", date(2026, 9, 29), "postmarket"), date(2026, 9, 29))
        self.assertEqual(resolve("vn", date(2026, 9, 29), "intraday"), date(2026, 9, 28))
        self.assertEqual(resolve("vn", date(2026, 9, 28), "premarket"), date(2026, 9, 25))
        self.assertEqual(resolve("vn", date(2026, 9, 27), "non_trading"), date(2026, 9, 25))

    def test_vn_inconsistent_or_unknown_phase_fails_closed(self):
        resolve = trading_calendar.resolve_historical_daily_bar_date
        self.assertIsNone(resolve("vn", date(2026, 9, 27), "postmarket"))
        self.assertIsNone(resolve("vn", date(2026, 9, 29), "non_trading"))
        self.assertIsNone(resolve("vn", date(2026, 9, 29), None))
        self.assertIsNone(resolve("unknown", date(2026, 9, 29), "postmarket"))


class InferMarketPhaseTestCase(unittest.TestCase):
    def test_vn_phase_boundaries(self):
        cases = [
            ((29, 8, 59), "premarket"),
            ((29, 9, 0), "intraday"),
            ((29, 11, 29), "intraday"),
            ((29, 11, 30), "lunch_break"),
            ((29, 12, 59), "lunch_break"),
            ((29, 13, 0), "intraday"),
            ((29, 14, 29), "intraday"),
            ((29, 14, 30), "closing_auction"),
            ((29, 14, 44), "closing_auction"),
            ((29, 14, 45), "postmarket"),
            ((27, 10, 0), "non_trading"),
        ]
        for args, expected in cases:
            with self.subTest(at=args):
                self.assertEqual(trading_calendar.infer_market_phase("vn", _vn(*args)).value, expected)

    def test_unknown_market_returns_unknown(self):
        self.assertEqual(
            trading_calendar.infer_market_phase(None, _vn(29, 10, 0)),
            trading_calendar.MarketPhase.UNKNOWN,
        )
        self.assertEqual(
            trading_calendar.infer_market_phase("cn", _vn(29, 10, 0)),
            trading_calendar.MarketPhase.UNKNOWN,
        )


class MarketPhaseContextTestCase(unittest.TestCase):
    def test_context_to_dict_is_json_safe_for_intraday(self):
        ctx = trading_calendar.build_market_phase_context(
            market="vn", current_time=_vn(29, 10, 0), trigger_source="web"
        )
        payload = json.loads(json.dumps(ctx.to_dict(), ensure_ascii=False))

        self.assertEqual(payload["market"], "vn")
        self.assertEqual(payload["phase"], "intraday")
        self.assertEqual(payload["market_local_time"], "2026-09-29T10:00:00+07:00")
        self.assertEqual(payload["session_date"], "2026-09-29")
        self.assertEqual(payload["effective_daily_bar_date"], "2026-09-29")
        self.assertTrue(payload["is_trading_day"])
        self.assertTrue(payload["is_market_open_now"])
        self.assertTrue(payload["is_partial_bar"])
        self.assertIsNone(payload["minutes_to_open"])
        self.assertEqual(payload["minutes_to_close"], 285)
        self.assertEqual(payload["trigger_source"], "web")
        self.assertEqual(payload["analysis_intent"], "auto")
        self.assertEqual(payload["warnings"], [])

    def test_context_derived_flags_for_regular_session_phases(self):
        cases = [
            # (thời điểm, phase, trading_day, open_now, partial, to_open, to_close)
            ((29, 8, 30), "premarket", True, False, False, 30, None),
            ((29, 12, 0), "lunch_break", True, False, True, None, 165),
            ((29, 14, 35), "closing_auction", True, True, True, None, 10),
            ((29, 15, 0), "postmarket", True, False, False, None, None),
            ((27, 10, 0), "non_trading", False, False, False, None, None),
        ]
        for args, phase, trading_day, open_now, partial, to_open, to_close in cases:
            with self.subTest(phase=phase):
                payload = trading_calendar.build_market_phase_context(
                    market="vn", current_time=_vn(*args)
                ).to_dict()
                self.assertEqual(payload["phase"], phase)
                self.assertEqual(payload["is_trading_day"], trading_day)
                self.assertEqual(payload["is_market_open_now"], open_now)
                self.assertEqual(payload["is_partial_bar"], partial)
                self.assertEqual(payload["minutes_to_open"], to_open)
                self.assertEqual(payload["minutes_to_close"], to_close)
                self.assertNotIn("unknown_market", payload["warnings"])

    def test_manual_analysis_phase_overrides_non_trading_day_without_rewriting_calendar_fields(self):
        payload = trading_calendar.build_market_phase_context(
            market="vn",
            current_time=_vn(27, 10, 0),
            trigger_source="api",
            analysis_phase="intraday",
        ).to_dict()

        self.assertEqual(payload["phase"], "intraday")
        self.assertEqual(payload["analysis_intent"], "intraday")
        self.assertEqual(payload["market_local_time"], "2026-09-27T10:00:00+07:00")
        self.assertEqual(payload["effective_daily_bar_date"], "2026-09-27")
        self.assertTrue(payload["is_market_open_now"])
        self.assertIsNone(payload["minutes_to_close"])

    def test_legacy_analysis_intent_alias_can_override_phase(self):
        ctx = trading_calendar.build_market_phase_context(
            market="vn", current_time=_vn(29, 10, 0), analysis_intent="postmarket"
        )
        self.assertEqual(ctx.phase, trading_calendar.MarketPhase.POSTMARKET)
        self.assertEqual(ctx.analysis_intent, "postmarket")

    def test_invalid_manual_analysis_phase_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "invalid analysis_phase"):
            trading_calendar.build_market_phase_context(
                market="vn", current_time=_vn(29, 10, 0), analysis_phase="lunch_break"
            )

    def test_unknown_market_uses_null_tristate_flags_and_warning_code(self):
        payload = trading_calendar.build_market_phase_context(
            market=None, current_time=datetime(2026, 9, 29, 10, 0)
        ).to_dict()

        self.assertEqual(payload["phase"], "unknown")
        self.assertIn("unknown_market", payload["warnings"])
        self.assertIsNone(payload["is_trading_day"])
        self.assertIsNone(payload["is_market_open_now"])
        self.assertIsNone(payload["is_partial_bar"])


class ComputeEffectiveRegionTestCase(unittest.TestCase):
    def test_get_open_markets_today_is_vn_on_weekdays_only(self):
        result = trading_calendar.get_open_markets_today()
        self.assertTrue(result <= {"vn"})

    def test_vn_open_returns_vn(self):
        self.assertEqual(trading_calendar.compute_effective_region("vn", {"vn"}), "vn")

    def test_legacy_region_value_still_resolves_to_vn(self):
        # Giá trị cũ (cn/both...) được quy về vn. / Legacy values fold into vn.
        self.assertEqual(trading_calendar.compute_effective_region("both", {"vn"}), "vn")
        self.assertEqual(trading_calendar.compute_effective_region("cn", {"vn"}), "vn")

    def test_vn_closed_returns_empty(self):
        self.assertEqual(trading_calendar.compute_effective_region("vn", set()), "")


class VietnamMatchingSessionTestCase(unittest.TestCase):
    def test_matching_session_grace_covers_atc_match_tick(self):
        self.assertTrue(trading_calendar.is_vn_matching_session(_vn(29, 10, 0)))
        self.assertFalse(trading_calendar.is_vn_matching_session(_vn(29, 12, 0)))
        at = _vn(29, 14, 46)
        self.assertFalse(trading_calendar.is_vn_matching_session(at))
        self.assertTrue(trading_calendar.is_vn_matching_session(at, grace_minutes=2))
        self.assertFalse(trading_calendar.is_vn_matching_session(_vn(29, 14, 47), grace_minutes=2))

    def test_weekend_is_never_matching_session(self):
        self.assertFalse(trading_calendar.is_vn_matching_session(_vn(27, 10, 0)))


if __name__ == "__main__":
    unittest.main()
