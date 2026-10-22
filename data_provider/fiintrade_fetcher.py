# -*- coding: utf-8 -*-
"""
===================================
Nguồn dữ liệu FiinTrade - thị trường Việt Nam (tuỳ chọn)
===================================

Endpoint (máy chủ ``wlgw-technical``):

- ``GET /TradingView/GetStockChartData``: nến ngày cổ phiếu và chỉ số
- ``GET /PriceDepth/GetPriceDepth``: giá trong phiên + định giá + khối ngoại +
  sổ lệnh 10 bước (chỉ cổ phiếu HOSE/HNX)
- ``GET /TradingView/GetStockEvents``: cổ tức, phát hành, giao dịch nội bộ, ngày
  công bố KQKD

Máy chủ ``wlgw-market`` (điểm lại thị trường):

- ``GET /MarketInDepth/GetLatestIndices``: 29 chỉ số trong một request, kèm độ
  rộng (tăng/giảm/đứng/trần/sàn), khớp lệnh, thoả thuận, khối ngoại
- ``GET /MoneyFlow/GetForeign``: khối ngoại theo kỳ (hôm nay/1 tuần/1 tháng/YTD)
- ``GET /MoneyFlow/GetProprietaryV2``: tự doanh theo kỳ (công bố sau phiên)

Mặc định bật, nhưng chỉ chạy khi có ``FIINTRADE_ORIGIN``: server chỉ trả dữ liệu
khi header ``Origin`` nằm trong whitelist của đối tác (thiếu thì 403). Repo không
hardcode origin; người triển khai tự điền giá trị được cấp. Tắt hẳn bằng
``FIINTRADE_ENABLED=false``.
On by default but only active once ``FIINTRADE_ORIGIN`` is set (the server rejects
non-whitelisted origins with HTTP 403); disable with ``FIINTRADE_ENABLED=false``.

Hành vi API đã kiểm chứng trực tiếp (2026-10-21):

1. **Giá đã điều chỉnh quyền, cùng cơ sở với VCI**: FPT 26/11 → 03/12/2025 khớp
   tuyệt đối cả close lẫn volume với VCI, nên dữ liệu được phép ghi
   ``stock_daily``. ``rateAdjusted`` khác 0 tại ngày GDKHQ. Không có tham số tắt
   điều chỉnh.
2. **Đơn vị**: giá cổ phiếu tính bằng VND (``57500``), chỉ số tính bằng điểm;
   không nhân 1000.
3. **API nhận ``organCode`` (mã tổ chức), không phải mã giao dịch**: với 647/1.547
   mã (2026-10-22) hai mã này khác nhau, ví dụ VHM → ``NHN``, ACV → ``ACVN``,
   MBS → ``TLSC``. Gửi ticker cho các mã đó trả HTTP 200 kèm ``status="Failed"``
   (``Sequence contains no matching element``). Fetcher tra bảng ánh xạ từ
   ``wlgw-core /Master/GetListOrganization`` (đủ HOSE, HNX, UPCOM, ETF), cache 24
   giờ. Mã vẫn không tìm thấy thì trả DataFrame rỗng để manager chuyển sang VCI mà
   không mở circuit breaker.
4. **Chỉ số** dùng ``Type=Index``. ``VNINDEX``/``VN30``/``VN100``/``HNXIndex``/
   ``HNX30`` khớp close với VCI. ``UpcomIndex`` khớp VCI tới 11/08/2025; từ
   12/08/2025 close nến ngày của FiinTrade khác VCI/SSI biểu đồ (16/10/2026: 124,87
   vs 125,97) nhưng trùng bảng điện SSI. Theo quyết định của chủ dự án
   (2026-10-22), UPCoM-Index vẫn lấy từ FiinTrade trước, lỗi mới sang VCI.
5. **Khối lượng** không trùng hoàn toàn VCI: cổ phiếu lệch < 1% (FiinTrade có vẻ
   gồm cả lô lẻ), chỉ số thấp hơn ~10-15% (chỉ khớp lệnh). Giá thì trùng.
6. **Thời gian**: ``From``/``To`` là UTC, nến ngày đóng dấu 00:00 giờ VN. Nến
   hôm nay trong phiên là nến dở, có kèm giờ (``2026-10-21T14:30:00.068+07:00``).
7. **Thiếu** giá trị giao dịch, giá tham chiếu, trần/sàn: ``amount`` được ước
   lượng (xem ``_normalize_data``).

PriceDepth (kiểm chứng 2026-10-21):

8. Chỉ số không có trên PriceDepth: ``get_realtime_quote`` trả ``None`` để manager
   chuyển sang VCI. Mã chưa khớp lệnh trong phiên có ``matchPrice = 0`` nên cũng
   rơi sang nguồn kế.
9. ``percentPriceChange`` là tỷ lệ (``0.0133`` = 1.33%).
10. ``extraInfo`` dùng mã chỉ tiêu nội bộ, ý nghĩa **suy ra** bằng đối chiếu chéo:
    ``rtd11`` vốn hoá (VND, = giá × số CP), ``rtd14`` EPS TTM, ``rtd21`` P/E
    (= giá / rtd14), ``rtd25`` P/B, ``rtq12`` ROE (tỷ lệ), ``rtd35`` beta (ước đoán).

GetStockEvents (kiểm chứng 2026-10-21):

11. ``exrightDate`` là **ngày đăng ký cuối cùng**, không phải ngày GDKHQ: nó luôn
    đứng sau ngày có ``rateAdjusted`` của API biểu đồ đúng một phiên (FPT
    28/11/2025 → 01/12/2025, 18/09/2026 → 21/09/2026). GDKHQ = phiên liền trước.
12. Không trả sự kiện tương lai; mã không tra được trả danh sách rỗng. Số liệu
    ``revenue``/``profit`` của sự kiện ``Earning`` chưa rõ loại lợi nhuận nên
    không được dùng, chỉ lấy kỳ báo cáo và ngày công bố.

Thị trường (kiểm chứng 2026-10-21):

13. Giá trị giao dịch chỉ số của VCI = khớp lệnh + thoả thuận của FiinTrade
    (VNINDEX 20.290 = 17.071 + 3.219 tỷ), nên ``amount`` chỉ số dùng tổng này.
14. UPCoM-Index: ``referenceIndex`` (125,22) là close nến ngày FiinTrade phiên trước,
    khác SSI/VCI (125,51); nhất quán với nến ngày FiinTrade (xem mục 4).
15. ``foreignNetBuyValue``/``foreignNetSellValue`` của khối tổng là tổng các mã
    mua ròng / bán ròng, **không** phải ròng thị trường: ròng = mua − bán.
16. Tự doanh được công bố sau phiên và khoảng ngày của khối ``today`` thay đổi
    theo thời điểm gọi: trong phiên là phiên trước (``20/10 → 20/10``), sau khi
    công bố lại gộp hai phiên (``20/10 → 21/10``). Vì vậy luôn giữ nguyên
    ``fromDate``/``toDate`` của API và để tầng prompt nêu rõ khoảng ngày, không
    gán là "hôm nay". Danh sách top lẫn chứng quyền nên phải lọc.
"""

