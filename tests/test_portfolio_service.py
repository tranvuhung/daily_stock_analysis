# -*- coding: utf-8 -*-
"""Unit tests for portfolio replay service (P0 PR1 scope)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import pandas as pd
from sqlalchemy.exc import OperationalError
from sqlalchemy import select

from src.config import Config
from src.repositories.portfolio_repo import PortfolioBusyError, PortfolioRepository
from src.services.portfolio_service import _AvgState, PortfolioConflictError, PortfolioOversellError, PortfolioService
from src.storage import DatabaseManager, PortfolioDailySnapshot, PortfolioPosition, PortfolioPositionLot, PortfolioTrade


class PortfolioServiceTestCase(unittest.TestCase):
    """Portfolio service replay tests for FIFO/AVG and corporate actions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.db_path = Path(self.temp_dir.name) / "portfolio_test.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=HPG",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.db = DatabaseManager.get_instance()
        self.service = PortfolioService()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _save_close(self, symbol: str, on_date: date, close: float) -> None:
        df = pd.DataFrame(
            [
                {
                    "date": on_date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1.0,
                    "amount": close,
                    "pct_chg": 0.0,
                }
            ]
        )
        self.db.save_daily_data(df, code=symbol, data_source="unit-test")

    def _create_account_with_position(
        self,
        *,
        market: str,
        currency: str,
        symbol: str,
        quantity: float = 10.0,
        price: float = 100.0,
        close: Optional[float] = None,
        close_date: Optional[date] = None,
    ) -> int:
        account = self.service.create_account(name=f"{market}-account", broker="Demo", market=market, base_currency=currency)
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=100000,
            currency=currency,
        )
        self.service.record_trade(
            account_id=aid,
            symbol=symbol,
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=quantity,
            price=price,
            market=market,
            currency=currency,
        )
        if close is not None:
            self._save_close(self.service._normalize_symbol(symbol), close_date or date(2026, 1, 3), close)
        return aid

    def test_current_snapshot_uses_realtime_price_when_close_missing(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )

        with patch.object(PortfolioService, "_fetch_realtime_position_price", return_value=(125.0, "unit-test")):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 125.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1250.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 250.0, places=6)
        self.assertEqual(pos["price_source"], "realtime_quote")
        self.assertEqual(pos["price_provider"], "unit-test")
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_prefers_realtime_price_over_stale_close(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", today - timedelta(days=1), 110.0)

        with patch.object(PortfolioService, "_fetch_realtime_position_price", return_value=(125.0, "unit-test")):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 125.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1250.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 250.0, places=6)
        self.assertEqual(pos["price_source"], "realtime_quote")
        self.assertEqual(pos["price_provider"], "unit-test")
        self.assertEqual(pos["price_date"], today.isoformat())
        self.assertFalse(pos["price_stale"])
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_prefers_realtime_price_over_same_day_close(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", today, 118.0)

        with patch.object(PortfolioService, "_fetch_realtime_position_price", return_value=(125.0, "unit-test")):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 125.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1250.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 250.0, places=6)
        self.assertEqual(pos["price_source"], "realtime_quote")
        self.assertEqual(pos["price_provider"], "unit-test")
        self.assertEqual(pos["price_date"], today.isoformat())
        self.assertFalse(pos["price_stale"])
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_falls_back_to_close_when_realtime_unavailable(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", today, 118.0)

        with patch.object(
            PortfolioService,
            "_fetch_realtime_position_price",
            return_value=(None, None),
        ):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 118.0, places=6)
        self.assertAlmostEqual(pos["market_value_base"], 1180.0, places=6)
        self.assertAlmostEqual(pos["unrealized_pnl_base"], 180.0, places=6)
        self.assertEqual(pos["price_source"], "history_close")
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_can_skip_realtime_quote_for_fast_load(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=today,
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )
        # Giá đóng cửa cũ 1 tuần -> luôn stale, không phụ thuộc phiên hiện tại.
        self._save_close("HPG", today - timedelta(days=7), 118.0)

        with patch.object(
            PortfolioService,
            "_fetch_realtime_position_price",
            side_effect=AssertionError("fast portfolio snapshot should not fetch realtime quote"),
        ):
            snapshot = self.service.get_portfolio_snapshot(
                account_id=aid,
                as_of=today,
                cost_method="fifo",
                include_realtime=False,
            )

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertAlmostEqual(pos["last_price"], 118.0, places=6)
        self.assertEqual(pos["price_source"], "history_close")
        self.assertEqual(pos["price_date"], (today - timedelta(days=7)).isoformat())
        self.assertTrue(pos["price_stale"])
        self.assertTrue(pos["price_available"])

    def test_current_snapshot_does_not_serialize_non_bulk_realtime_prefetch(self) -> None:
        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        for symbol in ["HPG", "VCB", "FPT"]:
            self.service.record_trade(
                account_id=aid,
                symbol=symbol,
                trade_date=today,
                side="buy",
                quantity=10,
                price=100,
                market="vn",
                currency="VND",
            )

        fetch_state = {"active": 0, "max_active": 0}
        lock = threading.Lock()
        release = threading.Event()
        called_symbols: list[str] = []
        manager_instances: list[object] = []

        class FakeDataFetcherManager:
            def __init__(self) -> None:
                self._fetcher_call_lock = threading.Lock()
                manager_instances.append(self)

            def get_realtime_quote(self, symbol: str, log_final_failure: bool = True) -> SimpleNamespace:
                del log_final_failure
                with self._fetcher_call_lock:
                    with lock:
                        called_symbols.append(symbol)
                        fetch_state["active"] += 1
                        fetch_state["max_active"] = max(fetch_state["max_active"], fetch_state["active"])
                        if fetch_state["active"] >= 2:
                            release.set()
                    release.wait(timeout=1.0)
                    with lock:
                        fetch_state["active"] -= 1
                    return SimpleNamespace(price=125.0, source="unit-test")

        with patch("data_provider.base.DataFetcherManager", new=FakeDataFetcherManager):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        positions = snapshot["accounts"][0]["positions"]
        self.assertEqual(len(positions), 3)
        self.assertEqual(set(called_symbols), {position["symbol"] for position in positions})
        self.assertGreaterEqual(len(manager_instances), 2)
        self.assertGreaterEqual(fetch_state["max_active"], 2)
        self.assertTrue(all(position["price_source"] == "realtime_quote" for position in positions))
        self.assertTrue(all(position["price_provider"] == "unit-test" for position in positions))

    def test_current_snapshot_does_not_serialize_when_bulk_prefetch_cache_misses(self) -> None:
        """Bulk-prefetch path must not share a fetcher manager across workers.

        Reproduces the #1803 regression where ``prefetch_realtime_quotes`` reports a
        full-count result but per-symbol ``get_realtime_quote`` still issues a live
        fetch (mixed markets / cache miss). Each worker must create its own manager
        so manager-owned per-fetcher locks do not re-serialize the requests.
        """

        today = date.today()
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        symbols = ["HPG", "VCB", "FPT", "MWG", "VNM", "TCB"]
        for symbol in symbols:
            self.service.record_trade(
                account_id=aid,
                symbol=symbol,
                trade_date=today,
                side="buy",
                quantity=10,
                price=100,
                market="vn",
                currency="VND",
            )

        fetch_state = {"active": 0, "max_active": 0}
        lock = threading.Lock()
        release = threading.Event()
        manager_instances: list[object] = []
        prefetch_calls: list[list[str]] = []

        class FakeDataFetcherManager:
            def __init__(self) -> None:
                # Per-instance lock mirrors DataFetcherManager._call_fetcher_method,
                # which serializes per-fetcher access through manager-owned locks.
                self._fetcher_call_lock = threading.Lock()
                manager_instances.append(self)

            def prefetch_realtime_quotes(self, stock_codes: list) -> int:
                prefetch_calls.append(list(stock_codes))
                # Report a full-count bulk prefetch, simulating the optimistic
                # "cache fully filled" signal the previous implementation trusted.
                return len(stock_codes)

            def get_realtime_quote(self, symbol: str, log_final_failure: bool = True) -> SimpleNamespace:
                del log_final_failure
                with self._fetcher_call_lock:
                    with lock:
                        fetch_state["active"] += 1
                        fetch_state["max_active"] = max(fetch_state["max_active"], fetch_state["active"])
                        if fetch_state["active"] >= 2:
                            release.set()
                    release.wait(timeout=2.0)
                    with lock:
                        fetch_state["active"] -= 1
                    return SimpleNamespace(price=125.0, source="unit-test")

        with patch("data_provider.base.DataFetcherManager", new=FakeDataFetcherManager):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=today, cost_method="fifo")

        positions = snapshot["accounts"][0]["positions"]
        self.assertEqual(len(positions), len(symbols))
        # Bulk prefetch path must still be exercised for large batches.
        self.assertEqual(len(prefetch_calls), 1)
        self.assertEqual(len(prefetch_calls[0]), len(symbols))
        # One manager warmed the cache; each per-symbol worker created its own.
        self.assertGreaterEqual(len(manager_instances), len(symbols) + 1)
        # Per-worker independent managers must allow real concurrency even when
        # the bulk prefetch claims a full cache fill.
        self.assertGreaterEqual(fetch_state["max_active"], 2)
        self.assertTrue(all(position["price_source"] == "realtime_quote" for position in positions))
        self.assertTrue(all(position["price_provider"] == "unit-test" for position in positions))

    def test_historical_snapshot_marks_missing_price_without_cost_fallback(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )

        with patch.object(
            PortfolioService,
            "_fetch_realtime_position_price",
            side_effect=AssertionError("historical snapshot should not fetch realtime quote"),
        ):
            snapshot = self.service.get_portfolio_snapshot(
                account_id=aid,
                as_of=date(2026, 1, 2),
                cost_method="fifo",
            )

        pos = snapshot["accounts"][0]["positions"][0]
        self.assertEqual(pos["last_price"], 0.0)
        self.assertEqual(pos["market_value_base"], 0.0)
        self.assertEqual(pos["unrealized_pnl_base"], 0.0)
        self.assertEqual(pos["price_source"], "missing")
        self.assertFalse(pos["price_available"])
        self.assertTrue(pos["price_stale"])
        self.assertEqual(snapshot["accounts"][0]["total_market_value"], 0.0)
        self.assertEqual(snapshot["accounts"][0]["unrealized_pnl"], 0.0)

    def test_snapshot_fifo_vs_avg_on_partial_sell(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=100000,
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=100,
            price=10,
            fee=10,
            tax=0,
            market="vn",
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 3),
            side="buy",
            quantity=100,
            price=20,
            fee=10,
            tax=0,
            market="vn",
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 4),
            side="sell",
            quantity=150,
            price=30,
            fee=10,
            tax=5,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 5), 25)

        fifo = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")
        avg = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="avg")

        fifo_acc = fifo["accounts"][0]
        avg_acc = avg["accounts"][0]
        self.assertAlmostEqual(fifo_acc["total_equity"], avg_acc["total_equity"], places=6)

        self.assertAlmostEqual(fifo_acc["realized_pnl"], 2470.0, places=6)
        self.assertAlmostEqual(avg_acc["realized_pnl"], 2220.0, places=6)
        self.assertAlmostEqual(fifo_acc["unrealized_pnl"], 245.0, places=6)
        self.assertAlmostEqual(avg_acc["unrealized_pnl"], 495.0, places=6)

        self.assertEqual(len(fifo_acc["positions"]), 1)
        self.assertEqual(len(avg_acc["positions"]), 1)
        self.assertAlmostEqual(fifo_acc["positions"][0]["quantity"], 50.0, places=6)
        self.assertAlmostEqual(avg_acc["positions"][0]["quantity"], 50.0, places=6)

    def test_snapshot_position_price_metadata_uses_backend_values_for_vn(self) -> None:
        # Mã VN nhập dạng thường / hậu tố .VN đều quy về mã chuẩn viết hoa.
        # Lowercase / .VN-suffixed VN symbols canonicalize to the bare uppercase code.
        for symbol, close, expected_symbol in [
            ("HPG", 28500.0, "HPG"),
            ("fpt", 125000.0, "FPT"),
            ("VNM.VN", 61000.0, "VNM"),
        ]:
            with self.subTest(symbol=symbol):
                aid = self._create_account_with_position(
                    market="vn", currency="VND", symbol=symbol, price=100.0, close=close,
                )
                position = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")["accounts"][0]["positions"][0]

                self.assertEqual(position["symbol"], expected_symbol)
                self.assertEqual(position["price_source"], "history_close")
                self.assertEqual(position["price_date"], "2026-01-03")
                self.assertFalse(position["price_stale"])
                self.assertTrue(position["price_available"])
                self.assertAlmostEqual(position["last_price"], close, places=6)
                self.assertAlmostEqual(position["market_value_base"], close * 10, places=6)
                self.assertAlmostEqual(position["unrealized_pnl_base"], close * 10 - 1000, places=6)
                self.assertAlmostEqual(position["unrealized_pnl_pct"], (close * 10 - 1000) / 1000 * 100, places=6)
                self.assertEqual(position["data_quality"], "ok")
                self.assertEqual(position["limitations"], [])

    def test_snapshot_marks_stale_close_and_missing_price(self) -> None:
        aid = self._create_account_with_position(
            market="vn",
            currency="VND",
            symbol="HPG",
            close=110,
            close_date=date(2026, 1, 2),
        )
        self.service.record_trade(
            account_id=aid,
            symbol="VCB",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=5,
            price=20,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 2), 110)

        # Thứ Hai 2026-01-05: giá thứ Sáu 01-02 cũ hơn phiên hoàn tất gần nhất -> stale.
        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")
        positions = {item["symbol"]: item for item in snapshot["accounts"][0]["positions"]}

        stale_close = positions["HPG"]
        self.assertEqual(stale_close["price_source"], "history_close")
        self.assertEqual(stale_close["price_date"], "2026-01-02")
        self.assertTrue(stale_close["price_stale"])
        self.assertTrue(stale_close["price_available"])
        self.assertAlmostEqual(stale_close["last_price"], 110.0, places=6)
        self.assertAlmostEqual(stale_close["unrealized_pnl_pct"], 10.0, places=6)

        missing = positions["VCB"]
        self.assertEqual(missing["price_source"], "missing")
        self.assertIsNone(missing["price_date"])
        self.assertTrue(missing["price_stale"])
        self.assertFalse(missing["price_available"])
        self.assertAlmostEqual(missing["last_price"], 0.0, places=6)
        self.assertAlmostEqual(missing["market_value_base"], 0.0, places=6)
        self.assertAlmostEqual(missing["unrealized_pnl_base"], 0.0, places=6)
        self.assertIsNone(missing["unrealized_pnl_pct"])
        self.assertEqual(missing["limitations"], ["price_unavailable"])
        self.assertEqual(missing["data_quality"], "partial")
        self.assertEqual(stale_close["limitations"], [])
        self.assertEqual(snapshot["accounts"][0]["data_quality"], "partial")
        self.assertIn("price_unavailable", snapshot["accounts"][0]["limitations"])
        self.assertEqual(snapshot["data_quality"], "partial")

    def test_weekend_snapshot_friday_close_not_stale(self) -> None:
        aid = self._create_account_with_position(
            market="vn",
            currency="VND",
            symbol="HPG",
            close=110,
            close_date=date(2026, 1, 2),
        )
        # Thứ 7 / CN: giá thứ Sáu là phiên hoàn tất gần nhất, không coi là cũ.
        for as_of in (date(2026, 1, 3), date(2026, 1, 4)):
            snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=as_of, cost_method="fifo")
            pos = snapshot["accounts"][0]["positions"][0]
            self.assertEqual(pos["price_date"], "2026-01-02")
            self.assertFalse(pos["price_stale"])
            self.assertEqual(pos["data_quality"], "ok")

    def test_build_positions_handles_zero_cost_without_division(self) -> None:
        account = SimpleNamespace(base_currency="VND")

        positions, _, _, _, _ = self.service._build_positions(
            account=account,
            as_of_date=date(2026, 1, 3),
            cost_method="avg",
            fifo_lots={},
            avg_state={("HPG", "vn", "VND"): _AvgState(quantity=10.0, total_cost=0.0)},
        )

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["price_source"], "missing")
        self.assertIsNone(positions[0]["unrealized_pnl_pct"])
        self.assertAlmostEqual(positions[0]["last_price"], 0.0, places=6)

    def test_symbol_filter_matches_legacy_vn_suffix_variants(self) -> None:
        account = self.service.create_account(name="Legacy", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        for symbol in ["HPG", "HPG.VN"]:
            self.service.repo.add_trade(
                account_id=aid,
                trade_uid=None,
                symbol=symbol,
                market="vn",
                currency="VND",
                trade_date=date(2026, 1, 2),
                side="buy",
                quantity=1,
                price=10,
                fee=0,
                tax=0,
            )

        rows = self.service.list_trade_events(account_id=aid, symbol="HPG", page=1, page_size=20)["items"]
        self.assertEqual({row["symbol"] for row in rows}, {"HPG", "HPG.VN"})

        suffixed = self.service.list_trade_events(account_id=aid, symbol="hpg.vn", page=1, page_size=20)["items"]
        self.assertEqual({row["symbol"] for row in suffixed}, {"HPG", "HPG.VN"})

    def test_corporate_actions_dividend_and_split(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000,
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=100,
            price=10,
            fee=0,
            tax=0,
            market="vn",
            currency="VND",
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="HPG",
            effective_date=date(2026, 1, 3),
            action_type="cash_dividend",
            market="vn",
            currency="VND",
            cash_dividend_per_share=1.0,
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="HPG",
            effective_date=date(2026, 1, 4),
            action_type="split_adjustment",
            market="vn",
            currency="VND",
            split_ratio=2.0,
        )
        self._save_close("HPG", date(2026, 1, 5), 6.0)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")
        acc = snapshot["accounts"][0]
        pos = acc["positions"][0]

        self.assertAlmostEqual(acc["total_cash"], 9100.0, places=6)
        self.assertAlmostEqual(acc["total_market_value"], 1200.0, places=6)
        self.assertAlmostEqual(acc["total_equity"], 10300.0, places=6)
        self.assertAlmostEqual(pos["quantity"], 200.0, places=6)
        self.assertAlmostEqual(pos["avg_cost"], 5.0, places=6)

    def test_same_day_dividend_processed_before_trade(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=2000,
            currency="VND",
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="HPG",
            effective_date=date(2026, 1, 2),
            action_type="cash_dividend",
            market="vn",
            currency="VND",
            cash_dividend_per_share=1.0,
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=100,
            price=10,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 2), 10.0)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")
        acc = snapshot["accounts"][0]

        self.assertAlmostEqual(acc["total_cash"], 1000.0, places=6)
        self.assertAlmostEqual(acc["total_market_value"], 1000.0, places=6)
        self.assertAlmostEqual(acc["total_equity"], 2000.0, places=6)

    def test_same_day_split_processed_before_trade(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=2000,
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=10,
            market="vn",
            currency="VND",
        )
        self.service.record_corporate_action(
            account_id=aid,
            symbol="HPG",
            effective_date=date(2026, 1, 2),
            action_type="split_adjustment",
            market="vn",
            currency="VND",
            split_ratio=2.0,
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="sell",
            quantity=100,
            price=6,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 2), 6.0)

        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")
        acc = snapshot["accounts"][0]
        pos = acc["positions"][0]

        self.assertAlmostEqual(acc["realized_pnl"], 100.0, places=6)
        self.assertAlmostEqual(acc["total_cash"], 1600.0, places=6)
        self.assertAlmostEqual(pos["quantity"], 100.0, places=6)
        self.assertAlmostEqual(pos["avg_cost"], 5.0, places=6)

    def test_sell_oversell_rejected_before_write(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=10,
            market="vn",
            currency="VND",
        )

        with self.assertRaises(PortfolioOversellError):
            self.service.record_trade(
                account_id=aid,
                symbol="HPG",
                trade_date=date(2026, 1, 3),
                side="sell",
                quantity=20,
                price=11,
                market="vn",
                currency="VND",
            )

        trades = self.service.list_trade_events(account_id=aid, page=1, page_size=20)
        self.assertEqual(len(trades["items"]), 1)
        self.assertEqual(trades["items"][0]["side"], "buy")

    def _trade(self, aid: int, day: date, side: str, quantity: float) -> int:
        return self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=day,
            side=side,
            quantity=quantity,
            price=10,
            market="vn",
            currency="VND",
        )["id"]

    def test_backdated_sell_rejected_when_later_sell_would_oversell(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        self._trade(aid, date(2026, 1, 1), "buy", 10)
        self._trade(aid, date(2026, 1, 5), "sell", 10)

        # Bán lùi ngày 01-03: đủ hàng tại ngày đó nhưng làm lệnh bán 01-05 vượt số lượng.
        with self.assertRaises(PortfolioOversellError):
            self._trade(aid, date(2026, 1, 3), "sell", 5)

        trades = self.service.list_trade_events(account_id=aid, page=1, page_size=20)
        self.assertEqual(len(trades["items"]), 2)

    def test_delete_buy_rejected_when_later_sell_depends_on_it(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        buy_id = self._trade(aid, date(2026, 1, 1), "buy", 10)
        self._trade(aid, date(2026, 1, 5), "sell", 10)

        with self.assertRaises(PortfolioOversellError):
            self.service.delete_trade_event(buy_id)

        trades = self.service.list_trade_events(account_id=aid, page=1, page_size=20)
        self.assertEqual(len(trades["items"]), 2)
        snapshot = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 6), cost_method="fifo")
        self.assertEqual(snapshot["accounts"][0]["positions"], [])

    def test_delete_split_rejected_when_later_sell_depends_on_it(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        self._trade(aid, date(2026, 1, 1), "buy", 10)
        split = self.service.record_corporate_action(
            account_id=aid,
            symbol="HPG",
            effective_date=date(2026, 1, 2),
            action_type="split_adjustment",
            split_ratio=2.0,
        )
        self._trade(aid, date(2026, 1, 5), "sell", 20)

        with self.assertRaises(PortfolioOversellError):
            self.service.delete_corporate_action_event(split["id"])

        actions = self.service.list_corporate_action_events(account_id=aid, page=1, page_size=20)
        self.assertEqual(len(actions["items"]), 1)

    def test_reverse_split_rejected_when_later_sell_would_oversell(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        self._trade(aid, date(2026, 1, 1), "buy", 10)
        self._trade(aid, date(2026, 1, 5), "sell", 10)

        with self.assertRaises(PortfolioOversellError):
            self.service.record_corporate_action(
                account_id=aid,
                symbol="HPG",
                effective_date=date(2026, 1, 2),
                action_type="split_adjustment",
                split_ratio=0.5,
            )

    def test_delete_buy_allowed_without_dependent_sell(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        buy_id = self._trade(aid, date(2026, 1, 1), "buy", 10)
        self._trade(aid, date(2026, 1, 2), "buy", 10)
        self._trade(aid, date(2026, 1, 5), "sell", 10)

        self.assertTrue(self.service.delete_trade_event(buy_id))
        self.assertFalse(self.service.delete_trade_event(buy_id))

    def test_duplicate_full_close_sell_keeps_conflict_semantics(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=10,
            market="vn",
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="sell",
            quantity=10,
            price=11,
            market="vn",
            currency="VND",
            trade_uid="sell-full-close-1",
        )

        with self.assertRaises(PortfolioConflictError) as ctx:
            self.service.record_trade(
                account_id=aid,
                symbol="HPG",
                trade_date=date(2026, 1, 2),
                side="sell",
                quantity=10,
                price=11,
                market="vn",
                currency="VND",
                trade_uid="sell-full-close-1",
            )

        self.assertIn("Duplicate trade_uid", str(ctx.exception))

    def test_backdated_trade_write_invalidates_future_cache(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000,
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 3),
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 3), 100.0)
        self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 3), cost_method="fifo")

        with self.db.get_session() as session:
            snapshot_count = session.execute(
                select(PortfolioDailySnapshot).where(PortfolioDailySnapshot.account_id == aid)
            ).scalars().all()
            position_count = session.execute(
                select(PortfolioPosition).where(PortfolioPosition.account_id == aid)
            ).scalars().all()
            lot_count = session.execute(
                select(PortfolioPositionLot).where(PortfolioPositionLot.account_id == aid)
            ).scalars().all()
        self.assertEqual(len(snapshot_count), 1)
        self.assertEqual(len(position_count), 1)
        self.assertEqual(len(lot_count), 1)

        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=5,
            price=80,
            market="vn",
            currency="VND",
        )

        with self.db.get_session() as session:
            snapshot_rows = session.execute(
                select(PortfolioDailySnapshot).where(PortfolioDailySnapshot.account_id == aid)
            ).scalars().all()
            position_rows = session.execute(
                select(PortfolioPosition).where(PortfolioPosition.account_id == aid)
            ).scalars().all()
            lot_rows = session.execute(
                select(PortfolioPositionLot).where(PortfolioPositionLot.account_id == aid)
            ).scalars().all()
        self.assertEqual(len(snapshot_rows), 0)
        self.assertEqual(len(position_rows), 0)
        self.assertEqual(len(lot_rows), 0)

    def test_delete_trade_invalidates_cache_and_removes_source_event(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000,
            currency="VND",
        )
        trade = self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=100,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 2), 100.0)
        self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")

        self.assertTrue(self.service.delete_trade_event(trade["id"]))

        with self.db.get_session() as session:
            trade_rows = session.execute(
                select(PortfolioTrade).where(PortfolioTrade.account_id == aid)
            ).scalars().all()
            snapshot_rows = session.execute(
                select(PortfolioDailySnapshot).where(PortfolioDailySnapshot.account_id == aid)
            ).scalars().all()
            lot_rows = session.execute(
                select(PortfolioPositionLot).where(PortfolioPositionLot.account_id == aid)
            ).scalars().all()
        self.assertEqual(len(trade_rows), 0)
        self.assertEqual(len(snapshot_rows), 0)
        self.assertEqual(len(lot_rows), 0)

    def test_concurrent_sell_race_allows_only_one_write(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=10,
            market="vn",
            currency="VND",
        )

        barrier = threading.Barrier(3)
        results: list[str] = []
        errors: list[Exception] = []

        def _worker(uid: str) -> None:
            svc = PortfolioService()
            barrier.wait()
            try:
                svc.record_trade(
                    account_id=aid,
                    symbol="HPG",
                    trade_date=date(2026, 1, 2),
                    side="sell",
                    quantity=10,
                    price=11,
                    market="vn",
                    currency="VND",
                    trade_uid=uid,
                )
                results.append(uid)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [
            threading.Thread(target=_worker, args=(f"sell-race-{idx}",), daemon=True)
            for idx in range(2)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PortfolioOversellError)

        trades = self.service.list_trade_events(account_id=aid, page=1, page_size=20)
        sell_count = sum(1 for item in trades["items"] if item["side"] == "sell")
        self.assertEqual(sell_count, 1)

    def test_concurrent_duplicate_full_close_sell_keeps_conflict_semantics(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=10,
            market="vn",
            currency="VND",
        )

        barrier = threading.Barrier(3)
        results: list[str] = []
        errors: list[Exception] = []

        def _worker() -> None:
            svc = PortfolioService()
            barrier.wait()
            try:
                svc.record_trade(
                    account_id=aid,
                    symbol="HPG",
                    trade_date=date(2026, 1, 2),
                    side="sell",
                    quantity=10,
                    price=11,
                    market="vn",
                    currency="VND",
                    trade_uid="dup-race-sell-1",
                )
                results.append("ok")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=_worker, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PortfolioConflictError)
        self.assertIn("Duplicate trade_uid", str(errors[0]))

    def test_event_symbol_filters_match_vn_suffix_symbols(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        self.service.repo.add_trade(
            account_id=aid,
            trade_uid="legacy-vn-suffix-trade",
            symbol="HPG.VN",
            market="vn",
            currency="VND",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=100,
            fee=0,
            tax=0,
        )
        self.service.repo.add_corporate_action(
            account_id=aid,
            symbol="HPG.VN",
            market="vn",
            currency="VND",
            effective_date=date(2026, 1, 3),
            action_type="cash_dividend",
            cash_dividend_per_share=1.0,
        )

        trades = self.service.list_trade_events(account_id=aid, symbol="HPG", page=1, page_size=20)
        actions = self.service.list_corporate_action_events(account_id=aid, symbol="HPG", page=1, page_size=20)

        self.assertEqual(trades["total"], 1)
        self.assertEqual(actions["total"], 1)
        self.assertEqual(trades["items"][0]["symbol"], "HPG.VN")
        self.assertEqual(actions["items"][0]["symbol"], "HPG.VN")

    def test_portfolio_write_session_maps_sqlite_locked_error(self) -> None:
        repo = PortfolioRepository(db_manager=self.db)
        session = self.db.get_session()
        stmt_exc = OperationalError(
            "BEGIN IMMEDIATE",
            None,
            sqlite3.OperationalError("database is locked"),
        )

        with patch.object(self.db, "get_session", return_value=session):
            with patch.object(
                session.connection(),
                "exec_driver_sql",
                side_effect=stmt_exc,
            ):
                with self.assertRaises(PortfolioBusyError):
                    with repo.portfolio_write_session():
                        pass


if __name__ == "__main__":
    unittest.main()
