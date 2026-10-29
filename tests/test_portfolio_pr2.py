# -*- coding: utf-8 -*-
"""PR2 tests for portfolio CSV import, risk thresholds and decision-signal risk (VN only)."""

from __future__ import annotations

import os
import sys
import tempfile
import unicodedata
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.services.decision_signal_service import DecisionSignalService
from src.services.portfolio_import_service import PortfolioImportService
from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import PortfolioBusyError, PortfolioService
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


_VN_TONE_MARKS = {"\u0300", "\u0301", "\u0303", "\u0309", "\u0323"}


def _encode_cp1258(text: str) -> bytes:
    """Mã hoá như Windows tiếng Việt: chữ cơ sở (â, ê, ơ...) dựng sẵn, dấu thanh tách rời.

    Encode like Vietnamese Windows exports: precomposed base letters, separate tone marks.
    """
    out = []
    for char in text:
        try:
            out.append(char.encode("cp1258"))
            continue
        except UnicodeEncodeError:
            pass
        decomposed = unicodedata.normalize("NFD", char)
        base = "".join(c for c in decomposed if c not in _VN_TONE_MARKS)
        tones = "".join(c for c in decomposed if c in _VN_TONE_MARKS)
        out.append((unicodedata.normalize("NFC", base) + tones).encode("cp1258"))
    return b"".join(out)