import logging
import os
import random
import time
from datetime import date, datetime, timedelta
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    is_vn_index_code,
    is_vn_security_code,
)
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote

logger = logging.getLogger(__name__)

FIINTRADE_BASE_URL = os.getenv(
    "FIINTRADE_BASE_URL", "https://wlgw-technical.fiintrade.vn"
).rstrip("/")
FIINTRADE_MARKET_BASE_URL = os.getenv(
    "FIINTRADE_MARKET_BASE_URL", "https://wlgw-market.fiintrade.vn"
).rstrip("/")
_LATEST_INDICES_PATH = "/MarketInDepth/GetLatestIndices"
_FOREIGN_FLOW_PATH = "/MoneyFlow/GetForeign"
_PROPRIETARY_FLOW_PATH = "/MoneyFlow/GetProprietaryV2"
_CONTRIBUTION_PATH = "/MoneyFlow/GetContribution"
_TOP_BREAKOUT_PATH = "/TopMover/GetTopBreakout"
_INDEX_SERIES_PATH = "/MarketInDepth/GetIndexSeries"
FIINTRADE_CORE_BASE_URL = os.getenv(
    "FIINTRADE_CORE_BASE_URL", "https://wlgw-core.fiintrade.vn"
).rstrip("/")
_ORGANIZATIONS_PATH = "/Master/GetListOrganization"
_CHART_PATH = "/TradingView/GetStockChartData"
_PRICE_DEPTH_PATH = "/PriceDepth/GetPriceDepth"
_EVENTS_PATH = "/TradingView/GetStockEvents"

_DEFAULT_TIMEOUT = 20.0
_MIN_INTERVAL_SECONDS = 0.3
_JITTER_SECONDS = 0.2

# Thông báo lỗi của server khi không tìm thấy mã (mã sai, đã huỷ niêm yết, chưa tra
# được organCode). / Server error text when the symbol is unknown.
_NOT_FOUND_MARKER = "no matching element"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Chỉ số lấy từ FiinTrade, khoá là mã nội bộ viết hoa (VN_INDEX_SYMBOLS). Lưu ý
# UPCoM-Index: từ 12/08/2025 close của FiinTrade khác VCI (xem mục 4 ở đầu file).
# Indices served by FiinTrade; note UPCoM-Index closes differ from VCI since 2025-08-12.
_FIIN_INDEX_SYMBOLS = {
    "VNINDEX": "VNINDEX",
    "VN30": "VN30",
    "VN100": "VN100",
    "HNXINDEX": "HNXIndex",
    "HNX30": "HNX30",
    "UPCOMINDEX": "UpcomIndex",
    "UPCOM": "UpcomIndex",
}

# Sự kiện doanh nghiệp ít thay đổi trong ngày: cache theo mã 6 giờ.
# Corporate events rarely change intraday: cache per symbol for 6 hours.
_EVENTS_CACHE_TTL = 6 * 3600.0
_EVENTS_LOOKBACK_DAYS = 730

# Sàn niêm yết theo ``comGroupCode`` của PriceDepth (khớp ``vn_exchange`` của SSI).
_EXCHANGE_BY_GROUP = {"VNINDEX": "hose", "HNXINDEX": "hnx"}

# Chỉ số chính của điểm lại thị trường: (mã FiinTrade, mã chuẩn của repo, tên hiển thị).
# Mã/tên giống VciFetcher/SsiFetcher để báo cáo không đổi khi nguồn đổi.
# Main review indices: (FiinTrade code, canonical repo code, display name).
_MAIN_INDEX_CODES = (
    ("VNINDEX", "VNINDEX", "VN-Index"),
    ("VN30", "VN30", "VN30"),
    ("HNXIndex", "HNXIndex", "HNX-Index"),
    ("UpcomIndex", "UPCOMINDEX", "UPCoM-Index"),
)
# Ba chỉ số sàn dùng để cộng độ rộng toàn thị trường / Exchange-wide indices
_EXCHANGE_INDEX_CODES = (("hose", "VNINDEX"), ("hnx", "HNXIndex"), ("upcom", "UpcomIndex"))
# Dòng tiền lấy theo HOSE (~90% giá trị giao dịch toàn thị trường).
_FLOW_SCOPE_CODE = "VNINDEX"
_FLOW_PERIODS = (("today", "today"), ("oneWeek", "one_week"), ("oneMonth", "one_month"), ("yearToDate", "ytd"))
_FLOW_TOP_N = 5
# Snapshot chỉ số dùng chung cho chỉ số + độ rộng trong một lượt review.
_INDICES_CACHE_TTL = 30.0
_MONEY_FLOW_CACHE_TTL = 300.0
# Động lực thị trường (mã dẫn dắt chỉ số, đột biến KL, diễn biến trong ngày).
# Market drivers: index contributors, volume breakouts, intraday path.
_DRIVERS_CACHE_TTL = 120.0
_CONTRIBUTION_PERIODS = (("contrib1Day", "1d"), ("contrib5Day", "5d"), ("contrib20Day", "20d"))
_CONTRIBUTION_TOP_N = 5
# Đột biến KL: KL phiên ≥ 1,5 lần bình quân 1 tuần, cả 3 sàn (API trả tối đa 30 mã).
# Volume breakouts: session volume >= 1.5x the 1-week average, all exchanges.
_BREAKOUT_PARAMS = {"ComGroupCode": "All", "TimeRange": "OneWeek", "Rate": "OnePointFive"}
_BREAKOUT_TOP_N = 10
# Mốc trong ngày (giờ VN): hết phiên sáng và bắt đầu 15 phút cuối + ATC của HOSE.
# Intraday checkpoints (VN time): morning close and start of the closing window.
_MORNING_CLOSE_TIME = "11:30"
_CLOSING_WINDOW_START = "14:30"

