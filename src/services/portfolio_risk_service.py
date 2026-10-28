# -*- coding: utf-8 -*-
"""Portfolio risk service for concentration, drawdown and stop-loss proximity."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config, get_config
from src.core.icb_sectors import icb_supersector_name
from src.core.trading_calendar import is_market_open
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.decision_signal_service import DecisionSignalService
from src.services.decision_signal_summary import summarize_decision_signal
from src.services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

DEFENSIVE_DECISION_SIGNAL_ACTIONS = ("sell", "reduce", "alert")


class PortfolioRiskService:
    """Compute portfolio risk blocks on top of replayed snapshot data."""

    def __init__(
        self,
        *,
        repo: Optional[PortfolioRepository] = None,
        portfolio_service: Optional[PortfolioService] = None,
        decision_signal_service: Optional[DecisionSignalService] = None,
        config: Optional[Config] = None,
        sector_fetcher: Optional[Any] = None,
    ):
        self.repo = repo or PortfolioRepository()
        # Nguồn mã ICB (cần ``get_icb_code``); None thì khởi tạo FiinTrade khi cần.
        # ICB code source (needs ``get_icb_code``); lazily uses FiinTrade when None.
        self._sector_fetcher = sector_fetcher
        self.portfolio_service = portfolio_service or PortfolioService(repo=self.repo)
        self.decision_signal_service = decision_signal_service or DecisionSignalService(portfolio_repo=self.repo)
        self.config = config or get_config()

    def get_risk_report(
        self,
        *,
        account_id: Optional[int] = None,
        as_of: Optional[date] = None,
        cost_method: str = "fifo",
        include_realtime: bool = True,
    ) -> Dict[str, Any]:
        as_of_date = as_of or date.today()
        snapshot = self.portfolio_service.get_portfolio_snapshot(
            account_id=account_id,
            as_of=as_of_date,
            cost_method=cost_method,
            include_realtime=include_realtime,
        )

        thresholds = {
            "concentration_alert_pct": float(getattr(self.config, "portfolio_risk_concentration_alert_pct", 35.0)),
            "drawdown_alert_pct": float(getattr(self.config, "portfolio_risk_drawdown_alert_pct", 15.0)),
            "stop_loss_alert_pct": float(getattr(self.config, "portfolio_risk_stop_loss_alert_pct", 10.0)),
            "stop_loss_near_ratio": float(getattr(self.config, "portfolio_risk_stop_loss_near_ratio", 0.8)),
            "lookback_days": int(getattr(self.config, "portfolio_risk_lookback_days", 180)),
        }

        concentration = self._build_concentration(
            snapshot,
            thresholds["concentration_alert_pct"],
            as_of_date=as_of_date,
        )
        sector_concentration = self._build_sector_concentration(
            snapshot,
            thresholds["concentration_alert_pct"],
            as_of_date=as_of_date,
        )
        self._ensure_drawdown_snapshot_window(
            account_id=account_id,
            as_of_date=as_of_date,
            cost_method=cost_method,
            lookback_days=thresholds["lookback_days"],
            include_realtime=include_realtime,
        )
        drawdown = self._build_drawdown(
            account_id=account_id,
            as_of_date=as_of_date,
            cost_method=cost_method,
            threshold_pct=thresholds["drawdown_alert_pct"],
            lookback_days=thresholds["lookback_days"],
        )
        stop_loss = self._build_stop_loss(snapshot, thresholds)
        decision_signal_risk = self._build_decision_signal_risk(snapshot)

        return {
            "as_of": as_of_date.isoformat(),
            "account_id": account_id,
            "cost_method": cost_method,
            "currency": snapshot["currency"],
            "thresholds": thresholds,
            "concentration": concentration,
            "sector_concentration": sector_concentration,
            "drawdown": drawdown,
            "stop_loss": stop_loss,
            "decision_signal_risk": decision_signal_risk,
        }

    def _build_decision_signal_risk(
        self,
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            held_positions = self._held_position_identities(snapshot)
            if not held_positions:
                return self._empty_decision_signal_risk(available=True)
            stock_identities = sorted({
                (position["market"], position["signal_stock_code"])
                for position in held_positions
            })

            defensive_actions = set(DEFENSIVE_DECISION_SIGNAL_ACTIONS)
            latest_by_identity: Dict[Tuple[str, str], Dict[str, Any]] = {}
            page = 1
            while True:
                response = self.decision_signal_service.list_signals(
                    stock_identities=stock_identities,
                    status="active",
                    page=page,
                    page_size=100,
                )
                items = response.get("items", []) if isinstance(response, dict) else []
                for item in items:
                    if str(item.get("action") or "") not in defensive_actions:
                        continue
                    key = (
                        str(item.get("market") or "").strip().lower(),
                        str(item.get("stock_code") or "").strip().upper(),
                    )
                    if key[0] and key[1] and key not in latest_by_identity:
                        latest_by_identity[key] = item
                total = int(response.get("total", 0) or 0) if isinstance(response, dict) else 0
                if page * 100 >= total or not items:
                    break
                page += 1

            risk_items: List[Dict[str, Any]] = []
            action_counts = {action: 0 for action in DEFENSIVE_DECISION_SIGNAL_ACTIONS}
            seen: set[Tuple[Optional[int], str, str, int]] = set()
            for position in held_positions:
                signal = latest_by_identity.get((position["market"], position["signal_stock_code"]))
                summary = summarize_decision_signal(signal)
                if not summary:
                    continue
                action = str(summary.get("action") or "")
                if action not in action_counts:
                    continue
                signal_id = int(summary.get("id") or 0)
                dedupe_key = (position["account_id"], position["market"], position["signal_stock_code"], signal_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                action_counts[action] += 1
                risk_items.append({
                    "account_id": position["account_id"],
                    "symbol": position["symbol"],
                    "market": position["market"],
                    "signal": summary,
                })

            return {
                "available": True,
                "total": len(risk_items),
                "actions": action_counts,
                "items": risk_items,
            }
        except Exception:
            logger.exception("[PortfolioRiskService] Decision signal risk unavailable")
            return self._empty_decision_signal_risk(available=False)

    @staticmethod
    def _empty_decision_signal_risk(*, available: bool) -> Dict[str, Any]:
        return {
            "available": available,
            "total": 0,
            "actions": {action: 0 for action in DEFENSIVE_DECISION_SIGNAL_ACTIONS},
            "items": [],
        }

    @staticmethod
    def _held_position_identities(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        positions: List[Dict[str, Any]] = []
        for account in snapshot.get("accounts", []) or []:
            account_id = account.get("account_id")
            for pos in account.get("positions", []) or []:
                symbol = str(pos.get("symbol") or "").strip().upper()
                market = str(pos.get("market") or "").strip().lower()
                if not symbol or market != "vn":
                    continue
                signal_stock_code = DecisionSignalService.normalize_stock_code_for_signal(symbol, market=market)
                positions.append({
                    "account_id": account_id,
                    "symbol": symbol,
                    "market": market,
                    "signal_stock_code": signal_stock_code,
                })
        return positions

    def _ensure_drawdown_snapshot_window(
        self,
        *,
        account_id: Optional[int],
        as_of_date: date,
        cost_method: str,
        lookback_days: int,
        include_realtime: bool,
    ) -> None:
        if lookback_days <= 0:
            return

        start_date = self._resolve_backfill_start_date(
            account_id=account_id,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
        )
        if start_date > as_of_date:
            return

        existing_rows = self.repo.list_daily_snapshots_for_risk(
            as_of=as_of_date,
            cost_method=cost_method,
            account_id=account_id,
            lookback_days=lookback_days,
        )
        if account_id is not None:
            existing_dates = {row.snapshot_date for row in existing_rows if int(row.account_id) == int(account_id)}
            current_date = start_date
            while current_date <= as_of_date:
                # Bỏ qua ngày không giao dịch (thứ 7/CN): giá trị trùng phiên trước, chỉ tốn replay.
                # Skip non-trading days: they duplicate the previous session and cost a replay each.
                if current_date not in existing_dates and is_market_open("vn", current_date):
                    self.portfolio_service.get_portfolio_snapshot(
                        account_id=account_id,
                        as_of=current_date,
                        cost_method=cost_method,
                        include_realtime=include_realtime,
                    )
                    existing_dates.add(current_date)
                current_date += timedelta(days=1)
            return

        account_ids = [int(account.id) for account in self.repo.list_accounts(include_inactive=False)]
        if not account_ids:
            return
        existing_pairs = {(int(row.account_id), row.snapshot_date) for row in existing_rows}
        current_date = start_date
        while current_date <= as_of_date:
            if is_market_open("vn", current_date) and not all(
                (aid, current_date) in existing_pairs for aid in account_ids
            ):
                self.portfolio_service.get_portfolio_snapshot(
                    account_id=None,
                    as_of=current_date,
                    cost_method=cost_method,
                    include_realtime=include_realtime,
                )
                for aid in account_ids:
                    existing_pairs.add((aid, current_date))
            current_date += timedelta(days=1)

    def _resolve_backfill_start_date(
        self,
        *,
        account_id: Optional[int],
        as_of_date: date,
        lookback_days: int,
    ) -> date:
        window_start = as_of_date - timedelta(days=lookback_days)
        if account_id is not None:
            first_activity = self.repo.get_first_activity_date(account_id=account_id, as_of=as_of_date)
            return max(window_start, first_activity or as_of_date)

        first_activity_candidates: List[date] = []
        for account in self.repo.list_accounts(include_inactive=False):
            first_activity = self.repo.get_first_activity_date(account_id=int(account.id), as_of=as_of_date)
            if first_activity is not None:
                first_activity_candidates.append(first_activity)
        if not first_activity_candidates:
            return as_of_date
        return max(window_start, min(first_activity_candidates))

    def _build_concentration(self, snapshot: Dict[str, Any], threshold_pct: float, *, as_of_date: date) -> Dict[str, Any]:
        total_mv = float(snapshot.get("total_market_value", 0.0) or 0.0)
        exposure_by_symbol: Dict[str, float] = {}
        for account in snapshot.get("accounts", []):
            for pos in account.get("positions", []):
                symbol = str(pos.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                market_value = float(pos.get("market_value_base") or 0.0)
                valuation_currency = str(pos.get("valuation_currency") or account.get("base_currency") or "VND")
                converted, _, _ = self.portfolio_service.convert_amount(
                    amount=market_value,
                    from_currency=valuation_currency,
                    to_currency="VND",
                    as_of_date=as_of_date,
                )
                exposure_by_symbol[symbol] = exposure_by_symbol.get(symbol, 0.0) + converted

        rows = []
        for symbol, exposure in exposure_by_symbol.items():
            weight = (exposure / total_mv * 100.0) if total_mv > 0 else 0.0
            rows.append(
                {
                    "symbol": symbol,
                    "market_value_base": round(exposure, 6),
                    "weight_pct": round(weight, 4),
                    "is_alert": bool(weight >= threshold_pct),
                }
            )
        rows.sort(key=lambda item: item["market_value_base"], reverse=True)

        top_weight = rows[0]["weight_pct"] if rows else 0.0
        return {
            "total_market_value": round(total_mv, 6),
            "top_weight_pct": round(float(top_weight), 4),
            "alert": bool(top_weight >= threshold_pct),
            "top_positions": rows[:10],
        }

    def _build_sector_concentration(
        self,
        snapshot: Dict[str, Any],
        threshold_pct: float,
        *,
        as_of_date: date,
    ) -> Dict[str, Any]:
        total_mv = float(snapshot.get("total_market_value", 0.0) or 0.0)
        sector_exposure: Dict[str, float] = {}
        sector_symbols: Dict[str, set] = {}
        coverage = {
            "classified_count": 0,
            "unclassified_count": 0,
            "failed_count": 0,
        }
        errors: List[str] = []
        board_cache: Dict[Tuple[str, str], str] = {}

        for account in snapshot.get("accounts", []):
            for pos in account.get("positions", []):
                symbol = str(pos.get("symbol") or "").strip().upper()
                market = str(pos.get("market") or account.get("market") or "").strip().lower()
                if not symbol:
                    continue

                market_value = float(pos.get("market_value_base") or 0.0)
                valuation_currency = str(pos.get("valuation_currency") or account.get("base_currency") or "VND")
                converted, _, _ = self.portfolio_service.convert_amount(
                    amount=market_value,
                    from_currency=valuation_currency,
                    to_currency="VND",
                    as_of_date=as_of_date,
                )

                sector = self._resolve_primary_sector(
                    symbol=symbol,
                    market=market,
                    board_cache=board_cache,
                    coverage=coverage,
                    errors=errors,
                )
                sector_exposure[sector] = sector_exposure.get(sector, 0.0) + converted
                sector_symbols.setdefault(sector, set()).add(symbol)

        rows = []
        for sector, exposure in sector_exposure.items():
            weight = (exposure / total_mv * 100.0) if total_mv > 0 else 0.0
            rows.append(
                {
                    "sector": sector,
                    "market_value_base": round(exposure, 6),
                    "weight_pct": round(weight, 4),
                    "symbol_count": len(sector_symbols.get(sector, set())),
                    "is_alert": bool(weight >= threshold_pct),
                }
            )
        rows.sort(key=lambda item: item["market_value_base"], reverse=True)
        top_weight = rows[0]["weight_pct"] if rows else 0.0

        return {
            "total_market_value": round(total_mv, 6),
            "top_weight_pct": round(float(top_weight), 4),
            "alert": bool(top_weight >= threshold_pct),
            "top_sectors": rows[:10],
            "coverage": coverage,
            "errors": errors[:20],
        }

    def _resolve_primary_sector(
        self,
        *,
        symbol: str,
        market: str,
        board_cache: Dict[Tuple[str, str], str],
        coverage: Dict[str, int],
        errors: List[str],
    ) -> str:
        cache_key = (symbol, market)
        if cache_key in board_cache:
            return board_cache[cache_key]

        sector: Optional[str] = None
        if market == "vn":
            try:
                fetcher = self._get_sector_fetcher()
                if fetcher is not None:
                    sector = icb_supersector_name(fetcher.get_icb_code(symbol))
            except Exception as exc:
                # Lỗi tra ngành không được làm hỏng báo cáo rủi ro. / Never fail the risk report.
                logger.warning("[PortfolioRiskService] Tra ngành ICB cho %s thất bại: %s", symbol, exc)
                coverage["failed_count"] += 1
                errors.append(f"{symbol}: {exc}")
                board_cache[cache_key] = "UNCLASSIFIED"
                return board_cache[cache_key]

        if sector:
            coverage["classified_count"] += 1
            board_cache[cache_key] = sector
        else:
            coverage["unclassified_count"] += 1
            board_cache[cache_key] = "UNCLASSIFIED"
        return board_cache[cache_key]

    def _get_sector_fetcher(self) -> Optional[Any]:
        """FiinTrade là nguồn mã ICB duy nhất; chưa cấu hình ``FIINTRADE_ORIGIN`` thì trả None.

        FiinTrade is the only ICB source; returns None when it is not configured.
        """

        if self._sector_fetcher is None:
            from data_provider.fiintrade_fetcher import FiinTradeFetcher, is_fiintrade_enabled

            if not is_fiintrade_enabled():
                return None
            self._sector_fetcher = FiinTradeFetcher()
        return self._sector_fetcher

    def _build_drawdown(
        self,
        *,
        account_id: Optional[int],
        as_of_date: date,
        cost_method: str,
        threshold_pct: float,
        lookback_days: int,
    ) -> Dict[str, Any]:
        rows = self.repo.list_daily_snapshots_for_risk(
            as_of=as_of_date,
            cost_method=cost_method,
            account_id=account_id,
            lookback_days=lookback_days,
        )
        if not rows:
            return {
                "series_points": 0,
                "excluded_points": 0,
                "max_drawdown_pct": 0.0,
                "current_drawdown_pct": 0.0,
                "alert": False,
                "fx_stale": False,
            }

        # Loại cả ngày nếu là ngày không giao dịch hoặc có tài khoản thiếu giá (equity bị đánh giá
        # thấp -> drawdown giả). Loại theo ngày để tổng nhiều tài khoản không bị lệch.
        # Exclude whole dates that are non-trading or have any missing-price snapshot.
        excluded_dates = {
            row.snapshot_date.isoformat()
            for row in rows
            if not is_market_open("vn", row.snapshot_date) or self._snapshot_has_missing_price(row)
        }
        grouped: Dict[str, float] = {}
        stale_flag = False
        for row in rows:
            key = row.snapshot_date.isoformat()
            if key in excluded_dates:
                continue
            converted, stale, _ = self.portfolio_service.convert_amount(
                amount=float(row.total_equity or 0.0),
                from_currency=str(row.base_currency or "VND"),
                to_currency="VND",
                as_of_date=row.snapshot_date,
            )
            grouped[key] = grouped.get(key, 0.0) + converted
            stale_flag = stale_flag or stale or bool(row.fx_stale)

        excluded_points = len(excluded_dates)
        series: List[Tuple[str, float]] = sorted(grouped.items(), key=lambda item: item[0])
        peak = 0.0
        max_drawdown = 0.0
        current_drawdown = 0.0
        for _, equity in series:
            peak = max(peak, equity)
            if peak <= 0:
                drawdown = 0.0
            else:
                drawdown = (peak - equity) / peak * 100.0
            max_drawdown = max(max_drawdown, drawdown)
            current_drawdown = drawdown

        return {
            "series_points": len(series),
            "excluded_points": excluded_points,
            "max_drawdown_pct": round(max_drawdown, 4),
            "current_drawdown_pct": round(current_drawdown, 4),
            "alert": bool(max_drawdown >= threshold_pct),
            "fx_stale": stale_flag,
        }

    @staticmethod
    def _snapshot_has_missing_price(row: Any) -> bool:
        try:
            payload = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            return False
        limitations = payload.get("limitations") if isinstance(payload, dict) else None
        return isinstance(limitations, list) and "price_unavailable" in limitations

    @staticmethod
    def _build_stop_loss(snapshot: Dict[str, Any], thresholds: Dict[str, Any]) -> Dict[str, Any]:
        stop_loss_pct = float(thresholds["stop_loss_alert_pct"])
        near_ratio = float(thresholds["stop_loss_near_ratio"])
        near_threshold = stop_loss_pct * near_ratio

        warnings: List[Dict[str, Any]] = []
        for account in snapshot.get("accounts", []):
            for pos in account.get("positions", []):
                avg_cost = float(pos.get("avg_cost", 0.0) or 0.0)
                last_price = float(pos.get("last_price", 0.0) or 0.0)
                if avg_cost <= 0:
                    continue
                # Mã thiếu giá không có last_price thật; bỏ qua để không báo lỗ 100% giả.
                # Skip positions without a real price to avoid a false 100% loss.
                if not pos.get("price_available", True) or last_price <= 0:
                    continue
                loss_pct = max(0.0, (avg_cost - last_price) / avg_cost * 100.0)
                if loss_pct < near_threshold:
                    continue
                warnings.append(
                    {
                        "account_id": account.get("account_id"),
                        "symbol": pos.get("symbol"),
                        "avg_cost": round(avg_cost, 8),
                        "last_price": round(last_price, 8),
                        "loss_pct": round(loss_pct, 4),
                        "near_threshold_pct": round(near_threshold, 4),
                        "is_triggered": bool(loss_pct >= stop_loss_pct),
                    }
                )

        warnings.sort(key=lambda item: item["loss_pct"], reverse=True)
        return {
            "near_alert": len(warnings) > 0,
            "triggered_count": sum(1 for item in warnings if item["is_triggered"]),
            "near_count": len(warnings),
            "items": warnings[:20],
        }
