# -*- coding: utf-8 -*-
"""Test cảnh báo realtime: parser TVSI, feed MQTT, rule giá, engine theo tick, event bus, API."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from data_provider.realtime_feed import (
    LatestTickStore,
    RealtimeTick,
    get_fresh_tick,
    parse_tvsi_stock_tick,
    set_active_feed,
    tick_to_quote,
    vn_bare_symbol,
)
from data_provider.tvsi_mqtt_feed import TvsiMqttFeed, TvsiMqttSettings
from src.config import Config
from src.core.trading_calendar import is_vn_matching_session
from src.notification import ChannelAttemptResult, NotificationDispatchResult
from src.services.alert_service import AlertService, AlertServiceError
from src.services.alert_worker import AlertWorker
from src.services.event_bus import EventBus
from src.services.realtime_alert_engine import RealtimeAlertEngine, set_active_realtime_engine
from src.storage import DatabaseManager

FIXTURES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "tvsi_mqtt" / "samples.json").read_text(encoding="utf-8")
)
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _tick(symbol="HPG", price=20000.0, sequence=1, ceiling=22000.0, floor=18000.0, change_pct=-1.0) -> RealtimeTick:
    return RealtimeTick(
        symbol=symbol,
        price=price,
        change_pct=change_pct,
        ref=20000.0,
        ceiling=ceiling,
        floor=floor,
        sequence=sequence,
        ts=datetime(2026, 9, 28, 10, 0, 0, tzinfo=VN_TZ),
        received_at=__import__("time").time(),
    )


class FakeMqttClient:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def tls_set(self, **kwargs) -> None:
        pass

    def username_pw_set(self, username, password) -> None:
        self.credentials = (username, password)

    def reconnect_delay_set(self, **kwargs) -> None:
        pass

    def connect_async(self, host, port, keepalive=30) -> None:
        self.target = (host, port)

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)
        return (0, 1)

    def unsubscribe(self, topic):
        self.unsubscribed.append(topic)
        return (0, 2)


class FakeFeed:
    """Feed giả cho engine: không kết nối mạng."""

    def __init__(self, connected: bool = True) -> None:
        self.store = LatestTickStore()
        self.connected = connected
        self.symbols: set[str] = set()
        self.listeners = []

    def is_connected(self) -> bool:
        return self.connected

    def is_subscribed(self, symbol: str) -> bool:
        return self.connected and symbol in self.symbols

    def set_symbols(self, symbols) -> None:
        self.symbols = set(symbols)

    def add_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_listener(self, listener) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    def status(self):
        return {"state": "connected" if self.connected else "reconnecting", "connected": self.connected}


class TvsiParserTestCase(unittest.TestCase):
    def test_parses_real_hose_and_hnx_payloads_in_vnd(self) -> None:
        for topic in ("topic/HPG", "topic/SHS"):
            raw = FIXTURES[topic]
            tick = parse_tvsi_stock_tick(json.dumps(raw))
            with self.subTest(topic=topic):
                self.assertIsNotNone(tick)
                self.assertEqual(tick.symbol, raw["StockSymbol"])
                self.assertAlmostEqual(tick.price, raw["MatchPrice"] * 1000)
                self.assertAlmostEqual(tick.ceiling, raw["CeilingPrice"] * 1000)
                self.assertAlmostEqual(tick.floor, raw["FloorPrice"] * 1000)
                self.assertAlmostEqual(tick.change_pct, raw["PriceChangePercent"])
                self.assertEqual(tick.sequence, int(raw["SequenceNumber"]))
                self.assertEqual(tick.ts.tzinfo, VN_TZ)
                self.assertEqual(tick.ts.strftime("%H%M%S"), raw["Time"])
        self.assertEqual(parse_tvsi_stock_tick(json.dumps(FIXTURES["topic/HPG"])).exchange, "hose")
        self.assertEqual(parse_tvsi_stock_tick(json.dumps(FIXTURES["topic/SHS"])).exchange, "hnx")

    def test_rejects_non_stock_or_unmatched_payloads(self) -> None:
        self.assertIsNone(parse_tvsi_stock_tick(b"ping"))
        self.assertIsNone(parse_tvsi_stock_tick(json.dumps(FIXTURES["market/^VNINDEX"])))
        no_match = dict(FIXTURES["topic/HPG"], MatchPrice=0)
        self.assertIsNone(parse_tvsi_stock_tick(json.dumps(no_match)))

    def test_store_drops_out_of_order_ticks(self) -> None:
        store = LatestTickStore()
        self.assertTrue(store.update(_tick(price=20000, sequence=5)))
        self.assertFalse(store.update(_tick(price=19000, sequence=4)))
        self.assertEqual(store.get("hpg").price, 20000)
        self.assertTrue(store.update(_tick(price=20100, sequence=6)))
        self.assertEqual(store.get("HPG").price, 20100)

    def test_tick_to_quote_carries_limits_and_timestamp(self) -> None:
        quote = tick_to_quote(_tick())
        self.assertEqual(quote.source.value, "tvsi_mqtt")
        self.assertEqual(quote.market, "vn")
        self.assertEqual(quote.vn_ceiling, 22000.0)
        self.assertEqual(quote.vn_floor, 18000.0)
        self.assertTrue(quote.provider_timestamp.startswith("2026-09-28T10:00:00"))
        self.assertEqual(vn_bare_symbol("hpg.vn"), "HPG")


class TvsiMqttFeedTestCase(unittest.TestCase):
    def _feed(self):
        client = FakeMqttClient()
        settings = TvsiMqttSettings(host="broker.example", username="user", password="secret")
        return TvsiMqttFeed(settings, client_factory=lambda: client), client

    def test_topic_template_rejects_wildcards(self) -> None:
        settings = TvsiMqttSettings(host="h", topic_template="#")
        with self.assertRaisesRegex(ValueError, "wildcard"):
            settings.topic_for("HPG")
        self.assertEqual(TvsiMqttSettings(host="h").symbol_from_topic("topic/HPG"), "HPG")
        self.assertIsNone(TvsiMqttSettings(host="h").symbol_from_topic("order/abc"))

    def test_subscribes_on_connect_and_diffs_symbol_set(self) -> None:
        feed, client = self._feed()
        feed.set_symbols(["HPG", "FPT"])
        feed.start()
        self.assertEqual(client.credentials, ("user", "secret"))
        self.assertEqual(client.subscribed, [])  # chưa kết nối thì chưa subscribe
        client.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
        self.assertEqual(sorted(client.subscribed), ["topic/FPT", "topic/HPG"])
        self.assertTrue(feed.is_subscribed("hpg"))

        feed.set_symbols(["HPG", "VNM"])
        self.assertIn("topic/VNM", client.subscribed)
        self.assertEqual(client.unsubscribed, ["topic/FPT"])
        self.assertFalse(any(topic in ("#", "order/#") for topic in client.subscribed))

    def test_message_updates_store_and_listeners_and_disconnect_resets_state(self) -> None:
        feed, client = self._feed()
        feed.set_symbols(["HPG"])
        feed.start()
        client.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
        received = []
        feed.add_listener(received.append)
        client.on_message(client, None, SimpleNamespace(topic="topic/HPG", payload=json.dumps(FIXTURES["topic/HPG"]).encode()))
        client.on_message(client, None, SimpleNamespace(topic="order/123", payload=b'{"accountNumber":"1"}'))
        self.assertEqual(len(received), 1)
        self.assertEqual(feed.store.get("HPG").symbol, "HPG")
        self.assertEqual(feed.status()["message_count"], 1)

        client.on_disconnect(client, None, None, "network", None)
        self.assertFalse(feed.is_connected())
        self.assertFalse(feed.is_subscribed("HPG"))
        self.assertEqual(feed.status()["state"], "reconnecting")

    def test_fresh_tick_requires_connected_feed_and_max_age(self) -> None:
        feed = FakeFeed()
        feed.store.update(_tick())
        set_active_feed(feed)
        try:
            self.assertIsNotNone(get_fresh_tick("HPG", 30))
            old = RealtimeTick(symbol="VNM", price=1.0, received_at=0.0)
            feed.store.update(old)
            self.assertIsNone(get_fresh_tick("VNM", 30))
            feed.connected = False
            self.assertIsNone(get_fresh_tick("HPG", 30))
        finally:
            set_active_feed(None)


class RealtimeQuoteIntegrationTestCase(unittest.TestCase):
    def test_vn_quote_prefers_fresh_feed_tick_over_ssi(self) -> None:
        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager()
        feed = FakeFeed()
        feed.store.update(_tick(price=20300))
        set_active_feed(feed)
        try:
            with patch.object(DataFetcherManager, "_vn_realtime_source_order", side_effect=AssertionError("SSI called")):
                quote = manager.get_realtime_quote("HPG.VN", log_final_failure=False)
            self.assertEqual(quote.price, 20300)
            self.assertEqual(quote.source.value, "tvsi_mqtt")
            self.assertEqual(quote.realtime_provider, "tvsi_mqtt")
            self.assertIsNotNone(quote.provider_timestamp)

            feed.connected = False  # feed mất kết nối → quay về chuỗi SSI
            with patch.object(DataFetcherManager, "_vn_realtime_source_order", return_value=[]) as order:
                self.assertIsNone(manager.get_realtime_quote("HPG.VN", log_final_failure=False))
            order.assert_called_once()
        finally:
            set_active_feed(None)


    def test_out_of_session_quote_records_fiintrade_as_actual_provider(self) -> None:
        """Không có tick (ngoài phiên): FiinTrade đứng đầu, quote ghi nguồn thật ``fiintrade``."""
        from data_provider.base import DataFetcherManager
        from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote

        manager = DataFetcherManager()
        fiin_quote = UnifiedRealtimeQuote(code="HPG", source=RealtimeSource.FALLBACK, price=26500.0)
        with patch.object(DataFetcherManager, "_vn_realtime_source_order", return_value=["FiinTradeFetcher", "SsiFetcher"]), \
                patch.object(DataFetcherManager, "_try_fetcher_quote", side_effect=lambda code, name: fiin_quote if name == "FiinTradeFetcher" else None) as fetch:
            quote = manager.get_realtime_quote("HPG.VN", log_final_failure=False)
        self.assertEqual(quote.price, 26500.0)
        self.assertEqual(quote.realtime_provider, "fiintrade")
        self.assertEqual(fetch.call_args_list[0].args[1], "FiinTradeFetcher")

        from src.services.portfolio_service import PortfolioService

        with patch.object(DataFetcherManager, "get_realtime_quote", return_value=quote):
            self.assertEqual(PortfolioService._fetch_realtime_position_price("HPG"), (26500.0, "fiintrade"))


class VnSessionTestCase(unittest.TestCase):
    def test_matching_sessions(self) -> None:
        cases = {
            (9, 0): True,
            (11, 29): True,
            (11, 30): False,
            (12, 59): False,
            (13, 0): True,
            (14, 44): True,
            (14, 45): False,
        }
        for (hour, minute), expected in cases.items():
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(is_vn_matching_session(datetime(2026, 9, 28, hour, minute, tzinfo=VN_TZ)), expected)
        self.assertFalse(is_vn_matching_session(datetime(2026, 9, 27, 10, 0, tzinfo=VN_TZ)))  # Chủ nhật


class _DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        env_path = data_dir / ".env"
        db_path = data_dir / "realtime_alerts.db"
        env_path.write_text(f"STOCK_LIST=HPG.VN\nGEMINI_API_KEY=test\nDATABASE_PATH={db_path}\n", encoding="utf-8")
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.service = AlertService()

    def tearDown(self) -> None:
        set_active_realtime_engine(None)
        set_active_feed(None)
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _rule(self, alert_type="price_cross", target="HPG.VN", **params) -> dict:
        defaults = {
            "price_cross": {"direction": "above", "price": 20500},
            "price_limit_touch": {"side": "both"},
            "price_change_percent": {"direction": "down", "change_pct": 2},
        }[alert_type]
        return self.service.create_rule({
            "target_scope": "single_symbol",
            "target": target,
            "alert_type": alert_type,
            "parameters": {**defaults, **params},
        })

    def _runtime_rule(self, rule: dict):
        row = self.service.repo.get_rule(rule["id"])
        return self.service.build_runtime_payloads(row)[0].rule


class PriceRuleTestCase(_DbTestCase):
    def test_trigger_mode_is_only_stored_when_cross(self) -> None:
        level = self._rule()
        cross = self._rule(trigger_mode="cross")
        self.assertNotIn("trigger_mode", level["parameters"])
        self.assertEqual(cross["parameters"]["trigger_mode"], "cross")
        with self.assertRaisesRegex(AlertServiceError, "trigger_mode"):
            self._rule(trigger_mode="edge")

    def test_cross_mode_triggers_only_on_actual_cross(self) -> None:
        rule = self._runtime_rule(self._rule(trigger_mode="cross"))
        prices = [20400, 20600, 20700, 20400, 20550]
        statuses = [
            self.service.evaluate_price_quote(rule, tick_to_quote(_tick(price=price)))["status"]
            for price in prices
        ]
        # 20400 (mốc) → 20600 cắt lên → 20700 vẫn trên → 20400 → 20550 cắt lên lần nữa
        self.assertEqual(statuses, ["not_triggered", "triggered", "not_triggered", "not_triggered", "triggered"])

    def test_level_mode_keeps_legacy_behaviour(self) -> None:
        rule = self._runtime_rule(self._rule())
        first = self.service.evaluate_price_quote(rule, tick_to_quote(_tick(price=20600)))
        second = self.service.evaluate_price_quote(rule, tick_to_quote(_tick(price=20700)))
        self.assertEqual((first["status"], second["status"]), ("triggered", "triggered"))
        self.assertEqual(first["reason"], "HPG.VN price above 20500.0: current = 20600.0")

    def test_price_limit_touch(self) -> None:
        both = self._runtime_rule(self._rule("price_limit_touch"))
        near_ceiling = self._runtime_rule(self._rule("price_limit_touch", side="ceiling", near_pct=1))
        self.assertEqual(self.service.evaluate_price_quote(both, tick_to_quote(_tick(price=22000)))["status"], "triggered")
        self.assertEqual(self.service.evaluate_price_quote(both, tick_to_quote(_tick(price=18000)))["status"], "triggered")
        self.assertEqual(self.service.evaluate_price_quote(both, tick_to_quote(_tick(price=21800)))["status"], "not_triggered")
        self.assertEqual(
            self.service.evaluate_price_quote(near_ceiling, tick_to_quote(_tick(price=21800)))["status"], "triggered"
        )
        no_limits = self.service.evaluate_price_quote(both, tick_to_quote(_tick(ceiling=None, floor=None)))
        self.assertEqual(no_limits["record_status"], "skipped")
        with self.assertRaisesRegex(AlertServiceError, "near_pct"):
            self._rule("price_limit_touch", near_pct=50)
        with self.assertRaisesRegex(AlertServiceError, "side"):
            self._rule("price_limit_touch", side="top")


class RealtimeEngineTestCase(_DbTestCase):
    def _dispatch(self) -> NotificationDispatchResult:
        return NotificationDispatchResult(
            dispatched=True,
            success=True,
            status="sent",
            channel_results=[ChannelAttemptResult(channel="telegram", success=True)],
        )

    def _engine(self, feed: FakeFeed, *, in_session: bool = True):
        notifier = MagicMock()
        notifier.send_with_results.return_value = self._dispatch()
        config = SimpleNamespace(agent_event_monitor_enabled=True, agent_event_alert_rules_json="", trading_day_check_enabled=False)
        worker = AlertWorker(config_provider=lambda: config, service=self.service, notifier=notifier)
        engine = RealtimeAlertEngine(feed, worker=worker, config_provider=lambda: config, session_checker=lambda: in_session)
        return engine, worker, notifier

    def test_reload_indexes_only_vn_price_rules_and_subscribes_them(self) -> None:
        self._rule()
        self._rule("price_limit_touch", target="FPT.VN")
        self.service.create_rule({
            "target_scope": "single_symbol",
            "target": "600519",
            "alert_type": "price_cross",
            "parameters": {"direction": "above", "price": 1800},
        })
        feed = FakeFeed()
        engine, _, _ = self._engine(feed)
        engine.reload_rules()
        self.assertEqual(engine.watched_symbols(), ["FPT", "HPG"])
        self.assertEqual(feed.symbols, {"FPT", "HPG"})

    def test_tick_triggers_notification_trigger_history_and_event(self) -> None:
        self._rule(trigger_mode="cross")
        feed = FakeFeed()
        engine, _, notifier = self._engine(feed)
        engine.reload_rules()
        with patch("src.services.alert_worker.publish_event") as publish:
            for price in (20400, 20600):
                engine._on_tick(_tick(price=price, sequence=price))
                engine._drain()
        notifier.send_with_results.assert_called_once()
        self.assertIn("price crossed above", notifier.send_with_results.call_args.args[0])
        triggers = self.service.list_triggers(page_size=10)["items"]
        self.assertEqual([item["status"] for item in triggers], ["triggered"])
        self.assertEqual(triggers[0]["data_source"], "tvsi_mqtt")
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[0], "alert_triggered")

        # Tick tiếp theo vẫn cắt lên nhưng đang trong cooldown 24h → không gửi lại và không ghi
        # thêm trigger theo từng tick (trước đây mỗi tick sinh một dòng lịch sử + dòng __cooldown__).
        for price in (20400, 20600, 20400, 20600):
            engine._on_tick(_tick(price=price, sequence=price + 1))
            engine._drain()
        notifier.send_with_results.assert_called_once()
        self.assertEqual(self.service.list_triggers(page_size=10)["total"], 1)
        self.assertEqual(engine.stats["cooldown_skipped"], 2)

    def test_level_rule_ticks_during_cooldown_do_not_flood_trigger_history(self) -> None:
        self._rule()  # level: giá trên ngưỡng ở mọi tick
        engine, _, notifier = self._engine(FakeFeed())
        engine.reload_rules()
        for sequence in range(1, 21):
            engine._on_tick(_tick(price=20600 + sequence, sequence=sequence))
            engine._drain()
        notifier.send_with_results.assert_called_once()
        self.assertEqual(self.service.list_triggers(page_size=50)["total"], 1)

        # CRUD / nạp lại rule xoá cache nhưng cooldown DB vẫn chặn.
        engine.reload_rules()
        engine._on_tick(_tick(price=20700, sequence=99))
        engine._drain()
        self.assertEqual(self.service.list_triggers(page_size=50)["total"], 1)

    def test_failed_dispatch_retries_after_backoff_not_every_tick(self) -> None:
        self._rule()
        clock = {"now": 1_000_000.0}
        engine, _, notifier = self._engine(FakeFeed())
        engine.now = lambda: clock["now"]
        notifier.send_with_results.return_value = NotificationDispatchResult(
            dispatched=True,
            success=False,
            status="failed",
            channel_results=[ChannelAttemptResult(channel="telegram", success=False, error_code="timeout")],
        )
        engine.reload_rules()
        for sequence in range(1, 6):
            engine._on_tick(_tick(price=20600 + sequence, sequence=sequence))
            engine._drain()
        self.assertEqual(notifier.send_with_results.call_count, 1)

        clock["now"] += 61  # hết backoff → thử gửi lại
        engine._on_tick(_tick(price=20700, sequence=50))
        engine._drain()
        self.assertEqual(notifier.send_with_results.call_count, 2)

    def test_out_of_session_ticks_are_not_evaluated(self) -> None:
        self._rule()
        engine, _, notifier = self._engine(FakeFeed(), in_session=False)
        engine.reload_rules()
        engine._on_tick(_tick(price=21000))
        engine._drain()
        notifier.send_with_results.assert_not_called()
        self.assertEqual(engine.stats["out_of_session"], 1)

    def test_periodic_worker_skips_vn_price_rules_outside_session(self) -> None:
        self._rule()
        engine, worker, _ = self._engine(FakeFeed())
        worker.now_provider = lambda: datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc).timestamp()  # Chủ nhật 10:00 VN
        with patch.object(worker.service, "_evaluate_rule") as evaluate:
            stats = worker.run_once()
        evaluate.assert_not_called()
        self.assertEqual(stats["out_of_session"], 1)

        worker.now_provider = lambda: datetime(2026, 9, 29, 7, 46, tzinfo=timezone.utc).timestamp()  # 14:46 VN, còn trong ân hạn ATC
        with patch.object(worker.service, "_evaluate_rule") as evaluate:
            evaluate.return_value = {"record_status": None, "triggered": False}
            stats = worker.run_once()
        evaluate.assert_called_once()
        self.assertNotIn("out_of_session", stats)

    @patch("src.services.alert_worker.is_vn_price_alert_window", return_value=True)
    def test_periodic_worker_skips_claimed_rules_only_while_feed_connected(self, _window) -> None:
        self._rule()
        feed = FakeFeed()
        engine, worker, _ = self._engine(feed)
        engine.reload_rules()
        set_active_realtime_engine(engine)
        with patch.object(worker.service, "_evaluate_rule") as evaluate:
            stats = worker.run_once()
        evaluate.assert_not_called()
        self.assertEqual(stats["realtime_claimed"], 1)

        feed.connected = False  # mất kết nối → engine định kỳ đánh giá lại (SSI)
        with patch.object(worker.service, "_evaluate_rule") as evaluate:
            evaluate.return_value = {"record_status": None, "triggered": False}
            stats = worker.run_once()
        evaluate.assert_called_once()
        self.assertNotIn("realtime_claimed", stats)

    def test_extra_symbols_are_subscribed_and_published_without_rule_evaluation(self) -> None:
        self._rule()
        feed = FakeFeed()
        engine, _, notifier = self._engine(feed)
        engine.extra_symbols_provider = lambda: ["FPT", "hpg.vn"]
        engine.reload_rules()
        self.assertEqual(feed.symbols, {"FPT", "HPG"})
        # Mã có rule vẫn giữ nghĩa cũ của watched_symbols (API /alerts).
        self.assertEqual(engine.watched_symbols(), ["HPG"])

        with patch("src.services.realtime_alert_engine.publish_event") as publish, \
                patch.object(engine, "evaluate_tick") as evaluate:
            engine._on_tick(_tick(symbol="FPT", price=95000))
            engine._drain()
        evaluate.assert_not_called()
        notifier.send_with_results.assert_not_called()
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[0], "price_update")
        self.assertEqual(publish.call_args.args[1]["symbol"], "FPT")

    def test_price_only_ticks_publish_outside_session_without_stats(self) -> None:
        feed = FakeFeed()
        engine, _, _ = self._engine(feed, in_session=False)
        engine.extra_symbols_provider = lambda: ["FPT"]
        engine.reload_rules()
        with patch("src.services.realtime_alert_engine.publish_event") as publish:
            engine._on_tick(_tick(symbol="FPT", price=95000))
            engine._drain()
        publish.assert_called_once()
        self.assertNotIn("out_of_session", engine.stats)

    def test_failing_extra_symbols_provider_keeps_rule_subscriptions(self) -> None:
        self._rule()
        feed = FakeFeed()
        engine, _, _ = self._engine(feed)

        def broken():
            raise RuntimeError("db locked")

        engine.extra_symbols_provider = broken
        engine.reload_rules()
        self.assertEqual(feed.symbols, {"HPG"})

    def test_unwatched_ticks_are_dropped(self) -> None:
        engine, _, _ = self._engine(FakeFeed())
        engine.reload_rules()
        engine._on_tick(_tick(symbol="VNM"))
        self.assertEqual(engine._pending, {})

    def test_crud_requests_engine_reload(self) -> None:
        engine, _, _ = self._engine(FakeFeed())
        set_active_realtime_engine(engine)
        engine._reload_requested = False
        self._rule()
        self.assertTrue(engine._reload_requested)


class PortfolioHeldSymbolsTestCase(_DbTestCase):
    """Mã đang nắm giữ trong danh mục được subscribe feed để trang danh mục có giá live."""

    def setUp(self) -> None:
        super().setUp()
        from src.services.portfolio_service import PortfolioService

        self.portfolio = PortfolioService()
        self.account_id = self.portfolio.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]

    def _trade(self, symbol, side, quantity, *, account_id=None, trade_date="2026-01-05"):
        return self.portfolio.record_trade(
            account_id=account_id or self.account_id,
            symbol=symbol,
            trade_date=datetime.strptime(trade_date, "%Y-%m-%d").date(),
            side=side,
            quantity=quantity,
            price=10000,
        )

    def test_lists_open_symbols_and_split_symbols_of_active_accounts(self) -> None:
        from src.services.realtime_alert_runtime import portfolio_held_symbols

        self._trade("HPG", "buy", 100)
        self._trade("FPT", "buy", 100)
        self._trade("FPT", "sell", 100, trade_date="2026-01-06")
        self._trade("VNM", "buy", 100)
        self._trade("VNM", "sell", 100, trade_date="2026-01-06")
        self.portfolio.record_corporate_action(
            account_id=self.account_id,
            symbol="VNM",
            effective_date=datetime(2026, 1, 7).date(),
            action_type="split_adjustment",
            split_ratio=2.0,
        )
        archived = self.portfolio.create_account(name="Old", broker="Demo", market="vn", base_currency="VND")["id"]
        self._trade("MBB", "buy", 100, account_id=archived)
        self.portfolio.repo.deactivate_account(archived)

        self.assertEqual(portfolio_held_symbols(), ["HPG", "VNM"])

    def test_trade_writes_request_engine_reload(self) -> None:
        engine = MagicMock()
        set_active_realtime_engine(engine)
        trade_id = self._trade("HPG", "buy", 100)["id"]
        engine.request_reload.assert_called_once()

        self.portfolio.delete_trade_event(trade_id)
        self.assertEqual(engine.request_reload.call_count, 2)


class EventBusTestCase(unittest.TestCase):
    def test_publish_from_thread_reaches_async_subscriber_and_drops_when_full(self) -> None:
        bus = EventBus("test")

        async def scenario():
            queue = bus.subscribe(maxsize=1)
            thread = threading.Thread(target=lambda: [bus.publish("price_update", {"n": i}) for i in range(3)])
            thread.start()
            thread.join()
            event = await asyncio.wait_for(queue.get(), timeout=2)
            await asyncio.sleep(0.05)
            bus.unsubscribe(queue)
            return event, queue.qsize(), bus.subscriber_count()

        event, remaining, subscribers = asyncio.run(scenario())
        self.assertEqual(event, {"type": "price_update", "data": {"n": 0}})
        self.assertEqual(remaining, 0)
        self.assertEqual(subscribers, 0)


class LiveQuotesApiTestCase(_DbTestCase):
    def test_live_quotes_use_feed_ticks_and_report_feed_status(self) -> None:
        from fastapi.testclient import TestClient

        from api.app import create_app

        self._rule()
        feed = FakeFeed()
        feed.store.update(_tick(price=20300))
        set_active_feed(feed)
        with patch.dict(os.environ, {"ADMIN_AUTH_ENABLED": "false"}):
            client = TestClient(create_app())
            resp = client.get("/api/v1/alerts/live-quotes")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        quotes = {item["target"]: item for item in payload["quotes"]}
        self.assertEqual(quotes["HPG.VN"]["price"], 20300)
        self.assertEqual(quotes["HPG.VN"]["source"], "tvsi_mqtt")
        self.assertEqual(quotes["HPG.VN"]["symbol"], "HPG")
        self.assertTrue(payload["feed"]["enabled"])
        self.assertTrue(payload["feed"]["connected"])


if __name__ == "__main__":
    unittest.main()