_last_request_at = 0.0
_request_lock = RLock()
_events_cache: Dict[str, Dict[str, Any]] = {}
_events_lock = RLock()
# Bảng ticker -> organCode: danh mục ít đổi nên cache 24 giờ; tải lỗi thì chờ 5 phút
# mới thử lại để không làm chậm mọi request.
# Ticker -> organCode map: cached 24h; after a failed load, retry only after 5 min.
_ORGANIZATIONS_CACHE_TTL = 24 * 3600.0
_ORGANIZATIONS_RETRY_SECONDS = 300.0
_organizations_cache: Dict[str, Any] = {"data": None, "ts": 0.0, "failed_at": 0.0}
_organizations_lock = RLock()
_market_cache: Dict[str, Dict[str, Any]] = {}
_market_lock = RLock()


def reset_organizations_cache() -> None:
    """Xoá cache bảng organCode (dùng cho test). / Clear the organCode cache (for tests)."""

    with _organizations_lock:
        _organizations_cache.update({"data": None, "ts": 0.0, "failed_at": 0.0})


def reset_events_cache() -> None:
    """Xoá cache sự kiện (dùng cho test). / Clear the events cache (for tests)."""

    with _events_lock:
        _events_cache.clear()


def reset_market_cache() -> None:
    """Xoá cache dữ liệu thị trường (dùng cho test). / Clear market caches (for tests)."""

    with _market_lock:
        _market_cache.clear()


def _to_billion(value: Optional[float]) -> Optional[float]:
    return round(value / 1e9, 2) if value is not None else None


