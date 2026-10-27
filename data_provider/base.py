# -*- coding: utf-8 -*-
"""
===================================
Lớp cơ sở và manager cho các nguồn dữ liệu (chỉ thị trường Việt Nam)
===================================

Mẫu thiết kế: Strategy Pattern
- BaseFetcher: lớp trừu tượng, định nghĩa giao diện thống nhất
- DataFetcherManager: quản lý chiến lược, tự động chuyển nguồn

Repo chỉ phục vụ thị trường Việt Nam (HOSE/HNX/UPCoM). Các nguồn dữ liệu:
- FiinTradeFetcher: nến ngày HOSE/HNX/chỉ số trước VCI, mặc định bật khi có
  ``FIINTRADE_ORIGIN`` (tắt bằng ``FIINTRADE_ENABLED=false``)
- VciFetcher: nến ngày (nguồn chính), chỉ số, bảng mã
- SsiFetcher: snapshot realtime toàn thị trường, nến ngày dự phòng, độ rộng thị trường
- VnstockFetcher: tuỳ chọn, chỉ bật khi ``VNSTOCK_ENABLED=true``
- YfinanceFetcher: dự phòng cấp 3 cho nến ngày (chỉ sàn HOSE, không ghi vào DB)

The repository serves the Vietnam market only. Strategy:
1. Each fetcher owns its own rate limiting
2. On failure, fall back to the next source
3. Retries with exponential backoff
"""

import os
import logging
import re
import random
import time
from threading import BoundedSemaphore, RLock, Thread
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional, List, Tuple, Dict, Any

import pandas as pd
import numpy as np
from src.data.stock_index_loader import get_index_stock_name
from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name
from src.services.run_diagnostics import record_provider_run, record_provider_run_started
from src.services.stock_list_parser import ParseStatus, parse_analysis_target
from .realtime_types import CircuitBreaker

# Cấu hình log
logger = logging.getLogger(__name__)


# === Định nghĩa tên cột chuẩn hoá ===
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']


def unwrap_exception(exc: Exception) -> Exception:
    """
    Follow chained exceptions and return the deepest non-cyclic cause.
    """
    current = exc
    visited = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))
        next_exc = current.__cause__ or current.__context__
        if next_exc is None:
            break
        current = next_exc

    return current


def summarize_exception(exc: Exception) -> Tuple[str, str]:
    """
    Build a stable summary for logs while preserving the application-layer message.
    """
    root = unwrap_exception(exc)
    error_type = type(root).__name__
    message = str(exc).strip() or str(root).strip() or error_type
    return error_type, " ".join(message.split())


# Mã chỉ số Việt Nam. Khoá là mã người dùng nhập đã quy chuẩn về chữ hoa (chỉ số
# UPCoM có mã chuẩn ``UPCOMINDEX``, nhận thêm ``UPCOM``); giá trị là cách viết mà
# **API VCI** yêu cầu. VCI phân biệt hoa/thường (``HNXINDEX`` trả rỗng, ``HNXIndex``
# mới có dữ liệu) và gọi chỉ số UPCoM là ``HNXUpcomIndex``. Nguồn khác có bảng riêng
# (SSI ``HNXUPCOMINDEX``, FiinTrade ``UpcomIndex``).
# Keys: normalised user codes; values: the spelling VCI's API expects.
VN_INDEX_SYMBOLS = {
    "VNINDEX": "VNINDEX",
    "VN30": "VN30",
    "VN100": "VN100",
    "HNXINDEX": "HNXIndex",
    "HNX30": "HNX30",
    "UPCOMINDEX": "HNXUpcomIndex",
    "UPCOM": "HNXUpcomIndex",
}

# Mã cổ phiếu Việt Nam cố định 3 ký tự chữ hoa hoặc số (đã đối chiếu bảng mã
# đầy đủ của VCI: 1602 mã thuần chữ + 151 mã có số, ví dụ ``X77``/``VW3``).
# Vietnam stock codes: exactly 3 uppercase alphanumerics.
_VN_STOCK_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")

# Chứng chỉ quỹ ETF / quỹ đóng Việt Nam: 8 ký tự, tiền tố ``FU`` (``FUEVN100``,
# ``FUESSV30``, ``FUCVREIT``) hoặc ``E1VF`` (``E1VFVN30``).
# Chứng quyền (``CHPG2401``), phái sinh (``VN30F2410``) và trái phiếu vẫn nằm
# ngoài phạm vi phân tích.
# Vietnam ETF / closed-end fund certificates (8 chars, FU*/E1VF* prefixes).
_VN_FUND_CODE_RE = re.compile(r"^(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})$")


def is_vn_fund_code(code: str) -> bool:
    """Có phải mã chứng chỉ quỹ ETF / quỹ đóng Việt Nam (dạng trần) hay không."""

    return bool(_VN_FUND_CODE_RE.match((code or "").strip().upper()))


def is_vn_security_code(code: str) -> bool:
    """Mã trần có đúng hình dạng mã cổ phiếu (3 ký tự) hoặc chứng chỉ quỹ Việt Nam."""

    normalized = (code or "").strip().upper()
    return bool(_VN_STOCK_CODE_RE.match(normalized) or _VN_FUND_CODE_RE.match(normalized))


def is_vn_index_code(code: str) -> bool:
    """Có phải mã chỉ số Việt Nam đã đăng ký hay không."""

    return (code or "").strip().upper() in VN_INDEX_SYMBOLS


def get_vn_index_provider_symbol(code: str) -> Optional[str]:
    """Chuyển mã chỉ số Việt Nam người dùng nhập sang đúng cách viết provider yêu cầu."""

    return VN_INDEX_SYMBOLS.get((code or "").strip().upper())


def normalize_stock_code(stock_code: str) -> str:
    """
    Quy chuẩn mã cổ phiếu Việt Nam về dạng trần mà provider yêu cầu.

    Normalize a Vietnam stock code to the bare form providers expect:
    - ``HPG``       -> ``HPG``
    - ``HPG.VN``    -> ``HPG``   (bỏ hậu tố ``.VN``)
    - ``vnHPG``     -> ``HPG``   (bỏ tiền tố ``vn`` viết thường)
    - ``VNFUEVN100``-> ``FUEVN100`` (tiền tố ``VN`` + mã quỹ)
    - ``VNINDEX``   -> ``VNINDEX`` (mã chỉ số giữ nguyên)

    Các dạng khác được trả nguyên (chỉ cắt khoảng trắng).
    """
    code = (stock_code or "").strip()
    upper = code.upper()

    if upper.endswith(".VN"):
        candidate = upper[:-3]
        if is_vn_security_code(candidate):
            return candidate
    # Tiền tố ``vn`` viết thường; ``VN30``/``VNINDEX`` là mã chỉ số nên không cắt.
    if code[:2] == "vn" and len(code) > 2:
        candidate = upper[2:]
        if upper not in VN_INDEX_SYMBOLS and is_vn_security_code(candidate):
            return candidate
    # Mã quỹ có tiền tố ``VN`` viết hoa (``VNFUEVN100``, sinh ra khi tầng API chuẩn
    # hoá chữ hoa ``vnFUEVN100``). Upper-case ``VN`` + fund code is unambiguous.
    if upper.startswith("VN") and _VN_FUND_CODE_RE.match(upper[2:]):
        return upper[2:]

    return code


def _is_vn_market(code: str) -> bool:
    """Kiểm tra có phải mã cổ phiếu / chỉ số / quỹ Việt Nam hay không.

    Chấp nhận các cách viết:
    - Hậu tố tường minh ``HPG.VN``
    - Chỉ số đã đăng ký ``VNINDEX`` / ``VN30`` / ``HNXIndex`` / ``UPCOMINDEX``
    - Tiền tố ``vnHPG`` (tiền tố bắt buộc viết thường)
    - Mã trần ``HPG`` (3 ký tự chữ hoa hoặc số)
    - Mã chứng chỉ quỹ ``FUEVN100`` / ``E1VFVN30``

    Accepts suffixed, prefixed and bare Vietnam stock / index / fund codes.
    """

    normalized = (code or "").strip().upper()
    if not normalized:
        return False

    if normalized.endswith(".VN"):
        return is_vn_security_code(normalized[:-3])

    if normalized in VN_INDEX_SYMBOLS:
        return True

    raw = (code or "").strip()
    if raw[:2] == "vn" and is_vn_security_code(raw[2:]):
        return True
    if normalized.startswith("VN") and _VN_FUND_CODE_RE.match(normalized[2:]):
        return True

    return is_vn_security_code(normalized)


# Nguồn có cơ sở điều chỉnh giá khác với nguồn chính của thị trường. Những nguồn
# này vẫn được phép phục vụ phân tích trong một phiên chạy, nhưng KHÔNG được ghi
# vào bảng ``stock_daily``: ``Database.save_daily_data`` UPSERT theo khoá
# ``(code, date)``, nên một lần failover sẽ ghi đè lẻ tẻ vài ngày và để lại chuỗi
# lịch sử trộn hai cơ sở giá — MA, volume_ratio, vùng hỗ trợ/kháng cự sai mà không
# có bất kỳ tín hiệu lỗi nào.
#
# Trường hợp cụ thể: Yahoo và VCI điều chỉnh quyền theo mốc khác nhau. Đo thực tế
# trên mã MBB (82 phiên, yfinance auto_adjust=True): các phiên từ 2026-08-11 trở đi
# khớp tuyệt đối (tỷ lệ 1,0), nhưng toàn bộ phần lịch sử trước 2026-07-09 lệch đúng
# một hệ số cố định 1,043938. Đây là bậc thang tại ngày chốt quyền, không phải
# nhiễu — nếu trộn hai nguồn thì chuỗi lịch sử sẽ có một bước nhảy giả ~4,4% ngay
# tại đó. Phần lớn mã khác lệch trung bình chỉ 0,01-0,05%.
_NON_PERSISTABLE_DAILY_SOURCES = {
    "YfinanceFetcher": {"vn"},
}


def is_daily_source_persistable(source_name: str, market: str) -> bool:
    """Nến ngày lấy từ ``source_name`` cho ``market`` có được ghi vào DB hay không.

    Args:
        source_name: Tên fetcher đã phục vụ, đúng như ``get_daily_data`` trả về.
        market: Nhãn thị trường (``vn``).

    Returns:
        ``False`` khi nguồn lệch cơ sở điều chỉnh so với nguồn chính của thị trường.
    """

    blocked = _NON_PERSISTABLE_DAILY_SOURCES.get((source_name or "").strip())
    if not blocked:
        return True
    return (market or "").strip().lower() not in blocked