class PortfolioPr2TestCase(unittest.TestCase):
    """End-to-end style tests for PR2 import, dedup, risk and fx behavior."""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.env_path = data_dir / ".env"
        self.db_path = data_dir / "portfolio_pr2_test.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=HPG",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    "PORTFOLIO_RISK_CONCENTRATION_ALERT_PCT=70.0",
                    "PORTFOLIO_RISK_DRAWDOWN_ALERT_PCT=10.0",
                    "PORTFOLIO_RISK_STOP_LOSS_ALERT_PCT=25.0",
                    "PORTFOLIO_RISK_STOP_LOSS_NEAR_RATIO=0.8",
                    "PORTFOLIO_RISK_LOOKBACK_DAYS=365",
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
        self.import_service = PortfolioImportService(portfolio_service=self.service)
        self.risk_service = PortfolioRiskService(portfolio_service=self.service)
        self.client = TestClient(create_app(static_dir=data_dir / "empty-static"))

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
        self.db.save_daily_data(df, code=symbol, data_source="portfolio-pr2-test")

    def _create_position(self, account_id: int, symbol: str, price: float = 100.0, *, market: str = "vn") -> None:
        self.service.record_trade(
            account_id=account_id,
            symbol=symbol,
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=price,
            market=market,
            currency="VND",
        )
        self._save_close(symbol, date(2026, 1, 1), price)

    def _create_signal(self, symbol: str, action: str, **overrides) -> dict:
        payload = {
            "stock_code": symbol,
            "stock_name": symbol,
            "market": "vn",
            "source_type": "manual",
            "trace_id": f"signal-{symbol}-{action}-{len(overrides)}",
            "trigger_source": "api",
            "action": action,
            "reason": f"{symbol} {action} reason",
            "status": "active",
        }
        payload.update(overrides)
        return DecisionSignalService().create_signal(payload)["item"]

    @staticmethod
    def _csv_bytes(with_trade_uid: bool = True) -> bytes:
        if with_trade_uid:
            csv_text = (
                "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp,Số hiệu lệnh\n"
                "2026-01-02,HPG,Mua,10,100,HT-001\n"
            )
        else:
            csv_text = (
                "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp\n"
                "2026-01-02,HPG,Mua,10,100\n"
            )
        return csv_text.encode("utf-8")

    def test_import_dedup_trade_uid_and_hash(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        parsed_uid = self.import_service.parse_trade_csv(broker="generic", content=self._csv_bytes(with_trade_uid=True))
        first_uid = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=parsed_uid["records"],
        )
        second_uid = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=parsed_uid["records"],
        )
        self.assertEqual(first_uid["inserted_count"], 1)
        self.assertEqual(second_uid["duplicate_count"], 1)

        parsed_hash = self.import_service.parse_trade_csv(
            broker="generic",
            content=self._csv_bytes(with_trade_uid=False),
        )
        first_hash = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=parsed_hash["records"],
        )
        second_hash = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=parsed_hash["records"],
        )
        self.assertEqual(first_hash["inserted_count"], 0)
        self.assertEqual(first_hash["duplicate_count"], 1)
        self.assertEqual(second_hash["inserted_count"], 0)

    def test_import_side_parser_avoids_false_sell_match(self) -> None:
        csv_text = (
            "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp,Số hiệu lệnh\n"
            "2026-01-02,HPG,Asset Transfer,10,100,HT-002\n"
        )
        parsed = self.import_service.parse_trade_csv(
            broker="generic",
            content=csv_text.encode("utf-8"),
        )
        self.assertEqual(parsed["record_count"], 0)

    def test_import_supported_broker_registry(self) -> None:
        items = self.import_service.list_supported_brokers()
        broker_map = {item["broker"]: item for item in items}
        # Chỉ còn parser chung cho Việt Nam / Only the generic Vietnam parser remains.
        self.assertEqual(set(broker_map), {"generic"})
        self.assertIn("vn", broker_map["generic"]["aliases"])
        self.assertIn("default", broker_map["generic"]["aliases"])

    def test_import_preserves_fund_certificate_symbol(self) -> None:
        csv_text = (
            "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp,Số hiệu lệnh\n"
            "2026-01-02,FUEVN100,Mua,10,100,HT-003\n"
        )
        parsed = self.import_service.parse_trade_csv(
            broker="generic",
            content=csv_text.encode("utf-8"),
        )
        self.assertEqual(parsed["record_count"], 1)
        self.assertEqual(parsed["records"][0]["symbol"], "FUEVN100")

    def test_import_dry_run_counts_in_file_duplicates(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        csv_text = (
            "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp,Số hiệu lệnh\n"
            "2026-01-02,HPG,Mua,10,100,HT-004\n"
            "2026-01-02,HPG,Mua,10,100,HT-004\n"
        )
        parsed = self.import_service.parse_trade_csv(
            broker="generic",
            content=csv_text.encode("utf-8"),
        )
        result = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=parsed["records"],
            dry_run=True,
        )
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["inserted_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)

    def test_import_allows_identical_split_fills_without_trade_uid(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        csv_text = (
            "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp\n"
            "2026-01-02,HPG,Mua,10,100\n"
            "2026-01-02,HPG,Mua,10,100\n"
        )
        parsed = self.import_service.parse_trade_csv(
            broker="generic",
            content=csv_text.encode("utf-8"),
        )
        self.assertEqual(parsed["record_count"], 2)
        self.assertEqual(len({item["dedup_hash"] for item in parsed["records"]}), 2)

        first_commit = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=parsed["records"],
        )
        second_commit = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=parsed["records"],
        )

        self.assertEqual(first_commit["inserted_count"], 2)
        self.assertEqual(first_commit["duplicate_count"], 0)
        self.assertEqual(second_commit["inserted_count"], 0)
        self.assertEqual(second_commit["duplicate_count"], 2)

    def test_import_oversell_counts_failed_not_duplicate(self) -> None:
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

        result = self.import_service.commit_trade_records(
            account_id=aid,
            broker="generic",
            records=[
                {
                    "trade_date": "2026-01-02",
                    "symbol": "HPG",
                    "side": "sell",
                    "quantity": 20,
                    "price": 90,
                    "fee": 0.0,
                    "tax": 0.0,
                    "trade_uid": "HT-SELL-001",
                    "dedup_hash": "oversell-hash-001",
                    "market": "vn",
                    "currency": "VND",
                }
            ],
        )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["duplicate_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(result["errors"]), 1)

    def test_import_busy_counts_failed_not_duplicate(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]

        with patch.object(
            self.import_service.portfolio_service,
            "record_trade",
            side_effect=PortfolioBusyError("Portfolio ledger is busy; please retry shortly."),
        ):
            result = self.import_service.commit_trade_records(
                account_id=aid,
                broker="generic",
                records=[
                    {
                        "trade_date": "2026-01-02",
                        "symbol": "HPG",
                        "side": "buy",
                        "quantity": 10,
                        "price": 90,
                        "fee": 0.0,
                        "tax": 0.0,
                        "trade_uid": "HT-BUSY-001",
                        "dedup_hash": "busy-hash-001",
                        "market": "vn",
                        "currency": "VND",
                    }
                ],
            )

        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(result["duplicate_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("portfolio_busy", result["errors"][0])

    def test_risk_threshold_boundary(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=20000,
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=100,
            market="vn",
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="VCB",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=20,
            market="vn",
            currency="VND",
        )

        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("VCB", date(2026, 1, 1), 20.0)
        self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 1), cost_method="fifo")

        self._save_close("HPG", date(2026, 1, 2), 70.0)
        self._save_close("VCB", date(2026, 1, 2), 20.0)
        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")

        self.assertTrue(report["concentration"]["alert"])
        self.assertTrue(report["drawdown"]["alert"])
        self.assertTrue(report["stop_loss"]["near_alert"])
        self.assertGreaterEqual(report["stop_loss"]["triggered_count"], 1)
        self.assertAlmostEqual(report["thresholds"]["drawdown_alert_pct"], 10.0, places=6)

    def test_risk_drawdown_backfills_snapshot_window_on_first_call(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=20000,
            currency="VND",
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=100,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("HPG", date(2026, 1, 2), 70.0)

        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")
        self.assertGreaterEqual(report["drawdown"]["series_points"], 2)
        self.assertGreater(report["drawdown"]["max_drawdown_pct"], 10.0)
        self.assertTrue(report["drawdown"]["alert"])

    def test_risk_stop_loss_ignores_missing_price_positions(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        self._create_position(aid, "HPG", price=100.0)
        self.service.record_trade(
            account_id=aid,
            symbol="VCB",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=10,
            price=50,
            market="vn",
            currency="VND",
        )
        # Ngưỡng cắt lỗ test = 25%: HPG lỗ 30% -> kích hoạt; VCB thiếu giá -> bỏ qua.
        self._save_close("HPG", date(2026, 1, 2), 70.0)

        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")

        symbols = [item["symbol"] for item in report["stop_loss"]["items"]]
        self.assertIn("HPG", symbols)
        self.assertNotIn("VCB", symbols)
        self.assertEqual(report["stop_loss"]["triggered_count"], 1)

    def test_risk_drawdown_backfill_skips_weekends(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        self._create_position(aid, "HPG", price=100.0)
        self._save_close("HPG", date(2026, 1, 5), 100.0)

        original = PortfolioService.get_portfolio_snapshot
        calls = []

        def _spy(service_self, *args, **kwargs):
            calls.append(kwargs.get("as_of"))
            return original(service_self, *args, **kwargs)

        with patch.object(PortfolioService, "get_portfolio_snapshot", autospec=True, side_effect=_spy):
            report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")

        # 01-01 (thứ 5) .. 01-05 (thứ 2): thứ 7 01-03 và CN 01-04 không được replay.
        self.assertNotIn(date(2026, 1, 3), calls)
        self.assertNotIn(date(2026, 1, 4), calls)
        self.assertIn(date(2026, 1, 2), calls)
        self.assertEqual(report["drawdown"]["series_points"], 3)

    def test_risk_drawdown_excludes_missing_price_days(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        self.service.record_cash_ledger(
            account_id=aid, event_date=date(2026, 1, 1), direction="in", amount=20000, currency="VND"
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=100,
            market="vn",
            currency="VND",
        )
        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("HPG", date(2026, 1, 5), 100.0)
        # Mã VCB mua 01-02 không có giá đến 01-05 -> 01-02 thiếu giá; equity bị đánh giá thấp.
        self.service.record_trade(
            account_id=aid,
            symbol="VCB",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=100,
            price=50,
            market="vn",
            currency="VND",
        )
        self._save_close("VCB", date(2026, 1, 5), 50.0)

        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")

        self.assertEqual(report["drawdown"]["excluded_points"], 1)
        self.assertAlmostEqual(report["drawdown"]["max_drawdown_pct"], 0.0, places=6)
        self.assertFalse(report["drawdown"]["alert"])

    def _seed_hpg_account(self, name: str = "Main") -> int:
        # Nạp 20.000, mua HPG 100 x 100 ngày 01-01 -> equity 20.000 (tiền 10.000 + cổ phiếu 10.000).
        aid = self.service.create_account(name=name, broker="Demo", market="vn", base_currency="VND")["id"]
        self.service.record_cash_ledger(
            account_id=aid, event_date=date(2026, 1, 1), direction="in", amount=20000, currency="VND"
        )
        self.service.record_trade(
            account_id=aid,
            symbol="HPG",
            trade_date=date(2026, 1, 1),
            side="buy",
            quantity=100,
            price=100,
            market="vn",
            currency="VND",
        )
        return aid

    def test_risk_drawdown_ignores_withdrawal(self) -> None:
        aid = self._seed_hpg_account()
        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("HPG", date(2026, 1, 2), 100.0)
        # Rút 10.000 khi giá không đổi: equity 20.000 -> 10.000 nhưng không lỗ đồng nào.
        self.service.record_cash_ledger(
            account_id=aid, event_date=date(2026, 1, 2), direction="out", amount=10000, currency="VND"
        )

        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 2), cost_method="fifo")

        self.assertEqual(report["drawdown"]["method"], "time_weighted")
        self.assertAlmostEqual(report["drawdown"]["max_drawdown_pct"], 0.0, places=6)
        self.assertAlmostEqual(report["drawdown"]["net_external_flow"], -10000.0, places=6)
        self.assertFalse(report["drawdown"]["alert"])

    def test_risk_drawdown_deposit_does_not_hide_loss(self) -> None:
        aid = self._seed_hpg_account()
        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("HPG", date(2026, 1, 2), 80.0)
        self._save_close("HPG", date(2026, 1, 5), 80.0)
        # 01-02: lỗ 2.000 / 20.000 = 10%. 01-05: nạp 10.000, giá giữ 80 -> vẫn sụt 10%.
        self.service.record_cash_ledger(
            account_id=aid, event_date=date(2026, 1, 5), direction="in", amount=10000, currency="VND"
        )

        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")

        self.assertAlmostEqual(report["drawdown"]["current_drawdown_pct"], 10.0, places=4)
        self.assertAlmostEqual(report["drawdown"]["max_drawdown_pct"], 10.0, places=4)

    def test_risk_drawdown_flow_on_non_trading_day_carries_forward(self) -> None:
        aid = self._seed_hpg_account()
        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("HPG", date(2026, 1, 5), 100.0)
        # Rút tiền thứ 7 (01-03, không có snapshot) -> tính vào phiên thứ 2 (01-05), không tạo sụt giảm.
        self.service.record_cash_ledger(
            account_id=aid, event_date=date(2026, 1, 3), direction="out", amount=5000, currency="VND"
        )

        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")

        self.assertAlmostEqual(report["drawdown"]["max_drawdown_pct"], 0.0, places=6)
        self.assertAlmostEqual(report["drawdown"]["net_external_flow"], -5000.0, places=6)

    def test_risk_drawdown_all_accounts_transfer_cancels_out(self) -> None:
        aid_a = self._seed_hpg_account("A")
        aid_b = self._seed_hpg_account("B")
        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("HPG", date(2026, 1, 2), 100.0)
        # Chuyển 8.000 từ A sang B cùng ngày: tổng danh mục không đổi.
        self.service.record_cash_ledger(
            account_id=aid_a, event_date=date(2026, 1, 2), direction="out", amount=8000, currency="VND"
        )
        self.service.record_cash_ledger(
            account_id=aid_b, event_date=date(2026, 1, 2), direction="in", amount=8000, currency="VND"
        )

        report = self.risk_service.get_risk_report(account_id=None, as_of=date(2026, 1, 2), cost_method="fifo")
        report_a = self.risk_service.get_risk_report(account_id=aid_a, as_of=date(2026, 1, 2), cost_method="fifo")

        self.assertAlmostEqual(report["drawdown"]["max_drawdown_pct"], 0.0, places=6)
        self.assertAlmostEqual(report["drawdown"]["net_external_flow"], 0.0, places=6)
        self.assertAlmostEqual(report_a["drawdown"]["max_drawdown_pct"], 0.0, places=6)

    def test_import_parses_vietnamese_dates_and_numbers(self) -> None:
        csv_text = (
            "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp,Phí\n"
            "05/03/2026,HPG,Mua,1.000,\"25.300\",\"37,95\"\n"
        )
        parsed = self.import_service.parse_trade_csv(broker="generic", content=csv_text.encode("utf-8"))
        record = parsed["records"][0]
        self.assertEqual(record["trade_date"], date(2026, 3, 5))
        self.assertAlmostEqual(record["quantity"], 1000.0)
        self.assertAlmostEqual(record["price"], 25300.0)
        self.assertAlmostEqual(record["fee"], 37.95)

    def test_import_reads_cp1258_and_utf16_exports(self) -> None:
        csv_text = "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp\n2026-01-02,HPG,Bán,10,100\n"
        cp1258_bytes = _encode_cp1258(csv_text)
        with self.assertRaises(UnicodeDecodeError):
            cp1258_bytes.decode("utf-8")
        utf16_bytes = csv_text.replace(",", "\t").encode("utf-16")

        for content in (cp1258_bytes, utf16_bytes):
            with self.subTest(size=len(content)):
                parsed = self.import_service.parse_trade_csv(broker="generic", content=content)
                self.assertEqual(parsed["record_count"], 1)
                self.assertEqual(parsed["records"][0]["side"], "sell")
                self.assertEqual(parsed["records"][0]["symbol"], "HPG")

    def test_import_commits_newest_first_file_in_date_order(self) -> None:
        aid = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")["id"]
        csv_text = (
            "Ngày giao dịch,Mã CK,Loại lệnh,Khối lượng khớp,Giá khớp\n"
            "05/01/2026,HPG,Bán,10,110\n"
            "02/01/2026,HPG,Mua,10,100\n"
        )
        parsed = self.import_service.parse_trade_csv(broker="generic", content=csv_text.encode("utf-8"))
        result = self.import_service.commit_trade_records(
            account_id=aid, broker="generic", records=parsed["records"]
        )
        self.assertEqual(result["inserted_count"], 2, result["errors"])
        self.assertEqual(result["failed_count"], 0)

    def test_sector_concentration_marks_vn_positions_unclassified(self) -> None:
        account = self.service.create_account(name="VN", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=100000.0,
            currency="VND",
        )
        self._create_position(aid, "HPG")
        # FiinTrade chưa cấu hình -> không có nguồn ICB.
        with patch("data_provider.fiintrade_fetcher.is_fiintrade_enabled", return_value=False):
            report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 1), cost_method="fifo")
        self.assertIn("sector_concentration", report)
        sectors = report["sector_concentration"]["top_sectors"]
        self.assertTrue(len(sectors) >= 1)
        self.assertEqual(sectors[0]["sector"], "UNCLASSIFIED")
        self.assertEqual(report["sector_concentration"]["coverage"]["unclassified_count"], 1)

    def test_sector_concentration_groups_positions_by_icb_supersector(self) -> None:
        account = self.service.create_account(name="VN", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid, event_date=date(2026, 1, 1), direction="in", amount=100000.0, currency="VND",
        )
        for symbol in ("HPG", "VCB", "CTG", "ZZZ"):
            self._create_position(aid, symbol)

        class FakeSectorFetcher:
            icb = {"HPG": "1757", "VCB": "8355", "CTG": "8355"}

            def get_icb_code(self, symbol: str):
                return self.icb.get(symbol)

        risk_service = PortfolioRiskService(portfolio_service=self.service, sector_fetcher=FakeSectorFetcher())
        report = risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 1), cost_method="fifo")
        block = report["sector_concentration"]
        by_sector = {row["sector"]: row for row in block["top_sectors"]}
        self.assertEqual(by_sector["Ngân hàng"]["symbol_count"], 2)
        self.assertEqual(by_sector["Tài nguyên cơ bản"]["symbol_count"], 1)
        self.assertEqual(by_sector["UNCLASSIFIED"]["symbol_count"], 1)
        self.assertEqual(block["top_sectors"][0]["sector"], "Ngân hàng")
        self.assertEqual(block["coverage"], {"classified_count": 3, "unclassified_count": 1, "failed_count": 0})

    def test_sector_lookup_failure_does_not_break_risk_report(self) -> None:
        account = self.service.create_account(name="VN", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid, event_date=date(2026, 1, 1), direction="in", amount=100000.0, currency="VND",
        )
        self._create_position(aid, "HPG")

        class BrokenSectorFetcher:
            def get_icb_code(self, symbol: str):
                raise RuntimeError("down")

        risk_service = PortfolioRiskService(portfolio_service=self.service, sector_fetcher=BrokenSectorFetcher())
        report = risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 1), cost_method="fifo")
        block = report["sector_concentration"]
        self.assertEqual(block["top_sectors"][0]["sector"], "UNCLASSIFIED")
        self.assertEqual(block["coverage"]["failed_count"], 1)
        self.assertEqual(len(block["errors"]), 1)

    def test_risk_report_aggregates_active_defensive_decision_signals_for_holdings(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=100000.0,
            currency="VND",
        )
        for symbol in ("HPG", "FPT", "VCB", "MWG", "VNM"):
            self._create_position(aid, symbol)

        self._create_signal("HPG.VN", "sell")
        self._create_signal("FPT", "reduce")
        self._create_signal("VCB", "alert")
        self._create_signal("MWG", "buy")
        self._create_signal("VNM", "watch")
        self._create_signal("TCB", "sell")
        self._create_signal("HPG", "alert", status="expired", trace_id="expired-alert-HPG")

        report = self.risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 1), cost_method="fifo")

        block = report["decision_signal_risk"]
        self.assertTrue(block["available"])
        self.assertEqual(block["total"], 3)
        self.assertEqual(block["actions"], {"sell": 1, "reduce": 1, "alert": 1})
        symbols = {item["symbol"] for item in block["items"]}
        self.assertEqual(symbols, {"HPG", "FPT", "VCB"})
        signal_actions = {item["symbol"]: item["signal"]["action"] for item in block["items"]}
        self.assertNotIn("MWG", signal_actions)
        self.assertNotIn("VNM", signal_actions)
        self.assertNotIn("TCB", signal_actions)
        self.assertEqual(signal_actions["HPG"], "sell")
        self.assertEqual(signal_actions["FPT"], "reduce")
        self.assertEqual(signal_actions["VCB"], "alert")

    def test_risk_report_uses_requested_snapshot_for_decision_signal_filters(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=100000.0,
            currency="VND",
        )
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
        self._save_close("HPG", date(2026, 1, 1), 100.0)
        self._save_close("HPG", date(2026, 1, 2), 100.0)
        self.service.record_trade(
            account_id=aid,
            symbol="VCB",
            trade_date=date(2026, 1, 2),
            side="buy",
            quantity=10,
            price=20,
            market="vn",
            currency="VND",
        )
        self._save_close("VCB", date(2026, 1, 2), 20.0)
        self._create_signal("VCB", "sell")

        original = self.risk_service.decision_signal_service.list_signals
        with patch.object(
            self.risk_service.decision_signal_service,
            "list_signals",
            wraps=original,
        ) as spy:
            report = self.risk_service.get_risk_report(
                account_id=aid,
                as_of=date(2026, 1, 2),
                cost_method="fifo",
            )

        block = report["decision_signal_risk"]
        self.assertTrue(block["available"])
        self.assertEqual(block["total"], 1)
        self.assertEqual(block["items"][0]["symbol"], "VCB")
        self.assertEqual(block["items"][0]["signal"]["action"], "sell")
        for call in spy.mock_calls:
            self.assertIsNot(call.kwargs.get("holding_only"), True)
        identity_calls = [
            call.kwargs.get("stock_identities")
            for call in spy.mock_calls
            if call.kwargs.get("stock_identities") is not None
        ]
        observed_identities = {
            identity
            for ids in identity_calls
            for identity in ids
        }
        self.assertIn(("vn", "VCB"), observed_identities)
        self.assertIn(("vn", "HPG"), observed_identities)

    def test_risk_report_decision_signal_fail_open(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000.0,
            currency="VND",
        )
        self._create_position(aid, "HPG")

        class BrokenSignalService:
            def list_signals(self, **_kwargs):
                raise RuntimeError("decision signal unavailable")

        risk_service = PortfolioRiskService(
            portfolio_service=self.service,
            decision_signal_service=BrokenSignalService(),
        )
        report = risk_service.get_risk_report(account_id=aid, as_of=date(2026, 1, 1), cost_method="fifo")

        self.assertFalse(report["decision_signal_risk"]["available"])
        self.assertEqual(report["decision_signal_risk"]["total"], 0)
        self.assertEqual(report["decision_signal_risk"]["items"], [])

    def test_portfolio_risk_endpoint_returns_defensive_decision_signals(self) -> None:
        account = self.service.create_account(name="Main", broker="Demo", market="vn", base_currency="VND")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=10000.0,
            currency="VND",
        )
        self._create_position(aid, "HPG")
        self._create_signal("HPG", "sell")

        response = self.client.get(
            "/api/v1/portfolio/risk",
            params={"account_id": aid, "as_of": "2026-01-01", "cost_method": "fifo"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("decision_signal_risk", payload)
        self.assertEqual(payload["decision_signal_risk"]["total"], 1)
        self.assertEqual(payload["decision_signal_risk"]["items"][0]["signal"]["action"], "sell")

    def test_import_and_risk_endpoints(self) -> None:
        create_resp = self.client.post(
            "/api/v1/portfolio/accounts",
            json={"name": "Main", "broker": "Demo", "market": "vn", "base_currency": "VND"},
        )
        self.assertEqual(create_resp.status_code, 200)
        account_id = create_resp.json()["id"]

        import_resp = self.client.post(
            "/api/v1/portfolio/imports/csv/commit",
            data={"account_id": str(account_id), "broker": "generic", "dry_run": "false"},
            files={"file": ("generic.csv", self._csv_bytes(with_trade_uid=True), "text/csv")},
        )
        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["inserted_count"], 1)

        self._save_close("HPG", date(2026, 1, 2), 95.0)
        self.service.get_portfolio_snapshot(account_id=account_id, as_of=date(2026, 1, 2), cost_method="fifo")
        risk_resp = self.client.get(
            "/api/v1/portfolio/risk",
            params={"account_id": account_id, "as_of": "2026-01-02", "cost_method": "fifo"},
        )
        self.assertEqual(risk_resp.status_code, 200)
        payload = risk_resp.json()
        self.assertEqual(payload["cost_method"], "fifo")
        self.assertIn("concentration", payload)
        self.assertIn("sector_concentration", payload)
        self.assertIn("drawdown", payload)
        self.assertIn("stop_loss", payload)


if __name__ == "__main__":
    unittest.main()
