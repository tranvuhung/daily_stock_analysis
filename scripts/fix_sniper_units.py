#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sửa một lần các giá điểm bắn tỉa bị lưu sai đơn vị (nghìn đồng thay vì VND).

One-off repair for sniper prices stored in thousands of VND instead of VND.

Bối cảnh / Background:

- Parser cũ đọc "51.500 VND" thành 51.5 (dấu "." hàng nghìn bị coi là thập phân),
  nên ``analysis_history.ideal_buy/secondary_buy/stop_loss/take_profit`` và
  ``decision_signals.entry_low/entry_high/stop_loss/target_price`` bị nhỏ hơn 1000 lần.
  The old parser read "51.500 VND" as 51.5, so these columns are 1000x too small.
- Backtest so stop/take-profit (nghìn) với giá đóng cửa (VND) nên kết quả cũ sai;
  dùng ``--rerun-backtest`` để tính lại các mã bị ảnh hưởng.
  Backtests compared those values with VND closes; ``--rerun-backtest`` recomputes them.

Quy tắc quy đổi dùng chung ``normalize_price_unit`` với runtime: chỉ nhân 1000 khi giá
trị < giá hiện tại lúc phân tích / 100 và kết quả nằm gần giá đó.
Uses the same ``normalize_price_unit`` rule as runtime.

Usage:

    python scripts/fix_sniper_units.py                    # dry-run, chỉ in thay đổi
    python scripts/fix_sniper_units.py --apply            # backup DB rồi ghi
    python scripts/fix_sniper_units.py --apply --rerun-backtest
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.storage import AnalysisHistory, DatabaseManager, DecisionSignalRecord  # noqa: E402
from src.utils.sniper_points import normalize_price_unit  # noqa: E402

HISTORY_FIELDS = ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")
SIGNAL_FIELDS = ("entry_low", "entry_high", "stop_loss", "target_price")


def _reference_price(raw_result: Optional[str]) -> Optional[float]:
    """Giá hiện tại lúc phân tích từ raw_result / report-time price from raw_result."""
    if not raw_result:
        return None
    try:
        data = json.loads(raw_result)
    except (TypeError, ValueError):
        return None
    value = data.get("current_price") if isinstance(data, dict) else None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _plan_changes(row: Any, fields: Tuple[str, ...], reference: Optional[float]) -> Dict[str, Tuple[float, float]]:
    changes: Dict[str, Tuple[float, float]] = {}
    if reference is None:
        return changes
    for field in fields:
        old = getattr(row, field, None)
        if old is None:
            continue
        new = normalize_price_unit(float(old), reference)
        if new is not None and new != old:
            changes[field] = (float(old), float(new))
    return changes


def _backup_sqlite(db: DatabaseManager) -> Optional[Path]:
    """Sao lưu file SQLite trước khi ghi / back up the SQLite file before writing."""
    url = str(getattr(db, "_db_url", ""))
    if not url.startswith("sqlite:///"):
        print(f"[cảnh báo] Không phải SQLite ({url}); hãy tự sao lưu trước khi --apply.")
        return None
    db_path = Path(url[len("sqlite:///"):])
    backup = db_path.with_name(f"{db_path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{db_path.suffix}")
    # Gộp WAL vào file chính để bản sao đầy đủ / checkpoint WAL so the copy is complete.
    with db.session_scope() as session:
        session.connection().exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    shutil.copy2(db_path, backup)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Ghi thay đổi (mặc định chỉ dry-run)")
    parser.add_argument(
        "--rerun-backtest",
        action="store_true",
        help="Sau khi --apply, tính lại backtest (force) cho các mã bị ảnh hưởng",
    )
    args = parser.parse_args()

    db = DatabaseManager.get_instance()
    affected_codes: set[str] = set()
    history_updates: List[Tuple[int, Dict[str, Tuple[float, float]]]] = []
    signal_updates: List[Tuple[int, Dict[str, Tuple[float, float]]]] = []

    with db.session_scope() as session:
        references: Dict[int, Optional[float]] = {}
        for row in session.query(AnalysisHistory).all():
            reference = _reference_price(row.raw_result)
            references[row.id] = reference
            changes = _plan_changes(row, HISTORY_FIELDS, reference)
            if changes:
                history_updates.append((row.id, changes))
                affected_codes.add(row.code)
                print(f"analysis_history #{row.id} {row.code} (giá {reference:g}): "
                      + ", ".join(f"{k} {o:g} -> {n:g}" for k, (o, n) in changes.items()))

        signals = (
            session.query(DecisionSignalRecord)
            .filter(DecisionSignalRecord.source_type == "analysis")
            .filter(DecisionSignalRecord.source_report_id.isnot(None))
            .all()
        )
        for signal in signals:
            changes = _plan_changes(signal, SIGNAL_FIELDS, references.get(signal.source_report_id))
            if changes:
                signal_updates.append((signal.id, changes))
                print(f"decision_signals #{signal.id} {signal.stock_code} (report #{signal.source_report_id}): "
                      + ", ".join(f"{k} {o:g} -> {n:g}" for k, (o, n) in changes.items()))

    print(f"\nTổng: {len(history_updates)} analysis_history, {len(signal_updates)} decision_signals cần sửa; "
          f"mã bị ảnh hưởng: {', '.join(sorted(affected_codes)) or '-'}")

    if not args.apply:
        print("Dry-run: chưa ghi gì. Chạy lại với --apply để áp dụng.")
        return 0
    if not history_updates and not signal_updates:
        print("Không có gì để sửa.")
        return 0

    backup = _backup_sqlite(db)
    if backup:
        print(f"Đã sao lưu DB: {backup}")

    with db.session_scope() as session:
        for row_id, changes in history_updates:
            row = session.get(AnalysisHistory, row_id)
            for field, (_, new) in changes.items():
                setattr(row, field, new)
        for row_id, changes in signal_updates:
            row = session.get(DecisionSignalRecord, row_id)
            for field, (_, new) in changes.items():
                setattr(row, field, new)
    print("Đã ghi thay đổi.")

    if args.rerun_backtest and affected_codes:
        from src.services.backtest_service import BacktestService

        service = BacktestService(db)
        for code in sorted(affected_codes):
            stats = service.run_backtest(code=code, force=True, limit=1000)
            print(f"Backtest {code}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