def _market_tag(code: str) -> str:
    """Trả về nhãn thị trường: ``vn`` khi là mã Việt Nam, ngược lại ``unknown``.

    Return the market tag: ``vn`` for Vietnam codes, otherwise ``unknown``.
    """

    return "vn" if _is_vn_market(code) else "unknown"


def should_persist_daily_data(source_name: str, stock_code: str) -> bool:
    """Nến ngày vừa lấy về có được ghi vào ``stock_daily`` hay không.

    Bản tiện dụng của :func:`is_daily_source_persistable`, tự suy ra thị trường từ
    mã nên điểm gọi chỉ còn một dòng.

    Args:
        source_name: Tên fetcher đã phục vụ, đúng như ``get_daily_data`` trả về.
        stock_code: Mã cổ phiếu ở dạng người dùng nhập.
    """

    return is_daily_source_persistable(source_name, _market_tag(stock_code))


def canonical_stock_code(code: str) -> str:
    """
    Return the canonical (uppercase) form of a stock code.

    This is a display/storage layer concern, distinct from normalize_stock_code
    which strips exchange prefixes. Apply at system input boundaries to ensure
    consistent case across BOT, WEB UI, API, and CLI paths (Issue #355).

    Examples:
        'hpg'     -> 'HPG'
        'HPG'     -> 'HPG'
        'vnindex' -> 'VNINDEX'
    """
    return (code or "").strip().upper()


class DataFetchError(Exception):
    """Lớp cơ sở cho các ngoại lệ khi lấy dữ liệu"""
    pass


class RateLimitError(DataFetchError):
    """Ngoại lệ khi bị giới hạn tần suất gọi API"""
    pass


class DataSourceUnavailableError(DataFetchError):
    """Ngoại lệ khi nguồn dữ liệu không khả dụng"""
    pass


class BaseFetcher(ABC):
    """
    Lớp trừu tượng cho nguồn dữ liệu

    Trách nhiệm:
    1. Định nghĩa giao diện lấy dữ liệu thống nhất
    2. Cung cấp phương thức chuẩn hoá dữ liệu
    3. Hiện thực phần tính chỉ báo kỹ thuật dùng chung

    Lớp con phải hiện thực:
    - _fetch_raw_data(): lấy dữ liệu thô từ nguồn cụ thể
    - _normalize_data(): chuyển dữ liệu thô về định dạng chuẩn
    """

    name: str = "BaseFetcher"
    priority: int = 99  # Số ưu tiên càng nhỏ càng được chọn trước
    allow_empty_daily_data: bool = False

    @abstractmethod
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Lấy dữ liệu thô từ nguồn (lớp con bắt buộc hiện thực)

        Args:
            stock_code: mã cổ phiếu, ví dụ 'HPG', 'VNINDEX'
            start_date: ngày bắt đầu, định dạng 'YYYY-MM-DD'
            end_date: ngày kết thúc, định dạng 'YYYY-MM-DD'

        Returns:
            DataFrame dữ liệu thô (tên cột khác nhau tuỳ nguồn)
        """
        pass

    @abstractmethod
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        Chuẩn hoá tên cột dữ liệu (lớp con bắt buộc hiện thực)

        Quy tên cột của các nguồn khác nhau về cùng một bộ:
        ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        """
        pass

    def get_main_indices(self, region: str = "vn") -> Optional[List[Dict[str, Any]]]:
        """
        Lấy giá của các chỉ số chính

        Args:
            region: khu vực thị trường (chỉ hỗ trợ ``vn``)

        Returns:
            List[Dict]: danh sách chỉ số, mỗi phần tử là dict gồm:
                - code: mã chỉ số
                - name: tên chỉ số
                - current: điểm số hiện tại
                - change: số điểm tăng/giảm
                - change_pct: % tăng/giảm
                - volume: khối lượng giao dịch
                - amount: giá trị giao dịch
        """
        return None

    def get_region_market_stats(self, region: str) -> Optional[Dict[str, Any]]:
        """Thống kê tăng/giảm toàn thị trường cho ``region`` (ví dụ ``vn``).

        Returns:
            Dict: gồm up_count / down_count / flat_count / limit_up_count /
            limit_down_count / total_amount; mặc định không hỗ trợ (None).
        """
        return None

    def get_market_money_flow(self, region: str = "vn") -> Optional[Dict[str, Any]]:
        """Dòng tiền khối ngoại / tự doanh toàn thị trường theo kỳ; mặc định không hỗ trợ.

        Market-level foreign / proprietary flows by period; unsupported by default.
        """
        return None

    def get_stock_money_flow(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """Dòng tiền khối ngoại / tự doanh của một mã theo kỳ; mặc định không hỗ trợ.

        Per-stock foreign / proprietary flows by period; unsupported by default.
        """
        return None

    def get_market_drivers(self, region: str = "vn") -> Optional[Dict[str, Any]]:
        """Mã dẫn dắt chỉ số, mã đột biến KL, diễn biến trong phiên; mặc định không hỗ trợ.

        Index contributors, volume breakouts and intraday path; unsupported by default.
        """
        return None

    def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> pd.DataFrame:
        """
        Lấy dữ liệu nến ngày (điểm vào thống nhất)

        Quy trình:
        1. Tính khoảng ngày
        2. Gọi lớp con để lấy dữ liệu thô
        3. Chuẩn hoá tên cột
        4. Tính các chỉ báo kỹ thuật

        Args:
            stock_code: mã cổ phiếu
            start_date: ngày bắt đầu (tuỳ chọn)
            end_date: ngày kết thúc (tuỳ chọn, mặc định hôm nay)
            days: số ngày cần lấy (dùng khi không chỉ định start_date)

        Returns:
            DataFrame đã chuẩn hoá, kèm các chỉ báo kỹ thuật
        """
        # Tính khoảng ngày
        if end_date is None:
            end_date = datetime.now().strftime('%Y-%m-%d')

        if start_date is None:
            # Mặc định lấy 30 phiên gần nhất (ước lượng theo ngày lịch nên lấy dư một chút)
            from datetime import timedelta
            start_dt = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=days * 2)
            start_date = start_dt.strftime('%Y-%m-%d')

        request_start = time.time()
        logger.info(f"[{self.name}] Bắt đầu lấy nến ngày {stock_code}: khoảng={start_date} ~ {end_date}")

        try:
            # Bước 1: lấy dữ liệu thô
            raw_df = self._fetch_raw_data(stock_code, start_date, end_date)

            if raw_df is None:
                raise DataFetchError(f"[{self.name}] Không lấy được dữ liệu của {stock_code}")
            if raw_df.empty:
                elapsed = time.time() - request_start
                logger.info(
                    f"[{self.name}] {stock_code} trả về nến ngày rỗng: khoảng={start_date} ~ {end_date}, "
                    f"elapsed={elapsed:.2f}s"
                )
                if self.allow_empty_daily_data:
                    return pd.DataFrame(columns=STANDARD_COLUMNS)
                raise DataFetchError(f"[{self.name}] Không lấy được dữ liệu của {stock_code}")

            # Bước 2: chuẩn hoá tên cột
            df = self._normalize_data(raw_df, stock_code)

            # Bước 3: làm sạch dữ liệu
            df = self._clean_data(df)

            # Bước 4: tính các chỉ báo kỹ thuật
            df = self._calculate_indicators(df)

            elapsed = time.time() - request_start
            logger.info(
                f"[{self.name}] {stock_code} lấy thành công: khoảng={start_date} ~ {end_date}, "
                f"rows={len(df)}, elapsed={elapsed:.2f}s"
            )
            return df

        except Exception as e:
            elapsed = time.time() - request_start
            error_type, error_reason = summarize_exception(e)
            logger.error(
                f"[{self.name}] {stock_code} lấy thất bại: khoảng={start_date} ~ {end_date}, "
                f"error_type={error_type}, elapsed={elapsed:.2f}s, reason={error_reason}"
            )
            raise DataFetchError(f"[{self.name}] {stock_code}: {error_reason}") from e

    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Làm sạch dữ liệu

        Xử lý:
        1. Đảm bảo cột ngày đúng định dạng
        2. Ép kiểu số
        3. Loại bỏ dòng rỗng
        4. Sắp xếp theo ngày
        """
        df = df.copy()

        # Đảm bảo cột ngày có kiểu datetime
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])

        # Ép kiểu cho các cột số
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # Loại bỏ các dòng rỗng ở cột trọng yếu
        df = df.dropna(subset=['close', 'volume'])

        # Sắp xếp tăng dần theo ngày
        df = df.sort_values('date', ascending=True).reset_index(drop=True)

        return df

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Tính các chỉ báo kỹ thuật

        Các chỉ báo được tính:
        - MA5, MA10, MA20: đường trung bình động
        - Volume_Ratio: tỷ lệ khối lượng (khối lượng hôm nay / trung bình 5 phiên)
        """
        df = df.copy()

        # Đường trung bình động
        df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['ma10'] = df['close'].rolling(window=10, min_periods=1).mean()
        df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()

        # Tỷ lệ khối lượng: khối lượng phiên hiện tại / khối lượng trung bình 5 phiên.
        # Lưu ý: volume_ratio ở đây là bội số của "khối lượng nến ngày / trung bình 5
        # phiên trước (shift 1)", khác với cách tính "tỷ lệ khối lượng trong phiên
        # (so cùng thời điểm)" của một số phần mềm giao dịch; ý nghĩa gần với "bội số
        # tăng khối lượng" hơn. Hành vi này được giữ nguyên (yêu cầu không đổi logic).
        avg_volume_5 = df['volume'].rolling(window=5, min_periods=1).mean()
        df['volume_ratio'] = df['volume'] / avg_volume_5.shift(1)
        df['volume_ratio'] = df['volume_ratio'].fillna(1.0)

        # Giữ 2 chữ số thập phân
        for col in ['ma5', 'ma10', 'ma20', 'volume_ratio']:
            if col in df.columns:
                df[col] = df[col].round(2)

        return df

    @staticmethod
    def random_sleep(min_seconds: float = 1.0, max_seconds: float = 3.0) -> None:
        """
        Nghỉ ngẫu nhiên thông minh (Jitter)

        Chiến lược tránh bị chặn: tạo độ trễ ngẫu nhiên mô phỏng hành vi con người,
        chèn thời gian chờ không đều giữa các request
        """
        sleep_time = random.uniform(min_seconds, max_seconds)
        logger.debug(f"Nghỉ ngẫu nhiên {sleep_time:.2f} giây...")
        time.sleep(sleep_time)