def _to_float(value: Any) -> Optional[float]:
    """Ép về float hữu hạn; None/NaN/không hợp lệ trả None."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _date_part(value: Any) -> Optional[str]:
    """``2025-12-01T00:00:00`` -> ``2025-12-01``; giá trị rỗng/năm 0001 trả None."""

    text = str(value or "").strip()[:10]
    if len(text) != 10 or text.startswith("0001"):
        return None
    return text


def _previous_weekday(day_text: Optional[str]) -> Optional[str]:
    """Ngày làm việc liền trước (bỏ qua thứ Bảy/Chủ nhật).

    Dùng để suy ngày GDKHQ từ ngày đăng ký cuối cùng. Chưa trừ ngày nghỉ lễ: repo
    chưa có lịch nghỉ lễ HOSE, nên GDKHQ sát kỳ nghỉ có thể lệch một ngày.
    """

    if not day_text:
        return None
    try:
        current = datetime.strptime(day_text, "%Y-%m-%d").date()
    except ValueError:
        return None
    current -= timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def _parse_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r không phải số hợp lệ, quay về mặc định %s", name, raw, default)
        return default


def is_fiintrade_enabled() -> bool:
    """Bật khi đã cấu hình ``FIINTRADE_ORIGIN`` và không đặt ``FIINTRADE_ENABLED=false``.

    On by default; active only when an origin is configured and the flag is not false.
    """

    flag = str(os.getenv("FIINTRADE_ENABLED", "true")).strip().lower() == "true"
    return flag and bool(str(os.getenv("FIINTRADE_ORIGIN", "")).strip())


class FiinTradeFetcher(BaseFetcher):
    """Nguồn nến ngày Việt Nam qua API biểu đồ FiinTrade (HOSE, HNX, chỉ số)."""

    name = "FiinTradeFetcher"
    # Mặc định -1 để đứng trước VCI (0) khi được bật.
    # Defaults to -1 so it runs ahead of VCI (0) once enabled.
    priority = int(os.getenv("FIINTRADE_PRIORITY", "-1"))
    # Mã không tra được trên FiinTrade trả rỗng: manager ghi nhận là "rỗng" chứ
    # không phải lỗi, nên không làm mở circuit breaker, rồi chuyển sang VCI.
    # Unknown symbols return empty so the manager falls through to VCI without
    # tripping the circuit breaker.
    allow_empty_daily_data = True

    def __init__(
        self,
        timeout: Optional[float] = None,
        min_interval: Optional[float] = None,
        origin: Optional[str] = None,
    ) -> None:
        self.timeout = timeout if timeout is not None else _parse_env_float(
            "FIINTRADE_CALL_TIMEOUT", _DEFAULT_TIMEOUT
        )
        self.min_interval = (
            min_interval
            if min_interval is not None
            else _parse_env_float("FIINTRADE_MIN_INTERVAL", _MIN_INTERVAL_SECONDS)
        )
        resolved_origin = (
            origin if origin is not None else os.getenv("FIINTRADE_ORIGIN", "")
        ).strip().rstrip("/")
        self._session = requests.Session()
        headers = {"User-Agent": _UA, "Accept": "application/json"}
        if resolved_origin:
            headers["Origin"] = resolved_origin
            headers["Referer"] = f"{resolved_origin}/"
        self._session.headers.update(headers)

    # ------------------------------------------------------------------
    # Điều tiết tần suất request
    # ------------------------------------------------------------------
    def _enforce_rate_limit(self) -> None:
        global _last_request_at
        with _request_lock:
            now = time.monotonic()
            elapsed = now - _last_request_at
            wait = self.min_interval - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, _JITTER_SECONDS))
            _last_request_at = time.monotonic()

    # ------------------------------------------------------------------
    # Mã nội bộ -> (Code, Type) của FiinTrade
    # ------------------------------------------------------------------
    @staticmethod
    def to_provider_symbol(stock_code: str) -> Tuple[Optional[str], str]:
        """Trả về ``(Code, Type)``: chỉ số dùng ``Type=Index``, còn lại ``Stock``.

        Chỉ số chưa đăng ký trong ``_FIIN_INDEX_SYMBOLS`` trả ``Code=None``. Cổ phiếu
        trả ticker; quy về ``organCode`` ở ``_resolve_code``.
        """

        normalized = (stock_code or "").strip().upper()
        if normalized.endswith(".VN"):
            normalized = normalized[:-3]
        if is_vn_index_code(normalized):
            return _FIIN_INDEX_SYMBOLS.get(normalized), "Index"
        return normalized, "Stock"

    def _organization_map(self) -> Dict[str, str]:
        """Bảng ticker -> organCode từ danh mục FiinTrade; lỗi thì trả rỗng (fail-open)."""

        with _organizations_lock:
            now = time.time()
            data = _organizations_cache.get("data")
            if data is not None and now - float(_organizations_cache.get("ts") or 0) < _ORGANIZATIONS_CACHE_TTL:
                return data
            if now - float(_organizations_cache.get("failed_at") or 0) < _ORGANIZATIONS_RETRY_SECONDS:
                return data or {}
        try:
            payload = self._get_json(
                _ORGANIZATIONS_PATH, {"language": "vi"}, base_url=FIINTRADE_CORE_BASE_URL
            )
            mapping = {
                str(item.get("ticker")).strip().upper(): str(item.get("organCode")).strip()
                for item in payload.get("items") or []
                if isinstance(item, dict) and item.get("ticker") and item.get("organCode")
            }
            if not mapping:
                raise DataFetchError(f"[{self.name}] danh mục organCode rỗng")
        except Exception as exc:
            logger.warning(
                "[%s] Tải danh mục organCode thất bại, tạm dùng ticker: %s", self.name, exc
            )
            with _organizations_lock:
                _organizations_cache["failed_at"] = time.time()
                return _organizations_cache.get("data") or {}
        with _organizations_lock:
            _organizations_cache.update({"data": mapping, "ts": time.time(), "failed_at": 0.0})
        return mapping

    def _resolve_code(self, stock_code: str) -> Tuple[Optional[str], str, str]:
        """``(mã gửi API, Type, ticker)``: cổ phiếu/ETF được quy về ``organCode``.

        Chỉ số giữ nguyên cách viết riêng của FiinTrade; ticker không có trong danh
        mục thì gửi nguyên ticker.
        Returns ``(api code, Type, ticker)``; stocks/ETFs are mapped to organCode.
        """

        code, chart_type = self.to_provider_symbol(stock_code)
        if code is None or chart_type != "Stock":
            return code, chart_type, code or ""
        return self._organization_map().get(code, code), chart_type, code

    # ------------------------------------------------------------------
    # Nến ngày
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError, TimeoutError)),
        reraise=True,
    )
    def _get_json(
        self, path: str, params: Dict[str, Any], base_url: Optional[str] = None
    ) -> Dict[str, Any]:
        self._enforce_rate_limit()
        response = self._session.get(
            f"{base_url or FIINTRADE_BASE_URL}{path}", params=params, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise DataFetchError(
                f"[{self.name}] FiinTrade trả về cấu trúc không mong đợi: {type(data).__name__}"
            )
        return data

    @staticmethod
    def _utc_window(start_date: str, end_date: str) -> Tuple[str, str]:
        """Đổi khoảng ngày giờ VN sang ``From``/``To`` UTC cho API.

        Nến ngày đóng dấu 00:00 giờ VN (= 17:00 UTC hôm trước), nên ``From`` lùi
        về 17:00 UTC của ngày trước ``start_date``. ``To`` là 00:00 UTC ngày sau
        ``end_date`` để nến dở hôm nay (có kèm giờ) không bị cắt mất.
        """

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            start = datetime.now() - timedelta(days=60)
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            end = datetime.now()
        from_utc = (start - timedelta(days=1)).replace(hour=17, minute=0, second=0)
        to_utc = (end + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        return from_utc.strftime(fmt), to_utc.strftime(fmt)

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        code, chart_type, _ticker = self._resolve_code(stock_code)
        if code is None:
            # Chỉ số không lấy từ FiinTrade: trả rỗng, không gửi request, để VCI xử lý.
            logger.info("[%s] %s không lấy từ FiinTrade, chuyển nguồn kế tiếp", self.name, stock_code)
            return pd.DataFrame()
        from_utc, to_utc = self._utc_window(start_date, end_date)
        params = {
            "language": "vi",
            "Code": code,
            "DerivativeCode": "",
            "Frequency": "Daily",
            "From": from_utc,
            "To": to_utc,
            "Type": chart_type,
        }
        data = self._get_json(_CHART_PATH, params)

        if str(data.get("status") or "").lower() == "failed":
            errors: List[str] = [str(e) for e in (data.get("errors") or [])]
            if any(_NOT_FOUND_MARKER in e.lower() for e in errors):
                # Mã không tra được trên FiinTrade: để VCI xử lý.
                logger.info(
                    "[%s] %s (Code=%s, Type=%s) không có trên FiinTrade, chuyển nguồn kế tiếp",
                    self.name,
                    stock_code,
                    code,
                    chart_type,
                )
                return pd.DataFrame()
            raise DataFetchError(f"[{self.name}] FiinTrade trả về lỗi: {errors or data}")

        items = data.get("items") or []
        if not items:
            return pd.DataFrame()
        raw = pd.DataFrame(items)
        if "tradingDate" not in raw.columns:
            raise DataFetchError(f"[{self.name}] FiinTrade thiếu trường tradingDate")

        # Cắt theo khoảng ngày giờ VN; 10 ký tự đầu của tradingDate là ngày VN.
        dates = raw["tradingDate"].astype(str).str[:10]
        raw = raw[(dates >= start_date) & (dates <= end_date)]
        return raw.reset_index(drop=True)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        out = pd.DataFrame()
        # Nến dở hôm nay có kèm giờ: chỉ lấy phần ngày để khớp nến đã chốt của VCI.
        out["date"] = pd.to_datetime(df["tradingDate"].astype(str).str[:10])
        # Giá điều chỉnh có phần lẻ dài, làm tròn 2 chữ số như VCI.
        out["open"] = pd.to_numeric(df["openPrice"], errors="coerce").round(2)
        out["high"] = pd.to_numeric(df["highestPrice"], errors="coerce").round(2)
        out["low"] = pd.to_numeric(df["lowestPrice"], errors="coerce").round(2)
        out["close"] = pd.to_numeric(df["closePrice"], errors="coerce").round(2)
        out["volume"] = pd.to_numeric(df["totalMatchVolume"], errors="coerce")
        # API không có giá trị giao dịch: ước lượng bằng giá giữa × khối lượng
        # (cùng cách SsiFetcher), chỉ dùng để đánh giá thanh khoản.
        # No traded value in the API: approximate as mid price × volume.
        out["amount"] = (out["high"] + out["low"]) / 2 * out["volume"]
        out["pct_chg"] = (out["close"].pct_change() * 100).round(2)
        return out[STANDARD_COLUMNS]

    # ------------------------------------------------------------------
    # Giá trong phiên + định giá (PriceDepth)
    # ------------------------------------------------------------------
    def get_realtime_quote(self, stock_code: str, **_: Any) -> Optional[UnifiedRealtimeQuote]:
        code, chart_type, ticker = self._resolve_code(stock_code)
        if code is None or chart_type != "Stock":
            # PriceDepth không có chỉ số: để VCI suy ra từ nến ngày.
            return None
        try:
            data = self._get_json(_PRICE_DEPTH_PATH, {"language": "vi", "Code": code})
        except Exception as exc:
            logger.warning("[%s] %s lấy giá realtime thất bại: %s", self.name, code, exc)
            return None

        items = data.get("items") or []
        record = items[0] if items and isinstance(items[0], dict) else {}
        price = record.get("price") if isinstance(record.get("price"), dict) else None
        if str(data.get("status") or "").lower() == "failed" or not price:
            # Mã không tra được: để SSI xử lý.
            logger.info("[%s] PriceDepth không có mã %s (Code=%s)", self.name, ticker, code)
            return None
        bid_ask = record.get("bidAsk") if isinstance(record.get("bidAsk"), dict) else {}
        extra = record.get("extraInfo") if isinstance(record.get("extraInfo"), dict) else {}

        match_price = _to_float(price.get("matchPrice"))
        ref_price = _to_float(price.get("referencePrice"))
        high = _to_float(price.get("highestPrice"))
        low = _to_float(price.get("lowestPrice"))
        volume = _to_float(price.get("totalMatchVolume"))
        market_cap = _to_float(extra.get("rtd11"))
        change_ratio = _to_float(price.get("percentPriceChange"))

        amplitude = None
        if ref_price and high is not None and low is not None:
            amplitude = round((high - low) / ref_price * 100, 2)

        # Tỷ lệ vòng quay tính trên TỔNG số cổ phiếu (= vốn hoá / giá), không phải
        # free-float: API không có tỷ lệ tự do chuyển nhượng.
        # Turnover is computed on total shares (market cap / price), not free float.
        turnover_rate = None
        if market_cap and match_price and volume is not None:
            shares = market_cap / match_price
            if shares > 0:
                turnover_rate = round(volume / shares * 100, 4)

        foreign_buy = _to_float(price.get("foreignBuyValueTotal"))
        foreign_sell = _to_float(price.get("foreignSellValueTotal"))
        foreign_net = (
            foreign_buy - foreign_sell
            if foreign_buy is not None and foreign_sell is not None
            else None
        )

        # Tỷ lệ dư mua trên 3 bước giá tốt nhất: > 0.5 nghĩa là bên mua áp đảo.
        # Top-3 bid share of top-3 bid+ask volume: > 0.5 means bids dominate.
        bid_vol = sum(_to_float(bid_ask.get(f"best{i}BidVolume")) or 0.0 for i in (1, 2, 3))
        ask_vol = sum(_to_float(bid_ask.get(f"best{i}OfferVolume")) or 0.0 for i in (1, 2, 3))
        imbalance = round(bid_vol / (bid_vol + ask_vol), 4) if bid_vol + ask_vol > 0 else None

        roe = _to_float(extra.get("rtq12"))
        ownership = _to_float(extra.get("foreignerPercentage"))
        avg_volume = _to_float(extra.get("averageVolume1Month"))

        quote = UnifiedRealtimeQuote(
            # Luôn trả mã giao dịch, không trả organCode nội bộ / Always the ticker
            code=ticker,
            name="",
            source=RealtimeSource.FALLBACK,
            provider_timestamp=str(price.get("tradingDate") or "") or None,
            market="vn",
            currency="VND",
            price=match_price,
            change_pct=round(change_ratio * 100, 2) if change_ratio is not None else None,
            change_amount=_to_float(price.get("priceChange")),
            volume=int(volume) if volume is not None else None,
            amount=_to_float(price.get("totalMatchValue")),
            turnover_rate=turnover_rate,
            amplitude=amplitude,
            open_price=_to_float(price.get("openPrice")),
            high=high,
            low=low,
            pre_close=ref_price,
            pe_ratio=_round_or_none(_to_float(extra.get("rtd21")), 2),
            pb_ratio=_round_or_none(_to_float(extra.get("rtd25")), 2),
            total_mv=market_cap,
            ref_price=ref_price,
            ceiling=_to_float(price.get("ceilingPrice")),
            floor=_to_float(price.get("floorPrice")),
            eps_ttm=_round_or_none(_to_float(extra.get("rtd14")), 0),
            roe=round(roe * 100, 2) if roe is not None else None,
            beta=_round_or_none(_to_float(extra.get("rtd35")), 2),
            foreign_buy_value=foreign_buy,
            foreign_sell_value=foreign_sell,
            foreign_net_value=foreign_net,
            foreign_room_remaining=_to_float(price.get("foreignCurrentRoom")),
            foreign_ownership_pct=round(ownership * 100, 2) if ownership is not None else None,
            put_through_volume=_to_float(price.get("totalDealVolume")),
            put_through_value=_to_float(price.get("totalDealValue")),
            bid_ask_imbalance=imbalance,
            avg_volume_1m=int(avg_volume) if avg_volume is not None else None,
        )
        # Không có free-float và KL chuẩn hoá theo thời gian trong phiên.
        quote.missing_fields = [
            field for field in ("circ_mv", "volume_ratio", "pe_ratio", "pb_ratio", "total_mv")
            if getattr(quote, field, None) is None
        ]
        quote.data_quality = "partial" if quote.missing_fields else "ok"
        # Giữ cùng thuộc tính gắn thêm như SsiFetcher để cảnh báo trần/sàn dùng chung.
        setattr(quote, "vn_ceiling", quote.ceiling)
        setattr(quote, "vn_floor", quote.floor)
        setattr(
            quote,
            "vn_exchange",
            _EXCHANGE_BY_GROUP.get(str(price.get("comGroupCode") or "").upper(), ""),
        )
        return quote

    # ------------------------------------------------------------------
    # Sự kiện doanh nghiệp (GetStockEvents)
    # ------------------------------------------------------------------
    def get_corporate_events(
        self,
        stock_code: str,
        lookback_days: int = _EVENTS_LOOKBACK_DAYS,
        today: Optional[date] = None,
    ) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """Cổ tức, phát hành, giao dịch nội bộ, ngày công bố KQKD của một mã.

        Trả ``None`` cho chỉ số; lỗi mạng ném ngoại lệ để tầng fundamentals ghi
        ``failed``. Mỗi danh sách sắp xếp mới nhất trước.
        Returns ``None`` for indices; each list is newest first.
        """

        code, chart_type, _ticker = self._resolve_code(stock_code)
        if code is None or chart_type != "Stock":
            return None

        today = today or datetime.now().date()
        cache_key = f"{code}:{lookback_days}:{today.isoformat()}"
        with _events_lock:
            cached = _events_cache.get(cache_key)
            if cached and (time.time() - float(cached.get("ts") or 0)) < _EVENTS_CACHE_TTL:
                return cached["data"]

        start = today - timedelta(days=lookback_days)
        params = {
            "language": "vi",
            "OrganCode": code,
            "From": f"{start.isoformat()}T00:00:00.000Z",
            "To": f"{(today + timedelta(days=1)).isoformat()}T00:00:00.000Z",
        }
        data = self._get_json(_EVENTS_PATH, params)
        if str(data.get("status") or "").lower() == "failed":
            raise DataFetchError(f"[{self.name}] FiinTrade trả về lỗi: {data.get('errors') or data}")

        events = self._normalize_events(data.get("items") or [])
        with _events_lock:
            _events_cache[cache_key] = {"ts": time.time(), "data": events}
        return events

    @staticmethod
    def _normalize_events(items: List[Any]) -> Dict[str, List[Dict[str, Any]]]:
        dividends: List[Dict[str, Any]] = []
        issuances: List[Dict[str, Any]] = []
        insiders: List[Dict[str, Any]] = []
        earnings: List[Dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "")
            public_date = _date_part(item.get("publicDate"))
            if kind == "Dividend":
                record_date = _date_part(item.get("exrightDate"))
                dividends.append({
                    "ex_date": _previous_weekday(record_date),
                    "record_date": record_date,
                    "public_date": public_date,
                    "cash_per_share": _to_float(item.get("valuePershare")),
                    "rate_of_par": _to_float(item.get("exerciseRate")),
                })
            elif kind == "ShareIssuance":
                record_date = _date_part(item.get("exrightDate"))
                issuances.append({
                    "method": str(item.get("issueMethodName") or "").strip(),
                    "method_en": str(item.get("en_IssueMethodName") or "").strip(),
                    "ex_date": _previous_weekday(record_date),
                    "record_date": record_date,
                    "public_date": public_date,
                    "volume": _to_float(item.get("issueVolumn")),
                    "ratio": _to_float(item.get("exerciseRatio")),
                    "status": str(item.get("issueStatusCode") or "").strip(),
                })
            elif kind == "InternalTransaction":
                action = str(item.get("actionTypeCode") or "").strip().upper()
                insiders.append({
                    "name": str(item.get("fullName") or "").strip(),
                    "action": "buy" if action == "B" else ("sell" if action == "S" else action.lower()),
                    "shares_registered": _to_float(item.get("shareRegister")),
                    "shares_executed": _to_float(item.get("shareAcquire")),
                    "status": str(item.get("tradeStatusCode") or "").strip(),
                    "public_date": public_date,
                })
            elif kind == "Earning":
                earnings.append({
                    "year": item.get("yearReport"),
                    "quarter": item.get("lengthReport"),
                    "public_date": public_date,
                })

        def _newest_first(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
            return sorted(rows, key=lambda row: row.get(key) or row.get("public_date") or "", reverse=True)

        return {
            "dividends": _newest_first(dividends, "ex_date"),
            "share_issuances": _newest_first(issuances, "ex_date"),
            "insider_transactions": _newest_first(insiders, "public_date"),
            "earnings_releases": _newest_first(earnings, "public_date"),
        }

    # ------------------------------------------------------------------
    # Điểm lại thị trường: chỉ số, độ rộng, dòng tiền
    # ------------------------------------------------------------------
    def _cached_market(self, key: str, ttl: float, loader) -> Any:
        with _market_lock:
            cached = _market_cache.get(key)
            if cached and (time.time() - float(cached.get("ts") or 0)) < ttl:
                return cached["data"]
        data = loader()
        if data:
            with _market_lock:
                _market_cache[key] = {"ts": time.time(), "data": data}
        return data

    def _load_latest_indices(self) -> Dict[str, Dict[str, Any]]:
        """Snapshot mọi chỉ số, khoá theo ``comGroupCode``; cache 30 giây."""

        def _load() -> Dict[str, Dict[str, Any]]:
            data = self._get_json(
                _LATEST_INDICES_PATH,
                {"language": "vi", "pageSize": 999999, "status": 1},
                base_url=FIINTRADE_MARKET_BASE_URL,
            )
            if str(data.get("status") or "").lower() == "failed":
                raise DataFetchError(f"[{self.name}] FiinTrade trả về lỗi: {data.get('errors') or data}")
            return {
                str(item.get("comGroupCode")): item
                for item in data.get("items") or []
                if isinstance(item, dict) and item.get("comGroupCode")
            }

        return self._cached_market("latest_indices", _INDICES_CACHE_TTL, _load) or {}

    def get_main_indices(self, region: str = "vn") -> Optional[List[Dict[str, Any]]]:
        if region != "vn":
            return None
        indices = self._load_latest_indices()
        results: List[Dict[str, Any]] = []
        for fiin_code, code, name in _MAIN_INDEX_CODES:
            item = indices.get(fiin_code)
            current = _to_float(item.get("indexValue")) if item else None
            if current is None:
                continue
            ref = _to_float(item.get("referenceIndex"))
            high = _to_float(item.get("highestIndex")) or current
            low = _to_float(item.get("lowestIndex")) or current
            ratio = _to_float(item.get("percentIndexChange"))
            matched = _to_float(item.get("totalMatchValue")) or 0.0
            deal = _to_float(item.get("totalDealValue")) or 0.0
            foreign_buy = _to_float(item.get("foreignBuyValueTotal"))
            foreign_sell = _to_float(item.get("foreignSellValueTotal"))
            results.append({
                "code": code,
                "name": name,
                "current": current,
                "change": _to_float(item.get("indexChange")) or 0.0,
                "change_pct": round(ratio * 100, 2) if ratio is not None else 0.0,
                "open": _to_float(item.get("openIndex")) or current,
                "high": high,
                "low": low,
                "prev_close": ref or 0.0,
                "volume": (_to_float(item.get("totalMatchVolume")) or 0.0)
                + (_to_float(item.get("totalDealVolume")) or 0.0),
                # Khớp lệnh + thoả thuận (VND), cùng ngữ nghĩa amount của VCI.
                # Matched + put-through (VND), same meaning as VCI's amount.
                "amount": matched + deal,
                "amplitude": round((high - low) / ref * 100, 2) if ref else 0.0,
                "matched_amount": matched,
                "put_through_amount": deal,
                "foreign_net_amount": (
                    foreign_buy - foreign_sell
                    if foreign_buy is not None and foreign_sell is not None
                    else None
                ),
                "up_count": item.get("totalStockUpPrice"),
                "down_count": item.get("totalStockDownPrice"),
            })
        return results or None

    def get_region_market_stats(self, region: str) -> Optional[Dict[str, Any]]:
        """Độ rộng toàn thị trường = cộng ba chỉ số sàn (HOSE + HNX + UPCoM).

        ``total_amount`` (tỷ đồng) chỉ gồm **khớp lệnh**, giữ đúng ngữ nghĩa của
        SsiFetcher; thoả thuận và khối ngoại ròng nằm ở key riêng.
        Market breadth summed over the three exchange indices; ``total_amount`` is
        matched value only (same as SSI), put-through and foreign net are separate.
        """

        if region != "vn":
            return None
        indices = self._load_latest_indices()
        totals = {
            "up_count": 0, "down_count": 0, "flat_count": 0,
            "limit_up_count": 0, "limit_down_count": 0,
        }
        matched = deal = foreign_net = 0.0
        by_exchange: Dict[str, Dict[str, Any]] = {}
        for exchange, fiin_code in _EXCHANGE_INDEX_CODES:
            item = indices.get(fiin_code)
            if not item:
                continue
            row = {
                "up_count": int(item.get("totalStockUpPrice") or 0),
                "down_count": int(item.get("totalStockDownPrice") or 0),
                "flat_count": int(item.get("totalStockNoChangePrice") or 0),
                "limit_up_count": int(_to_float(item.get("totalStockOverCeiling")) or 0),
                "limit_down_count": int(_to_float(item.get("totalStockUnderFloor")) or 0),
                "total_amount": _to_billion(_to_float(item.get("totalMatchValue")) or 0.0),
                "put_through_amount": _to_billion(_to_float(item.get("totalDealValue")) or 0.0),
                "foreign_net_amount": _to_billion(
                    (_to_float(item.get("foreignBuyValueTotal")) or 0.0)
                    - (_to_float(item.get("foreignSellValueTotal")) or 0.0)
                ),
            }
            by_exchange[exchange] = row
            for key in totals:
                totals[key] += row[key]
            matched += _to_float(item.get("totalMatchValue")) or 0.0
            deal += _to_float(item.get("totalDealValue")) or 0.0
            foreign_net += (_to_float(item.get("foreignBuyValueTotal")) or 0.0) - (
                _to_float(item.get("foreignSellValueTotal")) or 0.0
            )

        if not by_exchange or totals["up_count"] + totals["down_count"] + totals["flat_count"] == 0:
            return None
        return {
            **totals,
            "total_amount": _to_billion(matched),
            "put_through_amount": _to_billion(deal),
            "foreign_net_amount": _to_billion(foreign_net),
            "by_exchange": by_exchange,
        }

    @staticmethod
    def _flow_top(rows: Any, net_key: str, limit: int = _FLOW_TOP_N) -> List[Dict[str, Any]]:
        """Top mã theo giá trị ròng; bỏ chứng quyền/trái phiếu (chỉ giữ CP/ETF)."""

        top: List[Dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            if not is_vn_security_code(ticker):
                continue
            net = _to_float(row.get(net_key))
            if net is None:
                continue
            change = _to_float(row.get("percentPriceChange"))
            top.append({
                "ticker": ticker,
                "net": _to_billion(net),
                "change_pct": round(change * 100, 2) if change is not None else None,
            })
            if len(top) >= limit:
                break
        return top

    def _parse_flow(
        self, block: Any, buy_key: str, sell_key: str, net_key: str
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(block, dict):
            return None
        buy = _to_float(block.get(buy_key))
        sell = _to_float(block.get(sell_key))
        if buy is None or sell is None:
            return None
        return {
            "from": _date_part(block.get("fromDate")),
            "to": _date_part(block.get("toDate")),
            "buy": _to_billion(buy),
            "sell": _to_billion(sell),
            # Ròng thị trường = mua − bán (không dùng foreignNetBuyValue của khối tổng).
            "net": _to_billion(buy - sell),
            "top_net_buy": self._flow_top(block.get("netBuy"), net_key),
            "top_net_sell": self._flow_top(block.get("netSell"), net_key),
        }

    def _load_flow(
        self, path: str, buy_key: str, sell_key: str, net_key: str
    ) -> Optional[Dict[str, Any]]:
        data = self._get_json(
            path,
            {"language": "vi", "ComGroupCode": _FLOW_SCOPE_CODE},
            base_url=FIINTRADE_MARKET_BASE_URL,
        )
        if str(data.get("status") or "").lower() == "failed":
            raise DataFetchError(f"[{self.name}] FiinTrade trả về lỗi: {data.get('errors') or data}")
        items = data.get("items") or []
        record = items[0] if items and isinstance(items[0], dict) else {}
        periods: Dict[str, Any] = {}
        for api_key, out_key in _FLOW_PERIODS:
            parsed = self._parse_flow(record.get(api_key), buy_key, sell_key, net_key)
            if parsed:
                periods[out_key] = parsed
        return periods or None

    def get_market_money_flow(self, region: str = "vn") -> Optional[Dict[str, Any]]:
        """Dòng tiền khối ngoại và tự doanh sàn HOSE theo kỳ (tỷ đồng).

        Mỗi nhánh fail-open: một nhánh lỗi thì nhánh kia vẫn được trả. Khoảng ngày
        của từng kỳ giữ nguyên ``from``/``to`` của API (tự doanh có thể gộp hai phiên).
        Foreign and proprietary (dealer) flows on HOSE; each branch is fail-open.
        """

        if region != "vn":
            return None

        def _load() -> Optional[Dict[str, Any]]:
            result: Dict[str, Any] = {"scope": "HOSE", "unit": "VND bn"}
            try:
                foreign = self._load_flow(
                    _FOREIGN_FLOW_PATH, "foreignBuyValue", "foreignSellValue", "foreignNetBuyValue"
                )
                if foreign:
                    result["foreign"] = foreign
            except Exception as exc:
                logger.warning("[%s] Lấy dòng tiền khối ngoại thất bại: %s", self.name, exc)
            try:
                proprietary = self._load_flow(
                    _PROPRIETARY_FLOW_PATH,
                    "totalBuyTradeValue",
                    "totalSellTradeValue",
                    "totalNetBuyTradeValue",
                )
                if proprietary:
                    result["proprietary"] = proprietary
            except Exception as exc:
                logger.warning("[%s] Lấy dòng tiền tự doanh thất bại: %s", self.name, exc)
            return result if ("foreign" in result or "proprietary" in result) else None

        return self._cached_market("money_flow", _MONEY_FLOW_CACHE_TTL, _load)

    # ------------------------------------------------------------------
    # Động lực thị trường: mã dẫn dắt chỉ số, đột biến KL, diễn biến trong ngày
    # ------------------------------------------------------------------
    def _market_items(self, path: str, params: Dict[str, Any]) -> List[Any]:
        data = self._get_json(path, {"language": "vi", **params}, base_url=FIINTRADE_MARKET_BASE_URL)
        if str(data.get("status") or "").lower() == "failed":
            raise DataFetchError(f"[{self.name}] FiinTrade trả về lỗi: {data.get('errors') or data}")
        items = data.get("items")
        return items if isinstance(items, list) else []

    @staticmethod
    def _contribution_rows(rows: Any, with_change: bool) -> List[Dict[str, Any]]:
        top: List[Dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            # Dùng ``ticker``: ``organCode`` là mã nội bộ (NHN = VHM).
            # Use ``ticker``; ``organCode`` is FiinTrade's internal code.
            ticker = str(row.get("ticker") or "").strip().upper()
            points = _to_float(row.get("contribution"))
            if not ticker or points is None:
                continue
            entry: Dict[str, Any] = {"ticker": ticker, "points": round(points, 2)}
            # ``percentPriceChange`` luôn là % của phiên hiện tại, kể cả ở khối 5/20
            # phiên, nên chỉ gắn cho kỳ 1 phiên.
            # ``percentPriceChange`` is always today's move, so only keep it for 1d.
            change = _to_float(row.get("percentPriceChange")) if with_change else None
            if change is not None:
                entry["change_pct"] = round(change * 100, 2)
            top.append(entry)
            if len(top) >= _CONTRIBUTION_TOP_N:
                break
        return top

    def _load_index_contribution(self) -> Optional[Dict[str, Any]]:
        """Mã kéo/đè điểm VN-Index theo 1/5/20 phiên (điểm chỉ số).

        Top index point contributors (gainers/losers) of VN-Index over 1/5/20 sessions.
        """

        items = self._market_items(_CONTRIBUTION_PATH, {"ComGroupCode": _FLOW_SCOPE_CODE, "Type": "Total"})
        record = items[0] if items and isinstance(items[0], dict) else {}
        periods: Dict[str, Any] = {}
        for api_key, out_key in _CONTRIBUTION_PERIODS:
            block = record.get(api_key)
            if not isinstance(block, dict):
                continue
            with_change = out_key == "1d"
            gainers = self._contribution_rows(block.get("topGainers"), with_change)
            losers = self._contribution_rows(block.get("topLosers"), with_change)
            if gainers or losers:
                periods[out_key] = {"top_gainers": gainers, "top_losers": losers}
        if not periods:
            return None
        series = record.get("series") if isinstance(record.get("series"), dict) else {}
        return {
            "index": "VNINDEX",
            "index_change": _round_or_none(_to_float(series.get("indexChange")), 2),
            "periods": periods,
        }

    def _load_volume_breakouts(self) -> Optional[Dict[str, Any]]:
        """Mã có KL phiên ≥ 1,5 lần bình quân 1 tuần, xếp theo giá trị giao dịch.

        Volume breakouts (>= 1.5x the 1-week average), ranked by traded value.
        """

        rows: List[Dict[str, Any]] = []
        for row in self._market_items(_TOP_BREAKOUT_PATH, _BREAKOUT_PARAMS):
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            volume = _to_float(row.get("volume"))
            average = _to_float(row.get("averageMatchVolume"))
            if not is_vn_security_code(ticker) or not volume or not average:
                continue
            change = _to_float(row.get("percentPriceChange"))
            rows.append({
                "ticker": ticker,
                "sector": str(row.get("sectorName") or "").strip() or None,
                "change_pct": round(change * 100, 2) if change is not None else None,
                "volume_ratio": round(volume / average, 2),
                "value": _to_billion(_to_float(row.get("value"))),
            })
        if not rows:
            return None
        rows.sort(key=lambda r: r.get("value") or 0.0, reverse=True)
        return {"min_ratio": 1.5, "baseline": "1-week average volume", "items": rows[:_BREAKOUT_TOP_N]}

    def _load_index_intraday(self) -> Optional[Dict[str, Any]]:
        """Diễn biến VN-Index trong phiên từ chuỗi điểm theo phút.

        Trả điểm đầu phiên, hết phiên sáng, cao/thấp kèm giờ, điểm cuối và tỷ trọng
        giá trị khớp từ 14:30 (gồm ATC) khi phiên đã qua mốc đó.
        Intraday VN-Index path from the per-minute series.
        """

        points: List[Tuple[str, float, Optional[float]]] = []
        for row in self._market_items(_INDEX_SERIES_PATH, {"ComGroupCode": _FLOW_SCOPE_CODE, "TimeRange": "OneDay"}):
            if not isinstance(row, dict):
                continue
            stamp = str(row.get("tradingDate") or "")
            value = _to_float(row.get("indexValue"))
            if len(stamp) < 16 or value is None:
                continue
            points.append((stamp, value, _to_float(row.get("totalMatchValue"))))
        if len(points) < 2:
            return None
        points.sort(key=lambda p: p[0])
        high = max(points, key=lambda p: p[1])
        low = min(points, key=lambda p: p[1])
        morning = [p for p in points if p[0][11:16] <= _MORNING_CLOSE_TIME]
        result: Dict[str, Any] = {
            "index": "VNINDEX",
            "date": points[-1][0][:10],
            "first": {"time": points[0][0][11:16], "value": points[0][1]},
            "morning_close": (
                {"time": morning[-1][0][11:16], "value": morning[-1][1]} if morning else None
            ),
            "high": {"time": high[0][11:16], "value": high[1]},
            "low": {"time": low[0][11:16], "value": low[1]},
            "last": {"time": points[-1][0][11:16], "value": points[-1][1]},
        }
        before_close = [p for p in points if p[0][11:16] <= _CLOSING_WINDOW_START and p[2]]
        total = points[-1][2]
        if before_close and total and points[-1][0][11:16] > _CLOSING_WINDOW_START:
            # Tỷ trọng giá trị khớp từ 14:30 tới cuối phiên / Share of matched value after 14:30
            result["closing_window_value_share"] = round((total - before_close[-1][2]) / total * 100, 1)
        return result

    def get_market_drivers(self, region: str = "vn") -> Optional[Dict[str, Any]]:
        """Động lực thị trường: mã dẫn dắt VN-Index, đột biến KL, diễn biến trong phiên.

        Mỗi nhánh fail-open: nhánh lỗi chỉ log, các nhánh còn lại vẫn được trả.
        Market drivers (index contributors, volume breakouts, intraday path); fail-open.
        """

        if region != "vn":
            return None

        def _load() -> Optional[Dict[str, Any]]:
            result: Dict[str, Any] = {}
            for key, loader, label in (
                ("index_contribution", self._load_index_contribution, "mã dẫn dắt chỉ số"),
                ("volume_breakouts", self._load_volume_breakouts, "mã đột biến khối lượng"),
                ("intraday", self._load_index_intraday, "diễn biến trong phiên"),
            ):
                try:
                    data = loader()
                except Exception as exc:
                    logger.warning("[%s] Lấy %s thất bại: %s", self.name, label, exc)
                    continue
                if data:
                    result[key] = data
            return result or None

        return self._cached_market("market_drivers", _DRIVERS_CACHE_TTL, _load)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover - đóng session không được làm hỏng luồng
            pass
