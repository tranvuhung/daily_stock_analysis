# -*- coding: utf-8 -*-
"""Helpers for parsing report sniper-point price values."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Dict, Optional


SNIPER_KEYS = ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")

# Đơn vị tiền tệ VND đứng riêng (không dính vào chữ khác, vd. "đến").
# Standalone VND currency marker (not part of another word such as "đến").
_CURRENCY_MARKER_RE = re.compile(
    r"(?<![^\W\d_])(?:đồng|vnđ|vnd|đ)(?![^\W\d_])",
    re.IGNORECASE,
)

# Số kiểu Việt Nam dùng "." phân tách hàng nghìn: "51.500", "1.077.540".
# Vietnamese-style numbers using "." as the thousands separator.
_VN_THOUSANDS_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{3})+(?![\d.])")

# Giá cổ phiếu VN thấp nhất thực tế ~ vài trăm đồng; giá trị < reference/100
# gần như chắc chắn được viết theo đơn vị nghìn đồng (vd. 51.5 = 51.500 VND).
# Real VN prices never sit 100x below the reference; such values are in thousands.
_THOUSAND_UNIT_RATIO = 100
_THOUSAND_UNIT_TOLERANCE = (0.2, 5.0)


def strip_vn_thousands_separators(text: str) -> str:
    """'51.500 VND' -> '51500 VND'; giữ nguyên số thập phân như '100.5', '102.10'.

    Remove VN-style thousands dots while keeping real decimals intact.
    """
    return _VN_THOUSANDS_RE.sub(lambda m: m.group().replace(".", ""), text)


def normalize_price_unit(value: Optional[float], reference_price: Any = None) -> Optional[float]:
    """Quy đổi giá viết theo nghìn đồng về VND dựa trên giá tham chiếu.

    Convert a price written in thousands of VND (51.5) to VND (51500) when a
    reference price shows the value is ~1000x too small. Values that do not
    land near the reference after scaling are returned unchanged.
    """
    if value is None or value <= 0:
        return value
    try:
        reference = float(reference_price) if reference_price is not None else 0.0
    except (TypeError, ValueError):
        return value
    if reference <= 0 or value >= reference / _THOUSAND_UNIT_RATIO:
        return value
    scaled = value * 1000
    low, high = _THOUSAND_UNIT_TOLERANCE
    if low <= scaled / reference <= high:
        return scaled
    return value


def parse_sniper_value(value: Any, reference_price: Any = None) -> Optional[float]:
    """Parse a sniper point value from report text into a positive price.

    ``reference_price`` (giá hiện tại) là tuỳ chọn; khi có, giá trị viết theo
    nghìn đồng sẽ được quy đổi về VND. / Optional current price used to
    rescale values written in thousands of VND.
    """
    return normalize_price_unit(_parse_sniper_number(value), reference_price)


def _parse_sniper_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if parsed > 0 else None

    text = strip_vn_thousands_separators(str(value))
    text = text.replace(",", "").replace("，", "").strip()
    if not text or text in {"-", "—", "N/A"}:
        return None

    try:
        parsed = float(text)
        return parsed if parsed > 0 else None
    except ValueError:
        pass

    colon_pos = max(text.rfind("："), text.rfind(":"))
    # Tìm đơn vị tiền tệ VND (đ / VND / VNĐ / đồng) đứng sau dấu hai chấm.
    # Locate a VND currency marker (đ / VND / VNĐ / đồng) after the colon.
    currency_match = _CURRENCY_MARKER_RE.search(text, colon_pos + 1 if colon_pos != -1 else 0)
    currency_pos = currency_match.start() if currency_match else -1
    if currency_pos != -1:
        segment_start = colon_pos + 1 if colon_pos != -1 else 0
        segment = text[segment_start:currency_pos]
        valid_numbers = []
        for match in re.finditer(r"-?\d+(?:\.\d+)?", segment):
            start_idx = match.start()
            if start_idx >= 2 and segment[start_idx - 2:start_idx].upper() == "MA":
                continue
            valid_numbers.append(match.group())
        if valid_numbers:
            try:
                parsed = abs(float(valid_numbers[-1]))
                return parsed if parsed > 0 else None
            except ValueError:
                pass

    paren_pos = len(text)
    for paren_char in ("(", "（"):
        pos = text.find(paren_char)
        if pos != -1:
            paren_pos = min(paren_pos, pos)
    search_text = text[:paren_pos].strip() or text

    valid_numbers = []
    for match in re.finditer(r"\d+(?:\.\d+)?", search_text):
        start_idx = match.start()
        if start_idx >= 2 and search_text[start_idx - 2:start_idx].upper() == "MA":
            continue
        valid_numbers.append(match.group())
    if valid_numbers:
        try:
            parsed = float(valid_numbers[-1])
            return parsed if parsed > 0 else None
        except ValueError:
            pass
    return None


def extract_sniper_points(result: Any, reference_price: Any = None) -> Dict[str, Optional[float]]:
    """Extract normalized sniper-point prices from a completed analysis result.

    Mặc định dùng ``result.current_price`` làm giá tham chiếu quy đổi đơn vị.
    Defaults to ``result.current_price`` as the unit reference.
    """

    if reference_price is None:
        reference_price = getattr(result, "current_price", None)

    raw_points: Mapping[str, Any] = {}

    if hasattr(result, "get_sniper_points"):
        candidate = result.get_sniper_points() or {}
        if isinstance(candidate, Mapping):
            raw_points = candidate

    if not _has_any_sniper_value(raw_points):
        dashboard = getattr(result, "dashboard", None)
        if isinstance(dashboard, Mapping):
            raw_points = find_sniper_points(dashboard) or raw_points

    if not _has_any_sniper_value(raw_points):
        raw_response = getattr(result, "raw_response", None)
        if isinstance(raw_response, Mapping):
            raw_points = find_sniper_points(raw_response) or raw_points

    return {
        key: parse_sniper_value(raw_points.get(key), reference_price)
        for key in SNIPER_KEYS
    }


def _has_any_sniper_value(points: Mapping[str, Any]) -> bool:
    return any(points.get(key) not in (None, "") for key in SNIPER_KEYS)


def find_sniper_points(data: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    if not isinstance(data, Mapping):
        return None

    if any(key in data for key in SNIPER_KEYS):
        return data

    sniper_points = data.get("sniper_points")
    if isinstance(sniper_points, Mapping) and sniper_points:
        return sniper_points

    battle_plan = data.get("battle_plan")
    if isinstance(battle_plan, Mapping):
        sniper_points = battle_plan.get("sniper_points")
        if isinstance(sniper_points, Mapping) and sniper_points:
            return sniper_points

    inner_dashboard = data.get("dashboard")
    if isinstance(inner_dashboard, Mapping):
        found = find_sniper_points(inner_dashboard)
        if found:
            return found

    return None
