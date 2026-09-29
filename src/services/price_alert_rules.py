# -*- coding: utf-8 -*-
"""
Quy tắc cảnh báo giá dùng chung cho engine định kỳ và engine realtime.

Price alert rule helpers shared by the periodic worker and the realtime tick engine:

- ``trigger_mode`` của ``price_cross``: ``level`` (mặc định, hành vi cũ: giá đang ở bên kia
  ngưỡng là kích hoạt) hoặc ``cross`` (chỉ kích hoạt khi giá vừa cắt qua ngưỡng so với lần
  quan sát trước).
- Loại ``price_limit_touch``: giá chạm (hoặc cách trong ``near_pct`` %) giá trần / sàn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

PRICE_LIMIT_ALERT_TYPE = "price_limit_touch"
PRICE_ALERT_TYPES = frozenset({"price_cross", "price_change_percent", PRICE_LIMIT_ALERT_TYPE})
TRIGGER_MODES = frozenset({"level", "cross"})
PRICE_LIMIT_SIDES = frozenset({"ceiling", "floor", "both"})
MAX_NEAR_PCT = 20.0
# Nhận thêm giá khớp ATC (công bố ngay sau 14:45) cho rule giá VN.
VN_PRICE_ALERT_GRACE_MINUTES = 2


def alert_type_value(rule: Any) -> str:
    raw = getattr(rule, "alert_type", "")
    return str(getattr(raw, "value", raw) or "").strip().lower()


def is_vn_price_rule(rule: Any) -> bool:
    """Rule giá (cắt ngưỡng / % thay đổi / trần-sàn) của một mã Việt Nam."""

    from src.core.trading_calendar import get_market_for_stock

    if alert_type_value(rule) not in PRICE_ALERT_TYPES:
        return False
    code = str(getattr(rule, "stock_code", "") or "")
    return bool(code) and get_market_for_stock(code) == "vn"


def is_vn_price_alert_window(current_time: Any = None) -> bool:
    """Khung giờ đánh giá rule giá VN: phiên khớp lệnh + vài phút nhận giá khớp ATC."""

    from src.core.trading_calendar import is_vn_matching_session

    return is_vn_matching_session(current_time, grace_minutes=VN_PRICE_ALERT_GRACE_MINUTES)


def normalize_trigger_mode(value: Any) -> str:
    mode = str(value or "level").strip().lower()
    if mode not in TRIGGER_MODES:
        raise ValueError(f"invalid trigger_mode: {mode}")
    return mode


def normalize_price_limit_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    side = str(parameters.get("side") or "both").strip().lower()
    if side not in PRICE_LIMIT_SIDES:
        raise ValueError(f"invalid side: {side}")
    raw_near = parameters.get("near_pct", 0)
    try:
        near_pct = float(raw_near if raw_near not in (None, "") else 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid near_pct: {raw_near}") from exc
    if near_pct < 0 or near_pct > MAX_NEAR_PCT:
        raise ValueError(f"near_pct must be between 0 and {MAX_NEAR_PCT:g}")
    return {"side": side, "near_pct": near_pct}


@dataclass
class PriceLimitAlert:
    """Cảnh báo chạm / gần giá trần-sàn (thị trường Việt Nam)."""

    stock_code: str
    side: str = "both"
    near_pct: float = 0.0
    alert_type: str = PRICE_LIMIT_ALERT_TYPE
    metadata: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.description:
            self.description = f"{self.stock_code} price limit touch ({self.side}, near {self.near_pct:g}%)"


def crossed(direction: str, threshold: float, previous: Optional[float], current: float) -> bool:
    """Giá vừa cắt qua ngưỡng: lần trước ở phía dưới/trên, lần này ở phía trên/dưới."""

    if previous is None:
        return False
    if direction == "above":
        return previous < threshold <= current
    return previous > threshold >= current


def price_limit_hit(side: str, near_pct: float, price: float, ceiling: Optional[float], floor: Optional[float]):
    """Trả ``(hit_side, limit_price)`` nếu giá chạm/gần trần hoặc sàn, ngược lại ``(None, None)``."""

    tolerance = max(0.0, float(near_pct)) / 100.0
    if side in ("ceiling", "both") and ceiling and price >= ceiling * (1 - tolerance):
        return "ceiling", ceiling
    if side in ("floor", "both") and floor and price <= floor * (1 + tolerance):
        return "floor", floor
    return None, None
