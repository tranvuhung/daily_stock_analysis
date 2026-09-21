# -*- coding: utf-8 -*-
"""
===================================
Nguồn dữ liệu VCI (Vietcap) - nến ngày và bảng mã thị trường Việt Nam
===================================

Các endpoint công khai, không cần API Key:

- ``POST /api/chart/OHLCChart/gap-chart`` nến ngày
- ``GET  /api/price/symbols/getAll``      bảng mã đầy đủ (kèm tên công ty tiếng
  Việt và sàn niêm yết)

Hành vi API đã kiểm chứng trực tiếp (thiết kế dựa trên đây, không phải suy đoán):

1. **Mỗi request chỉ tra được một mã.** Trường ``symbols`` trong body tuy là
   mảng, nhưng khi truyền nhiều mã thì server trả về mảng rỗng ``[]``. Vì vậy
   nguồn này không hiện thực ``prefetch_daily_klines``; việc prefetch hàng loạt
   do snapshot toàn thị trường của SSI đảm nhận.
2. **Mã chỉ số phân biệt hoa/thường.** ``HNXINDEX`` trả về rỗng, phải là
   ``HNXIndex`` mới có dữ liệu. Cách viết chính xác được lưu tại
   :data:`data_provider.base.VN_INDEX_SYMBOLS`.
3. **Response được nén gzip**, ``requests`` tự động giải nén.
4. Đơn vị của ``accumulatedValue`` là **triệu đồng**, phải nhân 1e6 mới khớp
   đơn vị của ``amount`` (VND) trong ``STANDARD_COLUMNS``.
5. ``countBack=300`` lấy được 300 phiên giao dịch, đủ độ sâu lịch sử cho phân
   tích hằng ngày.
"""

import logging
import os
import random
import time
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    get_vn_index_provider_symbol,
    is_vn_index_code,
)

logger = logging.getLogger(__name__)

VCI_BASE_URL = os.getenv("VCI_BASE_URL", "https://trading.vietcap.com.vn").rstrip("/")
_CHART_PATH = "/api/chart/OHLCChart/gap-chart"
_SYMBOLS_PATH = "/api/price/symbols/getAll"

_DEFAULT_TIMEOUT = 20.0
# Bảng mã khoảng 165 KB và gần như không đổi, cache một ngày là đủ; 0 nghĩa là
# lần nào cũng tải lại.
_SYMBOLS_CACHE_TTL = 86400.0

