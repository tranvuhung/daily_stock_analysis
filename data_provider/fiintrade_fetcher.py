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
3. **Không có UPCOM**: mã không tồn tại (gồm toàn bộ mã UPCOM) trả HTTP 200 kèm
   ``status="Failed"`` và lỗi ``Sequence contains no matching element``. Fetcher
   trả DataFrame rỗng để manager chuyển sang VCI mà không mở circuit breaker.
4. **Chỉ số** dùng ``Type=Index``. ``VNINDEX``/``VN30``/``VN100``/``HNXIndex``/
   ``HNX30`` khớp close với VCI. ``UpcomIndex`` của FiinTrade **lệch close** so với
   ``HNXUpcomIndex`` của VCI (16/10/2026: 124.87 vs 125.97), nên chỉ số UPCoM
   không lấy từ đây để tránh trộn hai chuỗi trong ``stock_daily``.
5. **Khối lượng** không trùng hoàn toàn VCI: cổ phiếu lệch < 1% (FiinTrade có vẻ
   gồm cả lô lẻ), chỉ số thấp hơn ~10-15% (chỉ khớp lệnh). Giá thì trùng.
6. **Thời gian**: ``From``/``To`` là UTC, nến ngày đóng dấu 00:00 giờ VN. Nến
   hôm nay trong phiên là nến dở, có kèm giờ (``2026-10-21T14:30:00.068+07:00``).
7. **Thiếu** giá trị giao dịch, giá tham chiếu, trần/sàn: ``amount`` được ước
   lượng (xem ``_normalize_data``).

PriceDepth (kiểm chứng 2026-10-21):

8. Chỉ số và mã UPCOM trả ``status="Failed"``: ``get_realtime_quote`` trả ``None``
   để manager chuyển sang SSI/VCI.
9. ``percentPriceChange`` là tỷ lệ (``0.0133`` = 1.33%).
10. ``extraInfo`` dùng mã chỉ tiêu nội bộ, ý nghĩa **suy ra** bằng đối chiếu chéo:
    ``rtd11`` vốn hoá (VND, = giá × số CP), ``rtd14`` EPS TTM, ``rtd21`` P/E
    (= giá / rtd14), ``rtd25`` P/B, ``rtq12`` ROE (tỷ lệ), ``rtd35`` beta (ước đoán).

GetStockEvents (kiểm chứng 2026-10-21):

11. ``exrightDate`` là **ngày đăng ký cuối cùng**, không phải ngày GDKHQ: nó luôn
    đứng sau ngày có ``rateAdjusted`` của API biểu đồ đúng một phiên (FPT
    28/11/2025 → 01/12/2025, 18/09/2026 → 21/09/2026). GDKHQ = phiên liền trước.
12. Không trả sự kiện tương lai; mã UPCOM trả danh sách rỗng. Số liệu
    ``revenue``/``profit`` của sự kiện ``Earning`` chưa rõ loại lợi nhuận nên
    không được dùng, chỉ lấy kỳ báo cáo và ngày công bố.
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

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS, is_vn_index_code
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote

logger = logging.getLogger(__name__)

FIINTRADE_BASE_URL = os.getenv(
    "FIINTRADE_BASE_URL", "https://wlgw-technical.fiintrade.vn"
).rstrip("/")
_CHART_PATH = "/TradingView/GetStockChartData"
_PRICE_DEPTH_PATH = "/PriceDepth/GetPriceDepth"
_EVENTS_PATH = "/TradingView/GetStockEvents"

_DEFAULT_TIMEOUT = 20.0
_MIN_INTERVAL_SECONDS = 0.3
_JITTER_SECONDS = 0.2

# Thông báo lỗi của server khi không tìm thấy mã (UPCOM, mã sai, đã huỷ niêm yết).
# Server error text when the symbol is unknown (UPCOM, typo, delisted).
_NOT_FOUND_MARKER = "no matching element"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Chỉ số lấy từ FiinTrade, khoá là mã nội bộ viết hoa (VN_INDEX_SYMBOLS). Chỉ số
# UPCoM cố ý không có mặt: ``UpcomIndex`` lệch close so với VCI, để VCI phục vụ.
# Indices served by FiinTrade; UPCoM is deliberately left to VCI (close drifts).
_FIIN_INDEX_SYMBOLS = {
    "VNINDEX": "VNINDEX",
    "VN30": "VN30",
    "VN100": "VN100",
    "HNXINDEX": "HNXIndex",
    "HNX30": "HNX30",
}

# Sự kiện doanh nghiệp ít thay đổi trong ngày: cache theo mã 6 giờ.
# Corporate events rarely change intraday: cache per symbol for 6 hours.
_EVENTS_CACHE_TTL = 6 * 3600.0
_EVENTS_LOOKBACK_DAYS = 730

# Sàn niêm yết theo ``comGroupCode`` của PriceDepth (khớp ``vn_exchange`` của SSI).
_EXCHANGE_BY_GROUP = {"VNINDEX": "hose", "HNXINDEX": "hnx"}

_last_request_at = 0.0
_request_lock = RLock()
_events_cache: Dict[str, Dict[str, Any]] = {}
_events_lock = RLock()


def reset_events_cache() -> None:
    """Xoá cache sự kiện (dùng cho test). / Clear the events cache (for tests)."""

    with _events_lock:
        _events_cache.clear()


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
    # Mã không có trên FiinTrade (UPCOM) trả rỗng: manager ghi nhận là "rỗng" chứ
    # không phải lỗi, nên không làm mở circuit breaker, rồi chuyển sang VCI.
    # Unknown symbols (UPCOM) return empty so the manager falls through to VCI
    # without tripping the circuit breaker.
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

        Chỉ số không được FiinTrade phục vụ (UPCoM) trả ``Code=None``.
        """

        normalized = (stock_code or "").strip().upper()
        if normalized.endswith(".VN"):
            normalized = normalized[:-3]
        if is_vn_index_code(normalized):
            return _FIIN_INDEX_SYMBOLS.get(normalized), "Index"
        return normalized, "Stock"

    # ------------------------------------------------------------------
    # Nến ngày
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError, TimeoutError)),
        reraise=True,
    )
    def _get_json(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._enforce_rate_limit()
        response = self._session.get(
            f"{FIINTRADE_BASE_URL}{path}", params=params, timeout=self.timeout
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
        code, chart_type = self.to_provider_symbol(stock_code)
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
                # Mã không có trên FiinTrade (thường là UPCOM): để VCI xử lý.
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
        code, chart_type = self.to_provider_symbol(stock_code)
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
            # UPCOM / mã không có: để SSI xử lý.
            logger.info("[%s] PriceDepth không có mã %s", self.name, code)
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
            code=code,
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

        code, chart_type = self.to_provider_symbol(stock_code)
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

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover - đóng session không được làm hỏng luồng
            pass
