# -*- coding: utf-8 -*-
"""
===================================
Registry thị trường (chỉ Việt Nam)
===================================

Module này là nguồn sự thật duy nhất cho "hệ thống hỗ trợ thị trường nào". Repo chỉ
phục vụ thị trường Việt Nam (HOSE/HNX/UPCoM), nên registry chỉ có một ``MarketSpec``
là ``vn``. Các hàm ``enabled_markets()`` / ``is_market_enabled()`` vẫn được giữ làm
lớp mỏng để các nơi gọi không phải đổi.

Biến môi trường ``SUPPORTED_MARKETS`` vẫn được đọc để tương thích cấu hình cũ: giá trị
khác ``vn`` (ví dụ ``cn``, ``us``, ``all``) chỉ log cảnh báo rồi bị bỏ qua, không làm
dừng tiến trình.

Single source of truth for supported markets: Vietnam only. ``SUPPORTED_MARKETS`` is
still read for backward compatibility; any non-``vn`` value is ignored with a warning.

Ràng buộc thiết kế: module này **không import** ``src.config`` để tránh vòng import
(``src.config`` phụ thuộc gián tiếp vào module này qua ``src.utils.market_review_region``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from threading import RLock
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_MARKETS_ENV = "SUPPORTED_MARKETS"
DEFAULT_SUPPORTED_MARKETS = "vn"


@dataclass(frozen=True)
class MarketSpec:
    """Metadata tĩnh của một thị trường.

    Attributes:
        code: mã thị trường viết thường (``vn``)
        label_vi: tên hiển thị tiếng Việt
        label_en: tên hiển thị tiếng Anh
        timezone: múi giờ IANA, dùng để xác định "hôm nay" của thị trường
        currency: đơn vị tiền tệ niêm yết
        xcals_code: mã sàn của exchange-calendars; ``None`` nghĩa là thư viện không
            phủ thị trường này (Việt Nam), kiểm tra ngày giao dịch chạy theo nhánh fail-open
        detect_order: thứ tự nhận diện (giữ để tương thích)
        supports_market_review: có profile / prompt điểm lại thị trường hay không
        review_order: thứ tự trong báo cáo điểm lại thị trường
    """

    code: str
    label_vi: str
    label_en: str
    timezone: str
    currency: str
    xcals_code: Optional[str]
    detect_order: int
    supports_market_review: bool = True
    review_order: int = 0


ALL_MARKETS: Dict[str, MarketSpec] = {
    "vn": MarketSpec(
        code="vn",
        label_vi="Việt Nam",
        label_en="Vietnam",
        timezone="Asia/Ho_Chi_Minh",
        currency="VND",
        # exchange-calendars không phủ HOSE/HNX/UPCOM (đã kiểm: không lịch nào trong
        # 102 lịch của thư viện là của Việt Nam), nên kiểm tra ngày giao dịch dùng
        # nhánh fail-open.
        xcals_code=None,
        detect_order=10,
        review_order=0,
    ),
}

ALL_MARKET_CODES: Tuple[str, ...] = tuple(ALL_MARKETS)

_cache_lock = RLock()
_cached_raw: Optional[str] = None
_cached_markets: Tuple[str, ...] = ()


def parse_supported_markets(raw: Optional[str]) -> Tuple[str, ...]:
    """Đọc ``SUPPORTED_MARKETS`` và luôn trả ``("vn",)``.

    Giá trị khác ``vn`` (kể cả ``all``) chỉ log cảnh báo để người dùng biết cấu hình
    cũ đã hết tác dụng, không ném lỗi.

    Always returns ``("vn",)``; non-``vn`` tokens are ignored with a warning.
    """

    normalized = str(raw or "").strip().lower()
    if normalized:
        tokens = {token.strip() for token in normalized.split(",") if token.strip()}
        ignored = sorted(token for token in tokens if token not in ALL_MARKETS)
        if ignored:
            logger.warning(
                "%s chứa thị trường không còn được hỗ trợ: %s; đã bỏ qua. "
                "Hệ thống chỉ hỗ trợ thị trường Việt Nam (vn).",
                SUPPORTED_MARKETS_ENV,
                ", ".join(ignored),
            )
    return ALL_MARKET_CODES


def enabled_markets() -> Tuple[str, ...]:
    """Trả về các thị trường đang bật (luôn là ``("vn",)``).

    Kết quả được cache theo chuỗi gốc của biến môi trường để cảnh báo chỉ log một lần
    cho mỗi giá trị.
    """

    raw = os.getenv(SUPPORTED_MARKETS_ENV)
    global _cached_raw, _cached_markets
    with _cache_lock:
        if _cached_markets and _cached_raw == raw:
            return _cached_markets
        markets = parse_supported_markets(raw)
        _cached_raw = raw
        _cached_markets = markets
        return markets


def reset_cache() -> None:
    """Xoá cache (dùng cho test)."""

    global _cached_raw, _cached_markets
    with _cache_lock:
        _cached_raw = None
        _cached_markets = ()


def is_market_enabled(market: Optional[str]) -> bool:
    """Thị trường có được hỗ trợ hay không (chỉ ``vn``)."""

    if not market:
        return False
    return str(market).strip().lower() in enabled_markets()


def enabled_specs() -> Tuple[MarketSpec, ...]:
    """Trả về ``MarketSpec`` của các thị trường đang bật."""

    return tuple(ALL_MARKETS[code] for code in enabled_markets())


def get_spec(market: str) -> Optional[MarketSpec]:
    return ALL_MARKETS.get(str(market or "").strip().lower())


def market_label(market: str, language: str = "vi") -> str:
    """Tên hiển thị của thị trường; mã không rõ thì trả mã viết hoa để UI không bị trống."""

    spec = get_spec(market)
    if spec is None:
        return str(market or "").upper()
    return spec.label_en if str(language).lower().startswith("en") else spec.label_vi


def enabled_market_exchanges() -> Dict[str, str]:
    """``market -> MIC`` cho các thị trường có lịch exchange-calendars.

    Việt Nam không có lịch trong thư viện nên kết quả rỗng; ``trading_calendar`` xử lý
    bằng nhánh fail-open.
    """

    return {
        spec.code: spec.xcals_code
        for spec in enabled_specs()
        if spec.xcals_code
    }


def enabled_market_timezones() -> Dict[str, str]:
    """``market -> múi giờ IANA`` của các thị trường đang bật."""

    return {spec.code: spec.timezone for spec in enabled_specs()}


def market_review_regions() -> Tuple[str, ...]:
    """Các thị trường hỗ trợ điểm lại thị trường (chỉ ``vn``)."""

    regions = [spec for spec in enabled_specs() if spec.supports_market_review]
    regions.sort(key=lambda spec: spec.review_order)
    return tuple(spec.code for spec in regions)