# Đây là endpoint công khai do chính công ty chứng khoán vận hành, không công bố
# hạn mức. Theo đúng cách repo vẫn làm với các nguồn miễn phí: giữ khoảng cách
# tối thiểu giữa các request + jitter, tránh kích hoạt cơ chế chặn phía đối tác.
_MIN_INTERVAL_SECONDS = 0.6
_JITTER_SECONDS = 0.4

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Cache bảng mã ở cấp module, dùng chung giữa các instance manager (cùng cách làm
# với cache snapshot toàn thị trường của akshare/efinance).
_symbols_cache: Dict[str, Any] = {"data": None, "ts": 0.0}
_symbols_lock = RLock()
_last_request_at = 0.0
_request_lock = RLock()


def _parse_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r không phải số hợp lệ, quay về mặc định %s", name, raw, default)
        return default


class VciFetcher(BaseFetcher):
    """Nguồn dữ liệu nến ngày thị trường Việt Nam (API công khai của Vietcap)."""

    name = "VciFetcher"
    priority = int(os.getenv("VCI_PRIORITY", "0"))
    # Chỉ số vào ngày không giao dịch, hoặc mã mới niêm yết, có thể trả về mảng
    # rỗng. Để tầng trên tiếp tục fallback thay vì coi ngay là lỗi.
    allow_empty_daily_data = False

    def __init__(
        self,
        timeout: Optional[float] = None,
        min_interval: Optional[float] = None,
    ) -> None:
        self.timeout = timeout if timeout is not None else _parse_env_float(
            "VCI_CALL_TIMEOUT", _DEFAULT_TIMEOUT
        )
        self.min_interval = (
            min_interval
            if min_interval is not None
            else _parse_env_float("VCI_MIN_INTERVAL", _MIN_INTERVAL_SECONDS)
        )
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": _UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

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
    # Mã nội bộ -> symbol của provider
    # ------------------------------------------------------------------
    @staticmethod
    def to_provider_symbol(stock_code: str) -> str:
        """Chuyển mã nội bộ sang cách viết mà VCI yêu cầu.

        ``HPG.VN`` -> ``HPG``; các chỉ số đã đăng ký được trả về theo đúng cách
        viết phân biệt hoa/thường.
        """

        normalized = (stock_code or "").strip().upper()
        if normalized.endswith(".VN"):
            normalized = normalized[:-3]

        index_symbol = get_vn_index_provider_symbol(normalized)
        if index_symbol:
            return index_symbol
        return normalized

    # ------------------------------------------------------------------
    # Nến ngày
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError, TimeoutError)),
        reraise=True,
    )
    def _post_chart(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        self._enforce_rate_limit()
        response = self._session.post(
            f"{VCI_BASE_URL}{_CHART_PATH}", json=payload, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise DataFetchError(
                f"[{self.name}] VCI trả về lỗi: {data.get('message') or data}"
            )
        if not isinstance(data, list):
            raise DataFetchError(
                f"[{self.name}] VCI trả về cấu trúc không mong đợi: {type(data).__name__}"
            )
        return data

    @staticmethod
    def _estimate_count_back(start_date: str, end_date: str) -> int:
        """Ước lượng số nến ngày cần lấy ngược lại theo khoảng thời gian lịch.

        Việt Nam giao dịch 5 phiên/tuần, ở đây quy đổi theo tỷ lệ 5/7 rồi cộng
        thêm một khoảng đệm để phủ các kỳ nghỉ dài như Tết. Tầng trên vẫn cắt
        chính xác lại theo ``start_date``, nên thà lấy dư còn hơn thiếu.
        """

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return 60

        calendar_days = max((end - start).days, 1)
        estimated = int(calendar_days * 5 / 7) + 15
        return max(20, min(estimated, 2000))

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        symbol = self.to_provider_symbol(stock_code)
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            end_dt = datetime.now()
        # Lấy mốc 23:59:59 của ngày đó để lệch múi giờ không cắt mất nến mới nhất.
        to_epoch = int(
            end_dt.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp()
        )

        payload = {
            "timeFrame": "ONE_DAY",
            "symbols": [symbol],
            "to": to_epoch,
            "countBack": self._estimate_count_back(start_date, end_date),
        }
        series = self._post_chart(payload)
        if not series:
            logger.info(
                "[%s] %s (symbol=%s) trả về kết quả rỗng: %s ~ %s",
                self.name,
                stock_code,
                symbol,
                start_date,
                end_date,
            )
            return pd.DataFrame()

        record = series[0]
        raw = pd.DataFrame(
            {
                "t": record.get("t") or [],
                "o": record.get("o") or [],
                "h": record.get("h") or [],
                "l": record.get("l") or [],
                "c": record.get("c") or [],
                "v": record.get("v") or [],
                "value": record.get("accumulatedValue")
                or [None] * len(record.get("t") or []),
            }
        )
        if raw.empty:
            return raw

        # Cắt theo start_date: countBack chỉ diễn tả "đếm ngược bao nhiêu nến",
        # không biểu diễn được ngày bắt đầu.
        try:
            start_ts = pd.Timestamp(start_date)
            dates = pd.to_datetime(pd.to_numeric(raw["t"]), unit="s")
            raw = raw[dates >= start_ts]
        except Exception as exc:  # pragma: no cover - cắt lỗi không nên làm hỏng cả luồng
            logger.debug("[%s] %s bỏ qua bước cắt theo ngày bắt đầu: %s", self.name, stock_code, exc)

        return raw.reset_index(drop=True)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        out = pd.DataFrame()
        out["date"] = pd.to_datetime(pd.to_numeric(df["t"]), unit="s")
        out["open"] = pd.to_numeric(df["o"], errors="coerce")
        out["high"] = pd.to_numeric(df["h"], errors="coerce")
        out["low"] = pd.to_numeric(df["l"], errors="coerce")
        out["close"] = pd.to_numeric(df["c"], errors="coerce")
        out["volume"] = pd.to_numeric(df["v"], errors="coerce")
        # accumulatedValue có đơn vị triệu đồng, quy về VND để khớp STANDARD_COLUMNS
        out["amount"] = pd.to_numeric(df.get("value"), errors="coerce") * 1_000_000
        # API không trả % thay đổi, tự suy ra từ giá đóng cửa; nến đầu tiên không có
        # giá tham chiếu nên để trống, _clean_data sẽ xử lý
        out["pct_chg"] = out["close"].pct_change() * 100
        out["pct_chg"] = out["pct_chg"].round(2)
        return out[STANDARD_COLUMNS]

    # ------------------------------------------------------------------
    # Bảng mã / tên cổ phiếu
    # ------------------------------------------------------------------
    def _load_symbols(self) -> List[Dict[str, Any]]:
        with _symbols_lock:
            cached = _symbols_cache.get("data")
            ts = float(_symbols_cache.get("ts") or 0.0)
            if cached is not None and (time.time() - ts) < _SYMBOLS_CACHE_TTL:
                return cached

        self._enforce_rate_limit()
        response = self._session.get(
            f"{VCI_BASE_URL}{_SYMBOLS_PATH}", timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise DataFetchError(
                f"[{self.name}] bảng mã trả về cấu trúc không mong đợi: {type(data).__name__}"
            )

        with _symbols_lock:
            _symbols_cache["data"] = data
            _symbols_cache["ts"] = time.time()
        logger.info("[%s] Đã tải bảng mã Việt Nam: %d dòng", self.name, len(data))
        return data

    def get_symbol_records(self) -> List[Dict[str, Any]]:
        """Trả về bảng mã đầy đủ (gồm ``symbol`` / ``board`` / ``organName``).

        Dùng lại cho script sinh index autocomplete của frontend, để script không
        phải hiện thực lại logic tải dữ liệu một lần nữa.
        """

        try:
            return self._load_symbols()
        except Exception as exc:
            logger.warning("[%s] Tải bảng mã thất bại: %s", self.name, exc)
            return []

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """Trả về tên viết tắt tiếng Việt của công ty; thất bại trả None để tầng
        trên tiếp tục fallback."""

        symbol = self.to_provider_symbol(stock_code)
        if is_vn_index_code(stock_code):
            return symbol

        try:
            records = self._load_symbols()
        except Exception as exc:
            logger.warning("[%s] %s tra cứu tên thất bại: %s", self.name, stock_code, exc)
            return None

        for record in records:
            if str(record.get("symbol") or "").upper() == symbol:
                name = (
                    record.get("organShortName")
                    or record.get("organName")
                    or record.get("enOrganShortName")
                    or record.get("enOrganName")
                )
                return str(name).strip() or None
        return None

    # ------------------------------------------------------------------
    # Giá realtime của chỉ số (suy ra từ nến ngày mới nhất)
    # ------------------------------------------------------------------
    def get_realtime_quote(self, stock_code: str, **_: Any):
        """Chỉ cung cấp giá cho **các chỉ số Việt Nam đã đăng ký**, suy ra từ nến
        ngày mới nhất.

        Snapshot toàn thị trường của SSI trả về cổ phiếu thành phần theo từng sàn,
        không bao gồm các chỉ số như VNINDEX / VN30. Do đó chỉ số không có nguồn
        nào trên luồng realtime và ``/stocks/{code}/quote`` sẽ trả về 404. Ở đây
        ta bù bằng nến ngày mới nhất của VCI: trong phiên nến này cập nhật theo
        thị trường, sau khi đóng cửa chính là giá đóng cửa trong ngày.

        ``provider_timestamp`` được đặt bằng ngày của nến đó; tầng trên
        ``_enrich_realtime_quote`` dựa vào đây để đánh dấu ``is_stale`` theo
        ``REALTIME_CACHE_TTL``, nên dữ liệu qua đêm không bị nguỵ trang thành giá
        realtime. Cổ phiếu riêng lẻ vẫn đi qua snapshot SSI, không qua đây.
        """

        if not is_vn_index_code(stock_code):
            return None

        from .realtime_types import RealtimeSource, UnifiedRealtimeQuote

        symbol = self.to_provider_symbol(stock_code)
        try:
            df = self.get_daily_data(stock_code, days=5)
        except Exception as exc:
            logger.warning("[%s] %s lấy giá chỉ số thất bại: %s", self.name, stock_code, exc)
            return None

        if df is None or df.empty:
            return None

        last = df.iloc[-1]
        prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else None
        close = float(last["close"])
        change_amount = round(close - prev_close, 2) if prev_close else None
        change_pct = (
            round((close - prev_close) / prev_close * 100, 2)
            if prev_close
            else None
        )

        quote = UnifiedRealtimeQuote(
            code=symbol,
            name=symbol,
            source=RealtimeSource.FALLBACK,
            market="vn",
            currency="VND",
            price=close,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=int(last["volume"]) if last.get("volume") == last.get("volume") else None,
            amount=float(last["amount"]) if last.get("amount") == last.get("amount") else None,
            open_price=float(last["open"]),
            high=float(last["high"]),
            low=float(last["low"]),
            pre_close=prev_close,
        )
        bar_date = last["date"]
        try:
            quote.provider_timestamp = bar_date.to_pydatetime().replace(
                tzinfo=timezone.utc
            ).isoformat()
        except Exception:  # pragma: no cover - lỗi ngày tháng không nên chặn luồng giá
            pass
        return quote

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover
            pass


def reset_symbols_cache() -> None:
    """Xoá cache bảng mã (dùng cho test)."""

    with _symbols_lock:
        _symbols_cache["data"] = None
        _symbols_cache["ts"] = 0.0
