# -*- coding: utf-8 -*-
"""
===================================
Nguồn dữ liệu SSI iBoard - giá realtime Việt Nam và nến ngày dự phòng
===================================

Hai endpoint công khai, đều không cần API Key:

- ``GET iboard-query.ssi.com.vn/stock/exchange/{hose,hnx,upcom}``
  **Snapshot toàn thị trường**: một request trả về giá realtime của cả sàn
  (đã kiểm chứng: HOSE 408 / HNX 299 / UPCOM 819, tổng cộng 1526 mã).
  Đây đúng là dạng dữ liệu mà ``DataFetcherManager.prefetch_realtime_quotes``
  cần, cùng loại tối ưu với cách kéo toàn thị trường của efinance / akshare_em.
- ``GET iboard-api.ssi.com.vn/statistics/charts/history``
  Nến ngày theo định dạng TradingView UDF, dùng làm lớp dự phòng sau VCI.

⚠️ **Khác biệt đơn vị (chỗ dễ sai nhất)**: endpoint biểu đồ trả giá theo đơn vị
**nghìn đồng** (HPG báo ``21.3``), trong khi endpoint snapshot và VCI dùng **đồng**
(``21300``). Trộn lẫn hai đơn vị sẽ làm đường trung bình, % thay đổi và giá cắt lỗ
lệch đúng 1000 lần mà **không hề báo lỗi**. Vì vậy
:meth:`SsiFetcher._normalize_data` luôn nhân 1000 để quy về đồng.
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

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote

logger = logging.getLogger(__name__)

SSI_QUERY_BASE_URL = os.getenv(
    "SSI_QUERY_BASE_URL", "https://iboard-query.ssi.com.vn"
).rstrip("/")
SSI_CHART_BASE_URL = os.getenv(
    "SSI_CHART_BASE_URL", "https://iboard-api.ssi.com.vn"
).rstrip("/")

_EXCHANGES = ("hose", "hnx", "upcom")
_DEFAULT_TIMEOUT = 20.0
# Snapshot là của toàn thị trường, trong phiên dùng lại trong vòng 60 giây là đủ;
# ``_enrich_realtime_quote`` sẽ xét tiếp theo REALTIME_CACHE_TTL xem có stale không.
_SNAPSHOT_TTL_SECONDS = 60.0
_MIN_INTERVAL_SECONDS = 0.5
_JITTER_SECONDS = 0.3

# Endpoint biểu đồ trả giá theo nghìn đồng, còn snapshot và VCI theo đồng.
_CHART_PRICE_SCALE = 1000

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Cache snapshot ở cấp module: {symbol_upper: record}
_snapshot_cache: Dict[str, Any] = {"data": {}, "ts": 0.0}
_snapshot_lock = RLock()
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


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _to_int(value: Any) -> Optional[int]:
    parsed = _to_float(value)
    return None if parsed is None else int(parsed)


class SsiFetcher(BaseFetcher):
    """Nguồn giá realtime Việt Nam (snapshot toàn thị trường) và nến ngày dự phòng."""

    name = "SsiFetcher"
    priority = int(os.getenv("SSI_PRIORITY", "1"))

    def __init__(self, timeout: Optional[float] = None) -> None:
        self.timeout = timeout if timeout is not None else _parse_env_float(
            "SSI_CALL_TIMEOUT", _DEFAULT_TIMEOUT
        )
        self.min_interval = _parse_env_float("SSI_MIN_INTERVAL", _MIN_INTERVAL_SECONDS)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA, "Accept": "application/json"})

    # ------------------------------------------------------------------
    # Điều tiết tần suất request
    # ------------------------------------------------------------------
    def _enforce_rate_limit(self) -> None:
        global _last_request_at
        with _request_lock:
            now = time.monotonic()
            wait = self.min_interval - (now - _last_request_at)
            if wait > 0:
                time.sleep(wait + random.uniform(0, _JITTER_SECONDS))
            _last_request_at = time.monotonic()

    @staticmethod
    def to_provider_symbol(stock_code: str) -> str:
        normalized = (stock_code or "").strip().upper()
        if normalized.endswith(".VN"):
            normalized = normalized[:-3]
        return normalized

    # ------------------------------------------------------------------
    # Snapshot toàn thị trường
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError, TimeoutError)),
        reraise=True,
    )
    def _fetch_exchange_snapshot(self, exchange: str) -> List[Dict[str, Any]]:
        self._enforce_rate_limit()
        response = self._session.get(
            f"{SSI_QUERY_BASE_URL}/stock/exchange/{exchange}", timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "SUCCESS":
            raise DataFetchError(
                f"[{self.name}] snapshot {exchange} trả về bất thường: {payload.get('message')}"
            )
        return payload.get("data") or []

    def _load_snapshot(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """Tải và cache snapshot toàn thị trường của cả ba sàn.

        Một sàn lỗi không ảnh hưởng các sàn còn lại: ba sàn của Việt Nam là ba
        endpoint độc lập, UPCOM chập chờn thì không được kéo theo giá của HOSE
        cùng chết.
        """

        with _snapshot_lock:
            cached = _snapshot_cache.get("data") or {}
            ts = float(_snapshot_cache.get("ts") or 0.0)
            if not force and cached and (time.time() - ts) < _SNAPSHOT_TTL_SECONDS:
                return cached

        merged: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []
        for exchange in _EXCHANGES:
            try:
                for record in self._fetch_exchange_snapshot(exchange):
                    symbol = str(record.get("stockSymbol") or "").strip().upper()
                    if symbol:
                        merged[symbol] = record
            except Exception as exc:
                errors.append(f"{exchange}: {exc}")
                logger.warning("[%s] Tải snapshot %s thất bại: %s", self.name, exchange, exc)

        if not merged:
            raise DataFetchError(
                f"[{self.name}] Snapshot của cả ba sàn đều thất bại: "
                f"{'; '.join(errors) or 'không rõ nguyên nhân'}"
            )

        with _snapshot_lock:
            _snapshot_cache["data"] = merged
            _snapshot_cache["ts"] = time.time()
        logger.info(
            "[%s] Đã làm mới snapshot toàn thị trường: %d mã%s",
            self.name,
            len(merged),
            f" (một phần thất bại: {'; '.join(errors)})" if errors else "",
        )
        return merged

    def prefetch_realtime_quotes(self, stock_codes: List[str], **_: Any) -> int:
        """Kéo toàn bộ thị trường Việt Nam một lần, các truy vấn từng mã sau đó
        đều đọc từ cache.

        Trả về số mã trong danh sách yêu cầu mà cache lần này phủ được; ngữ nghĩa
        thống nhất với các hiện thực khác của
        ``DataFetcherManager.prefetch_realtime_quotes``.
        """

        try:
            snapshot = self._load_snapshot(force=True)
        except Exception as exc:
            logger.warning("[%s] Prefetch giá realtime thất bại: %s", self.name, exc)
            return 0

        wanted = {self.to_provider_symbol(code) for code in stock_codes or []}
        covered = len(wanted & set(snapshot)) if wanted else len(snapshot)
        logger.info(
            "[%s] Prefetch xong: snapshot %d mã, phủ được %d/%d mã của lượt này",
            self.name,
            len(snapshot),
            covered,
            len(wanted),
        )
        return covered

    # ------------------------------------------------------------------
    # Giá realtime
    # ------------------------------------------------------------------
    def get_realtime_quote(self, stock_code: str, **_: Any) -> Optional[UnifiedRealtimeQuote]:
        symbol = self.to_provider_symbol(stock_code)
        try:
            snapshot = self._load_snapshot()
        except Exception as exc:
            logger.warning("[%s] %s lấy giá realtime thất bại: %s", self.name, stock_code, exc)
            return None

        record = snapshot.get(symbol)
        if not record:
            logger.info("[%s] Snapshot không có mã %s", self.name, symbol)
            return None

        ref_price = _to_float(record.get("refPrice")) or _to_float(
            record.get("priorClosePrice")
        )
        highest = _to_float(record.get("highest"))
        lowest = _to_float(record.get("lowest"))
        amplitude = None
        if ref_price and highest is not None and lowest is not None and ref_price != 0:
            amplitude = round((highest - lowest) / ref_price * 100, 2)

        quote = UnifiedRealtimeQuote(
            code=symbol,
            name=str(record.get("companyNameVi") or record.get("companyNameEn") or "").strip(),
            source=RealtimeSource.FALLBACK,
            market="vn",
            currency=str(record.get("tradingCurrencyISOCode") or "VND"),
            price=_to_float(record.get("matchedPrice")),
            change_pct=_to_float(record.get("priceChangePercent")),
            change_amount=_to_float(record.get("priceChange")),
            volume=_to_int(record.get("nmTotalTradedQty")),
            amount=_to_float(record.get("nmTotalTradedValue")),
            amplitude=amplitude,
            open_price=_to_float(record.get("openPrice")),
            high=highest,
            low=lowest,
            pre_close=ref_price,
        )
        # Giá trần/sàn là mốc tham chiếu quản trị rủi ro quen thuộc ở thị trường
        # Việt Nam (HOSE ±7% / HNX ±10% / UPCOM ±15%). UnifiedRealtimeQuote không
        # có trường tương ứng nên gắn thẳng vào instance để tầng báo cáo đọc khi cần.
        setattr(quote, "vn_ceiling", _to_float(record.get("ceiling")))
        setattr(quote, "vn_floor", _to_float(record.get("floor")))
        setattr(quote, "vn_exchange", str(record.get("exchange") or "").lower())
        return quote

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        symbol = self.to_provider_symbol(stock_code)
        try:
            snapshot = self._load_snapshot()
        except Exception as exc:
            logger.warning("[%s] %s tra cứu tên thất bại: %s", self.name, stock_code, exc)
            return None
        record = snapshot.get(symbol) or {}
        name = record.get("companyNameVi") or record.get("companyNameEn")
        return str(name).strip() or None if name else None

    # ------------------------------------------------------------------
    # Nến ngày dự phòng
    # ------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError, TimeoutError)),
        reraise=True,
    )
    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        symbol = self.to_provider_symbol(stock_code)

        def _epoch(value: str, end_of_day: bool) -> int:
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d")
            except (TypeError, ValueError):
                parsed = datetime.now()
            if end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return int(parsed.replace(tzinfo=timezone.utc).timestamp())

        self._enforce_rate_limit()
        response = self._session.get(
            f"{SSI_CHART_BASE_URL}/statistics/charts/history",
            params={
                "resolution": "1D",
                "symbol": symbol,
                "from": _epoch(start_date, False),
                "to": _epoch(end_date, True),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "SUCCESS":
            raise DataFetchError(
                f"[{self.name}] nến ngày của {symbol} trả về bất thường: {payload.get('message')}"
            )

        data = payload.get("data") or {}
        timestamps = data.get("t") or []
        if not timestamps:
            return pd.DataFrame()

        return pd.DataFrame(
            {
                "t": timestamps,
                "o": data.get("o") or [],
                "h": data.get("h") or [],
                "l": data.get("l") or [],
                "c": data.get("c") or [],
                "v": data.get("v") or [],
            }
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        out = pd.DataFrame()
        out["date"] = pd.to_datetime(pd.to_numeric(df["t"]), unit="s")
        # Endpoint biểu đồ dùng nghìn đồng; nhân 1000 để quy về đồng, thống nhất
        # đơn vị với VCI và snapshot
        for src, dst in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")):
            out[dst] = pd.to_numeric(df[src], errors="coerce") * _CHART_PRICE_SCALE
        out["volume"] = pd.to_numeric(df["v"], errors="coerce")
        # Endpoint này không trả giá trị giao dịch, lấy giá trung bình × khối lượng
        # để xấp xỉ; để trống sẽ khiến tầng dưới hiểu nhầm là thiếu dữ liệu
        out["amount"] = ((out["high"] + out["low"]) / 2) * out["volume"]
        out["pct_chg"] = (out["close"].pct_change() * 100).round(2)
        return out[STANDARD_COLUMNS]

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover
            pass


def reset_snapshot_cache() -> None:
    """Xoá cache snapshot (dùng cho test)."""

    with _snapshot_lock:
        _snapshot_cache["data"] = {}
        _snapshot_cache["ts"] = 0.0