# Các khối dữ liệu cơ bản giữ trong fundamental_context. Thị trường Việt Nam có định
# giá (PE/PB/vốn hoá) từ snapshot giá; khi FiinTrade được bật thì có thêm cổ tức / sự
# kiện (earnings) và giao dịch nội bộ / sở hữu nước ngoài (institution). Tăng trưởng
# chưa có nguồn nên luôn là ``not_supported``.
# Fundamental blocks kept in fundamental_context.
FUNDAMENTAL_BLOCKS: Tuple[str, ...] = ("valuation", "growth", "earnings", "institution")
_VN_UNSUPPORTED_FUNDAMENTAL_BLOCKS: Tuple[str, ...] = ("growth", "earnings", "institution")
# Khối chỉ có nguồn khi FiinTrade được bật / Blocks served only when FiinTrade is enabled
_VN_FIINTRADE_FUNDAMENTAL_BLOCKS: Tuple[str, ...] = ("earnings", "institution")
_FIINTRADE_FETCHER_NAME = "FiinTradeFetcher"
# Cửa sổ thời gian của các mục sự kiện đưa vào fundamentals (ngày lịch).
_DIVIDEND_TTM_DAYS = 365
_ISSUANCE_WINDOW_DAYS = 365
_INSIDER_WINDOW_DAYS = 90
_INSIDER_MAX_ITEMS = 10
_EARNINGS_RELEASE_MAX_ITEMS = 4
_VN_FUNDAMENTAL_UNSUPPORTED_REASON = "no Vietnam fundamental provider for this block"


class DataFetcherManager:
    """
    Manager chiến lược cho các nguồn dữ liệu

    Trách nhiệm:
    1. Quản lý nhiều nguồn dữ liệu (sắp xếp theo mức ưu tiên)
    2. Tự động chuyển nguồn khi có sự cố (Failover)
    3. Cung cấp giao diện lấy dữ liệu thống nhất

    Chiến lược chuyển nguồn:
    - Ưu tiên dùng nguồn có mức ưu tiên cao
    - Thất bại thì tự động chuyển sang nguồn kế tiếp
    - Ném ngoại lệ khi tất cả nguồn đều thất bại
    """

    _daily_source_health = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)

    def __init__(self, fetchers: Optional[List[BaseFetcher]] = None):
        """
        Khởi tạo manager

        Args:
            fetchers: danh sách nguồn dữ liệu (tuỳ chọn, mặc định tự tạo theo mức ưu tiên)
        """
        self._fetchers: List[BaseFetcher] = []
        self._fetchers_lock = RLock()
        self._fetchers_by_name: Dict[str, BaseFetcher] = {}
        self._fetcher_call_locks: Dict[int, RLock] = {}
        self._fetcher_call_locks_lock = RLock()
        self._stock_name_cache: Dict[str, str] = {}
        self._stock_name_cache_lock = RLock()

        if fetchers:
            # Sắp xếp theo mức ưu tiên
            self._fetchers = sorted(fetchers, key=lambda f: f.priority)
            self._refresh_fetcher_indexes_locked()
        else:
            self._init_default_fetchers()
        self._fundamental_cache: Dict[str, Dict[str, Any]] = {}
        self._fundamental_cache_lock = RLock()
        self._fundamental_timeout_worker_limit = 8
        self._fundamental_timeout_slots = BoundedSemaphore(self._fundamental_timeout_worker_limit)

    def _ensure_concurrency_guards(self) -> None:
        """Lazily initialize thread-safety primitives for test scaffolds using __new__."""
        if not hasattr(self, "_fetchers_lock") or self._fetchers_lock is None:
            self._fetchers_lock = RLock()
        if not hasattr(self, "_fetchers_by_name") or self._fetchers_by_name is None:
            self._fetchers_by_name = {}
        if not hasattr(self, "_fetcher_call_locks") or self._fetcher_call_locks is None:
            self._fetcher_call_locks = {}
        if not hasattr(self, "_fetcher_call_locks_lock") or self._fetcher_call_locks_lock is None:
            self._fetcher_call_locks_lock = RLock()
        if not hasattr(self, "_stock_name_cache") or self._stock_name_cache is None:
            self._stock_name_cache = {}
        if not hasattr(self, "_stock_name_cache_lock") or self._stock_name_cache_lock is None:
            self._stock_name_cache_lock = RLock()

    def _get_fetchers_snapshot(self) -> List[BaseFetcher]:
        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            return list(getattr(self, "_fetchers", []))

    def _refresh_fetcher_indexes_locked(self) -> None:
        self._fetchers_by_name = {fetcher.name: fetcher for fetcher in self._fetchers}

    def _has_fetcher(self, fetcher_name: str) -> bool:
        """Nguồn có được đăng ký trong manager hay không (không xét khả dụng)."""
        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            if fetcher_name in self._fetchers_by_name:
                return True
            return any(getattr(f, "name", None) == fetcher_name for f in self._fetchers)

    def _get_fetcher_by_name(self, fetcher_name: str, capability: str = "") -> Optional[BaseFetcher]:
        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            fetcher = self._fetchers_by_name.get(fetcher_name)
            if fetcher is None and self._fetchers:
                self._refresh_fetcher_indexes_locked()
                fetcher = self._fetchers_by_name.get(fetcher_name)
        if fetcher is None:
            return None
        if not self._is_fetcher_available(fetcher, capability=capability):
            return None
        return fetcher

    @staticmethod
    def _call_availability_probe(fetcher: BaseFetcher, probe_name: str, capability: str) -> Optional[bool]:
        probe = getattr(fetcher, probe_name, None)
        if not callable(probe):
            return None
        try:
            if probe_name == "is_available_for_request":
                return bool(probe(capability))
            return bool(probe())
        except TypeError:
            return bool(probe())
        except Exception as exc:
            logger.debug(
                "[Nguồn khả dụng] %s.%s kiểm tra thất bại (capability=%s): %s",
                fetcher.name,
                probe_name,
                capability or "default",
                exc,
            )
            return False

    @classmethod
    def _is_fetcher_available(cls, fetcher: BaseFetcher, capability: str = "") -> bool:
        for probe_name in ("is_available_for_request", "is_available", "_is_available"):
            result = cls._call_availability_probe(fetcher, probe_name, capability)
            if result is not None:
                return result
        return True

    def _get_fetcher_call_lock(self, fetcher: BaseFetcher) -> RLock:
        self._ensure_concurrency_guards()
        fetcher_id = id(fetcher)
        with self._fetcher_call_locks_lock:
            lock = self._fetcher_call_locks.get(fetcher_id)
            if lock is None:
                lock = RLock()
                self._fetcher_call_locks[fetcher_id] = lock
            return lock

    def _call_fetcher_method(self, fetcher: BaseFetcher, method_name: str, *args, **kwargs):
        """Serialize shared fetcher state access through manager-owned per-instance locks."""
        method = getattr(fetcher, method_name)
        with self._get_fetcher_call_lock(fetcher):
            return method(*args, **kwargs)

    def _vn_realtime_source_order(self) -> List[str]:
        """Thứ tự nguồn dữ liệu cho giá realtime Việt Nam.

        Mặc định là ``SsiFetcher`` rồi ``VciFetcher``; nếu ``REALTIME_SOURCE_PRIORITY``
        ghi tường minh token (``fiintrade`` / ``ssi`` / ``vci``) thì chạy theo thứ tự
        người dùng cấu hình. FiinTrade khi được bật mà không được nhắc tới thì đứng
        đầu, nhất quán với nến ngày.
        FiinTrade, when registered but not listed, goes first (same as daily bars).
        """

        from src.config import get_config

        token_to_fetcher = {
            "fiintrade": _FIINTRADE_FETCHER_NAME,
            "ssi": "SsiFetcher",
            "vci": "VciFetcher",
        }
        configured = str(
            getattr(get_config(), "realtime_source_priority", "") or ""
        ).lower()
        ordered = [
            token_to_fetcher[token.strip()]
            for token in configured.split(",")
            if token.strip() in token_to_fetcher
        ]
        if _FIINTRADE_FETCHER_NAME not in ordered and self._has_fetcher(_FIINTRADE_FETCHER_NAME):
            ordered.insert(0, _FIINTRADE_FETCHER_NAME)
        # Snapshot toàn thị trường của SSI chỉ có cổ phiếu thành phần, còn VCI phụ
        # trách các chỉ số đã đăng ký (suy ra từ nến ngày mới nhất). Vì vậy cả hai đều
        # phải nằm trong chuỗi nguồn.
        for default_name in ("SsiFetcher", "VciFetcher"):
            if default_name not in ordered:
                ordered.append(default_name)
        return ordered

    @staticmethod
    def _require_vn_market(market: str, stock_code: str) -> None:
        """Từ chối mã không phải Việt Nam TRƯỚC khi phát bất kỳ request mạng nào.

        Reject non-Vietnam codes before any network request, at the manager layer so
        API, Bot and Agent tools that call the manager directly are covered too.
        """

        if market == "vn":
            return
        reason = f"không nhận diện được {stock_code} là mã chứng khoán Việt Nam"
        logger.info("[Lọc thị trường] %s: %s", stock_code, reason)
        raise DataFetchError(f"{stock_code}: {reason}")

    @classmethod
    def _filter_fetchers_by_capability(
        cls,
        fetchers: List[BaseFetcher],
        capability: str,
    ) -> List[BaseFetcher]:
        """Skip request-time unavailable fetchers before entering route-specific loops."""
        kept: List[BaseFetcher] = []
        skipped: List[str] = []

        for fetcher in fetchers:
            if cls._is_fetcher_available(fetcher, capability=capability):
                kept.append(fetcher)
            else:
                skipped.append(fetcher.name)

        if skipped:
            logger.info(
                "[Định tuyến nguồn] %s bỏ qua nguồn tạm thời không khả dụng: %s",
                capability or "request",
                ", ".join(skipped),
            )

        return kept

    @classmethod
    def _daily_health_key(cls, fetcher: BaseFetcher, market: str) -> str:
        return f"daily_data:{market}:{fetcher.name}"

    @classmethod
    def _is_daily_source_available(
        cls,
        fetcher: BaseFetcher,
        market: str,
    ) -> bool:
        key = cls._daily_health_key(fetcher, market)
        if cls._daily_source_health.is_available(key):
            return True
        logger.info(
            "[Sức khoẻ nguồn] %s nến ngày bỏ qua nguồn đang ngắt mạch ngắn hạn: %s",
            market,
            fetcher.name,
        )
        return False

    @staticmethod
    def _daily_source_unavailable_error(fetcher: BaseFetcher) -> str:
        return f"[{fetcher.name}] (CircuitOpen) nguồn đang ngắt mạch ngắn hạn"

    @classmethod
    def _record_daily_source_success(cls, fetcher: BaseFetcher, market: str) -> None:
        cls._daily_source_health.record_success(cls._daily_health_key(fetcher, market))

    @classmethod
    def _record_daily_source_failure(cls, fetcher: BaseFetcher, market: str, error: str) -> None:
        cls._daily_source_health.record_failure(cls._daily_health_key(fetcher, market), error=error)

    @classmethod
    def reset_daily_source_health(cls) -> None:
        """Reset daily source health state for tests/admin diagnostics."""
        cls._daily_source_health.reset()

    def _get_cached_stock_name(self, stock_code: str) -> Optional[str]:
        self._ensure_concurrency_guards()
        with self._stock_name_cache_lock:
            return self._stock_name_cache.get(stock_code)

    def _cache_stock_name(self, stock_code: str, name: Optional[str]) -> Optional[str]:
        if name is None:
            return None
        self._ensure_concurrency_guards()
        with self._stock_name_cache_lock:
            self._stock_name_cache[stock_code] = name
        return name

    def _discard_cached_stock_name(self, stock_code: str) -> None:
        self._ensure_concurrency_guards()
        with self._stock_name_cache_lock:
            self._stock_name_cache.pop(stock_code, None)

    def close(self) -> None:
        """Best-effort release of manager-owned resources."""
        for fetcher in self._get_fetchers_snapshot():
            close = getattr(fetcher, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.debug("[%s] close failed: %s", fetcher.name, exc)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Best-effort cleanup during interpreter shutdown.
            pass

    def _get_fundamental_cache_key(self, stock_code: str, budget_seconds: Optional[float] = None) -> str:
        """Sinh key cache dữ liệu cơ bản (có phân nhóm theo ngân sách để kết quả ngân sách
        thấp không làm bẩn request ngân sách cao)."""
        normalized_code = normalize_stock_code(stock_code)
        if budget_seconds is None:
            return f"{normalized_code}|budget=default"
        try:
            budget = max(0.0, float(budget_seconds))
        except (TypeError, ValueError):
            budget = 0.0
        # 100ms bucket to balance cache reuse and scenario isolation.
        budget_bucket = int(round(budget * 10))
        return f"{normalized_code}|budget={budget_bucket}"

    def _prune_fundamental_cache(self, ttl_seconds: int, max_entries: int) -> None:
        """Prune expired and overflow fundamental cache items."""
        with self._fundamental_cache_lock:
            if not self._fundamental_cache:
                return

            now_ts = time.time()
            if ttl_seconds > 0:
                cache_items = list(self._fundamental_cache.items())
                expired_keys = [
                    key
                    for key, value in cache_items
                    if now_ts - float(value.get("ts", 0)) > ttl_seconds
                ]
                for key in expired_keys:
                    self._fundamental_cache.pop(key, None)

            if max_entries > 0 and len(self._fundamental_cache) > max_entries:
                overflow = len(self._fundamental_cache) - max_entries
                sorted_items = sorted(
                    list(self._fundamental_cache.items()),
                    key=lambda item: float(item[1].get("ts", 0)),
                )
                for key, _ in sorted_items[:overflow]:
                    self._fundamental_cache.pop(key, None)

    @staticmethod
    def _try_scalar_isna(value: Any, context: str) -> Optional[bool]:
        """Return scalar ``pd.isna`` result, or ``None`` when callers should use fallback logic."""
        if isinstance(value, (dict, list, tuple, set, pd.DataFrame, pd.Series, pd.Index)):
            return None

        if isinstance(value, np.ndarray):
            if value.ndim != 0:
                return None
            value = value.item()

        try:
            isna_result = pd.isna(value)
        except (TypeError, ValueError) as exc:
            if hasattr(value, "__array__"):
                logger.debug(
                    "[%s] pd.isna failed for array-like object; re-raise: value_type=%s error_type=%s",
                    context,
                    type(value).__name__,
                    type(exc).__name__,
                )
                raise
            logger.debug(
                "[%s] pd.isna fallback: value_type=%s error_type=%s",
                context,
                type(value).__name__,
                type(exc).__name__,
            )
            return None

        if isinstance(isna_result, (bool, np.bool_)):
            return bool(isna_result)

        if isinstance(isna_result, np.ndarray):
            if isna_result.ndim == 0:
                return bool(isna_result.item())
            logger.debug(
                "[%s] pd.isna returned non-scalar result: value_type=%s result_type=%s",
                context,
                type(value).__name__,
                type(isna_result).__name__,
            )
            return None

        logger.debug(
            "[%s] pd.isna returned unexpected result type: value_type=%s result_type=%s",
            context,
            type(value).__name__,
            type(isna_result).__name__,
        )
        return None

    def _init_default_fetchers(self) -> None:
        """
        Khởi tạo danh sách nguồn dữ liệu mặc định của Việt Nam

        - FiinTradeFetcher: nến ngày trước VCI, mặc định bật khi có ``FIINTRADE_ORIGIN``
          (tắt bằng ``FIINTRADE_ENABLED=false``)
        - VciFetcher: nến ngày (nguồn chính), chỉ số, bảng mã
        - SsiFetcher: snapshot realtime toàn thị trường, nến ngày dự phòng
        - VnstockFetcher: tuỳ chọn, chỉ khi ``VNSTOCK_ENABLED=true`` và đã cài vnstock
        - YfinanceFetcher: dự phòng cấp 3 cho nến ngày (chỉ HOSE, không ghi vào DB)
        """
        from .fiintrade_fetcher import FiinTradeFetcher, is_fiintrade_enabled
        from .vci_fetcher import VciFetcher
        from .ssi_fetcher import SsiFetcher
        from .yfinance_fetcher import YfinanceFetcher

        candidates: List[BaseFetcher] = []

        # FiinTrade chỉ trả dữ liệu cho origin trong whitelist của đối tác: mặc định
        # bật nhưng chỉ chạy khi người triển khai điền origin được cấp.
        # FiinTrade only serves whitelisted origins: on by default once an origin is set.
        if is_fiintrade_enabled():
            candidates.append(FiinTradeFetcher())
        else:
            logger.debug("[Khởi tạo nguồn] Bỏ qua FiinTradeFetcher (thiếu FIINTRADE_ORIGIN hoặc FIINTRADE_ENABLED=false)")

        candidates.extend([VciFetcher(), SsiFetcher()])

        # vnstock là phụ thuộc tuỳ chọn: giấy phép của nó là "Custom (Personal Use)"
        # và kéo theo vnai - gói gửi dữ liệu sử dụng về phía tác giả. Vì vậy không
        # đưa vào requirements.txt; chỉ bật khi đặt tường minh VNSTOCK_ENABLED=true
        # và máy đã cài sẵn.
        if str(os.getenv("VNSTOCK_ENABLED", "false")).strip().lower() == "true":
            try:
                from .vnstock_fetcher import VnstockFetcher

                candidates.append(VnstockFetcher())
            except ImportError as exc:
                logger.warning(
                    "[Khởi tạo nguồn] VNSTOCK_ENABLED=true nhưng vnstock không khả dụng, bỏ qua: %s",
                    exc,
                )
        else:
            logger.debug("[Khởi tạo nguồn] Bỏ qua VnstockFetcher (chưa bật)")

        candidates.append(YfinanceFetcher())

        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            self._fetchers = candidates
            self._fetchers.sort(key=lambda f: f.priority)
            self._refresh_fetcher_indexes_locked()

        priority_info = ", ".join([f"{f.name}(P{f.priority})" for f in self._get_fetchers_snapshot()])
        logger.info(f"Đã khởi tạo {len(self._fetchers)} nguồn dữ liệu (theo ưu tiên): {priority_info}")

    def add_fetcher(self, fetcher: BaseFetcher) -> None:
        """Thêm nguồn dữ liệu rồi sắp xếp lại"""
        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            self._fetchers.append(fetcher)
            self._fetchers.sort(key=lambda f: f.priority)
            self._refresh_fetcher_indexes_locked()

    def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Tuple[pd.DataFrame, str]:
        """
        Lấy dữ liệu nến ngày (tự động chuyển nguồn)

        Chiến lược chuyển nguồn khi sự cố:
        1. Thử lần lượt từ nguồn có mức ưu tiên cao nhất (VCI -> SSI -> vnstock -> Yahoo)
        2. Bắt ngoại lệ xong thì tự chuyển sang nguồn kế tiếp và ghi lại lý do thất bại
        3. Mọi nguồn đều hỏng thì ném ngoại lệ kèm chi tiết

        Args:
            stock_code: mã cổ phiếu
            start_date: ngày bắt đầu
            end_date: ngày kết thúc
            days: số ngày cần lấy

        Returns:
            Tuple[DataFrame, str]: (dữ liệu, tên nguồn đã lấy thành công)

        Raises:
            DataFetchError: ném ra khi mọi nguồn đều thất bại hoặc mã không phải Việt Nam
        """
        raw_stock_code = (stock_code or "").strip()
        target = parse_analysis_target(raw_stock_code)
        if target.asset_type == ParseStatus.UNSUPPORTED:
            reason = target.unsupported_reason or "unsupported analysis target"
            raise DataFetchError(f"{raw_stock_code}: {reason}")

        stock_code = normalize_stock_code(stock_code)
        # Nhận diện thị trường bằng input gốc, trước khi quy chuẩn, để giữ cách viết
        # tường minh ``HPG.VN`` / ``vnHPG``.
        market = _market_tag(raw_stock_code) if raw_stock_code else "unknown"
        if market == "unknown":
            market = _market_tag(stock_code)
        self._require_vn_market(market, stock_code)

        fetchers = self._filter_fetchers_by_capability(
            self._get_fetchers_snapshot(), capability="daily_data"
        )
        total_fetchers = len(fetchers)
        errors: List[str] = []
        request_start = time.time()

        if total_fetchers == 0:
            error_summary = f"{stock_code} lấy thất bại:\nkhông có nguồn dữ liệu khả dụng"
            logger.error(f"[Kết thúc nguồn] {error_summary}")
            raise DataFetchError(error_summary)

        for attempt, fetcher in enumerate(fetchers, start=1):
            if not self._is_daily_source_available(fetcher, market):
                errors.append(self._daily_source_unavailable_error(fetcher))
                continue
            attempt_start = time.time()
            fallback_to = fetchers[attempt].name if attempt < total_fetchers else None
            try:
                logger.info(f"[Thử nguồn {attempt}/{total_fetchers}] [{fetcher.name}] lấy {stock_code}...")
                record_provider_run_started(
                    data_type="daily_data",
                    provider=fetcher.name,
                    operation="get_daily_data",
                )
                df = self._call_fetcher_method(
                    fetcher,
                    "get_daily_data",
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    days=days
                )

                if df is not None and not df.empty:
                    duration_ms = int((time.time() - attempt_start) * 1000)
                    record_provider_run(
                        data_type="daily_data",
                        provider=fetcher.name,
                        operation="get_daily_data",
                        success=True,
                        latency_ms=duration_ms,
                        record_count=len(df),
                    )
                    elapsed = time.time() - request_start
                    logger.info(
                        f"[Hoàn tất nguồn] {stock_code} lấy thành công qua [{fetcher.name}]: "
                        f"rows={len(df)}, elapsed={elapsed:.2f}s"
                    )
                    self._record_daily_source_success(fetcher, market)
                    return df, fetcher.name
                duration_ms = int((time.time() - attempt_start) * 1000)
                record_provider_run(
                    data_type="daily_data",
                    provider=fetcher.name,
                    operation="get_daily_data",
                    success=False,
                    latency_ms=duration_ms,
                    error_type="empty",
                    error_message="empty result",
                    fallback_to=fallback_to,
                    record_count=0,
                )
                if df is not None and df.empty:
                    self._record_daily_source_success(fetcher, market)

            except Exception as e:
                error_type, error_reason = summarize_exception(e)
                error_msg = f"[{fetcher.name}] ({error_type}) {error_reason}"
                duration_ms = int((time.time() - attempt_start) * 1000)
                record_provider_run(
                    data_type="daily_data",
                    provider=fetcher.name,
                    operation="get_daily_data",
                    success=False,
                    latency_ms=duration_ms,
                    error_type=error_type,
                    error_message=error_reason,
                    fallback_to=fallback_to,
                )
                logger.warning(
                    f"[Nguồn thất bại {attempt}/{total_fetchers}] [{fetcher.name}] {stock_code}: "
                    f"error_type={error_type}, reason={error_reason}"
                )
                self._record_daily_source_failure(fetcher, market, error_reason)
                errors.append(error_msg)
                if attempt < total_fetchers:
                    next_fetcher = fetchers[attempt]
                    logger.info(f"[Chuyển nguồn] {stock_code}: [{fetcher.name}] -> [{next_fetcher.name}]")
                continue

        # Tất cả nguồn đều thất bại
        error_summary = f"Mọi nguồn dữ liệu đều không lấy được {stock_code}:\n" + "\n".join(errors)
        elapsed = time.time() - request_start
        logger.error(f"[Kết thúc nguồn] {stock_code} lấy thất bại: elapsed={elapsed:.2f}s\n{error_summary}")
        raise DataFetchError(error_summary)

    @property
    def available_fetchers(self) -> List[str]:
        """Trả về danh sách tên các nguồn dữ liệu khả dụng"""
        return [f.name for f in self._get_fetchers_snapshot()]

    def prefetch_realtime_quotes(self, stock_codes: List[str]) -> int:
        """
        Prefetch giá realtime hàng loạt (gọi trước khi bắt đầu phân tích)

        Snapshot SSI phủ toàn bộ ba sàn chỉ bằng một request, nên khi ``ssi`` đứng đầu
        ``REALTIME_SOURCE_PRIORITY`` và danh sách có từ 5 mã trở lên, gọi một lần với
        mã đầu tiên để nạp sẵn cache snapshot; các mã sau sẽ trúng cache.

        Warm the SSI market-wide snapshot cache once for batches of 5+ codes.

        Args:
            stock_codes: danh sách mã cổ phiếu cần phân tích

        Returns:
            Số mã đã prefetch (0 nghĩa là đã bỏ qua prefetch)
        """
        stock_codes = [normalize_stock_code(code) for code in stock_codes]

        from src.config import get_config

        config = get_config()

        # Issue #455: PREFETCH_REALTIME_QUOTES=false để tắt prefetch
        if not getattr(config, "prefetch_realtime_quotes", True):
            logger.debug("[Prefetch] component=realtime_prefetch action=skip reason=disabled")
            return 0

        if not config.enable_realtime_quote:
            logger.debug("[Prefetch] component=realtime_prefetch action=skip reason=realtime_quote_disabled")
            return 0

        if self._vn_realtime_source_order()[0] != "SsiFetcher":
            logger.info("[Prefetch] component=realtime_prefetch action=skip reason=ssi_not_first")
            return 0

        # Dưới 5 mã thì không prefetch hàng loạt (truy vấn từng mã hiệu quả hơn)
        if len(stock_codes) < 5:
            logger.info(
                "[Prefetch] component=realtime_prefetch action=skip reason=small_batch "
                "stock_count=%d threshold=5",
                len(stock_codes),
            )
            return 0

        try:
            quote = self.get_realtime_quote(stock_codes[0])
        except Exception as e:
            logger.error(
                "[Prefetch] component=realtime_prefetch action=complete status=error "
                "stock_count=%d error=%s",
                len(stock_codes),
                e,
            )
            return 0
        if quote:
            logger.info(
                "[Prefetch] component=realtime_prefetch action=complete status=success stock_count=%d",
                len(stock_codes),
            )
            return len(stock_codes)
        logger.warning(
            "[Prefetch] component=realtime_prefetch action=complete status=failed "
            "stock_count=%d fallback=per_stock",
            len(stock_codes),
        )
        return 0

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_realtime_timestamp(value: Any) -> Optional[datetime]:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _realtime_fetcher_token(fetcher_name: str, **kw) -> str:
        mapping = {
            "FiinTradeFetcher": "fiintrade",
            "VciFetcher": "vci",
            "SsiFetcher": "ssi",
            "VnstockFetcher": "vnstock",
            "YfinanceFetcher": "yfinance",
        }
        return mapping.get(fetcher_name, fetcher_name.replace("Fetcher", "").lower())

    def _enrich_realtime_quote(
        self,
        quote,
        *,
        fallback_from: Optional[str] = None,
        realtime_cache_ttl: Optional[int] = None,
    ):
        """Attach runtime metadata without inventing provider-side timestamps."""
        if quote is None:
            return None

        fetched_at = self._utc_now_iso()
        setattr(quote, "fetched_at", fetched_at)
        if fallback_from:
            setattr(quote, "fallback_from", str(fallback_from))

        provider_dt = self._parse_realtime_timestamp(
            getattr(quote, "provider_timestamp", None)
        )
        if provider_dt is None:
            setattr(quote, "provider_timestamp", None)
            setattr(quote, "stale_seconds", None)
            setattr(quote, "is_stale", None)
            return quote

        setattr(quote, "provider_timestamp", provider_dt.isoformat())
        fetched_dt = self._parse_realtime_timestamp(fetched_at) or datetime.now(timezone.utc)
        stale_seconds = max(0, int((fetched_dt - provider_dt).total_seconds()))
        ttl = realtime_cache_ttl if realtime_cache_ttl is not None else 600
        setattr(quote, "stale_seconds", stale_seconds)
        setattr(quote, "is_stale", stale_seconds > int(ttl))
        return quote

    def get_realtime_quote(self, stock_code: str, *, log_final_failure: bool = True):
        """
        Lấy giá realtime (tự động chuyển nguồn)

        Chiến lược:
        1. Tick còn mới từ feed push MQTT (TVSI) nếu đang kết nối
        2. Snapshot toàn thị trường của SSI (cổ phiếu/quỹ)
        3. VCI (chỉ số, suy ra từ nến ngày mới nhất)
        4. Trả về None khi mọi nguồn đều thất bại (hạ cấp dự phòng)

        Args:
            stock_code: mã cổ phiếu
            log_final_failure: Whether to emit the final "all sources failed"
                summary log when no realtime quote is available.

        Returns:
            Đối tượng UnifiedRealtimeQuote; trả None nếu mọi nguồn đều thất bại
        """
        raw_stock_code = (stock_code or "").strip()
        stock_code = normalize_stock_code(stock_code)

        from src.config import get_config

        config = get_config()

        # Nếu tính năng giá realtime bị tắt thì trả về None ngay
        if not config.enable_realtime_quote:
            logger.debug(f"[Giá realtime] Tính năng đã tắt, bỏ qua {stock_code}")
            return None

        # Mã không phải Việt Nam thì hạ cấp thẳng về None: quy ước sẵn có của luồng giá
        # realtime là thất bại trả None chứ không ném exception.
        market = _market_tag(raw_stock_code) if raw_stock_code else "unknown"
        if market == "unknown":
            market = _market_tag(stock_code)
        if market != "vn":
            if log_final_failure:
                logger.info(
                    "[Giá realtime] %s không phải mã Việt Nam (market=%s), bỏ qua",
                    stock_code,
                    market,
                )
            return None

        # Feed push MQTT (TVSI) đang kết nối và có tick còn mới thì dùng trước SSI.
        # A fresh tick from the connected push feed wins over the SSI snapshot.
        from data_provider.realtime_feed import get_fresh_tick, tick_to_quote, vn_bare_symbol

        tick = get_fresh_tick(
            vn_bare_symbol(stock_code),
            float(getattr(config, "realtime_feed_stale_seconds", 30) or 30),
        )
        if tick is not None:
            quote = tick_to_quote(tick)
            setattr(quote, "realtime_provider", "tvsi_mqtt")
            return self._enrich_realtime_quote(
                quote,
                realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
            )
        for source_name in self._vn_realtime_source_order():
            quote = self._try_fetcher_quote(stock_code, source_name)
            if quote is not None:
                logger.info(
                    "[Giá realtime] %s lấy thành công (nguồn: %s)", stock_code, source_name
                )
                # Các fetcher HTTP đều gán source=FALLBACK; ghi lại nguồn thật (fiintrade/ssi/vci)
                # để tầng trên (vd. danh mục) hiển thị đúng. Actual provider token.
                setattr(quote, "realtime_provider", self._realtime_fetcher_token(source_name))
                return self._enrich_realtime_quote(
                    quote,
                    realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
                )
        if log_final_failure:
            logger.info("[Giá realtime] %s không có nguồn dữ liệu khả dụng", stock_code)
        return None

    def _try_fetcher_quote(self, stock_code: str, fetcher_name: str, **kw):
        """Try to get a realtime quote from a named fetcher; returns quote or None."""
        fetcher = self._get_fetcher_by_name(fetcher_name, capability="realtime_quote")
        if fetcher is None or not hasattr(fetcher, 'get_realtime_quote'):
            record_provider_run(
                data_type="realtime_quote",
                provider=fetcher_name,
                operation="get_realtime_quote",
                success=False,
                error_type="unavailable",
                error_message="fetcher unavailable",
            )
            return None
        attempt_start = time.time()
        try:
            record_provider_run_started(
                data_type="realtime_quote",
                provider=fetcher.name,
                operation="get_realtime_quote",
            )
            q = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code, **kw)
            if q is not None and q.has_basic_data():
                record_provider_run(
                    data_type="realtime_quote",
                    provider=fetcher.name,
                    operation="get_realtime_quote",
                    success=True,
                    latency_ms=int((time.time() - attempt_start) * 1000),
                    record_count=1,
                )
                return q
            record_provider_run(
                data_type="realtime_quote",
                provider=fetcher.name,
                operation="get_realtime_quote",
                success=False,
                latency_ms=int((time.time() - attempt_start) * 1000),
                error_type="empty",
                error_message="empty or incomplete quote",
                record_count=0,
            )
        except Exception as e:
            error_type, error_reason = summarize_exception(e)
            record_provider_run(
                data_type="realtime_quote",
                provider=fetcher.name,
                operation="get_realtime_quote",
                success=False,
                latency_ms=int((time.time() - attempt_start) * 1000),
                error_type=error_type,
                error_message=error_reason,
            )
            logger.debug(f"[Giá realtime] {stock_code} {fetcher_name} lấy thất bại: {e}")
        return None

    def get_stock_name(self, stock_code: str, allow_realtime: bool = True) -> Optional[str]:
        """
        Lấy tên cổ phiếu (tự động chuyển nguồn)

        Thử lấy tên cổ phiếu từ nhiều nguồn:
        1. Đọc từ cache trong bộ nhớ trước (nếu có)
        2. Rồi thử bảng ánh xạ cục bộ và index stocks.index.json
        3. Sau đó truy vấn giá realtime khi cần
        4. Cuối cùng thử lần lượt phương thức get_stock_name của từng nguồn

        Args:
            stock_code: mã cổ phiếu
            allow_realtime: Whether to query realtime quote first. Set False when
                caller only wants lightweight prefetch without triggering heavy
                realtime source calls.

        Returns:
            Tên cổ phiếu; trả chuỗi rỗng nếu mọi nguồn đều thất bại
        """
        raw_stock_code = (stock_code or "").strip()
        target = parse_analysis_target(raw_stock_code)
        if target.asset_type == ParseStatus.UNSUPPORTED:
            logger.warning(
                "[Tên cổ phiếu] Bỏ qua input không hỗ trợ %s: %s",
                raw_stock_code,
                target.unsupported_reason or "unsupported analysis target",
            )
            return ""

        stock_code = normalize_stock_code(stock_code)
        static_name = STOCK_NAME_MAP.get(stock_code)

        # 1. Kiểm tra cache trước
        cached_name = self._get_cached_stock_name(stock_code)
        if cached_name is not None:
            return cached_name

        if is_meaningful_stock_name(static_name, stock_code):
            return self._cache_stock_name(stock_code, static_name) or static_name

        index_name = get_index_stock_name(stock_code)
        if is_meaningful_stock_name(index_name, stock_code):
            return self._cache_stock_name(stock_code, index_name) or index_name

        # 2. Thử lấy từ giá realtime (nhanh nhất, có thể tắt khi cần)
        if allow_realtime:
            quote = self.get_realtime_quote(raw_stock_code or stock_code, log_final_failure=False)
            if quote and hasattr(quote, 'name') and is_meaningful_stock_name(getattr(quote, 'name', ''), stock_code):
                name = quote.name
                self._cache_stock_name(stock_code, name)
                logger.info(f"[Tên cổ phiếu] Lấy từ giá realtime: {stock_code} -> {name}")
                return name

        # 3. Lần lượt thử từng nguồn dữ liệu
        for fetcher in self._get_fetchers_snapshot():
            if not hasattr(fetcher, 'get_stock_name'):
                continue
            if not self._is_fetcher_available(fetcher, capability="stock_name"):
                continue
            try:
                name = self._call_fetcher_method(fetcher, 'get_stock_name', stock_code)
                if is_meaningful_stock_name(name, stock_code):
                    self._cache_stock_name(stock_code, name)
                    logger.info(f"[Tên cổ phiếu] Lấy từ {fetcher.name}: {stock_code} -> {name}")
                    return name
            except Exception as e:
                logger.debug(f"[Tên cổ phiếu] {fetcher.name} lấy thất bại: {e}")
                continue

        # 4. Tất cả nguồn đều thất bại
        logger.warning(f"[Tên cổ phiếu] Không nguồn nào lấy được tên của {stock_code}")
        return ""

    def prefetch_stock_names(self, stock_codes: List[str], use_bulk: bool = False) -> None:
        """
        Pre-fetch stock names into cache before parallel analysis (Issue #455).

        When use_bulk=False, only calls get_stock_name per code (no get_stock_list),
        avoiding full-market fetch. Sequential execution to avoid rate limits.

        Args:
            stock_codes: Stock codes to prefetch.
            use_bulk: If True, may use get_stock_list (full fetch). Default False.
        """
        if not stock_codes:
            return
        normalized_codes: List[str] = []
        for code in stock_codes:
            target = parse_analysis_target(code)
            if target.asset_type == ParseStatus.UNSUPPORTED:
                logger.warning(
                    "[Tên cổ phiếu] Prefetch bỏ qua input không hỗ trợ %s: %s",
                    code,
                    target.unsupported_reason or "unsupported analysis target",
                )
                continue
            normalized_codes.append(normalize_stock_code(code))
        stock_codes = normalized_codes
        if use_bulk:
            self.batch_get_stock_names(stock_codes)
            return
        for code in stock_codes:
            # Skip realtime lookup to avoid triggering expensive full-market quote
            # requests during the prefetch phase.
            self.get_stock_name(code, allow_realtime=False)

    def batch_get_stock_names(self, stock_codes: List[str]) -> Dict[str, str]:
        """
        Lấy tên cổ phiếu hàng loạt

        Trước hết thử lấy danh sách cổ phiếu từ những nguồn hỗ trợ truy vấn hàng loạt,
        sau đó mới tra từng mã còn thiếu tên.

        Args:
            stock_codes: danh sách mã cổ phiếu

        Returns:
            Dict {mã cổ phiếu: tên cổ phiếu}
        """
        result = {}
        missing_codes = set(stock_codes)

        # 1. Kiểm tra cache trước
        self._ensure_concurrency_guards()
        with self._stock_name_cache_lock:
            for code in stock_codes:
                cached_name = self._stock_name_cache.get(code)
                if cached_name is not None:
                    result[code] = cached_name
                    missing_codes.discard(code)

        if not missing_codes:
            return result

        # 2. Thử lấy danh sách cổ phiếu theo lô
        for fetcher in self._get_fetchers_snapshot():
            if not hasattr(fetcher, 'get_stock_list') or not missing_codes:
                continue
            if not self._is_fetcher_available(fetcher, capability="stock_list"):
                continue
            try:
                stock_list = self._call_fetcher_method(fetcher, 'get_stock_list')
                if stock_list is not None and not stock_list.empty:
                    cache_updates: Dict[str, str] = {}
                    for _, row in stock_list.iterrows():
                        code = row.get('code')
                        name = row.get('name')
                        if code and name:
                            cache_updates[code] = name
                            if code in missing_codes:
                                result[code] = name
                                missing_codes.discard(code)

                    if cache_updates:
                        with self._stock_name_cache_lock:
                            self._stock_name_cache.update(cache_updates)

                    if not missing_codes:
                        break

                    logger.info(
                        f"[Tên cổ phiếu] Lấy theo lô từ {fetcher.name} xong, còn {len(missing_codes)} mã chờ tra"
                    )
            except Exception as e:
                logger.debug(f"[Tên cổ phiếu] {fetcher.name} lấy theo lô thất bại: {e}")
                continue

        # 3. Lấy từng mã còn lại
        for code in list(missing_codes):
            name = self.get_stock_name(code)
            if name:
                result[code] = name
                missing_codes.discard(code)

        logger.info(f"[Tên cổ phiếu] Lấy theo lô xong, thành công {len(result)}/{len(stock_codes)}")
        return result

    def get_main_indices(self, region: str = "vn") -> List[Dict[str, Any]]:
        """Lấy giá các chỉ số chính của Việt Nam (tự động chuyển nguồn)."""
        for fetcher in self._get_fetchers_snapshot():
            try:
                data = fetcher.get_main_indices(region=region)
                if data:
                    logger.info(f"[{fetcher.name}] Lấy giá chỉ số thành công")
                    return data
            except Exception as e:
                logger.warning(f"[{fetcher.name}] Lấy giá chỉ số thất bại: {e}")
                continue
        return []

    def get_market_stats(self, *, purpose: str = "unspecified", region: str = "vn") -> Dict[str, Any]:
        """Thống kê tăng/giảm toàn thị trường; chỉ hỏi các fetcher tự khai báo hỗ trợ
        region qua ``get_region_market_stats``.

        Market breadth stats; only fetchers implementing ``get_region_market_stats``
        are asked.
        """
        logger.info(
            "[MarketStats] component=market_stats action=start purpose=%s region=%s",
            purpose,
            region,
        )
        for fetcher in self._get_fetchers_snapshot():
            started_at = time.monotonic()
            try:
                data = fetcher.get_region_market_stats(region)
            except Exception as e:
                logger.warning(
                    "[MarketStats] component=market_stats action=provider_failed "
                    "purpose=%s region=%s provider=%s elapsed=%.2fs error=%s",
                    purpose,
                    region,
                    fetcher.name,
                    time.monotonic() - started_at,
                    e,
                )
                continue
            if data:
                logger.info(
                    "[MarketStats] component=market_stats action=provider_success "
                    "purpose=%s region=%s provider=%s elapsed=%.2fs",
                    purpose,
                    region,
                    fetcher.name,
                    time.monotonic() - started_at,
                )
                return data
        logger.warning(
            "[MarketStats] component=market_stats action=complete status=empty purpose=%s region=%s",
            purpose,
            region,
        )
        return {}

    def get_market_money_flow(self, region: str = "vn") -> Dict[str, Any]:
        """Dòng tiền khối ngoại / tự doanh toàn thị trường (nguồn đầu tiên có dữ liệu).

        Lỗi của một nguồn chỉ được log, không làm hỏng luồng điểm lại thị trường.
        Market-level money flow from the first provider with data; fail-open.
        """
        for fetcher in self._get_fetchers_snapshot():
            started_at = time.monotonic()
            try:
                data = fetcher.get_market_money_flow(region)
            except Exception as e:
                logger.warning(
                    "[MoneyFlow] action=provider_failed region=%s provider=%s elapsed=%.2fs error=%s",
                    region,
                    fetcher.name,
                    time.monotonic() - started_at,
                    e,
                )
                continue
            if data:
                logger.info(
                    "[MoneyFlow] action=provider_success region=%s provider=%s elapsed=%.2fs",
                    region,
                    fetcher.name,
                    time.monotonic() - started_at,
                )
                return data
        return {}

    def get_stock_money_flow(self, stock_code: str) -> Dict[str, Any]:
        """Dòng tiền khối ngoại / tự doanh của một mã (nguồn đầu tiên có dữ liệu).

        Lỗi của một nguồn chỉ được log, không làm hỏng luồng phân tích / Hỏi đáp.
        Per-stock money flow from the first provider with data; fail-open.
        """
        for fetcher in self._get_fetchers_snapshot():
            started_at = time.monotonic()
            try:
                data = fetcher.get_stock_money_flow(stock_code)
            except Exception as e:
                logger.warning(
                    "[StockMoneyFlow] action=provider_failed code=%s provider=%s elapsed=%.2fs error=%s",
                    stock_code,
                    fetcher.name,
                    time.monotonic() - started_at,
                    e,
                )
                continue
            if data:
                logger.info(
                    "[StockMoneyFlow] action=provider_success code=%s provider=%s elapsed=%.2fs",
                    stock_code,
                    fetcher.name,
                    time.monotonic() - started_at,
                )
                return data
        return {}

    def get_market_drivers(self, region: str = "vn") -> Dict[str, Any]:
        """Động lực thị trường từ nguồn đầu tiên có dữ liệu; lỗi chỉ log (fail-open).

        Market drivers from the first provider with data; fail-open.
        """
        for fetcher in self._get_fetchers_snapshot():
            started_at = time.monotonic()
            try:
                data = fetcher.get_market_drivers(region)
            except Exception as e:
                logger.warning(
                    "[MarketDrivers] action=provider_failed region=%s provider=%s elapsed=%.2fs error=%s",
                    region,
                    fetcher.name,
                    time.monotonic() - started_at,
                    e,
                )
                continue
            if data:
                logger.info(
                    "[MarketDrivers] action=provider_success region=%s provider=%s elapsed=%.2fs",
                    region,
                    fetcher.name,
                    time.monotonic() - started_at,
                )
                return data
        return {}

    def _run_with_timeout(
        self,
        task: Callable[[], Any],
        timeout_seconds: float,
        task_name: str,
    ) -> Tuple[Optional[Any], Optional[str], int]:
        """
        Execute a task in a short-lived thread and enforce a timeout.

        Returns:
            (result, error, duration_ms)
        """
        start = time.time()
        timeout_value = max(0.0, timeout_seconds)
        if timeout_value <= 0:
            return None, f"{task_name} timeout", 0
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Exception] = {}

        if not self._fundamental_timeout_slots.acquire(blocking=False):
            return None, f"{task_name} timeout worker pool exhausted", int(timeout_value * 1000)

        def runner() -> None:
            try:
                result_holder["value"] = task()
            except Exception as exc:
                error_holder["value"] = exc
            finally:
                try:
                    self._fundamental_timeout_slots.release()
                except ValueError:
                    pass

        worker = Thread(target=runner, daemon=True, name=f"fundamental-{task_name}")
        try:
            worker.start()
        except Exception as exc:
            try:
                self._fundamental_timeout_slots.release()
            except ValueError:
                pass
            return None, str(exc), int((time.time() - start) * 1000)
        worker.join(timeout=timeout_value)
        if worker.is_alive():
            return None, f"{task_name} timeout", int(timeout_value * 1000)
        if "value" in error_holder:
            return None, str(error_holder["value"]), int((time.time() - start) * 1000)
        return result_holder.get("value"), None, int((time.time() - start) * 1000)

    def _run_with_retry(
        self,
        task: Callable[[], Any],
        timeout_seconds: float,
        task_name: str,
    ) -> Tuple[Optional[Any], Optional[str], int]:
        """
        Execute a task with bounded budget and best-effort retries.

        Returns:
            (result, error, total_duration_ms)
        """
        config = self._get_fundamental_config()
        attempts = max(1, int(config.fundamental_retry_max))
        remaining_seconds = max(0.0, float(timeout_seconds))
        total_cost_ms = 0
        last_error: Optional[str] = None

        for _ in range(attempts):
            if remaining_seconds <= 0:
                break
            result, err, cost_ms = self._run_with_timeout(task, remaining_seconds, task_name)
            total_cost_ms += cost_ms
            remaining_seconds = max(0.0, remaining_seconds - cost_ms / 1000)
            if err is None:
                return result, None, total_cost_ms
            last_error = err
            if remaining_seconds <= 0:
                break

        return None, last_error, total_cost_ms

    def _get_fundamental_config(self):
        from src.config import get_config
        return get_config()

    @staticmethod
    def _build_fundamental_block(
        status: str,
        payload: Optional[Dict[str, Any]] = None,
        source_chain: Optional[List[Dict[str, Any]]] = None,
        errors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "coverage": {"status": status},
            "source_chain": source_chain or [],
            "errors": errors or [],
            "data": payload or {},
        }

    @staticmethod
    def _has_meaningful_payload(payload: Any) -> bool:
        if payload is None:
            return False
        if isinstance(payload, str):
            normalized = payload.strip().lower()
            return normalized not in ("", "-", "nan", "none", "null", "n/a", "na")
        if isinstance(payload, dict):
            return any(DataFetcherManager._has_meaningful_payload(v) for v in payload.values())
        if isinstance(payload, pd.DataFrame):
            if payload.empty:
                return False
            return any(
                DataFetcherManager._has_meaningful_payload(v)
                for v in payload.to_numpy().flat
            )
        if isinstance(payload, (pd.Series, pd.Index)):
            return any(DataFetcherManager._has_meaningful_payload(v) for v in payload.tolist())
        if isinstance(payload, np.ndarray):
            if payload.ndim == 0:
                payload = payload.item()
            else:
                return any(
                    DataFetcherManager._has_meaningful_payload(v)
                    for v in payload.flat
                )
        if isinstance(payload, (list, tuple, set)):
            return any(DataFetcherManager._has_meaningful_payload(v) for v in payload)
        if DataFetcherManager._try_scalar_isna(payload, "fundamental_payload") is True:
            return False
        return True

    @staticmethod
    def _infer_block_status(payload: Any, fallback_status: str) -> str:
        if DataFetcherManager._has_meaningful_payload(payload):
            return "ok"
        if fallback_status in ("failed", "partial", "not_supported"):
            return fallback_status
        return "partial"

    @staticmethod
    def _should_cache_fundamental_context(context: Any) -> bool:
        if not isinstance(context, dict):
            return False
        status = str(context.get("status", "")).strip().lower()
        if status == "ok":
            return True
        if status == "failed":
            return False
        for block in FUNDAMENTAL_BLOCKS:
            payload = context.get(block, {})
            if isinstance(payload, dict) and DataFetcherManager._has_meaningful_payload(payload.get("data")):
                return True
        return False

    @classmethod
    def _not_supported_block(cls, reason: str) -> Dict[str, Any]:
        return cls._build_fundamental_block(
            "not_supported",
            {},
            [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
            [reason],
        )

    def _build_market_not_supported(self, market: str, reason: str) -> Dict[str, Any]:
        blocks = {block: self._not_supported_block(reason) for block in FUNDAMENTAL_BLOCKS}
        return {
            "market": market,
            "status": "not_supported",
            "coverage": {block: blocks[block]["status"] for block in blocks},
            "source_chain": [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
            "errors": [reason],
            **blocks,
        }

    def build_failed_fundamental_context(self, stock_code: str, reason: str) -> Dict[str, Any]:
        """Build a consistent failed-context payload for caller-side fallback."""
        market = _market_tag(stock_code)
        blocks = {
            block: self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                [reason],
            )
            for block in FUNDAMENTAL_BLOCKS
        }
        return {
            "market": market,
            "status": "failed",
            "coverage": {block: "failed" for block in FUNDAMENTAL_BLOCKS},
            "source_chain": [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
            "errors": [reason],
            **blocks,
        }

    def build_not_supported_fundamental_context(
        self, stock_code: str, reason: str
    ) -> Dict[str, Any]:
        """Build a consistent not-supported payload without calling providers."""
        return self._build_market_not_supported(_market_tag(stock_code), reason)

    def _get_valuation_quote(self, stock_code: str, fiintrade: Optional[BaseFetcher]):
        """Snapshot dùng cho khối định giá: FiinTrade trước (có PE/PB/vốn hoá), rồi
        chuỗi realtime thường. Tick MQTT và snapshot SSI không có định giá.

        Valuation snapshot: FiinTrade first (it carries PE/PB/market cap), then the
        regular realtime chain.
        """
        if fiintrade is not None:
            quote = self._call_fetcher_method(fiintrade, "get_realtime_quote", stock_code)
            # Trước khi khớp lệnh (ATO) giá bằng 0 nhưng P/E/P/B vẫn có: vẫn nhận để
            # khối định giá không bị trống. / Before the first match price is 0 but
            # PE/PB are still present, so keep the quote for valuation.
            if quote is not None and (
                quote.has_basic_data()
                or any(getattr(quote, f, None) is not None for f in ("pe_ratio", "pb_ratio", "total_mv"))
            ):
                return quote
        return self.get_realtime_quote(stock_code)

    def _build_vn_event_blocks(
        self,
        events: Optional[Dict[str, Any]],
        events_err: Optional[str],
        events_ms: int,
        quote: Any,
        today: Optional[date] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Dựng khối ``earnings`` và ``institution`` từ sự kiện FiinTrade.

        Build the ``earnings`` / ``institution`` blocks from FiinTrade events.
        """
        errors = [events_err] if events_err else []
        # Mã không tra được trên FiinTrade trả danh sách rỗng: rỗng hoàn toàn nghĩa là
        # "không có dữ liệu", không phải "không có sự kiện" (mã niêm yết luôn có ít
        # nhất ngày công bố KQKD trong 2 năm). Không được báo cổ tức = 0.
        # An all-empty payload means "not covered", never "no events".
        if isinstance(events, dict) and not any(events.get(key) for key in events):
            events = None
            errors = errors or ["no FiinTrade events for this symbol"]
        if not isinstance(events, dict):
            status = "failed" if events_err else "not_supported"
            chain = [{"provider": "fiintrade_events", "result": status, "duration_ms": events_ms}]
            return {
                block: self._build_fundamental_block(status, {}, chain, errors)
                for block in _VN_FIINTRADE_FUNDAMENTAL_BLOCKS
            }

        today = today or datetime.now().date()
        price = getattr(quote, "price", None) if quote is not None else None

        def _within(day_text: Optional[str], days: int) -> bool:
            if not day_text:
                return False
            try:
                day = datetime.strptime(day_text, "%Y-%m-%d").date()
            except ValueError:
                return False
            return today - timedelta(days=days) < day <= today

        # Cổ tức tiền mặt 12 tháng tính theo ngày GDKHQ, trước thuế.
        # TTM cash dividends by ex-date, pre-tax.
        ttm_events = [
            {
                "ex_dividend_date": row.get("ex_date"),
                "record_date": row.get("record_date"),
                "cash_per_share": row.get("cash_per_share"),
            }
            for row in events.get("dividends") or []
            if _within(row.get("ex_date"), _DIVIDEND_TTM_DAYS) and row.get("cash_per_share")
        ]
        earnings_payload: Dict[str, Any] = {}
        if ttm_events:
            ttm_cash = round(sum(float(row["cash_per_share"]) for row in ttm_events), 2)
            ttm_yield = (
                round(ttm_cash / float(price) * 100, 2)
                if isinstance(price, (int, float)) and price > 0
                else None
            )
            earnings_payload["dividend"] = {
                "ttm_cash_dividend_per_share": ttm_cash,
                "ttm_dividend_yield_pct": ttm_yield,
                "ttm_event_count": len(ttm_events),
                "currency": "VND",
                "events": ttm_events,
            }
        else:
            earnings_payload["dividend"] = {
                "ttm_cash_dividend_per_share": 0.0,
                "ttm_dividend_yield_pct": 0.0 if isinstance(price, (int, float)) and price > 0 else None,
                "ttm_event_count": 0,
                "currency": "VND",
                "events": [],
            }
        issuances = [
            row for row in events.get("share_issuances") or []
            if _within(row.get("ex_date") or row.get("public_date"), _ISSUANCE_WINDOW_DAYS)
        ]
        if issuances:
            earnings_payload["share_issuances"] = issuances
        releases = list(events.get("earnings_releases") or [])[:_EARNINGS_RELEASE_MAX_ITEMS]
        if releases:
            earnings_payload["earnings_releases"] = releases

        insiders = [
            row for row in events.get("insider_transactions") or []
            if _within(row.get("public_date"), _INSIDER_WINDOW_DAYS)
        ]
        net_shares = 0.0
        for row in insiders:
            shares = row.get("shares_executed") or 0.0
            if row.get("action") == "buy":
                net_shares += float(shares)
            elif row.get("action") == "sell":
                net_shares -= float(shares)
        institution_payload: Dict[str, Any] = {
            "insider_window_days": _INSIDER_WINDOW_DAYS,
            "insider_transaction_count": len(insiders),
            "insider_net_shares": net_shares,
            "insider_transactions": insiders[:_INSIDER_MAX_ITEMS],
        }
        for field in ("foreign_ownership_pct", "foreign_room_remaining"):
            value = getattr(quote, field, None) if quote is not None else None
            if value is not None:
                institution_payload[field] = value

        chain = [{"provider": "fiintrade_events", "result": "ok", "duration_ms": events_ms}]
        # Chưa có báo cáo tài chính (financial_report) nên khối earnings là "partial".
        # No financial_report yet, so the earnings block is "partial".
        return {
            "earnings": self._build_fundamental_block("partial", earnings_payload, chain, errors),
            "institution": self._build_fundamental_block("ok", institution_payload, chain, errors),
        }

    def get_fundamental_context(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Tổng hợp dữ liệu cơ bản theo ngữ nghĩa fail-open.

        Việt Nam có khối định giá (PE/PB/vốn hoá) lấy từ snapshot giá realtime (ưu
        tiên FiinTrade vì SSI không có định giá). Khi FiinTrade được bật thì có thêm
        khối ``earnings`` (cổ tức TTM, phát hành, ngày công bố KQKD) và
        ``institution`` (giao dịch nội bộ, sở hữu nước ngoài). Các khối còn lại trả
        ``not_supported`` mà không gọi provider nào.

        Aggregate fundamentals fail-open. Valuation comes from the realtime snapshot
        (FiinTrade first); earnings/institution come from FiinTrade events when it is
        enabled; other blocks are ``not_supported`` without provider calls.
        """
        from src.config import get_config

        config = get_config()
        if not config.enable_fundamental_pipeline:
            return self._build_market_not_supported(
                market=_market_tag(stock_code),
                reason="fundamental pipeline disabled",
            )

        stock_code = normalize_stock_code(stock_code)
        market = _market_tag(stock_code)
        if market != "vn":
            return self._build_market_not_supported(
                market=market,
                reason="not a Vietnam stock code",
            )

        stage_timeout = float(
            budget_seconds if budget_seconds is not None else config.fundamental_stage_timeout_seconds
        )
        stage_timeout = max(0.0, stage_timeout)
        fetch_timeout = max(0.0, float(config.fundamental_fetch_timeout_seconds))

        cache_ttl = int(config.fundamental_cache_ttl_seconds)
        cache_max_entries = max(0, int(getattr(config, "fundamental_cache_max_entries", 256)))
        cache_key = self._get_fundamental_cache_key(stock_code, stage_timeout)
        if cache_ttl > 0:
            self._prune_fundamental_cache(cache_ttl, cache_max_entries)
            with self._fundamental_cache_lock:
                cache_item = self._fundamental_cache.get(cache_key)
                if cache_item:
                    age = time.time() - float(cache_item.get("ts", 0))
                    if age <= cache_ttl:
                        return cache_item.get("context", {})

        start_ts = time.time()
        fiintrade = self._get_fetcher_by_name(_FIINTRADE_FETCHER_NAME, capability="fundamental")
        valuation_timeout = min(fetch_timeout, stage_timeout)
        if valuation_timeout > 0:
            quote_payload, valuation_err, valuation_ms = self._run_with_retry(
                lambda: self._get_valuation_quote(stock_code, fiintrade),
                valuation_timeout,
                "fundamental_valuation",
            )
        else:
            quote_payload, valuation_err, valuation_ms = None, "fundamental stage timeout", 0

        valuation_payload = {
            "pe_ratio": getattr(quote_payload, "pe_ratio", None) if quote_payload else None,
            "pb_ratio": getattr(quote_payload, "pb_ratio", None) if quote_payload else None,
            "total_mv": getattr(quote_payload, "total_mv", None) if quote_payload else None,
            "circ_mv": getattr(quote_payload, "circ_mv", None) if quote_payload else None,
        }
        # Trường bổ sung chỉ FiinTrade có; thêm vào khi có giá trị để payload SSI giữ
        # nguyên như cũ. / Extra FiinTrade-only fields, added only when present.
        for extra_field in ("eps_ttm", "roe", "beta", "foreign_ownership_pct"):
            extra_value = getattr(quote_payload, extra_field, None) if quote_payload else None
            if extra_value is not None:
                valuation_payload[extra_field] = extra_value
        valuation_status = self._infer_block_status(
            valuation_payload,
            "partial" if quote_payload is not None else "not_supported",
        )
        if valuation_status == "partial" and valuation_err and not self._has_meaningful_payload(valuation_payload):
            valuation_status = "failed"

        result_ctx: Dict[str, Any] = {
            "market": market,
            "valuation": self._build_fundamental_block(
                valuation_status,
                valuation_payload,
                [{"provider": "realtime_quote", "result": valuation_status, "duration_ms": valuation_ms}],
                [valuation_err] if valuation_err else [],
            ),
        }
        unsupported_blocks = list(_VN_UNSUPPORTED_FUNDAMENTAL_BLOCKS)
        if fiintrade is not None:
            unsupported_blocks = [b for b in unsupported_blocks if b not in _VN_FIINTRADE_FUNDAMENTAL_BLOCKS]
            remaining = max(0.0, stage_timeout - (time.time() - start_ts))
            events_timeout = min(fetch_timeout, remaining)
            if events_timeout > 0:
                events_payload, events_err, events_ms = self._run_with_retry(
                    lambda: self._call_fetcher_method(fiintrade, "get_corporate_events", stock_code),
                    events_timeout,
                    "fundamental_events",
                )
            else:
                events_payload, events_err, events_ms = None, "fundamental stage timeout", 0
            result_ctx.update(
                self._build_vn_event_blocks(events_payload, events_err, events_ms, quote_payload)
            )
        for block in unsupported_blocks:
            result_ctx[block] = self._not_supported_block(_VN_FUNDAMENTAL_UNSUPPORTED_REASON)

        block_statuses = {block: result_ctx[block].get("status", "not_supported") for block in FUNDAMENTAL_BLOCKS}
        result_ctx["coverage"] = block_statuses
        result_ctx["errors"] = []
        result_ctx["source_chain"] = []
        for block in FUNDAMENTAL_BLOCKS:
            result_ctx["errors"].extend(result_ctx[block].get("errors", []))
            result_ctx["source_chain"].extend(result_ctx[block].get("source_chain", []))

        if all(value == "not_supported" for value in block_statuses.values()):
            result_ctx["status"] = "not_supported"
        elif all(value == "ok" for value in block_statuses.values()):
            result_ctx["status"] = "ok"
        else:
            # Khối tăng trưởng chưa có nguồn nên thực tế kết quả tốt nhất là "partial".
            # Growth has no Vietnam source yet, so in practice the best is "partial".
            result_ctx["status"] = "partial"

        result_ctx["elapsed_ms"] = int((time.time() - start_ts) * 1000)
        if cache_ttl > 0 and self._should_cache_fundamental_context(result_ctx):
            with self._fundamental_cache_lock:
                self._fundamental_cache[cache_key] = {
                    "ts": time.time(),
                    "context": result_ctx,
                }
            self._prune_fundamental_cache(cache_ttl, cache_max_entries)
        return result_ctx
