# -*- coding: utf-8 -*-
"""
===================================
Lớp cơ sở và manager cho các nguồn dữ liệu
===================================

Mẫu thiết kế: Strategy Pattern
- BaseFetcher: lớp trừu tượng, định nghĩa giao diện thống nhất
- DataFetcherManager: quản lý chiến lược, tự động chuyển nguồn

Chiến lược tránh bị chặn:
1. Mỗi Fetcher tự có logic điều tiết tần suất
2. Thất bại thì tự động chuyển sang nguồn kế tiếp
3. Cơ chế retry với backoff luỹ thừa
"""

import os
import logging
import re
import random
import time
from threading import BoundedSemaphore, RLock, Thread
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable, Optional, List, Tuple, Dict, Any

import pandas as pd
import numpy as np
from src.data.stock_index_loader import get_index_stock_name
from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name
from src.services.market_symbol_utils import is_suffix_market_symbol
from src.services.run_diagnostics import record_provider_run, record_provider_run_started
from src.services.stock_list_parser import AnalysisTarget, ParseStatus, parse_analysis_target
from .fundamental_adapter import AkshareFundamentalAdapter
from .yfinance_fundamental_adapter import YfinanceFundamentalAdapter
from .realtime_types import CircuitBreaker

# Cấu hình logging
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


def normalize_stock_code(stock_code: str) -> str:
    """
    Normalize stock code by stripping exchange prefixes/suffixes.

    Accepted formats and their normalized results:
    - '600519'      -> '600519'   (already clean)
    - 'SH600519'    -> '600519'   (strip SH prefix)
    - 'SH.600519'   -> '600519'   (strip SH. prefix)
    - 'SZ000001'    -> '000001'   (strip SZ prefix)
    - 'SS600519'    -> '600519'   (strip legacy Yahoo Shanghai prefix)
    - 'SZ.000001'   -> '000001'   (strip SZ. prefix)
    - 'BJ920748'    -> '920748'   (strip BJ prefix, BSE)
    - 'BJ.920748'   -> '920748'   (strip BJ. prefix, BSE)
    - 'sh600519'    -> '600519'   (case-insensitive)
    - '600519.SH'   -> '600519'   (strip .SH suffix)
    - '000001.SZ'   -> '000001'   (strip .SZ suffix)
    - '920748.BJ'   -> '920748'   (strip .BJ suffix, BSE)
    - 'HK00700'     -> 'HK00700'  (keep HK prefix for HK stocks)
    - '1810.HK'     -> 'HK01810'  (normalize HK suffix to canonical prefix form)
    - '7203.T'      -> '7203.T'   (keep Japan Yahoo suffix form)
    - '005930.KS'   -> '005930.KS' (keep Korea Yahoo suffix form)
    - '2330.TW'     -> '2330.TW'  (keep Taiwan TWSE Yahoo suffix form)
    - '6505.TWO'    -> '6505.TWO' (keep Taiwan TPEx Yahoo suffix form)
    - 'AAPL'        -> 'AAPL'     (keep US stock ticker as-is)

    This function is applied at the DataProviderManager layer so that
    all individual fetchers receive a clean 6-digit code (for A-shares/ETFs).
    """
    code = stock_code.strip()
    upper = code.upper()

    # Việt Nam: ``vnHPG`` / ``HPG.VN`` đều quy về mã trần ``HPG`` (đúng cách viết mà
    # provider yêu cầu). Khác với HK vẫn giữ tiền tố ``HK``, mã trần của Việt Nam
    # chính là dạng chuẩn mà sàn và người dùng đang dùng.
    # Lưu ý: khi bật đồng thời vn và us, mã trần 3 ký tự thực sự chồng lấn không
    # gian mã của hai thị trường; sau khi quy chuẩn thì không còn phân biệt được
    # nữa, phải khử nhập nhằng bằng tiền tố tường minh ở tầng stock_list_parser.
    if upper.endswith(".VN"):
        candidate = upper[:-3]
        if is_vn_security_code(candidate):
            return candidate
    # Tiền tố ``vn`` bắt buộc viết thường, thống nhất với quy ước sẵn có của tiền
    # tố ``us``; những mã viết hoa toàn bộ như ``VNRDF`` phải coi là ticker Mỹ,
    # không được cắt thành ``RDF``.
    if code[:2] == "vn" and len(code) > 2:
        candidate = upper[2:]
        # ``VN30``/``VNINDEX`` bản thân là mã chỉ số, không được cắt như tiền tố ``vn``
        if upper not in VN_INDEX_SYMBOLS and is_vn_security_code(candidate):
            return candidate
    # Mã quỹ có tiền tố ``VN`` viết hoa (``VNFUEVN100``, sinh ra khi tầng API chuẩn
    # hoá chữ hoa ``vnFUEVN100``): dài 10 ký tự nên không thể là ticker Mỹ.
    # Upper-case ``VN`` + fund code is unambiguous, strip it too.
    if upper.startswith("VN") and _VN_FUND_CODE_RE.match(upper[2:]):
        return upper[2:]

    # Normalize HK prefix to a canonical 5-digit form (e.g. hk1810 -> HK01810)
    if upper.startswith('HK') and not upper.startswith('HK.'):
        candidate = upper[2:]
        if candidate.isdigit() and 1 <= len(candidate) <= 5:
            return f"HK{candidate.zfill(5)}"

    # Strip SH/SZ/SS prefix (e.g. SH600519 -> 600519, SS600519 -> 600519)
    if upper.startswith(('SH', 'SZ', 'SS')) and not upper.startswith(('SH.', 'SZ.', 'SS.')):
        candidate = code[2:]
        # Only strip if the remainder looks like a valid numeric code
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate

    # Strip dotted SH/SZ/SS prefix (e.g. SH.600519 -> 600519)
    if upper.startswith(('SH.', 'SZ.', 'SS.')):
        candidate = code[3:]
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate

    # Strip BJ prefix (e.g. BJ920748 -> 920748)
    if upper.startswith('BJ') and not upper.startswith('BJ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate

    # Strip dotted BJ prefix (e.g. BJ.920748 -> 920748)
    if upper.startswith('BJ.'):
        candidate = code[3:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate

    # Strip .SH/.SZ/.BJ suffix (e.g. 600519.SH -> 600519, 920748.BJ -> 920748)
    # while preserving explicit Yahoo suffix forms for JP/KR/TW.
    if '.' in code:
        base, suffix = code.rsplit('.', 1)
        if suffix.upper() == 'T' and base.isdigit() and len(base) in (4, 5):
            return f"{base}.{suffix.upper()}"
        if suffix.upper() in ('KS', 'KQ') and base.isdigit() and len(base) == 6:
            return f"{base}.{suffix.upper()}"
        if suffix.upper() in ('TW', 'TWO') and base.isdigit() and 4 <= len(base) <= 6:
            return f"{base}.{suffix.upper()}"
        if suffix.upper() == 'HK' and base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"
        if base.upper() in ('SH', 'SS', 'SZ', 'BJ') and suffix.isdigit():
            return suffix
        if suffix.upper() in ('SH', 'SZ', 'SS', 'BJ') and base.isdigit():
            return base

    return code


ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")


def _is_us_market(code: str) -> bool:
    """Kiểm tra có phải mã cổ phiếu/chỉ số Mỹ hay không (không kèm tiền tố/hậu tố)."""
    from .us_index_mapping import is_us_stock_code, is_us_index_code

    normalized = (code or "").strip().upper()
    return is_us_index_code(normalized) or is_us_stock_code(normalized)


def _is_hk_market(code: str) -> bool:
    """
    Kiểm tra có phải mã cổ phiếu Hồng Kông hay không.

    Hỗ trợ hậu tố ``.HK``, dạng tiền tố ``HK00700``, và mã trần thuần số 4-5 chữ
    số (ETF/cổ phiếu A-share có 6 chữ số nên không xung đột với mã trần 4-5 chữ
    số của HK). Hàm ``_is_hk_code`` trong ``YfinanceFetcher``,
    ``AkshareFetcher`` và ``LongbridgeFetcher`` dùng cùng khoảng độ dài với hàm
    này cho mã HK dạng trần.
    """
    normalized = (code or "").strip().upper()
    if normalized.endswith(".HK"):
        base = normalized[:-3]
        return base.isdigit() and 1 <= len(base) <= 5
    if normalized.startswith("HK"):
        digits = normalized[2:]
        return digits.isdigit() and 1 <= len(digits) <= 5
    if normalized.isdigit() and 4 <= len(normalized) <= 5:
        return True
    return False


def _is_jp_market(code: str) -> bool:
    """Kiểm tra có phải mã Nhật theo hậu tố Yahoo Finance hay không (ví dụ 7203.T)."""
    return is_suffix_market_symbol(code, "jp")


def _is_kr_market(code: str) -> bool:
    """Kiểm tra có phải mã Hàn Quốc theo hậu tố Yahoo Finance hay không
    (ví dụ 005930.KS / 035720.KQ)."""
    return is_suffix_market_symbol(code, "kr")


def _is_tw_market(code: str) -> bool:
    """Kiểm tra có phải mã Đài Loan theo hậu tố Yahoo Finance hay không
    (niêm yết TWSE 2330.TW / giao dịch TPEx 6505.TWO).

    Phần gốc của mã Đài Loan dài 4-6 ký tự (cổ phiếu thường 4 ký tự, ETF và loại
    khác tới 6 ký tự, ví dụ 00878 / 006208). Chỉ mã có hậu tố .TW/.TWO mới được
    nhận là cổ phiếu Đài Loan; mã trần 6 chữ số vẫn hiểu theo ngữ nghĩa A-share.
    """
    return is_suffix_market_symbol(code, "tw")


def _is_etf_code(code: str) -> bool:
    """Nhận diện mã quỹ ETF của A-share (quy tắc thận trọng)."""
    normalized = normalize_stock_code(code)
    return (
        normalized.isdigit()
        and len(normalized) == 6
        and normalized.startswith(ETF_PREFIXES)
    )


def _coerce_chip_metric(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        numeric = float(value)
        if np.isnan(numeric):
            return None
        return numeric
    except (TypeError, ValueError):
        return None


def _is_meaningful_chip_distribution(chip: Any) -> bool:
    """Validate that a provider returned usable core chip metrics."""
    if chip is None:
        return False
    avg_cost = _coerce_chip_metric(getattr(chip, "avg_cost", None))
    concentration_90 = _coerce_chip_metric(getattr(chip, "concentration_90", None))
    concentration_70 = _coerce_chip_metric(getattr(chip, "concentration_70", None))
    return (
        avg_cost is not None
        and avg_cost > 0
        and (
            (concentration_90 is not None and concentration_90 >= 0)
            or (concentration_70 is not None and concentration_70 >= 0)
        )
    )


# Mã chỉ số Việt Nam. API của VCI phân biệt hoa/thường (``HNXINDEX`` trả về rỗng,
# phải là ``HNXIndex`` mới có dữ liệu), nên ở đây lưu đúng cách viết mà provider
# yêu cầu; khoá là phần người dùng nhập đã quy chuẩn về chữ hoa.
VN_INDEX_SYMBOLS = {
    "VNINDEX": "VNINDEX",
    "VN30": "VN30",
    "VN100": "VN100",
    "HNXINDEX": "HNXIndex",
    "HNX30": "HNX30",
    "HNXUPCOMINDEX": "HNXUpcomIndex",
    "UPCOMINDEX": "HNXUpcomIndex",
    "UPCOM": "HNXUpcomIndex",
}

# Mã cổ phiếu Việt Nam cố định 3 ký tự chữ hoa hoặc số (đã đối chiếu bảng mã
# đầy đủ của VCI: 1602 mã thuần chữ + 151 mã có số, ví dụ ``X77``/``VW3``).
# Vietnam stock codes: exactly 3 uppercase alphanumerics.
_VN_STOCK_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{2}$")

# Chứng chỉ quỹ ETF / quỹ đóng Việt Nam: 8 ký tự, tiền tố ``FU`` (``FUEVN100``,
# ``FUESSV30``, ``FUCVREIT``) hoặc ``E1VF`` (``E1VFVN30``). Không trùng ticker Mỹ
# (1-5 ký tự) nên mã trần luôn được nhận là Việt Nam, không cần khử nhập nhằng.
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


def _is_vn_market(code: str) -> bool:
    """Kiểm tra có phải mã cổ phiếu / chỉ số Việt Nam hay không.

    Chấp nhận ba cách viết:
    - Hậu tố tường minh ``HPG.VN``
    - Chỉ số đã đăng ký ``VNINDEX`` / ``VN30`` / ``HNXIndex`` / ``HNXUpcomIndex``
    - Mã trần ``HPG`` (3 ký tự chữ hoa hoặc số)
    - Mã chứng chỉ quỹ ETF trần ``FUEVN100`` / ``E1VFVN30`` (luôn là Việt Nam)

    Mã trần chồng lấn thật sự với regex ticker Mỹ ``^[A-Z]{1,5}$`` (``FPT``/``VNM``
    vừa là cổ phiếu Việt Nam vừa có thể bị hiểu là ticker Mỹ). Vì vậy mã trần chỉ
    được nhận là Việt Nam khi **thị trường Mỹ chưa bật**; khi bật cả hai thị trường
    thì bắt buộc viết ``HPG.VN`` hoặc ``vnHPG`` để tránh nhận nhầm một cách âm thầm.
    Tiền tố ``vn`` đã bị cắt thành mã trần ngay ở bước :func:`normalize_stock_code`;
    lúc đó nếu thị trường Mỹ cũng đang bật thì luồng sẽ rơi vào nhánh nhập nhằng bên
    dưới, và phía gọi tự xử lý theo tiền tố tường minh.
    """

    normalized = (code or "").strip().upper()
    if not normalized:
        return False

    if normalized.endswith(".VN"):
        base = normalized[:-3]
        return is_vn_security_code(base)

    if normalized in VN_INDEX_SYMBOLS:
        return True

    # Tiền tố tường minh ``vnHPG``. Theo đúng quy ước sẵn có của repo với tiền tố
    # ``us``: **tiền tố bắt buộc viết thường** (trong ``stock_list_parser``,
    # ``usAAPL`` là dạng có tiền tố, còn ``USFD`` viết hoa toàn bộ là ticker trần).
    # Nếu không, ticker Mỹ 5 ký tự viết hoa (ví dụ ``VNRDF``) sẽ bị cắt nhầm thành
    # mã Việt Nam. ``VN30`` / ``VNINDEX`` đã được trả về ở nhánh chỉ số phía trên
    # nên không xuống tới đây.
    raw = (code or "").strip()
    if raw[:2] == "vn" and is_vn_security_code(raw[2:]):
        return True
    if normalized.startswith("VN") and _VN_FUND_CODE_RE.match(normalized[2:]):
        return True

    # Mã chứng chỉ quỹ 8 ký tự không chồng lấn ticker Mỹ nên nhận luôn là Việt Nam.
    # Fund certificates never collide with US tickers, accept them unconditionally.
    if _VN_FUND_CODE_RE.match(normalized):
        return True

    if not _VN_STOCK_CODE_RE.match(normalized):
        return False

    from src.core.markets import is_market_enabled

    return not is_market_enabled("us")


# market -> hàm nhận diện. ``cn`` không nằm ở đây: nó là nhánh dự phòng mang tính
# lịch sử, không có quy tắc nhận diện thuận (mã 6 số trần, ETF, mã sàn Bắc Kinh đều
# về nó), nên được xử lý riêng.
_MARKET_DETECTORS = {
    "vn": _is_vn_market,
    "hk": _is_hk_market,
    "us": _is_us_market,
    "jp": _is_jp_market,
    "kr": _is_kr_market,
    "tw": _is_tw_market,
}


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
        market: Nhãn thị trường (``vn``/``cn``/…).

    Returns:
        ``False`` khi nguồn lệch cơ sở điều chỉnh so với nguồn chính của thị trường.
    """

    blocked = _NON_PERSISTABLE_DAILY_SOURCES.get((source_name or "").strip())
    if not blocked:
        return True
    return (market or "").strip().lower() not in blocked


def _market_tag(code: str) -> str:
    """Trả về nhãn thị trường: vn/cn/us/hk/jp/kr/tw; thị trường chưa bật trả ``unknown``.

    Thứ tự nhận diện lấy từ ``detect_order`` trong :mod:`src.core.markets`, và chỉ
    duyệt các thị trường đang được ``SUPPORTED_MARKETS`` bật. Trước đây chỗ này là
    một chuỗi if viết cứng, thêm thị trường mới phải sửa nhiều nơi cùng lúc; giờ chỉ
    cần thêm một dòng vào registry và đăng ký hàm nhận diện.
    """

    from src.core.markets import enabled_markets, is_market_enabled

    for market in enabled_markets():
        detector = _MARKET_DETECTORS.get(market)
        if detector is not None and detector(code):
            return market

    # ``cn`` là nhánh dự phòng: mọi mã không nhánh nào ở trên nhận đều xử lý như
    # cổ phiếu A-share, giữ nguyên hành vi lịch sử.
    if is_market_enabled("cn"):
        return "cn"
    return "unknown"


def should_persist_daily_data(source_name: str, stock_code: str) -> bool:
    """Nến ngày vừa lấy về có được ghi vào ``stock_daily`` hay không.

    Bản tiện dụng của :func:`is_daily_source_persistable`, tự suy ra thị trường từ
    mã nên điểm gọi chỉ còn một dòng.

    Args:
        source_name: Tên fetcher đã phục vụ, đúng như ``get_daily_data`` trả về.
        stock_code: Mã cổ phiếu ở dạng người dùng nhập.
    """

    return is_daily_source_persistable(source_name, _market_tag(stock_code))


def is_bse_code(code: str) -> bool:
    """
    Check if the code is a Beijing Stock Exchange (BSE) A-share code.

    BSE rules (2026):
    - New format (2024+): 92xxxx main trading codes
    - Historical ranges: 43xxxx, 83xxxx, 87xxxx, 88xxxx
    - Special instruments: 81xxxx convertible bonds, 82xxxx preferred shares
    - Subscription codes: 889xxx
    Note: 900xxx are Shanghai B-shares and must return False.
    """
    c = (code or "").strip().split(".")[0]
    if len(c) != 6 or not c.isdigit():
        return False

    if c.startswith("900"):
        return False

    return c.startswith(("92", "43", "81", "82", "83", "87", "88"))

def is_st_stock(name: str) -> bool:
    """
    Check if the stock is an ST or *ST stock based on its name.

    ST stocks have special trading rules and typically a ±5% limit.
    """
    n = (name or "").upper()
    return 'ST' in n

def is_kc_cy_stock(code: str) -> bool:
    """
    Check if the stock is a STAR Market or ChiNext stock based on its code.

    - STAR Market: Codes starting with 688
    - ChiNext: Codes starting with 300
    Both have a ±20% limit.
    """
    c = (code or "").strip().split(".")[0]
    return c.startswith("688") or c.startswith("30")


def canonical_stock_code(code: str) -> str:
    """
    Return the canonical (uppercase) form of a stock code.

    This is a display/storage layer concern, distinct from normalize_stock_code
    which strips exchange prefixes. Apply at system input boundaries to ensure
    consistent case across BOT, WEB UI, API, and CLI paths (Issue #355).

    Examples:
        'aapl'    -> 'AAPL'
        'AAPL'    -> 'AAPL'
        '600519'  -> '600519'  (digits are unchanged)
        'hk00700' -> 'HK00700'
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
            stock_code: mã cổ phiếu, ví dụ '600519', '000001'
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

    def get_main_indices(self, region: str = "cn") -> Optional[List[Dict[str, Any]]]:
        """
        Lấy giá realtime của các chỉ số chính

        Args:
            region: khu vực thị trường, cn=A-share us=Mỹ

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

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        """
        Lấy thống kê tăng/giảm của thị trường

        Returns:
            Dict: gồm:
                - up_count: số mã tăng
                - down_count: số mã giảm
                - flat_count: số mã đứng giá
                - limit_up_count: số mã trần
                - limit_down_count: số mã sàn
                - total_amount: tổng giá trị giao dịch hai sàn
        """
        return None

    def get_region_market_stats(self, region: str) -> Optional[Dict[str, Any]]:
        """Thống kê tăng/giảm cho thị trường ngoài A-share (ví dụ ``vn``).

        Tách riêng khỏi ``get_market_stats`` vì các fetcher A-share bỏ qua region
        và luôn trả dữ liệu A-share; gọi chúng cho ``vn`` sẽ trộn nhầm thị trường.
        Cùng cấu trúc dict với ``get_market_stats``; mặc định không hỗ trợ.

        Kept separate from ``get_market_stats`` because A-share fetchers ignore
        the region and would return A-share breadth for another market.
        """
        return None

    def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        """
        Lấy bảng xếp hạng tăng/giảm theo nhóm ngành

        Args:
            n: số lượng phần tử đầu cần trả về

        Returns:
            Tuple: (danh sách nhóm dẫn dắt tăng, danh sách nhóm dẫn dắt giảm)
        """
        return None

    def get_concept_rankings(self, n: int = 5) -> Optional[Tuple[List[Dict], List[Dict]]]:
        """
        Lấy bảng xếp hạng tăng/giảm theo nhóm khái niệm/chủ đề.

        Returns:
            Tuple: (danh sách khái niệm dẫn dắt tăng, danh sách dẫn dắt giảm)
        """
        return None

    def get_hot_stocks(self, n: int = 10) -> Optional[List[Dict[str, Any]]]:
        """
        Lấy bảng xếp hạng cổ phiếu được quan tâm nhiều nhất.

        Returns:
            List[Dict]: danh sách cổ phiếu được quan tâm
        """
        return None

    def get_limit_up_pool(
        self,
        date: Optional[str] = None,
        n: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Lấy danh sách mã trần / nhóm mã trần nhiều phiên liên tiếp.

        Args:
            date: YYYYMMDD, mặc định do từng nguồn cụ thể quyết định
            n: số bản ghi trả về
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
        logger.info(f"[{self.name}] 开始获取 {stock_code} 日线数据: 范围={start_date} ~ {end_date}")
        
        try:
            # Bước 1: lấy dữ liệu thô
            raw_df = self._fetch_raw_data(stock_code, start_date, end_date)
            
            if raw_df is None:
                raise DataFetchError(f"[{self.name}] 未获取到 {stock_code} 的数据")
            if raw_df.empty:
                elapsed = time.time() - request_start
                logger.info(
                    f"[{self.name}] {stock_code} 返回空日线结果: 范围={start_date} ~ {end_date}, "
                    f"elapsed={elapsed:.2f}s"
                )
                if self.allow_empty_daily_data:
                    return pd.DataFrame(columns=STANDARD_COLUMNS)
                raise DataFetchError(f"[{self.name}] 未获取到 {stock_code} 的数据")
            
            # Bước 2: chuẩn hoá tên cột
            df = self._normalize_data(raw_df, stock_code)
            
            # Bước 3: làm sạch dữ liệu
            df = self._clean_data(df)
            
            # Bước 4: tính các chỉ báo kỹ thuật
            df = self._calculate_indicators(df)

            elapsed = time.time() - request_start
            logger.info(
                f"[{self.name}] {stock_code} 获取成功: 范围={start_date} ~ {end_date}, "
                f"rows={len(df)}, elapsed={elapsed:.2f}s"
            )
            return df
            
        except Exception as e:
            elapsed = time.time() - request_start
            error_type, error_reason = summarize_exception(e)
            logger.error(
                f"[{self.name}] {stock_code} 获取失败: 范围={start_date} ~ {end_date}, "
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
        logger.debug(f"随机休眠 {sleep_time:.2f} 秒...")
        time.sleep(sleep_time)


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

    _DAILY_MARKET_FETCHER_SUPPORT = {
        "VciFetcher": {"vn"},
        "SsiFetcher": {"vn"},
        "VnstockFetcher": {"vn"},
        "EfinanceFetcher": {"cn"},
        "TencentFetcher": {"cn"},
        "AkshareFetcher": {"cn", "hk"},
        "TushareFetcher": {"cn", "hk"},
        "TickFlowFetcher": {"cn"},
        "PytdxFetcher": {"cn"},
        "BaostockFetcher": {"cn"},
        # Yahoo phục vụ VN ở vai trò dự phòng cấp 3 (chỉ nến ngày, chỉ sàn HOSE).
        # Xem _NON_PERSISTABLE_DAILY_SOURCES về ràng buộc không ghi vào stock_daily.
        "YfinanceFetcher": {"cn", "hk", "us", "jp", "kr", "tw", "vn"},
        "LongbridgeFetcher": {"hk", "us"},
        "FutuFetcher": {"hk"},
        "FinnhubFetcher": {"us"},
        "AlphaVantageFetcher": {"us"},
    }
    _daily_source_health = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)
    _CN_INDEX_DAILY_SOURCE_ORDER = (
        "TencentFetcher",
        "AkshareFetcher",
        "TickFlowFetcher",
        "YfinanceFetcher",
    )
    _CN_INDEX_REALTIME_SOURCE_ORDER = (
        ("AkshareFetcher", "tencent"),
        ("AkshareFetcher", "sina"),
        ("EfinanceFetcher", "index"),
        ("TickFlowFetcher", "tickflow"),
    )
    _CN_INDEX_NAME_SOURCE_ORDER = (
        "TencentFetcher",
        "AkshareFetcher",
        "TickFlowFetcher",
    )
    _CN_INDEX_BARE_CODE_CONFLICTS = frozenset({"000001", "000016", "000688"})
    _CONCEPT_RANKINGS_CACHE_TTL_SECONDS = 300.0
    _CONCEPT_RANKINGS_EMPTY_CACHE_TTL_SECONDS = 30.0
    _concept_rankings_cache_lock = RLock()
    _concept_rankings_cache: Dict[int, Tuple[float, List[Dict], List[Dict]]] = {}

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
            # Các nguồn mặc định sẽ được nạp trễ (lazy) ở lần dùng đầu tiên
            self._init_default_fetchers()
        self._fundamental_adapter = AkshareFundamentalAdapter()
        self._yfinance_fundamental_adapter = YfinanceFundamentalAdapter()
        self._futu_fundamental_fetcher = None
        self._tickflow_fetcher = None
        self._tickflow_api_key: Optional[str] = None
        self._tickflow_lock = RLock()
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
                "[数据源可用性] %s.%s 检查失败(capability=%s): %s",
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

    @classmethod
    def _filter_fetchers_by_enabled_markets(
        cls, fetchers: List[BaseFetcher]
    ) -> List[BaseFetcher]:
        """Loại bỏ các nguồn mà mọi thị trường chúng hỗ trợ đều đã bị tắt.

        Nguồn chỉ phục vụ thị trường đã tắt thì không còn lý do tồn tại: giữ lại sẽ
        khiến chúng bị dò tìm khả dụng lặp đi lặp lại trong mỗi lần
        ``_filter_fetchers_by_capability``, đồng thời làm ``/api/v1/data/overview``
        báo ra những provider không dùng tới.

        Các nguồn chưa đăng ký trong ``_DAILY_MARKET_FETCHER_SUPPORT`` đều được giữ
        lại - chưa đăng ký nghĩa là "không rõ phủ thị trường nào", nên thận trọng
        không loại.
        """

        from src.core.markets import enabled_markets

        enabled = set(enabled_markets())
        kept: List[BaseFetcher] = []
        dropped: List[str] = []
        for fetcher in fetchers:
            supported = cls._DAILY_MARKET_FETCHER_SUPPORT.get(fetcher.name)
            if supported is not None and not (supported & enabled):
                dropped.append(fetcher.name)
                continue
            kept.append(fetcher)

        if dropped:
            logger.info(
                "[数据源初始化] 跳过仅服务已停用市场的数据源: %s（当前启用市场: %s）",
                ", ".join(dropped),
                ", ".join(sorted(enabled)),
            )
        return kept

    def _vn_realtime_source_order(self) -> List[str]:
        """Thứ tự nguồn dữ liệu cho giá realtime Việt Nam.

        Mặc định là ``SsiFetcher``; nếu ``REALTIME_SOURCE_PRIORITY`` có ghi tường
        minh token của nguồn Việt Nam (``ssi`` / ``vci``) thì chạy theo thứ tự người
        dùng cấu hình - dùng chung ngữ nghĩa cấu hình với A-share, tránh việc riêng
        Việt Nam lại phải thêm một công tắc mới.
        """

        from src.config import get_config

        token_to_fetcher = {"ssi": "SsiFetcher", "vci": "VciFetcher"}
        configured = str(
            getattr(get_config(), "realtime_source_priority", "") or ""
        ).lower()
        ordered = [
            token_to_fetcher[token.strip()]
            for token in configured.split(",")
            if token.strip() in token_to_fetcher
        ]
        # Snapshot toàn thị trường của SSI chỉ có cổ phiếu thành phần, còn VCI phụ
        # trách các chỉ số đã đăng ký (suy ra từ nến ngày mới nhất). Vì vậy cả hai đều
        # phải nằm trong chuỗi nguồn, thứ tự là "cổ phiếu ưu tiên SSI, chỉ số do VCI
        # đảm nhận".
        for default_name in ("SsiFetcher", "VciFetcher"):
            if default_name not in ordered:
                ordered.append(default_name)
        return ordered

    @staticmethod
    def _require_enabled_market(market: str, stock_code: str) -> None:
        """Từ chối mã thuộc thị trường chưa bật, TRƯỚC khi phát bất kỳ request mạng nào.

        Cổng chặn đặt ở tầng manager chứ không phải ở từng nơi gọi: mục đích của
        ``SUPPORTED_MARKETS`` là "thị trường đã tắt thì không gửi đi một request nào".
        Nếu chỉ lọc ở CLI/pipeline thì sẽ lọt các lối vào gọi thẳng manager như API,
        Bot và các tool của Agent.
        """

        from src.core.markets import ALL_MARKET_CODES, enabled_markets, is_market_enabled

        if is_market_enabled(market):
            return

        if market in ALL_MARKET_CODES:
            reason = (
                f"市场 {market} 未启用（当前 SUPPORTED_MARKETS={','.join(enabled_markets())}）"
            )
        else:
            reason = (
                f"无法识别 {stock_code} 所属市场（当前 SUPPORTED_MARKETS="
                f"{','.join(enabled_markets())}）"
            )
        logger.info("[市场过滤] %s: %s", stock_code, reason)
        raise DataFetchError(f"{stock_code}: {reason}")

    @classmethod
    def _filter_daily_fetchers_for_market(
        cls,
        fetchers: List[BaseFetcher],
        market: str,
    ) -> List[BaseFetcher]:
        """Skip built-in daily fetchers that are known not to support a market."""

        kept: List[BaseFetcher] = []
        skipped: List[str] = []
        for fetcher in fetchers:
            supported = cls._DAILY_MARKET_FETCHER_SUPPORT.get(fetcher.name)
            if supported is not None and market not in supported:
                skipped.append(fetcher.name)
            else:
                kept.append(fetcher)

        if skipped:
            logger.info(
                "[数据源路由] %s 日线跳过不支持的数据源: %s",
                market,
                ", ".join(skipped),
            )
        return kept

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
                "[数据源路由] %s 跳过暂不可用的数据源: %s",
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
            "[数据源健康度] %s 日线跳过短期熔断的数据源: %s",
            market,
            fetcher.name,
        )
        return False

    @staticmethod
    def _daily_source_unavailable_error(fetcher: BaseFetcher) -> str:
        return f"[{fetcher.name}] (CircuitOpen) 数据源短期熔断"

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

    @classmethod
    def _warn_bare_index_conflict(cls, target: AnalysisTarget) -> None:
        if target.asset_type != ParseStatus.STOCK or target.normalized_prefix is not None:
            return
        bare_code = target.normalized_code or (target.raw_input or "").strip()
        if (
            target.matched_index is None
            and bare_code not in cls._CN_INDEX_BARE_CODE_CONFLICTS
        ):
            return
        logger.warning(
            "[指数路由] 裸代码 %s 存在股票/指数歧义，按股票路由处理",
            bare_code,
        )

    @staticmethod
    def _cn_index_provider_symbol(target: AnalysisTarget, fetcher_name: str) -> str:
        entry = target.matched_index
        if entry is None:
            return ""
        exchange = entry.exchange.upper()
        if exchange == "CSI":
            # CSI indices are only supported by AkShare (``csi{code}``); the
            # other providers in the fixed daily chain return an empty symbol
            # so the caller records an ``unsupported`` provider-run and skips.
            if fetcher_name == "AkshareFetcher":
                return f"csi{entry.bare_code}"
            return ""
        if exchange not in {"SH", "SZ"}:
            return ""
        if fetcher_name in {"TencentFetcher", "AkshareFetcher"}:
            return f"{exchange.lower()}{entry.bare_code}"
        if fetcher_name == "TickFlowFetcher":
            return f"{entry.bare_code}.{exchange}"
        if fetcher_name == "YfinanceFetcher":
            suffix = "SS" if exchange == "SH" else "SZ"
            return f"{entry.bare_code}.{suffix}"
        return ""

    @classmethod
    def _is_meaningful_cn_index_name(
        cls, name: Optional[str], target: AnalysisTarget
    ) -> bool:
        if not is_meaningful_stock_name(name, target.canonical_id):
            return False

        aliases = {target.canonical_id}
        entry = target.matched_index
        if entry is not None:
            if normalize_stock_code(str(name).strip()) == entry.bare_code:
                return False
            aliases.add(entry.bare_code)
            aliases.update(entry.aliases)
            display_code = (target.display_code or "").strip()
            registry_name = (entry.display_name or "").strip()
            # The parser currently uses display_code for the human label too.
            if display_code and (
                display_code != registry_name
                or normalize_stock_code(display_code) == entry.bare_code
            ):
                aliases.add(display_code)
            for source_name in cls._CN_INDEX_DAILY_SOURCE_ORDER:
                aliases.add(cls._cn_index_provider_symbol(target, source_name))

        candidate = str(name).strip().upper()
        code_aliases = {
            str(alias).strip().upper()
            for alias in aliases
            if alias is not None and str(alias).strip()
        }
        return candidate not in code_aliases

    def _get_cn_index_daily_data(
        self,
        target: AnalysisTarget,
        start_date: Optional[str],
        end_date: Optional[str],
        days: int,
    ) -> Tuple[pd.DataFrame, str]:
        source_order = self._CN_INDEX_DAILY_SOURCE_ORDER
        fetchers_by_name = {
            fetcher.name: fetcher for fetcher in self._get_fetchers_snapshot()
        }
        errors: List[str] = []
        request_start = time.time()

        for index, source_name in enumerate(source_order):
            fallback_to = source_order[index + 1] if index + 1 < len(source_order) else None
            fetcher = fetchers_by_name.get(source_name)
            provider_symbol = self._cn_index_provider_symbol(target, source_name)

            if not provider_symbol:
                reason = (
                    "unsupported index provider symbol: "
                    f"{target.canonical_id} -> {source_name}"
                )
                record_provider_run(
                    data_type="daily_data",
                    provider=source_name,
                    operation="get_daily_data",
                    success=False,
                    latency_ms=0,
                    error_type="unsupported",
                    error_message=reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                logger.warning(
                    "[指数数据源不支持 %d/%d] [%s] %s: %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    reason,
                )
                errors.append(f"[{source_name}] {reason}")
                continue

            if fetcher is None or not self._is_fetcher_available(
                fetcher, capability="daily_data"
            ):
                reason = "数据源未配置或暂不可用"
                record_provider_run(
                    data_type="daily_data",
                    provider=source_name,
                    operation="get_daily_data",
                    success=False,
                    latency_ms=0,
                    error_type="unavailable",
                    error_message=reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                logger.warning(
                    "[指数数据源失败 %d/%d] [%s] %s: %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    reason,
                )
                errors.append(f"[{source_name}] {reason}")
                continue

            if not self._is_daily_source_available(fetcher, "cn_index"):
                reason = self._daily_source_unavailable_error(fetcher)
                record_provider_run(
                    data_type="daily_data",
                    provider=source_name,
                    operation="get_daily_data",
                    success=False,
                    latency_ms=0,
                    error_type="CircuitOpen",
                    error_message=reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                logger.warning(
                    "[指数数据源失败 %d/%d] [%s] %s: %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    reason,
                )
                errors.append(reason)
                continue

            attempt_start = time.time()
            try:
                logger.info(
                    "[指数数据源尝试 %d/%d] [%s] %s -> %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    provider_symbol,
                )
                record_provider_run_started(
                    data_type="daily_data",
                    provider=source_name,
                    operation="get_daily_data",
                )
                df = self._call_fetcher_method(
                    fetcher,
                    "get_daily_data",
                    stock_code=provider_symbol,
                    start_date=start_date,
                    end_date=end_date,
                    days=days,
                )
                duration_ms = int((time.time() - attempt_start) * 1000)
                if df is not None and not df.empty:
                    record_provider_run(
                        data_type="daily_data",
                        provider=source_name,
                        operation="get_daily_data",
                        success=True,
                        latency_ms=duration_ms,
                        record_count=len(df),
                    )
                    self._record_daily_source_success(fetcher, "cn_index")
                    logger.info(
                        "[指数数据源完成] %s 使用 [%s] 获取成功: rows=%d, elapsed=%.2fs",
                        target.canonical_id,
                        source_name,
                        len(df),
                        time.time() - request_start,
                    )
                    return df, source_name

                reason = "empty result"
                record_provider_run(
                    data_type="daily_data",
                    provider=source_name,
                    operation="get_daily_data",
                    success=False,
                    latency_ms=duration_ms,
                    error_type="empty",
                    error_message=reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                if df is not None and df.empty:
                    self._record_daily_source_success(fetcher, "cn_index")
                else:
                    self._record_daily_source_failure(fetcher, "cn_index", reason)
                logger.warning(
                    "[指数数据源失败 %d/%d] [%s] %s: %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    reason,
                )
                errors.append(f"[{source_name}] {reason}")
            except Exception as exc:
                error_type, error_reason = summarize_exception(exc)
                duration_ms = int((time.time() - attempt_start) * 1000)
                record_provider_run(
                    data_type="daily_data",
                    provider=source_name,
                    operation="get_daily_data",
                    success=False,
                    latency_ms=duration_ms,
                    error_type=error_type,
                    error_message=error_reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                self._record_daily_source_failure(
                    fetcher, "cn_index", error_reason
                )
                logger.warning(
                    "[指数数据源失败 %d/%d] [%s] %s: error_type=%s, reason=%s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    error_type,
                    error_reason,
                )
                errors.append(f"[{source_name}] ({error_type}) {error_reason}")

        logger.warning(
            "[指数数据源终止] %s 所有指数日线数据源均失败: elapsed=%.2fs; %s",
            target.canonical_id,
            time.time() - request_start,
            "; ".join(errors) or "暂无可用数据源",
        )
        return pd.DataFrame(columns=STANDARD_COLUMNS), ""

    def _get_cn_index_realtime_quote(
        self,
        target: AnalysisTarget,
        *,
        log_final_failure: bool = True,
    ):
        """Fetch a realtime quote for a registered CN index via a fixed chain.

        Chain (Story 1.5):
        1. Tencent via AkshareFetcher (``sh000016``/``sz399001`` prefixed symbol)
        2. Sina via AkshareFetcher (same prefixed symbol)
        3. Eastmoney single-stock secid via EfinanceFetcher (SH/SZ/CSI)
        4. TickFlow (``000016.SH`` symbol; SH/SZ only)

        The explicit index identity is preserved end-to-end: provider symbols
        are derived from the registry entry, never from ``normalize_stock_code``,
        so ``sh000016`` can never degrade into the stock ``000016`` path.
        """
        fetchers_by_name = {
            fetcher.name: fetcher for fetcher in self._get_fetchers_snapshot()
        }
        errors: List[str] = []
        request_start = time.time()
        source_order = self._CN_INDEX_REALTIME_SOURCE_ORDER

        for index, (source_name, source_kind) in enumerate(source_order):
            fallback_to = (
                source_order[index + 1][0] if index + 1 < len(source_order) else None
            )
            fetcher = fetchers_by_name.get(source_name)
            provider_symbol = self._cn_index_realtime_provider_symbol(
                target, source_name, source_kind
            )

            if not provider_symbol:
                reason = (
                    "unsupported index provider symbol: "
                    f"{target.canonical_id} -> {source_name}"
                )
                record_provider_run(
                    data_type="realtime_quote",
                    provider=source_name,
                    operation="get_realtime_quote",
                    success=False,
                    latency_ms=0,
                    error_type="unsupported",
                    error_message=reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                logger.warning(
                    "[指数实时行情不支持 %d/%d] [%s] %s: %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    reason,
                )
                errors.append(f"[{source_name}] {reason}")
                continue

            if fetcher is None or not self._is_fetcher_available(
                fetcher, capability="realtime_quote"
            ):
                reason = "数据源未配置或暂不可用"
                record_provider_run(
                    data_type="realtime_quote",
                    provider=source_name,
                    operation="get_realtime_quote",
                    success=False,
                    latency_ms=0,
                    error_type="unavailable",
                    error_message=reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                logger.warning(
                    "[指数实时行情失败 %d/%d] [%s] %s: %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    reason,
                )
                errors.append(f"[{source_name}] {reason}")
                continue

            attempt_start = time.time()
            try:
                logger.info(
                    "[指数实时行情尝试 %d/%d] [%s] %s -> %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    provider_symbol,
                )
                record_provider_run_started(
                    data_type="realtime_quote",
                    provider=source_name,
                    operation="get_realtime_quote",
                )
                if source_kind == "index":
                    quote = self._call_fetcher_method(
                        fetcher,
                        "get_index_realtime_quote",
                        target.canonical_id,
                    )
                elif source_kind == "tickflow":
                    quote = self._call_fetcher_method(
                        fetcher,
                        "get_realtime_quote",
                        provider_symbol,
                    )
                else:
                    quote = self._call_fetcher_method(
                        fetcher,
                        "get_realtime_quote",
                        provider_symbol,
                        source=source_kind,
                    )
                duration_ms = int((time.time() - attempt_start) * 1000)
                if quote is not None and quote.has_basic_data():
                    record_provider_run(
                        data_type="realtime_quote",
                        provider=source_name,
                        operation="get_realtime_quote",
                        success=True,
                        latency_ms=duration_ms,
                        record_count=1,
                    )
                    logger.info(
                        "[指数实时行情完成] %s 使用 [%s] 获取成功: elapsed=%.2fs",
                        target.canonical_id,
                        source_name,
                        time.time() - request_start,
                    )
                    from src.config import get_config as _get_config_safe

                    return self._enrich_realtime_quote(
                        quote,
                        realtime_cache_ttl=getattr(
                            _get_config_safe(), "realtime_cache_ttl", None
                        ),
                    )

                reason = "empty or incomplete quote"
                record_provider_run(
                    data_type="realtime_quote",
                    provider=source_name,
                    operation="get_realtime_quote",
                    success=False,
                    latency_ms=duration_ms,
                    error_type="empty",
                    error_message=reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                logger.warning(
                    "[指数实时行情失败 %d/%d] [%s] %s: %s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    reason,
                )
                errors.append(f"[{source_name}] {reason}")
            except Exception as exc:
                error_type, error_reason = summarize_exception(exc)
                duration_ms = int((time.time() - attempt_start) * 1000)
                record_provider_run(
                    data_type="realtime_quote",
                    provider=source_name,
                    operation="get_realtime_quote",
                    success=False,
                    latency_ms=duration_ms,
                    error_type=error_type,
                    error_message=error_reason,
                    fallback_to=fallback_to,
                    record_count=0,
                )
                logger.warning(
                    "[指数实时行情失败 %d/%d] [%s] %s: error_type=%s, reason=%s",
                    index + 1,
                    len(source_order),
                    source_name,
                    target.canonical_id,
                    error_type,
                    error_reason,
                )
                errors.append(f"[{source_name}] ({error_type}) {error_reason}")

        if log_final_failure:
            logger.warning(
                "[指数实时行情终止] %s 所有指数实时行情数据源均失败: elapsed=%.2fs; %s",
                target.canonical_id,
                time.time() - request_start,
                "; ".join(errors) or "暂无可用数据源",
            )
        return None

    @classmethod
    def _cn_index_realtime_provider_symbol(
        cls,
        target: AnalysisTarget,
        fetcher_name: str,
        source_kind: str,
    ) -> str:
        """Derive the provider symbol for the CN index realtime chain."""
        entry = target.matched_index
        if entry is None:
            return ""
        exchange = entry.exchange.upper()
        if exchange == "CSI":
            # CSI indices are only supported by the Eastmoney single-stock
            # secid endpoint (EfinanceFetcher); other providers return an
            # empty symbol so the caller records ``unsupported`` and skips.
            if fetcher_name == "EfinanceFetcher" and source_kind == "index":
                return target.canonical_id
            return ""
        if exchange not in {"SH", "SZ"}:
            return ""
        if fetcher_name == "AkshareFetcher" and source_kind in ("tencent", "sina"):
            return f"{exchange.lower()}{entry.bare_code}"
        if fetcher_name == "EfinanceFetcher" and source_kind == "index":
            return target.canonical_id
        if fetcher_name == "TickFlowFetcher" and source_kind == "tickflow":
            return f"{entry.bare_code}.{exchange}"
        return ""

    def _get_cn_index_name(self, target: AnalysisTarget) -> str:
        cache_key = target.canonical_id
        entry = target.matched_index
        display_name = entry.display_name if entry is not None else ""
        if self._is_meaningful_cn_index_name(display_name, target):
            return self._cache_stock_name(cache_key, display_name) or display_name

        cached_name = self._get_cached_stock_name(cache_key)
        if cached_name is not None:
            if self._is_meaningful_cn_index_name(cached_name, target):
                return cached_name
            self._discard_cached_stock_name(cache_key)

        fetchers_by_name = {
            fetcher.name: fetcher for fetcher in self._get_fetchers_snapshot()
        }
        for source_name in self._CN_INDEX_NAME_SOURCE_ORDER:
            provider_symbol = self._cn_index_provider_symbol(target, source_name)
            if not provider_symbol:
                logger.warning(
                    "[指数名称] [%s] 不支持 provider symbol: %s",
                    source_name,
                    cache_key,
                )
                continue
            fetcher = fetchers_by_name.get(source_name)
            if (
                fetcher is None
                or not hasattr(fetcher, "get_stock_name")
                or not self._is_fetcher_available(fetcher, capability="stock_name")
            ):
                logger.warning(
                    "[指数名称] [%s] 未配置或暂不可用: %s",
                    source_name,
                    cache_key,
                )
                continue
            try:
                name = self._call_fetcher_method(
                    fetcher, "get_stock_name", provider_symbol
                )
            except Exception as exc:
                logger.warning(
                    "[指数名称] [%s] 获取 %s 失败: %s",
                    source_name,
                    cache_key,
                    exc,
                )
                continue
            if self._is_meaningful_cn_index_name(name, target):
                self._cache_stock_name(cache_key, name)
                logger.info(
                    "[指数名称] 从 %s 获取: %s -> %s",
                    source_name,
                    cache_key,
                    name,
                )
                return name
            logger.warning(
                "[指数名称] [%s] 获取 %s 返回空结果",
                source_name,
                cache_key,
            )

        static_name = STOCK_NAME_MAP.get(cache_key)
        if static_name and self._is_meaningful_cn_index_name(static_name, target):
            self._cache_stock_name(cache_key, static_name)
            logger.info(
                "[指数名称] 从静态映射获取: %s -> %s",
                cache_key,
                static_name,
            )
            return static_name

        logger.warning("[指数名称] 所有数据源都无法获取 %s 的名称", cache_key)
        return cache_key

    def _get_tickflow_fetcher(self):
        """Lazily create a TickFlow fetcher for market-review-only calls."""
        from src.config import get_config

        config = get_config()
        api_key = (getattr(config, "tickflow_api_key", None) or "").strip()

        if not hasattr(self, "_tickflow_lock") or self._tickflow_lock is None:
            self._tickflow_lock = RLock()

        with self._tickflow_lock:
            current_fetcher = getattr(self, "_tickflow_fetcher", None)
            current_key = getattr(self, "_tickflow_api_key", None)

            if not api_key:
                if current_fetcher is not None and hasattr(current_fetcher, "close"):
                    try:
                        current_fetcher.close()
                    except Exception as exc:
                        logger.debug("[TickFlowFetcher] 关闭旧实例失败: %s", exc)
                self._tickflow_fetcher = None
                self._tickflow_api_key = None
                return None

            configured_fetcher = self._get_fetcher_by_name("TickFlowFetcher")
            if configured_fetcher is not None:
                return configured_fetcher

            if current_fetcher is not None and current_key == api_key:
                return current_fetcher

            if current_fetcher is not None and hasattr(current_fetcher, "close"):
                try:
                    current_fetcher.close()
                except Exception as exc:
                    logger.debug("[TickFlowFetcher] 切换实例时关闭失败: %s", exc)

            try:
                from .tickflow_fetcher import TickFlowFetcher

                fetcher = TickFlowFetcher(
                    api_key=api_key,
                    kline_adjust=getattr(config, "tickflow_kline_adjust", "none"),
                    batch_daily_enabled=getattr(config, "tickflow_batch_daily_enabled", True),
                    batch_size=getattr(config, "tickflow_batch_size", 100),
                    priority=getattr(config, "tickflow_priority", 2),
                )
                self._tickflow_fetcher = fetcher
                self._tickflow_api_key = api_key
                return fetcher
            except Exception as exc:
                logger.warning("[TickFlowFetcher] 初始化失败: %s", exc)
                self._tickflow_fetcher = None
                self._tickflow_api_key = None
                return None

    def close(self) -> None:
        """Best-effort release of manager-owned resources."""
        if not hasattr(self, "_tickflow_lock") or self._tickflow_lock is None:
            self._tickflow_lock = RLock()

        with self._tickflow_lock:
            current_fetcher = getattr(self, "_tickflow_fetcher", None)
            self._tickflow_fetcher = None
            self._tickflow_api_key = None

        if current_fetcher is not None and hasattr(current_fetcher, "close"):
            try:
                current_fetcher.close()
            except Exception as exc:
                logger.debug("[TickFlowFetcher] 关闭管理器资源失败: %s", exc)

        # The HK Futu fundamental path lazily creates and caches its own
        # FutuFetcher (an OpenQuoteContext-backed connection) on
        # _futu_fundamental_fetcher; release it here so explicit close /
        # reload paths do not leak the OpenD connection.
        futu_fundamental_fetcher = getattr(self, "_futu_fundamental_fetcher", None)
        if futu_fundamental_fetcher is not None:
            self._futu_fundamental_fetcher = None
            close_futu = getattr(futu_fundamental_fetcher, "close", None)
            if callable(close_futu):
                try:
                    close_futu()
                except Exception as exc:
                    logger.debug("[FutuFetcher] 关闭管理器资源失败: %s", exc)

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

    @staticmethod
    def _is_missing_board_value(value: Any) -> bool:
        """Return True when a board field value should be treated as missing."""
        if value is None:
            return True
        is_missing = DataFetcherManager._try_scalar_isna(value, "board_value")
        if is_missing is True:
            return True
        text = str(value).strip()
        return text == "" or text.lower() in {"nan", "none", "null", "na", "n/a"}

    @staticmethod
    def _normalize_belong_boards(raw_data: Any) -> List[Dict[str, Any]]:
        """Normalize belong-board results from heterogeneous providers."""
        if DataFetcherManager._is_missing_board_value(raw_data):
            return []

        normalized: List[Dict[str, Any]] = []
        dedupe = set()

        if isinstance(raw_data, pd.DataFrame):
            if raw_data.empty:
                return []
            name_col = next(
                (
                    col
                    for col in raw_data.columns
                    if str(col) in {"板块名称", "板块", "所属板块", "板块名", "name", "industry"}
                ),
                None,
            )
            code_col = next(
                (
                    col
                    for col in raw_data.columns
                    if str(col) in {"板块代码", "代码", "code"}
                ),
                None,
            )
            type_col = next(
                (
                    col
                    for col in raw_data.columns
                    if str(col) in {"板块类型", "类别", "type"}
                ),
                None,
            )
            if name_col is None:
                return []
            for _, row in raw_data.iterrows():
                board_name_raw = row.get(name_col, "")
                if DataFetcherManager._is_missing_board_value(board_name_raw):
                    continue
                board_name = str(board_name_raw).strip()
                if board_name in dedupe:
                    continue
                dedupe.add(board_name)
                item = {"name": board_name}
                if code_col is not None:
                    board_code_raw = row.get(code_col, "")
                    if not DataFetcherManager._is_missing_board_value(board_code_raw):
                        item["code"] = str(board_code_raw).strip()
                if type_col is not None:
                    board_type_raw = row.get(type_col, "")
                    if not DataFetcherManager._is_missing_board_value(board_type_raw):
                        item["type"] = str(board_type_raw).strip()
                normalized.append(item)
            return normalized

        if isinstance(raw_data, dict):
            raw_data = [raw_data]

        if isinstance(raw_data, (list, tuple, set)):
            for item in raw_data:
                if isinstance(item, dict):
                    board_name_raw = (
                        item.get("name")
                        or item.get("board_name")
                        or item.get("板块名称")
                        or item.get("板块")
                        or item.get("所属板块")
                        or item.get("板块名")
                        or item.get("industry")
                        or item.get("行业")
                    )
                    if DataFetcherManager._is_missing_board_value(board_name_raw):
                        continue
                    board_name = str(board_name_raw).strip()
                    if board_name in dedupe:
                        continue
                    dedupe.add(board_name)
                    normalized_item: Dict[str, Any] = {"name": board_name}
                    code_raw = (
                        item.get("code")
                        or item.get("板块代码")
                        or item.get("代码")
                    )
                    if not DataFetcherManager._is_missing_board_value(code_raw):
                        normalized_item["code"] = str(code_raw).strip()
                    type_raw = (
                        item.get("type")
                        or item.get("板块类型")
                        or item.get("类别")
                    )
                    if not DataFetcherManager._is_missing_board_value(type_raw):
                        normalized_item["type"] = str(type_raw).strip()
                    normalized.append(normalized_item)
                    continue
                if DataFetcherManager._is_missing_board_value(item):
                    continue
                board_name = str(item).strip()
                if board_name in dedupe:
                    continue
                dedupe.add(board_name)
                normalized.append({"name": board_name})
            return normalized

        if not DataFetcherManager._is_missing_board_value(raw_data):
            board_name = str(raw_data).strip()
            return [{"name": board_name}]
        return []
    
    def _init_default_fetchers(self) -> None:
        """
        Khởi tạo danh sách nguồn dữ liệu mặc định

        Logic điều chỉnh mức ưu tiên động:
        - Nếu đã cấu hình TUSHARE_TOKEN: khởi tạo TushareFetcher và nâng mức ưu tiên
          theo logic nội bộ của nó
        - Nếu đã cấu hình Longbridge OAuth hoặc credential Legacy: khởi tạo
          LongbridgeFetcher làm lớp dự phòng cho US/HK
        - Các nguồn tuỳ chọn chưa cấu hình thì không khởi tạo, tránh việc dò tìm lặp
          lại những nguồn vô hiệu khi kéo dữ liệu hàng loạt
        - Mức ưu tiên mặc định:
          0. EfinanceFetcher (Priority 0) - ưu tiên cao nhất
          1. AkshareFetcher (Priority 1)
          2. PytdxFetcher (Priority 2) - Tongdaxin
          3. BaostockFetcher (Priority 3)
          4. YfinanceFetcher (Priority 4)
          5. TencentFetcher (Priority 5) - lớp dự phòng cuối cho A-share
        """
        from src.config import get_config
        from src.core.markets import is_market_enabled
        from .efinance_fetcher import EfinanceFetcher
        from .tencent_fetcher import TencentFetcher
        from .akshare_fetcher import AkshareFetcher
        from .tushare_fetcher import TushareFetcher
        from .tickflow_fetcher import TickFlowFetcher
        from .pytdx_fetcher import PytdxFetcher
        from .baostock_fetcher import BaostockFetcher
        from .yfinance_fetcher import YfinanceFetcher
        from .longbridge_fetcher import LongbridgeFetcher
        from .futu_fetcher import FutuFetcher
        config = get_config()

        # Nguồn dữ liệu Việt Nam: VCI cung cấp nến ngày và bảng mã, SSI cung cấp
        # snapshot realtime toàn thị trường và nến ngày dự phòng. Cả hai đều là
        # endpoint công khai, không cần credential, nên chỉ dựa vào công tắc thị
        # trường để quyết định có khởi tạo hay không.
        vn_fetchers: List[BaseFetcher] = []
        if is_market_enabled("vn"):
            from .vci_fetcher import VciFetcher
            from .ssi_fetcher import SsiFetcher

            vn_fetchers.extend([VciFetcher(), SsiFetcher()])

            # vnstock là phụ thuộc tuỳ chọn: giấy phép của nó là "Custom (Personal Use)"
            # và kéo theo vnai - gói gửi dữ liệu sử dụng về phía tác giả. Vì vậy không
            # đưa vào requirements.txt; chỉ bật khi đặt tường minh VNSTOCK_ENABLED=true
            # và máy đã cài sẵn.
            if str(os.getenv("VNSTOCK_ENABLED", "false")).strip().lower() == "true":
                try:
                    from .vnstock_fetcher import VnstockFetcher

                    vn_fetchers.append(VnstockFetcher())
                except ImportError as exc:
                    logger.warning(
                        "[数据源初始化] VNSTOCK_ENABLED=true 但 vnstock 不可用，已跳过: %s",
                        exc,
                    )
            else:
                logger.debug("[数据源初始化] 跳过未启用的 VnstockFetcher")

        # Khởi tạo tất cả instance nguồn dữ liệu (mức ưu tiên được xác định trong
        # __init__ của từng Fetcher)
        efinance = EfinanceFetcher()
        tencent = TencentFetcher()
        akshare = AkshareFetcher()
        pytdx = PytdxFetcher()      # Nguồn Tongdaxin (có thể cấu hình PYTDX_HOST/PYTDX_PORT)
        baostock = BaostockFetcher()
        yfinance = YfinanceFetcher()
        optional_fetchers: List[BaseFetcher] = []

        tushare_token = (getattr(config, "tushare_token", None) or "").strip()
        if tushare_token:
            optional_fetchers.append(TushareFetcher())  # Mức ưu tiên tự động điều chỉnh theo cấu hình Token
        else:
            logger.debug("[数据源初始化] 跳过未配置的 TushareFetcher")

        tickflow_api_key = (getattr(config, "tickflow_api_key", None) or "").strip()
        if tickflow_api_key:
            optional_fetchers.append(
                TickFlowFetcher(
                    api_key=tickflow_api_key,
                    kline_adjust=getattr(config, "tickflow_kline_adjust", "none"),
                    batch_daily_enabled=getattr(config, "tickflow_batch_daily_enabled", True),
                    batch_size=getattr(config, "tickflow_batch_size", 100),
                    priority=getattr(config, "tickflow_priority", 2),
                )
            )
        else:
            logger.debug("[data source init] skip TickFlowFetcher because TICKFLOW_API_KEY is not configured")

        if LongbridgeFetcher.has_configured_credentials(config):
            optional_fetchers.append(LongbridgeFetcher())  # Longbridge (dự phòng cho US/HK, nạp trễ)
        else:
            logger.debug("[数据源初始化] 跳过未配置的 LongbridgeFetcher")

        if FutuFetcher.has_configured_endpoint():
            optional_fetchers.append(FutuFetcher())  # Futu (thị trường HK, phụ thuộc OpenD)
        else:
            logger.debug("[数据源初始化] 跳过未配置的 FutuFetcher")

        finnhub_api_key = (getattr(config, "finnhub_api_key", None) or "").strip()
        if finnhub_api_key:
            from .finnhub_fetcher import FinnhubFetcher
            optional_fetchers.append(FinnhubFetcher())
        else:
            logger.debug("[数据源初始化] 跳过未配置的 FinnhubFetcher")

        alphavantage_api_key = (getattr(config, "alphavantage_api_key", None) or "").strip()
        if alphavantage_api_key:
            from .alphavantage_fetcher import AlphaVantageFetcher
            optional_fetchers.append(AlphaVantageFetcher())
        else:
            logger.debug("[数据源初始化] 跳过未配置的 AlphaVantageFetcher")

        # Khởi tạo danh sách nguồn dữ liệu
        candidates: List[BaseFetcher] = [
            *vn_fetchers,
            efinance,
            akshare,
            pytdx,
            baostock,
            yfinance,
            tencent,
            *optional_fetchers,
        ]
        candidates = self._filter_fetchers_by_enabled_markets(candidates)

        self._ensure_concurrency_guards()
        with self._fetchers_lock:
            self._fetchers = candidates

            # Sắp xếp theo mức ưu tiên (Tushare nếu đã cấu hình Token và khởi tạo thành công
            # thì có mức ưu tiên 0)
            self._fetchers.sort(key=lambda f: f.priority)
            self._refresh_fetcher_indexes_locked()

        # Dựng phần mô tả thứ tự ưu tiên
        priority_info = ", ".join([f"{f.name}(P{f.priority})" for f in self._get_fetchers_snapshot()])
        logger.info(f"已初始化 {len(self._fetchers)} 个数据源（按优先级）: {priority_info}")
    
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
        1. Chỉ số Mỹ / cổ phiếu Mỹ định tuyến thẳng tới YfinanceFetcher
        2. Chỉ số A-share đã có trong registry dùng chuỗi nguồn chỉ số cố định
        3. Các mã còn lại thử lần lượt từ nguồn có mức ưu tiên cao nhất
        4. Bắt ngoại lệ xong thì tự chuyển sang nguồn kế tiếp và ghi lại lý do thất bại
        5. Chỉ số mà mọi nguồn đều hỏng thì trả kết quả rỗng chuẩn; mã không phải chỉ
           số mà mọi nguồn đều hỏng thì ném ngoại lệ kèm chi tiết

        Args:
            stock_code: mã cổ phiếu
            start_date: ngày bắt đầu
            end_date: ngày kết thúc
            days: số ngày cần lấy

        Returns:
            Tuple[DataFrame, str]: (dữ liệu, tên nguồn đã lấy thành công)

        Raises:
            DataFetchError: ném ra khi mã không phải chỉ số và mọi nguồn đều thất bại
        """
        from .us_index_mapping import is_us_index_code, is_us_stock_code

        raw_stock_code = (stock_code or "").strip()
        target = parse_analysis_target(raw_stock_code)
        self._warn_bare_index_conflict(target)
        if target.asset_type == ParseStatus.UNSUPPORTED:
            reason = target.unsupported_reason or "unsupported analysis target"
            raise DataFetchError(f"{raw_stock_code}: {reason}")
        if target.asset_type == ParseStatus.INDEX:
            return self._get_cn_index_daily_data(
                target,
                start_date=start_date,
                end_date=end_date,
                days=days,
            )

        # Normalize code (strip SH/SZ prefix etc.)
        stock_code = normalize_stock_code(stock_code)

        fetchers = self._get_fetchers_snapshot()
        errors = []
        request_start = time.time()

        # Đường nhanh: US dùng định tuyến nguồn chuyên biệt; HK lọc trước các nguồn
        # không hỗ trợ nến ngày HK
        #   - Đã cấu hình credential Longbridge: Longbridge ưu tiên, YFinance/AkShare dự phòng
        #   - Chưa cấu hình Longbridge:          YFinance ưu tiên (US), vòng lặp fetcher chung (HK)
        #   - Chỉ số Mỹ:                         luôn ưu tiên YFinance (Longbridge không có nến chỉ số)
        # Xác định thị trường trước, rồi mới suy ra các biến boolean theo thị trường.
        # Trước đây mỗi biến boolean tự nhận diện độc lập nên có thể lệch với
        # ``market``: mã trần Việt Nam (HPG/FPT) cũng khớp regex ticker Mỹ, bị
        # ``is_us`` cho là đúng và lọt vào nhánh định tuyến riêng của thị trường Mỹ.
        # Nhận diện thị trường bằng **input gốc, trước khi quy chuẩn**:
        # ``normalize_stock_code`` sẽ rút ``HPG.VN`` thành mã trần ``HPG``, mà mã trần
        # lại trùng hình dạng với ticker Mỹ. Nếu nhận diện sau khi quy chuẩn thì khi
        # bật đồng thời vn và us, cách viết tường minh ``HPG.VN`` sẽ bị hiểu nhầm là
        # cổ phiếu Mỹ, và người dùng mất luôn phương tiện khử nhập nhằng.
        market = _market_tag(raw_stock_code) if raw_stock_code else "unknown"
        if market == "unknown":
            market = _market_tag(stock_code)
        self._require_enabled_market(market, stock_code)
        is_us = market == "us"
        is_us_index = is_us and is_us_index_code(stock_code)
        is_hk = market == "hk"
        is_jp = market == "jp"
        is_kr = market == "kr"
        is_tw = market == "tw"
        # Lọc theo năng lực cho **mọi** thị trường (kể cả cn). Trước đây cn bị bỏ qua vì
        # khi đó tất cả nguồn đã đăng ký đều tình cờ hỗ trợ A-share; sau khi thêm nguồn
        # Việt Nam, VciFetcher/SsiFetcher sẽ bị đem đi chạy mã A-share và chắc chắn
        # thất bại - vừa phí request vừa làm nhiễu log lỗi.
        # Mọi nguồn A-share trong ``_DAILY_MARKET_FETCHER_SUPPORT`` đều khai báo cn,
        # nên lọc cho cn không làm hỏng chuỗi nguồn sẵn có.
        fetchers = self._filter_daily_fetchers_for_market(fetchers, market)
        fetchers = self._filter_fetchers_by_capability(fetchers, capability="daily_data")
        total_fetchers = len(fetchers)

        if total_fetchers == 0:
            market_label = "美股指数" if is_us_index else "美股" if is_us else "港股" if is_hk else "台股" if is_tw else "A股"
            error_summary = f"{market_label} {stock_code} 获取失败:\n暂无可用数据源"
            logger.error(f"[数据源终止] {stock_code} 获取失败: {error_summary}")
            raise DataFetchError(error_summary)

        # US (kể cả chỉ số Mỹ) dùng định tuyến chuyên biệt; HK đi tiếp vòng lặp nguồn chung bên dưới
        # Failover chain: Finnhub(P2) -> AlphaVantage(P3) -> Yfinance(P4) -> Longbridge(P5)
        # When Longbridge preferred: Longbridge -> Finnhub -> AlphaVantage -> Yfinance
        if is_us:
            prefer_lb = self._longbridge_preferred(capability="daily_data") and not is_us_index
            if is_us_index:
                # Chỉ số luôn ưu tiên YFinance (Longbridge không cung cấp nến chỉ số)
                source_order = ["YfinanceFetcher", "FinnhubFetcher"]
            elif prefer_lb:
                source_order = ["LongbridgeFetcher", "FinnhubFetcher", "AlphaVantageFetcher", "YfinanceFetcher"]
            else:
                source_order = ["FinnhubFetcher", "AlphaVantageFetcher", "YfinanceFetcher", "LongbridgeFetcher"]
            # Đọc mức ưu tiên hiện tại của từng nguồn (gồm cả biến môi trường *_PRIORITY):
            # ưu tiên mặc định khớp với chuỗi nguồn dựng sẵn, chỉnh riêng lẻ (ví dụ
            # YFINANCE_PRIORITY=0) có hiệu lực ngay; lựa chọn đầu đã neo cho chỉ số /
            # Longbridge preferred không bị mức ưu tiên thông thường ghi đè
            pin_first = bool(is_us_index or prefer_lb)
            source_order = self._order_us_sources_by_priority(source_order, pin_first=pin_first)
            market_label = "美股指数" if is_us_index else "美股"

            for order_index, src_name in enumerate(source_order):
                fallback_to = (
                    source_order[order_index + 1]
                    if order_index + 1 < len(source_order)
                    else None
                )
                for attempt, fetcher in enumerate(fetchers, start=1):
                    if fetcher.name != src_name:
                        continue
                    if not self._is_daily_source_available(fetcher, market):
                        errors.append(self._daily_source_unavailable_error(fetcher))
                        break
                    attempt_start = time.time()
                    try:
                        role = "首选" if src_name == source_order[0] else "兜底"
                        logger.info(
                            f"[数据源尝试 {attempt}/{total_fetchers}] [{fetcher.name}] "
                            f"{market_label} {stock_code} {role}路由..."
                        )
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
                            days=days,
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
                                f"[数据源完成] {stock_code} 使用 [{fetcher.name}] 获取成功: "
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
                            f"[数据源失败 {attempt}/{total_fetchers}] [{fetcher.name}] {stock_code}: "
                            f"error_type={error_type}, reason={error_reason}"
                        )
                        self._record_daily_source_failure(fetcher, market, error_reason)
                        errors.append(error_msg)
                    break

            error_summary = f"{market_label} {stock_code} 获取失败:\n" + "\n".join(errors)
            elapsed = time.time() - request_start
            logger.error(f"[数据源终止] {stock_code} 获取失败: elapsed={elapsed:.2f}s\n{error_summary}")
            raise DataFetchError(error_summary)

        for attempt, fetcher in enumerate(fetchers, start=1):
            if not self._is_daily_source_available(fetcher, market):
                errors.append(self._daily_source_unavailable_error(fetcher))
                continue
            attempt_start = time.time()
            fallback_to = fetchers[attempt].name if attempt < total_fetchers else None
            try:
                logger.info(f"[数据源尝试 {attempt}/{total_fetchers}] [{fetcher.name}] 获取 {stock_code}...")
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
                        f"[数据源完成] {stock_code} 使用 [{fetcher.name}] 获取成功: "
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
                    f"[数据源失败 {attempt}/{total_fetchers}] [{fetcher.name}] {stock_code}: "
                    f"error_type={error_type}, reason={error_reason}"
                )
                self._record_daily_source_failure(fetcher, market, error_reason)
                errors.append(error_msg)
                if attempt < total_fetchers:
                    next_fetcher = fetchers[attempt]
                    logger.info(f"[数据源切换] {stock_code}: [{fetcher.name}] -> [{next_fetcher.name}]")
                # Tiếp tục thử nguồn kế tiếp
                continue
        
        # Tất cả nguồn đều thất bại
        error_summary = f"所有数据源获取 {stock_code} 失败:\n" + "\n".join(errors)
        elapsed = time.time() - request_start
        logger.error(f"[数据源终止] {stock_code} 获取失败: elapsed={elapsed:.2f}s\n{error_summary}")
        raise DataFetchError(error_summary)
    
    @property
    def available_fetchers(self) -> List[str]:
        """Trả về danh sách tên các nguồn dữ liệu khả dụng"""
        return [f.name for f in self._get_fetchers_snapshot()]
    
    def prefetch_realtime_quotes(self, stock_codes: List[str]) -> int:
        """
        Prefetch giá realtime hàng loạt (gọi trước khi bắt đầu phân tích)

        Chiến lược:
        1. Kiểm tra danh sách ưu tiên có nguồn nào phù hợp để prefetch không
           (efinance/akshare_em/tushare/tickflow)
        2. Nếu không có thì bỏ qua prefetch (Sina/Tencent truy vấn từng mã nên không cần)
        3. Nếu số mã theo dõi >= 5 và đang dùng nguồn prefetch được thì prefetch để nạp cache

        Lợi ích:
        - Khi dùng Sina/Tencent: mỗi mã truy vấn độc lập, không có vấn đề kéo toàn bộ
        - Khi dùng efinance/Eastmoney/Tushare: prefetch một lần, các lần sau trúng cache
        - Khi dùng TickFlow: prefetch hàng loạt theo danh mục hiện tại, tránh lặp request từng mã

        Args:
            stock_codes: danh sách mã cổ phiếu cần phân tích

        Returns:
            Số mã đã prefetch (0 nghĩa là đã bỏ qua prefetch)
        """
        # Normalize all codes, preserving explicit index identities
        # (``sh000016`` / ``csi930955``) so the prefetch never degrades an
        # index into the colliding stock bucket (Story 1.5).
        normalized_codes: List[str] = []
        for code in stock_codes:
            target = parse_analysis_target(code)
            if target.asset_type == ParseStatus.INDEX:
                normalized_codes.append(target.canonical_id)
            else:
                normalized_codes.append(normalize_stock_code(code))
        stock_codes = normalized_codes

        from src.config import get_config

        config = get_config()

        # Issue #455: PREFETCH_REALTIME_QUOTES=false để tắt prefetch, tránh kéo toàn thị trường
        if not getattr(config, "prefetch_realtime_quotes", True):
            logger.debug("[预取] component=realtime_prefetch action=skip reason=disabled")
            return 0

        # Nếu giá realtime bị tắt thì bỏ qua prefetch
        if not config.enable_realtime_quote:
            logger.debug("[预取] component=realtime_prefetch action=skip reason=realtime_quote_disabled")
            return 0
        
        # Kiểm tra trong danh sách ưu tiên có nguồn nào phù hợp để prefetch hàng loạt
        # efinance/akshare_em/tushare chỉ cần một lời gọi là nạp đầy cache toàn thị trường;
        # tickflow prefetch cache danh mục theo dõi qua API symbols hàng loạt.
        priority = config.realtime_source_priority.lower()
        prefetch_sources = ['efinance', 'akshare_em', 'tushare', 'tickflow']
        
        # Nếu hai vị trí đầu trong danh sách ưu tiên đều không phải nguồn prefetch được
        # thì bỏ qua, vì Sina/Tencent chỉ truy vấn từng mã nên không cần prefetch
        priority_list = [s.strip() for s in priority.split(',')]
        first_prefetch_source_index = None
        for i, source in enumerate(priority_list):
            if source in prefetch_sources:
                first_prefetch_source_index = i
                break
        
        # Không có nguồn prefetch được, hoặc nó xếp sau vị trí thứ 3, thì bỏ qua prefetch
        if first_prefetch_source_index is None or first_prefetch_source_index >= 2:
            logger.info(
                "[预取] component=realtime_prefetch action=skip reason=no_early_prefetch_source priority=%s",
                priority,
            )
            return 0
        
        # Dưới 5 mã thì không prefetch hàng loạt (truy vấn từng mã hiệu quả hơn)
        if len(stock_codes) < 5:
            logger.info(
                "[预取] component=realtime_prefetch action=skip reason=small_batch "
                "stock_count=%d threshold=5 prefetch_source=%s",
                len(stock_codes),
                priority_list[first_prefetch_source_index],
            )
            return 0
        
        prefetch_source = priority_list[first_prefetch_source_index]
        logger.info(
            "[预取] component=realtime_prefetch action=start stock_count=%d prefetch_source=%s first_code=%s",
            len(stock_codes),
            prefetch_source,
            stock_codes[0],
        )
        
        # TickFlow dùng API symbols hàng loạt; các nguồn prefetch khác kích hoạt cache
        # của chính chúng qua lần truy vấn đầu tiên.
        if prefetch_source == "tickflow":
            fetcher = self._get_fetcher_by_name("TickFlowFetcher", capability="realtime_quote")
            if fetcher is None or not hasattr(fetcher, "prefetch_realtime_quotes"):
                logger.info(
                    "[prefetch] component=realtime_prefetch action=skip reason=tickflow_unavailable"
                )
                return 0
            try:
                return int(
                    self._call_fetcher_method(
                        fetcher,
                        "prefetch_realtime_quotes",
                        stock_codes,
                        batch_size=getattr(config, "tickflow_batch_size", 100),
                    )
                    or 0
                )
            except Exception as exc:
                logger.warning("[TickFlowFetcher] realtime prefetch failed: %s", exc)
                return 0

        try:
            # Dùng mã đầu tiên để kích hoạt việc kéo toàn bộ
            first_code = stock_codes[0]
            quote = self.get_realtime_quote(first_code)
            
            if quote:
                logger.info(
                    "[预取] component=realtime_prefetch action=complete status=success "
                    "stock_count=%d prefetch_source=%s",
                    len(stock_codes),
                    prefetch_source,
                )
                return len(stock_codes)
            else:
                logger.warning(
                    "[预取] component=realtime_prefetch action=complete status=failed "
                    "stock_count=%d prefetch_source=%s fallback=per_stock",
                    len(stock_codes),
                    prefetch_source,
                )
                return 0
                
        except Exception as e:
            logger.error(
                "[预取] component=realtime_prefetch action=complete status=error "
                "stock_count=%d prefetch_source=%s error=%s",
                len(stock_codes),
                prefetch_source,
                e,
            )
            return 0

    def prefetch_daily_klines(self, stock_codes: List[str], days: int = 30) -> int:
        """Batch-prefetch TickFlow daily K-lines without changing per-stock callers."""
        fetcher = self._get_fetcher_by_name("TickFlowFetcher", capability="daily_data")
        if fetcher is None or not hasattr(fetcher, "prefetch_daily_klines"):
            return 0

        try:
            return int(
                self._call_fetcher_method(
                    fetcher,
                    "prefetch_daily_klines",
                    stock_codes,
                    days=days,
                )
                or 0
            )
        except Exception as exc:
            logger.warning("[TickFlowFetcher] daily K-line prefetch failed: %s", exc)
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
        if fetcher_name == "AkshareFetcher" and kw.get("source") == "hk":
            return "akshare_hk"
        mapping = {
            "VciFetcher": "vci",
            "SsiFetcher": "ssi",
            "VnstockFetcher": "vnstock",
            "LongbridgeFetcher": "longbridge",
            "FutuFetcher": "futu",
            "YfinanceFetcher": "yfinance",
            "AkshareFetcher": "akshare",
            "FinnhubFetcher": "finnhub",
            "AlphaVantageFetcher": "alphavantage",
            "EfinanceFetcher": "efinance",
            "TushareFetcher": "tushare",
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
        Lấy giá realtime (tự động chuyển nguồn khi sự cố)

        Chiến lược chuyển nguồn (theo mức ưu tiên đã cấu hình):
        1. Thị trường Mỹ: dùng YfinanceFetcher.get_realtime_quote()
        2. EfinanceFetcher.get_realtime_quote()
        3. AkshareFetcher.get_realtime_quote(source="em")  - Eastmoney
        4. AkshareFetcher.get_realtime_quote(source="sina") - Sina
        5. AkshareFetcher.get_realtime_quote(source="tencent") - Tencent
        6. Trả về None (hạ cấp dự phòng)

        Args:
            stock_code: mã cổ phiếu
            log_final_failure: Whether to emit the final "all sources failed"
                summary log when no realtime quote is available.

        Returns:
            Đối tượng UnifiedRealtimeQuote; trả None nếu mọi nguồn đều thất bại
        """
        raw_stock_code = (stock_code or "").strip()
        # Normalize code (strip SH/SZ prefix etc.)
        stock_code = normalize_stock_code(stock_code)

        from .akshare_fetcher import _is_us_code
        from .us_index_mapping import is_us_index_code
        from src.config import get_config

        config = get_config()

        # Nếu tính năng giá realtime bị tắt thì trả về None ngay
        if not config.enable_realtime_quote:
            logger.debug(f"[实时行情] 功能已禁用，跳过 {stock_code}")
            return None

        # ----------------------------------------------------------
        # Chỉ số A-share đã đăng ký - chuỗi giá realtime cố định (Story 1.5)
        #   Danh tính chỉ số tường minh được phân giải TRƯỚC khi normalize, tránh việc
        #   sh000016 bị cắt thành mã cổ phiếu 000016 rồi lấy nhầm giá của cổ phiếu trùng tên.
        # ----------------------------------------------------------
        index_target = parse_analysis_target(raw_stock_code)
        if index_target.asset_type == ParseStatus.INDEX:
            return self._get_cn_index_realtime_quote(
                index_target, log_final_failure=log_final_failure
            )

        # ----------------------------------------------------------
        # US (chỉ số + cổ phiếu) / HK - định tuyến hai nguồn chuyên biệt
        #   Đã cấu hình Longbridge: Longbridge ưu tiên, YFinance/AkShare bổ sung
        #   Chưa cấu hình Longbridge: YFinance/AkShare ưu tiên, Longbridge bổ sung
        #   Chỉ số Mỹ: luôn ưu tiên YFinance (Longbridge không cung cấp giá chỉ số)
        # ----------------------------------------------------------
        is_us_index = is_us_index_code(stock_code)
        is_us = is_us_index or _is_us_code(stock_code)
        is_hk = (not is_us) and _is_hk_market(stock_code)
        is_jp = (not is_us) and (not is_hk) and _is_jp_market(stock_code)
        is_kr = (not is_us) and (not is_hk) and _is_kr_market(stock_code)
        is_tw = (not is_us) and (not is_hk) and _is_tw_market(stock_code)

        # Thị trường chưa bật thì hạ cấp thẳng về None: quy ước sẵn có của luồng giá
        # realtime là thất bại trả None chứ không ném exception. Giữ nhất quán ở đây để
        # một thị trường bị tắt không làm gãy cả lượt phân tích.
        from src.core.markets import is_market_enabled as _is_market_enabled

        # Như trên: nhận diện bằng input gốc để cách viết tường minh kiểu ``HPG.VN``
        # không bị bước quy chuẩn xoá mất.
        market = _market_tag(raw_stock_code) if raw_stock_code else "unknown"
        if market == "unknown":
            market = _market_tag(stock_code)
        if not _is_market_enabled(market):
            if log_final_failure:
                logger.info(
                    "[实时行情] %s 所属市场未启用（market=%s），跳过",
                    stock_code,
                    market,
                )
            return None

        # ----------------------------------------------------------
        # Việt Nam - ưu tiên snapshot toàn thị trường của SSI, VCI dự phòng
        #   SSI chỉ cần một request là phủ toàn bộ mã của HOSE/HNX/UPCOM, nên truy vấn
        #   từng mã gần như luôn trúng cache;
        #   VCI chỉ có nến ngày, không có endpoint realtime riêng, nên ở đây chỉ có duy
        #   nhất một chuỗi realtime là SSI; thất bại thì trả None (thống nhất với ngữ
        #   nghĩa hạ cấp realtime của các thị trường khác).
        # ----------------------------------------------------------
        if market == "vn":
            # Feed push MQTT (TVSI) đang kết nối và có tick còn mới thì dùng trước SSI.
            # A fresh tick from the connected push feed wins over the SSI snapshot.
            from data_provider.realtime_feed import get_fresh_tick, tick_to_quote, vn_bare_symbol

            tick = get_fresh_tick(
                vn_bare_symbol(stock_code),
                float(getattr(config, "realtime_feed_stale_seconds", 30) or 30),
            )
            if tick is not None:
                return self._enrich_realtime_quote(
                    tick_to_quote(tick),
                    realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
                )
            for source_name in self._vn_realtime_source_order():
                quote = self._try_fetcher_quote(stock_code, source_name)
                if quote is not None:
                    logger.info(
                        "[实时行情] 越南 %s 成功获取 (来源: %s)", stock_code, source_name
                    )
                    return self._enrich_realtime_quote(
                        quote,
                        realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
                    )
            if log_final_failure:
                logger.info("[实时行情] 越南 %s 无可用数据源", stock_code)
            return None

        if is_jp or is_kr or is_tw:
            market_label = "日股" if is_jp else "韩股" if is_kr else "台股"
            quote = self._try_fetcher_quote(stock_code, "YfinanceFetcher")
            if quote is not None:
                logger.info(f"[实时行情] {market_label} {stock_code} 成功获取 (来源: YfinanceFetcher)")
                return self._enrich_realtime_quote(
                    quote,
                    realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
                )
            if log_final_failure:
                logger.info(f"[实时行情] {market_label} {stock_code} 无可用数据源")
            return None

        if is_us or is_hk:
            prefer_lb = self._longbridge_preferred() and not is_us_index
            if is_us:
                primary_src = "LongbridgeFetcher" if prefer_lb else "YfinanceFetcher"
                secondary_src = "YfinanceFetcher" if prefer_lb else "LongbridgeFetcher"
                market_label = "美股指数" if is_us_index else "美股"
                primary_kw: dict = {}
                secondary_kw: dict = {}
            else:
                hk_priority = [
                    source.strip().lower()
                    for source in getattr(
                        config,
                        "futu_hk_realtime_source_priority",
                        "futu,longbridge,akshare,yfinance",
                    ).split(",")
                    if source.strip()
                ]
                source_map = {
                    "futu": ("FutuFetcher", {}),
                    "longbridge": ("LongbridgeFetcher", {}),
                    "akshare": ("AkshareFetcher", {"source": "hk"}),
                    "yfinance": ("YfinanceFetcher", {}),
                }
                primary_quote = None
                primary_token = None
                primary_src_index = -1
                fallback_from = None
                # Futu only participates when an OpenD endpoint is configured.
                # Skipping an unconfigured source here (instead of letting
                # _try_fetcher_quote fail on it) avoids recording a never-enabled
                # source as the failed primary, which would wrongly mark a
                # successful quote from the next enabled source as fallback.
                futu_enabled = False
                try:
                    from data_provider.futu_fetcher import FutuFetcher
                    futu_enabled = FutuFetcher.has_configured_endpoint()
                except Exception:  # noqa: BLE001 - fail closed: treat futu as disabled
                    futu_enabled = False
                for index, source in enumerate(hk_priority):
                    mapped = source_map.get(source)
                    if mapped is None:
                        logger.warning("[实时行情] 忽略未知港股数据源: %s", source)
                        continue
                    fetcher_name, fetcher_kw = mapped
                    if fetcher_name == "FutuFetcher" and not futu_enabled:
                        logger.info(
                            "[实时行情] 港股 %s 未配置 FUTU_OPEND_HOST，跳过 futu 源", stock_code
                        )
                        continue
                    quote = self._try_fetcher_quote(stock_code, fetcher_name, **fetcher_kw)
                    if quote is not None:
                        primary_quote = quote
                        primary_token = self._realtime_fetcher_token(fetcher_name, **fetcher_kw)
                        primary_src_index = index
                        logger.info("[实时行情] 港股 %s 成功获取 (来源: %s)", stock_code, fetcher_name)
                        break
                    # Nguồn này thất bại: ghi nhớ token của nó để nguồn thành công sau đó dùng
                    # làm fallback_from.
                    if fallback_from is None:
                        fallback_from = self._realtime_fetcher_token(fetcher_name, **fetcher_kw)
                if primary_quote is not None:
                    # Dùng các nguồn kế tiếp để bù các trường còn thiếu (volume_ratio /
                    # turnover_rate / định giá / vốn hoá), giữ khả năng bù trường
                    # _supplement_quote giống với nhánh thị trường Mỹ.
                    for source in hk_priority[primary_src_index + 1:]:
                        mapped = source_map.get(source)
                        if mapped is None:
                            continue
                        if not self._quote_needs_supplement(primary_quote):
                            break
                        fetcher_name, fetcher_kw = mapped
                        self._supplement_quote(stock_code, primary_quote, fetcher_name, **fetcher_kw)
                    return self._enrich_realtime_quote(
                        primary_quote,
                        fallback_from=fallback_from,
                        realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
                    )
                if log_final_failure:
                    logger.info("[实时行情] 港股 %s 无可用数据源", stock_code)
                return None
            primary_token = self._realtime_fetcher_token(primary_src, **primary_kw)
            primary_quote = self._try_fetcher_quote(stock_code, primary_src, **primary_kw)
            fallback_from = primary_token if primary_quote is None else None
            if primary_quote is not None:
                logger.info(f"[实时行情] {market_label} {stock_code} 成功获取 (来源: {primary_src})")
            # US index quotes are YFinance-only. Longbridge accepts index-like
            # symbols syntactically, but does not provide the index quote
            # contract used by this manager, so it must not be used as either
            # fallback or field supplement here.
            if not is_us_index:
                primary_quote = self._supplement_quote(
                    stock_code, primary_quote, secondary_src, **secondary_kw,
                )
            if is_us and not is_us_index and primary_quote is not None:
                for extra_src in ["FinnhubFetcher", "AlphaVantageFetcher"]:
                    primary_quote = self._supplement_quote(
                        stock_code, primary_quote, extra_src,
                    )
            if primary_quote is not None:
                return self._enrich_realtime_quote(
                    primary_quote,
                    fallback_from=fallback_from,
                    realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
                )
            if log_final_failure:
                logger.info(f"[实时行情] {market_label} {stock_code} 无可用数据源")
            return None
        
        # Lấy thứ tự ưu tiên nguồn dữ liệu đã cấu hình
        source_priority = [
            source.strip().lower()
            for source in config.realtime_source_priority.split(',')
            if source.strip()
        ]
        
        errors = []
        failed_sources: List[str] = []
        # primary_quote holds the first successful result; we may supplement
        # missing fields (volume_ratio, turnover_rate, etc.) from later sources.
        primary_quote = None
        primary_fallback_from: Optional[str] = None
        
        for source_index, source in enumerate(source_priority):
            attempt_start = time.time()
            fallback_to = source_priority[source_index + 1] if source_index + 1 < len(source_priority) else None
            fetcher = None
            try:
                quote = None
                
                if source == "efinance":
                    fetcher = self._get_fetcher_by_name("EfinanceFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        record_provider_run_started(
                            data_type="realtime_quote",
                            provider=fetcher.name,
                            operation="get_realtime_quote",
                        )
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code)
                
                elif source == "akshare_em":
                    fetcher = self._get_fetcher_by_name("AkshareFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        record_provider_run_started(
                            data_type="realtime_quote",
                            provider=fetcher.name,
                            operation="get_realtime_quote",
                        )
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code, source="em")
                
                elif source == "akshare_sina":
                    fetcher = self._get_fetcher_by_name("AkshareFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        record_provider_run_started(
                            data_type="realtime_quote",
                            provider=fetcher.name,
                            operation="get_realtime_quote",
                        )
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code, source="sina")
                
                elif source in ("tencent", "akshare_qq"):
                    fetcher = self._get_fetcher_by_name("AkshareFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        record_provider_run_started(
                            data_type="realtime_quote",
                            provider=fetcher.name,
                            operation="get_realtime_quote",
                        )
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code, source="tencent")
                
                elif source == "tushare":
                    fetcher = self._get_fetcher_by_name("TushareFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        record_provider_run_started(
                            data_type="realtime_quote",
                            provider=fetcher.name,
                            operation="get_realtime_quote",
                        )
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', raw_stock_code or stock_code)

                elif source == "tickflow":
                    fetcher = self._get_fetcher_by_name("TickFlowFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        record_provider_run_started(
                            data_type="realtime_quote",
                            provider=fetcher.name,
                            operation="get_realtime_quote",
                        )
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', raw_stock_code or stock_code)

                provider_name = fetcher.name if fetcher is not None else source
                
                if quote is not None and quote.has_basic_data():
                    record_provider_run(
                        data_type="realtime_quote",
                        provider=provider_name,
                        operation="get_realtime_quote",
                        success=True,
                        latency_ms=int((time.time() - attempt_start) * 1000),
                        fallback_to=fallback_to if primary_quote is None and self._quote_needs_supplement(quote) else None,
                        record_count=1,
                    )
                    if primary_quote is None:
                        # First successful source becomes primary
                        primary_quote = quote
                        primary_fallback_from = failed_sources[0] if failed_sources else None
                        logger.info(f"[实时行情] {stock_code} 成功获取 (来源: {source})")
                        # If all key supplementary fields are present, return early
                        if not self._quote_needs_supplement(primary_quote):
                            return self._enrich_realtime_quote(
                                primary_quote,
                                fallback_from=primary_fallback_from,
                                realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
                            )
                        # Otherwise, continue to try later sources for missing fields
                        logger.debug(f"[实时行情] {stock_code} 部分字段缺失，尝试从后续数据源补充")
                        supplement_attempts = 0
                    else:
                        # Supplement missing fields from this source (limit attempts)
                        supplement_attempts += 1
                        if supplement_attempts > 1:
                            logger.debug(f"[实时行情] {stock_code} 补充尝试已达上限，停止继续")
                            break
                        merged = self._merge_quote_fields(primary_quote, quote)
                        if merged:
                            logger.info(f"[实时行情] {stock_code} 从 {source} 补充了缺失字段: {merged}")
                        # Stop supplementing once all key fields are filled
                        if not self._quote_needs_supplement(primary_quote):
                            break
                else:
                    record_provider_run(
                        data_type="realtime_quote",
                        provider=provider_name,
                        operation="get_realtime_quote",
                        success=False,
                        latency_ms=int((time.time() - attempt_start) * 1000),
                        error_type="empty",
                        error_message="empty or incomplete quote",
                        fallback_to=fallback_to,
                        record_count=0,
                    )
                    if primary_quote is None:
                        failed_sources.append(source)
                    
            except Exception as e:
                error_msg = f"[{source}] 失败: {str(e)}"
                error_type, error_reason = summarize_exception(e)
                record_provider_run(
                    data_type="realtime_quote",
                    provider=getattr(fetcher, "name", source),
                    operation="get_realtime_quote",
                    success=False,
                    latency_ms=int((time.time() - attempt_start) * 1000),
                    error_type=error_type,
                    error_message=error_reason,
                    fallback_to=fallback_to,
                )
                logger.info(f"[实时行情] {stock_code} {error_msg}，继续尝试下一个数据源")
                errors.append(error_msg)
                if primary_quote is None:
                    failed_sources.append(source)
                continue
        
        # Return primary even if some fields are still missing
        if primary_quote is not None:
            return self._enrich_realtime_quote(
                primary_quote,
                fallback_from=primary_fallback_from,
                realtime_cache_ttl=getattr(config, "realtime_cache_ttl", None),
            )

        # Tất cả nguồn đều thất bại, trả về None (hạ cấp dự phòng)
        if log_final_failure:
            if errors:
                logger.info(f"[实时行情] {stock_code} 所有数据源均失败: {'; '.join(errors)}")
            else:
                logger.info(f"[实时行情] {stock_code} 无可用数据源")

        return None

    # Fields worth supplementing from secondary sources when the primary
    # source returns None for them. Ordered by importance.
    _SUPPLEMENT_FIELDS = [
        'volume_ratio', 'turnover_rate',
        'pe_ratio', 'pb_ratio', 'total_mv', 'circ_mv',
        'amplitude',
    ]

    @classmethod
    def _quote_needs_supplement(cls, quote) -> bool:
        """Check if any key supplementary field is still None."""
        for f in cls._SUPPLEMENT_FIELDS:
            if getattr(quote, f, None) is None:
                return True
        return False

    @classmethod
    def _merge_quote_fields(cls, primary, secondary) -> list:
        """
        Copy non-None fields from *secondary* into *primary* where
        *primary* has None. Returns list of field names that were filled.
        """
        filled = []
        for f in cls._SUPPLEMENT_FIELDS:
            if getattr(primary, f, None) is None:
                val = getattr(secondary, f, None)
                if val is not None:
                    setattr(primary, f, val)
                    filled.append(f)
        return filled

    def _order_us_sources_by_priority(self, source_order: List[str], *, pin_first: bool) -> List[str]:
        """Sắp xếp lại định tuyến nến ngày thị trường Mỹ theo mức ưu tiên hiện tại của
        từng nguồn (đọc cấu hình *_PRIORITY sẵn có).

        Sắp xếp ổn định: mức ưu tiên mặc định của các nguồn
        (Finnhub=2/AlphaVantage=3/Yfinance=4/Longbridge=5) khớp với chuỗi nguồn dựng
        sẵn nên hành vi mặc định không đổi; chỉnh riêng một *_PRIORITY có hiệu lực ngay.
        Khi pin_first=True thì giữ nguyên vị trí đầu chuỗi (chỉ số cố định Yfinance,
        Longbridge preferred), các thành viên còn lại sắp theo mức ưu tiên.
        """
        self._ensure_concurrency_guards()
        if not source_order:
            return source_order
        priority_by_name = {f.name: f.priority for f in self._get_fetchers_snapshot()}
        if pin_first:
            pinned, rest = source_order[0], source_order[1:]
            return [pinned] + sorted(rest, key=lambda name: priority_by_name.get(name, 10 ** 9))
        return sorted(source_order, key=lambda name: priority_by_name.get(name, 10 ** 9))

    def _longbridge_preferred(self, capability: str = "realtime_quote") -> bool:
        """Return True when Longbridge keys are configured and available.

        When True, non-A-share routing (US & HK) uses Longbridge as the
        primary data source with Yfinance/AkShare as fallback.
        """
        return self._get_fetcher_by_name(
            "LongbridgeFetcher",
            capability=capability,
        ) is not None

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
            logger.debug(f"[实时行情] {stock_code} {fetcher_name} 获取失败: {e}")
        return None

    def _supplement_quote(self, stock_code: str, primary_quote, fetcher_name: str, **kw):
        """Supplement *primary_quote* with data from *fetcher_name*.

        If *primary_quote* is None, try *fetcher_name* as the sole source.
        Returns the (potentially enriched) quote, or None.
        """
        if primary_quote is not None:
            if not self._quote_needs_supplement(primary_quote):
                return primary_quote
            try:
                secondary = self._try_fetcher_quote(stock_code, fetcher_name, **kw)
                if secondary is not None:
                    filled = self._merge_quote_fields(primary_quote, secondary)
                    if filled:
                        logger.info(f"[实时行情] {stock_code} 从 {fetcher_name} 补充了: {filled}")
            except Exception as e:
                logger.debug(f"[实时行情] {stock_code} {fetcher_name} 补充失败: {e}")
            return primary_quote

        q = self._try_fetcher_quote(stock_code, fetcher_name, **kw)
        if q is not None:
            logger.info(f"[实时行情] {stock_code} 从 {fetcher_name} 获取成功 (独立数据源)")
        return q

    def _supplement_from_longbridge(self, stock_code: str, primary_quote):
        """Shortcut kept for backward-compat with A-share general loop."""
        return self._supplement_quote(stock_code, primary_quote, "LongbridgeFetcher")

    def get_chip_distribution(self, stock_code: str):
        """
        Lấy dữ liệu phân bố chip (có circuit breaker và hạ cấp qua nhiều nguồn)

        Chiến lược:
        1. Kiểm tra công tắc cấu hình
        2. Kiểm tra trạng thái circuit breaker
        3. Thử lần lượt nhiều nguồn: thứ tự ưu tiên giống với khi lấy dữ liệu nến ngày
        4. Mọi nguồn thất bại thì trả None (hạ cấp dự phòng)

        Args:
            stock_code: mã cổ phiếu

        Returns:
            Đối tượng ChipDistribution; trả None nếu thất bại
        """
        # Normalize code (strip SH/SZ prefix etc.)
        stock_code = normalize_stock_code(stock_code)

        from .realtime_types import get_chip_circuit_breaker
        from src.config import get_config

        config = get_config()

        # Nếu tính năng phân bố chip bị tắt thì trả về None ngay
        if not config.enable_chip_distribution:
            logger.debug(f"[筹码分布] 功能已禁用，跳过 {stock_code}")
            return None

        circuit_breaker = get_chip_circuit_breaker()

        candidate_fetchers = []
        # Duyệt thẳng danh sách nguồn mà manager đã sắp xếp sẵn theo priority
        for fetcher in self._get_fetchers_snapshot():
            # Chỉ xử lý các nguồn có hiện thực logic phân bố chip
            if not hasattr(fetcher, 'get_chip_distribution'):
                continue

            fetcher_name = fetcher.name
            # Sinh key của circuit breaker một cách động, ví dụ "TushareFetcher" -> "tushare_chip"
            source_key = f"{fetcher_name.replace('Fetcher', '').lower()}_chip"

            # Kiểm tra trạng thái circuit breaker
            if not circuit_breaker.is_available(source_key):
                logger.debug(f"[熔断] {fetcher_name} 筹码接口处于熔断状态，尝试下一个")
                continue

            candidate_fetchers.append((fetcher, fetcher_name, source_key))

        for index, (fetcher, fetcher_name, source_key) in enumerate(candidate_fetchers):
            fallback_to = (
                candidate_fetchers[index + 1][1]
                if index + 1 < len(candidate_fetchers)
                else None
            )
            attempt_start = time.time()
            try:
                record_provider_run_started(
                    data_type="chip",
                    provider=fetcher_name,
                    operation="get_chip_distribution",
                )
                chip = self._call_fetcher_method(fetcher, 'get_chip_distribution', stock_code)
                latency_ms = int((time.time() - attempt_start) * 1000)
                if _is_meaningful_chip_distribution(chip):
                    record_provider_run(
                        data_type="chip",
                        provider=fetcher_name,
                        operation="get_chip_distribution",
                        success=True,
                        latency_ms=latency_ms,
                        record_count=1,
                    )
                    circuit_breaker.record_success(source_key)
                    logger.info(f"[筹码分布] {stock_code} 成功获取 (来源: {fetcher_name})")
                    return chip
                else:
                    record_provider_run(
                        data_type="chip",
                        provider=fetcher_name,
                        operation="get_chip_distribution",
                        success=False,
                        latency_ms=latency_ms,
                        error_type="empty",
                        error_message="empty or incomplete chip distribution",
                        fallback_to=fallback_to,
                        record_count=0,
                    )
                    if chip is not None:
                        logger.warning(
                            "[筹码分布] %s 返回字段不完整或占位值，继续尝试下一个数据源",
                            fetcher_name,
                        )
                    # Kết quả rỗng hoặc chỉ là placeholder: trả lại suất thăm dò HALF_OPEN để
                    # không bị kẹt
                    circuit_breaker.record_inconclusive(source_key)
            except Exception as e:
                error_type, error_reason = summarize_exception(e)
                record_provider_run(
                    data_type="chip",
                    provider=fetcher_name,
                    operation="get_chip_distribution",
                    success=False,
                    latency_ms=int((time.time() - attempt_start) * 1000),
                    error_type=error_type,
                    error_message=error_reason,
                    fallback_to=fallback_to,
                )
                logger.warning(f"[筹码分布] {fetcher_name} 获取 {stock_code} 失败: {e}")
                circuit_breaker.record_failure(source_key, str(e))
                continue

        logger.warning(f"[筹码分布] {stock_code} 所有数据源均失败")
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
            Tên cổ phiếu; trả None nếu mọi nguồn đều thất bại
        """
        raw_stock_code = (stock_code or "").strip()
        target = parse_analysis_target(raw_stock_code)
        self._warn_bare_index_conflict(target)
        if target.asset_type == ParseStatus.UNSUPPORTED:
            logger.warning(
                "[股票名称] 跳过不支持的输入 %s: %s",
                raw_stock_code,
                target.unsupported_reason or "unsupported analysis target",
            )
            return ""
        if target.asset_type == ParseStatus.INDEX:
            return self._get_cn_index_name(target)

        # Normalize code (strip SH/SZ prefix etc.)
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
                logger.info(f"[股票名称] 从实时行情获取: {stock_code} -> {name}")
                return name

        # 3. Lần lượt thử từng nguồn dữ liệu
        from .akshare_fetcher import _is_us_code
        is_us = _is_us_code(stock_code)
        _US_CAPABLE_FETCHERS = {"YfinanceFetcher", "LongbridgeFetcher", "FinnhubFetcher", "AlphaVantageFetcher"}
        for fetcher in self._get_fetchers_snapshot():
            if not hasattr(fetcher, 'get_stock_name'):
                continue
            if is_us and fetcher.name not in _US_CAPABLE_FETCHERS:
                continue
            if not self._is_fetcher_available(fetcher, capability="stock_name"):
                continue
            try:
                name = self._call_fetcher_method(fetcher, 'get_stock_name', stock_code)
                if is_meaningful_stock_name(name, stock_code):
                    self._cache_stock_name(stock_code, name)
                    logger.info(f"[股票名称] 从 {fetcher.name} 获取: {stock_code} -> {name}")
                    return name
            except Exception as e:
                logger.debug(f"[股票名称] {fetcher.name} 获取失败: {e}")
                continue

        # 4. Tất cả nguồn đều thất bại
        logger.warning(f"[股票名称] 所有数据源都无法获取 {stock_code} 的名称")
        return ""

    def get_belong_boards(self, stock_code: str) -> List[Dict[str, Any]]:
        """
        Get stock membership boards through capability probing.

        Keep this at manager layer to avoid changing BaseFetcher abstraction.
        """
        stock_code = normalize_stock_code(stock_code)
        if _market_tag(stock_code) != "cn":
            return []
        candidate_fetchers = [
            fetcher
            for fetcher in self._fetchers
            if hasattr(fetcher, "get_belong_board")
        ]
        for index, fetcher in enumerate(candidate_fetchers):
            fallback_to = (
                candidate_fetchers[index + 1].name
                if index + 1 < len(candidate_fetchers)
                else None
            )
            start = time.time()
            try:
                record_provider_run_started(
                    data_type="belong_boards",
                    provider=fetcher.name,
                    operation="get_belong_board",
                )
                raw_data = fetcher.get_belong_board(stock_code)
                boards = self._normalize_belong_boards(raw_data)
                if boards:
                    record_provider_run(
                        data_type="belong_boards",
                        provider=fetcher.name,
                        operation="get_belong_board",
                        success=True,
                        latency_ms=int((time.time() - start) * 1000),
                        record_count=len(boards),
                    )
                    logger.info(f"[{fetcher.name}] 获取所属板块成功: {stock_code}, count={len(boards)}")
                    return boards
                record_provider_run(
                    data_type="belong_boards",
                    provider=fetcher.name,
                    operation="get_belong_board",
                    success=False,
                    latency_ms=int((time.time() - start) * 1000),
                    error_type="empty",
                    error_message="empty belong boards",
                    fallback_to=fallback_to,
                    record_count=0,
                )
            except Exception as e:
                error_type, error_reason = summarize_exception(e)
                record_provider_run(
                    data_type="belong_boards",
                    provider=fetcher.name,
                    operation="get_belong_board",
                    success=False,
                    latency_ms=int((time.time() - start) * 1000),
                    error_type=error_type,
                    error_message=error_reason,
                    fallback_to=fallback_to,
                )
                logger.debug(f"[{fetcher.name}] 获取所属板块失败: {e}")
                continue
        return []

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
                    "[股票名称] 预取跳过不支持的输入 %s: %s",
                    code,
                    target.unsupported_reason or "unsupported analysis target",
                )
                continue
            normalized_codes.append(
                target.canonical_id
                if target.asset_type == ParseStatus.INDEX
                else normalize_stock_code(code)
            )
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
                    
                    logger.info(f"[股票名称] 从 {fetcher.name} 批量获取完成，剩余 {len(missing_codes)} 个待查")
            except Exception as e:
                logger.debug(f"[股票名称] {fetcher.name} 批量获取失败: {e}")
                continue
        
        # 3. Lấy từng mã còn lại
        for code in list(missing_codes):
            name = self.get_stock_name(code)
            if name:
                result[code] = name
                missing_codes.discard(code)
        
        logger.info(f"[股票名称] 批量获取完成，成功 {len(result)}/{len(stock_codes)}")
        return result

    def get_main_indices(self, region: str = "cn") -> List[Dict[str, Any]]:
        """Lấy giá realtime của các chỉ số chính (tự động chuyển nguồn)"""
        if region == "cn":
            tickflow_fetcher = self._get_tickflow_fetcher()
            if tickflow_fetcher is not None:
                try:
                    data = tickflow_fetcher.get_main_indices(region=region)
                    if data:
                        logger.info("[TickFlowFetcher] 获取指数行情成功")
                        return data
                except Exception as e:
                    logger.warning(f"[TickFlowFetcher] 获取指数行情失败: {e}")

        for fetcher in self._fetchers:
            if region == "cn" and fetcher.name == "TickFlowFetcher":
                continue
            try:
                data = fetcher.get_main_indices(region=region)
                if data:
                    logger.info(f"[{fetcher.name}] 获取指数行情成功")
                    return data
            except Exception as e:
                logger.warning(f"[{fetcher.name}] 获取指数行情失败: {e}")
                continue
        return []

    def get_market_stats(self, *, purpose: str = "unspecified", region: str = "cn") -> Dict[str, Any]:
        """Lấy thống kê tăng/giảm của thị trường (tự động chuyển nguồn)"""
        logger.info(
            "[MarketStats] component=market_stats action=start purpose=%s region=%s",
            purpose,
            region,
        )
        if region != "cn":
            return self._get_region_market_stats(region, purpose=purpose)
        tickflow_fetcher = self._get_tickflow_fetcher()
        if tickflow_fetcher is not None:
            started_at = time.monotonic()
            try:
                data = tickflow_fetcher.get_market_stats()
                elapsed = time.monotonic() - started_at
                if data:
                    logger.info(
                        "[MarketStats] component=market_stats action=provider_success "
                        "purpose=%s provider=TickFlowFetcher elapsed=%.2fs",
                        purpose,
                        elapsed,
                    )
                    return data
                logger.info(
                    "[MarketStats] component=market_stats action=provider_empty "
                    "purpose=%s provider=TickFlowFetcher elapsed=%.2fs",
                    purpose,
                    elapsed,
                )
            except Exception as e:
                elapsed = time.monotonic() - started_at
                logger.warning(
                    "[MarketStats] component=market_stats action=provider_failed "
                    "purpose=%s provider=TickFlowFetcher elapsed=%.2fs error=%s",
                    purpose,
                    elapsed,
                    e,
                )

        for fetcher in self._fetchers:
            if fetcher.name == "TickFlowFetcher":
                continue
            started_at = time.monotonic()
            try:
                data = fetcher.get_market_stats()
                elapsed = time.monotonic() - started_at
                if data:
                    logger.info(
                        "[MarketStats] component=market_stats action=provider_success "
                        "purpose=%s provider=%s elapsed=%.2fs",
                        purpose,
                        fetcher.name,
                        elapsed,
                    )
                    return data
                logger.info(
                    "[MarketStats] component=market_stats action=provider_empty "
                    "purpose=%s provider=%s elapsed=%.2fs",
                    purpose,
                    fetcher.name,
                    elapsed,
                )
            except Exception as e:
                elapsed = time.monotonic() - started_at
                logger.warning(
                    "[MarketStats] component=market_stats action=provider_failed "
                    "purpose=%s provider=%s elapsed=%.2fs error=%s",
                    purpose,
                    fetcher.name,
                    elapsed,
                    e,
                )
                continue
        logger.warning("[MarketStats] component=market_stats action=complete status=empty purpose=%s", purpose)
        return {}

    def _get_region_market_stats(self, region: str, *, purpose: str) -> Dict[str, Any]:
        """Thống kê tăng/giảm cho thị trường ngoài A-share; chỉ hỏi các fetcher
        tự khai báo hỗ trợ region đó qua ``get_region_market_stats``."""
        for fetcher in self._fetchers:
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
    def _normalize_source_chain(
        entries: Any,
        provider: str,
        result: str,
        duration_ms: int,
    ) -> List[Dict[str, Any]]:
        """Normalize free-form source chain entries to structured dict list."""
        if entries is None:
            return [{"provider": provider, "result": result, "duration_ms": duration_ms}]

        normalized: List[Dict[str, Any]] = []
        if not isinstance(entries, (list, tuple)):
            entries = [entries]

        for item in entries:
            if isinstance(item, dict):
                normalized.append({
                    "provider": str(item.get("provider") or provider),
                    "result": str(item.get("result") or result),
                    "duration_ms": int(item.get("duration_ms", duration_ms)),
                })
                continue

            if item is None:
                continue

            provider_name = str(item)
            normalized.append({
                "provider": provider_name,
                "result": result,
                "duration_ms": duration_ms,
            })

        if not normalized:
            return [{"provider": provider, "result": result, "duration_ms": duration_ms}]

        return normalized

    @staticmethod
    def _block_status(payload: Dict[str, Any], available: bool = True) -> str:
        if not available:
            return "not_supported"
        if not payload:
            return "partial"
        return "ok"

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
    def _dividend_contract_has_values(payload: Any) -> bool:
        """Check whether the dividend block satisfies the repo contract.

        Downstream consumers read ttm_cash_dividend_per_share /
        ttm_dividend_yield_pct and events[].cash_dividend_per_share /
        ex_dividend_date / event_date. Raw provider events that only carry
        provider-native keys (statement/ex_date/record_date) do not satisfy
        the contract, so they must be treated as missing.
        """
        if not isinstance(payload, dict):
            return DataFetcherManager._has_meaningful_payload(payload)
        # The repo contract consumes ttm_cash_dividend_per_share and
        # ttm_dividend_yield_pct as a pair. A block with TTM cash but no
        # yield (e.g. the extra realtime price snapshot failed or returned
        # no price) is still missing a consumed field, so it must count as
        # a gap and be supplemented instead of being treated as complete.
        if DataFetcherManager._has_meaningful_payload(payload.get("ttm_cash_dividend_per_share")):
            return DataFetcherManager._has_meaningful_payload(payload.get("ttm_dividend_yield_pct"))
        for key in ("ttm_cash_dividend_per_share", "ttm_dividend_yield_pct"):
            if DataFetcherManager._has_meaningful_payload(payload.get(key)):
                return True
        events = payload.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                if DataFetcherManager._has_meaningful_payload(
                    event.get("cash_dividend_per_share")
                ) or DataFetcherManager._has_meaningful_payload(
                    event.get("ex_dividend_date") or event.get("event_date")
                ):
                    return True
        return False

    @staticmethod
    def _earnings_block_has_values(payload: Any) -> bool:
        """Field-level check for the earnings block.

        A truthy dict with only metadata (report_date/period/currency) is a
        shell, not usable earnings. Require a core numeric field (revenue /
        net_profit_parent / basic_eps / gross_profit) or a populated dividend
        section before treating the block as usable.
        """
        if not isinstance(payload, dict):
            return DataFetcherManager._has_meaningful_payload(payload)
        report = payload.get("financial_report")
        if isinstance(report, dict):
            for key in ("revenue", "net_profit_parent", "basic_eps", "gross_profit"):
                if DataFetcherManager._has_meaningful_payload(report.get(key)):
                    return True
        dividend = payload.get("dividend")
        if DataFetcherManager._has_meaningful_payload(dividend):
            return True
        # Fall back to the generic check for other earnings sub-blocks.
        for key in ("financial_reports", "indicators"):
            if key in payload and DataFetcherManager._has_meaningful_payload(payload.get(key)):
                return True
        return False

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
        for block in (
            "valuation",
            "growth",
            "earnings",
            "institution",
            "capital_flow",
            "dragon_tiger",
            "boards",
        ):
            payload = context.get(block, {})
            if isinstance(payload, dict) and DataFetcherManager._has_meaningful_payload(payload.get("data")):
                return True
        return False

    def _build_market_not_supported(self, market: str, reason: str) -> Dict[str, Any]:
        blocks = {
            "valuation": self._build_fundamental_block(
                "partial" if market == "etf" else "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "growth": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "earnings": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "institution": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "capital_flow": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "dragon_tiger": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
            "boards": self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                [reason],
            ),
        }
        return {
            "market": market,
            "status": "partial" if market == "etf" else "not_supported",
            "coverage": {
                block: blocks[block]["status"] for block in blocks
            },
            "source_chain": [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
            "errors": [reason],
            **blocks,
        }

    def _fetch_offshore_fundamental_bundle(
        self,
        stock_code: str,
        market: str,
        bundle_timeout: float,
    ) -> Tuple[Dict[str, Any], Optional[str], int, str]:
        """Fetch the fundamental bundle for offshore markets.

        For HK with a configured Futu OpenD endpoint, try the Futu fundamental
        adapter first (company profile, statements, dividends/splits, capital
        flow, boards). When Futu succeeds only partially (e.g. statements
        failed but static info worked), fetch the yfinance bundle as well and
        merge the missing blocks so existing HK growth/earnings capability is
        never silently downgraded. Returns (payload, error, duration_ms,
        provider_name).
        """
        from src.config import get_config

        config = get_config()

        def _use_yfinance() -> Tuple[Dict[str, Any], Optional[str], int, str]:
            payload, err, ms = self._run_with_retry(
                lambda: self._yfinance_fundamental_adapter.get_fundamental_bundle(stock_code),
                bundle_timeout,
                "fundamental_bundle_yfinance",
            )
            return payload or {}, err, ms, "fundamental_bundle_yfinance"

        def _field_gaps(payload: Dict[str, Any]) -> List[str]:
            """List core growth/earnings fields that are missing or value-less.

            A field is a gap when it has no usable value, so the yfinance
            bundle should be consulted to fill it regardless of block-level
            truthiness.
            """
            gaps: List[str] = []
            growth = payload.get("growth")
            if isinstance(growth, dict):
                for field in ("revenue_yoy", "net_profit_yoy", "gross_margin"):
                    if not self._has_meaningful_payload(growth.get(field)):
                        gaps.append(f"growth.{field}")
            earnings = payload.get("earnings")
            report = earnings.get("financial_report") if isinstance(earnings, dict) else None
            if isinstance(report, dict):
                for field in ("revenue", "net_profit_parent", "basic_eps", "gross_profit"):
                    if not self._has_meaningful_payload(report.get(field)):
                        gaps.append(f"earnings.financial_report.{field}")
            # Dividend: the repo contract consumes ttm_* fields and/or
            # events[].cash_dividend_per_share / ex_dividend_date. Raw OpenD
            # events (statement/ex_date/record_date) without normalization do
            # not satisfy it, so treat the block as a gap unless the contract
            # fields carry usable values.
            dividend = earnings.get("dividend") if isinstance(earnings, dict) else None
            if not DataFetcherManager._dividend_contract_has_values(dividend):
                gaps.append("earnings.dividend")
            return gaps

        def _merge_bundles(
            futu_payload: Dict[str, Any],
            yfinance_payload: Dict[str, Any],
            futu_ms: int,
            yfinance_ms: int,
        ) -> Tuple[Dict[str, Any], Optional[str], int, str]:
            """Field-level merge: keep Futu values, fill gaps from yfinance."""
            merged: Dict[str, Any] = dict(futu_payload)

            # growth: field-level fill.
            futu_growth = futu_payload.get("growth")
            yf_growth = yfinance_payload.get("growth")
            if isinstance(yf_growth, dict):
                growth = dict(futu_growth) if isinstance(futu_growth, dict) else {}
                for field, value in yf_growth.items():
                    if not self._has_meaningful_payload(growth.get(field)):
                        growth[field] = value
                if any(self._has_meaningful_payload(v) for v in growth.values()):
                    merged["growth"] = growth

            # earnings: financial_report field-level fill, dividend block-level.
            futu_earnings = futu_payload.get("earnings")
            yf_earnings = yfinance_payload.get("earnings")
            if isinstance(yf_earnings, dict):
                earnings = dict(futu_earnings) if isinstance(futu_earnings, dict) else {}
                futu_report = earnings.get("financial_report")
                yf_report = yf_earnings.get("financial_report")
                if isinstance(yf_report, dict):
                    report = dict(futu_report) if isinstance(futu_report, dict) else {}
                    for field, value in yf_report.items():
                        if not self._has_meaningful_payload(report.get(field)):
                            report[field] = value
                    if any(self._has_meaningful_payload(v) for v in report.values()):
                        earnings["financial_report"] = report
                if not DataFetcherManager._dividend_contract_has_values(
                    earnings.get("dividend")
                ) and DataFetcherManager._dividend_contract_has_values(
                    yf_earnings.get("dividend")
                ):
                    earnings["dividend"] = yf_earnings.get("dividend")
                if any(
                    DataFetcherManager._earnings_block_has_values(earnings.get(key))
                    or self._has_meaningful_payload(earnings.get(key))
                    for key in ("financial_report", "financial_reports", "dividend", "indicators")
                ):
                    merged["earnings"] = earnings

            # Other blocks stay block-level (Futu wins, yfinance fills absent blocks).
            for key in ("institution", "capital_flow", "belong_boards"):
                if not self._has_meaningful_payload(merged.get(key)) and self._has_meaningful_payload(
                    yfinance_payload.get(key)
                ):
                    merged[key] = yfinance_payload.get(key)

            merged["source_chain"] = list(
                futu_payload.get("source_chain", [])
            ) + list(yfinance_payload.get("source_chain", []))
            merged["errors"] = list(futu_payload.get("errors", [])) + list(
                yfinance_payload.get("errors", [])
            )
            has_content = any(
                merged.get(key)
                for key in ("growth", "earnings", "institution", "capital_flow", "belong_boards")
            )
            merged["status"] = "partial" if has_content else "not_supported"
            return merged, None, futu_ms + yfinance_ms, "fundamental_bundle_futu"

        try:
            from data_provider.futu_fetcher import FutuFetcher
            from data_provider.futu_fundamental_adapter import FutuFundamentalAdapter
        except Exception as exc:  # noqa: BLE001 - fail open to yfinance
            logger.warning("[futu-fundamental] import failed, using yfinance: %s", exc)
            return _use_yfinance()

        if market != "hk" or not FutuFetcher.has_configured_endpoint():
            return _use_yfinance()

        futu_fetcher = getattr(self, "_futu_fundamental_fetcher", None)
        if futu_fetcher is None:
            try:
                futu_fetcher = FutuFetcher()
                self._futu_fundamental_fetcher = futu_fetcher
            except Exception as exc:  # noqa: BLE001 - fail open to yfinance
                logger.warning("[futu-fundamental] fetcher init failed, using yfinance: %s", exc)
                return _use_yfinance()

        adapter = FutuFundamentalAdapter(futu_fetcher)
        futu_payload, futu_err, futu_ms = self._run_with_retry(
            lambda: adapter.get_fundamental_bundle(stock_code),
            bundle_timeout,
            "fundamental_bundle_futu",
        )
        if futu_err is None and isinstance(futu_payload, dict):
            has_content = any(
                futu_payload.get(key)
                for key in ("growth", "earnings", "institution", "capital_flow", "belong_boards")
            )
            if has_content:
                # Futu partial success: keep the blocks it returned but do not
                # silently drop any core growth/earnings field that yfinance
                # could still provide. Decide by field gaps (not block-level
                # truthiness) so partial Futu results are supplemented.
                gaps = _field_gaps(futu_payload)
                remaining_timeout = max(bundle_timeout - futu_ms / 1000.0, 0.0)
                if gaps and remaining_timeout > 0:
                    yfinance_payload, yfinance_err, yfinance_ms = self._run_with_retry(
                        lambda: self._yfinance_fundamental_adapter.get_fundamental_bundle(stock_code),
                        remaining_timeout,
                        "fundamental_bundle_yfinance",
                    )
                    if yfinance_err is None and isinstance(yfinance_payload, dict):
                        return _merge_bundles(futu_payload, yfinance_payload, futu_ms, yfinance_ms)
                    logger.warning(
                        "[futu-fundamental] %s yfinance supplement failed (%s); keeping partial Futu bundle",
                        stock_code,
                        yfinance_err,
                    )
                return futu_payload, None, futu_ms, "fundamental_bundle_futu"
            logger.info(
                "[futu-fundamental] %s bundle empty (status=%s), falling back to yfinance",
                stock_code,
                futu_payload.get("status"),
            )
        return _use_yfinance()

    def _build_offshore_fundamental_context(
        self,
        stock_code: str,
        market: str,
        budget_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """HK/US fundamental aggregation via yfinance.

        Mirrors :meth:`get_fundamental_context` but skips A-share-specific
        blocks (capital_flow, dragon_tiger, sector rankings). belong_boards is
        sourced from yfinance ``info.sector`` / ``info.industry``.

        Cache, retry and fail-open semantics intentionally match the CN path so
        upstream callers see the same shape regardless of market.
        """
        from src.config import get_config

        config = get_config()
        stage_timeout = float(
            budget_seconds if budget_seconds is not None else config.fundamental_stage_timeout_seconds
        )
        stage_timeout = max(0.0, stage_timeout)
        fetch_timeout = float(config.fundamental_fetch_timeout_seconds)
        fetch_timeout = max(0.0, fetch_timeout)

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

        result_ctx: Dict[str, Any] = {
            "market": market,
            "provider": "yfinance",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "data_quality": "unavailable",
            "missing_fields": [],
            "valuation": {},
            "growth": {},
            "earnings": {},
            "institution": {},
            "capital_flow": {},
            "dragon_tiger": {},
            "boards": {},
            "belong_boards": [],
            "coverage": {},
            "source_chain": [],
            "errors": [],
        }
        start_ts = time.time()

        # Valuation: reuse realtime quote payload — yfinance returns pe/pb in the
        # same shape as AkShare, so the existing block formatter still works.
        valuation_timeout = min(fetch_timeout, stage_timeout) if stage_timeout > 0 else 0
        if valuation_timeout > 0:
            quote_payload, valuation_err, valuation_ms = self._run_with_retry(
                lambda: self.get_realtime_quote(stock_code),
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
        valuation_status = self._infer_block_status(
            valuation_payload,
            "partial" if quote_payload is not None else "not_supported",
        )
        if valuation_status == "partial" and valuation_err and not self._has_meaningful_payload(valuation_payload):
            valuation_status = "failed"
        result_ctx["valuation"] = self._build_fundamental_block(
            valuation_status,
            valuation_payload,
            self._normalize_source_chain(
                [{"provider": "realtime_quote", "result": valuation_status, "duration_ms": valuation_ms}],
                "realtime_quote",
                valuation_status,
                valuation_ms,
            ),
            [valuation_err] if valuation_err else [],
        )

        # Fundamental bundle via Futu (HK only, when OpenD is configured), then
        # fall back to yfinance for the same payload shape.
        bundle_timeout = min(fetch_timeout, max(stage_timeout - (time.time() - start_ts), 0.0))
        if bundle_timeout <= 0:
            bundle_payload, bundle_err, bundle_ms = {}, "fundamental stage timeout", 0
            bundle_provider = "fundamental_bundle_yfinance"
        else:
            bundle_payload, bundle_err, bundle_ms, bundle_provider = (
                self._fetch_offshore_fundamental_bundle(
                    stock_code,
                    market,
                    bundle_timeout,
                )
            )
        if not isinstance(bundle_payload, dict):
            bundle_payload = {}

        bundle_chain = self._normalize_source_chain(
            bundle_payload.get("source_chain", []),
            bundle_provider,
            str(bundle_payload.get("status", "not_supported")),
            bundle_ms,
        )
        adapter_errors = list(bundle_payload.get("errors", []))
        if bundle_err:
            adapter_errors.append(bundle_err)

        growth_payload = bundle_payload.get("growth", {}) if isinstance(bundle_payload.get("growth"), dict) else {}
        earnings_payload = bundle_payload.get("earnings", {}) if isinstance(bundle_payload.get("earnings"), dict) else {}
        belong_boards = bundle_payload.get("belong_boards") if isinstance(bundle_payload.get("belong_boards"), list) else []

        growth_status = self._infer_block_status(growth_payload, str(bundle_payload.get("status", "not_supported")))
        earnings_status = self._infer_block_status(earnings_payload, str(bundle_payload.get("status", "not_supported")))

        result_ctx["growth"] = self._build_fundamental_block(
            growth_status,
            growth_payload,
            bundle_chain,
            list(adapter_errors),
        )
        result_ctx["earnings"] = self._build_fundamental_block(
            earnings_status,
            earnings_payload,
            bundle_chain,
            list(adapter_errors),
        )

        # capital_flow / dragon_tiger / boards: Futu fills capital_flow and
        # belong_boards for HK; everything else keeps not_supported (fail-open).
        futu_capital_flow = (
            bundle_payload.get("capital_flow")
            if isinstance(bundle_payload.get("capital_flow"), dict) and bundle_payload.get("capital_flow")
            else {}
        )
        if futu_capital_flow:
            result_ctx["capital_flow"] = self._build_fundamental_block(
                "ok" if futu_capital_flow.get("latest") or futu_capital_flow.get("rows") else "partial",
                futu_capital_flow,
                bundle_chain,
                [],
            )
        else:
            result_ctx["capital_flow"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported for this source"],
            )
        result_ctx["dragon_tiger"] = self._build_fundamental_block(
            "not_supported",
            {},
            [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
            ["not supported for offshore market"],
        )
        futu_boards = (
            bundle_payload.get("belong_boards")
            if isinstance(bundle_payload.get("belong_boards"), list) and bundle_payload.get("belong_boards")
            else []
        )
        if futu_boards:
            result_ctx["boards"] = self._build_fundamental_block(
                "ok",
                {"boards": futu_boards},
                bundle_chain,
                [],
            )
        else:
            result_ctx["boards"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported for offshore market"],
            )

        # institution: tw (thị trường Đài Loan) has a free official three-institutional-investor
    # (institutional net buy/sell)
        # feed (TWSE T86 / TPEx OpenAPI); every other offshore market keeps not_supported.
        # tw-only + strictly additive + fail-open: any error or no-data -> not_supported,
        # which never interrupts the main analysis. Raw net figures only — no derived
        # signal / score / schema (per the v2 scope confirmed on issue #1777).
        tw_record = None
        if market == "tw":
            fetcher = getattr(self, "_tw_institutional_fetcher", None)
            if fetcher is None:
                # Wiring (import + construct) is a one-time op; a failure here is a
                # programming / deploy bug, so log it LOUD (error). Still fail-open
                # (never interrupt the main analysis — a hard requirement of #1777).
                try:
                    from data_provider.tw_institutional_fetcher import TwInstitutionalFetcher

                    fetcher = TwInstitutionalFetcher()
                    self._tw_institutional_fetcher = fetcher
                except Exception as exc:  # noqa: BLE001 - wiring failure: loud but fail-open
                    logger.error("[tw-inst] fetcher init failed (wiring bug?) code=%s: %s", stock_code, exc)
                    fetcher = None
            # fetch_timeout == 0 disables per-fetch fundamental fetches (same as valuation /
            # bundle above, which gate on fetch_timeout); honour that for institution too so
            # the FUNDAMENTAL_FETCH_TIMEOUT_SECONDS=0 config semantic is not bypassed.
            if fetcher is not None and fetch_timeout > 0:
                # The tw institution block is a WHOLE-MARKET download (~4-5s), far slower
                # than the per-symbol quote/bundle fetches, and it is the LAST offshore
                # block. When enabled, give it the full REMAINING stage budget rather than
                # the ~3s per-fetch cap that starves it and makes the first/only stock of a
                # run coin-flip between ok and not_supported. Bounded by the stage deadline
                # via _run_with_retry, so it fails open (never blocks).
                inst_timeout = max(stage_timeout - (time.time() - start_ts), 0.0)
                if inst_timeout > 0:
                    tw_record, inst_err, _inst_ms = self._run_with_retry(
                        lambda: fetcher.get_institutional_net(stock_code),
                        inst_timeout,
                        "fundamental_tw_institution",
                    )
                    if inst_err:
                        logger.warning("[tw-inst] fetch failed/timeout code=%s: %s", stock_code, inst_err)
                else:
                    tw_record = None
        # status 'ok' only when the record carries all core net figures (a genuine 0 is
        # kept — 0 is not None); None / missing core field / fetch failure -> not_supported.
        _tw_core = ("foreign_net", "trust_net", "dealer_net", "total_net")
        if tw_record is not None and all(tw_record.get(key) is not None for key in _tw_core):
            institution_status = "ok"
            result_ctx["institution"] = self._build_fundamental_block(
                "ok",
                {
                    "foreign_net": tw_record.get("foreign_net"),
                    "trust_net": tw_record.get("trust_net"),
                    "dealer_net": tw_record.get("dealer_net"),
                    "total_net": tw_record.get("total_net"),
                    "unit": tw_record.get("unit"),
                    "date": tw_record.get("date"),
                    "source": tw_record.get("source"),
                },
                [{"provider": tw_record.get("source", "tw-institutional"), "result": "ok", "duration_ms": 0}],
                [],
            )
        else:
            institution_status = "not_supported"
            result_ctx["institution"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported for offshore market"],
            )

        result_ctx["belong_boards"] = belong_boards or futu_boards

        capital_flow_status = result_ctx["capital_flow"].get("status", "not_supported")
        boards_status = result_ctx["boards"].get("status", "not_supported")
        block_statuses = {
            "valuation": result_ctx["valuation"].get("status", "not_supported"),
            "growth": growth_status,
            "earnings": earnings_status,
            "institution": institution_status,
            "capital_flow": capital_flow_status,
            "dragon_tiger": "not_supported",
            "boards": boards_status,
        }
        result_ctx["coverage"] = block_statuses
        for block in ("valuation", "growth", "earnings", "institution", "capital_flow", "dragon_tiger", "boards"):
            result_ctx["errors"].extend(result_ctx[block].get("errors", []))
            result_ctx["source_chain"].extend(result_ctx[block].get("source_chain", []))

        active_statuses = {
            "valuation": valuation_status,
            "growth": growth_status,
            "earnings": earnings_status,
            "capital_flow": capital_flow_status,
            "boards": boards_status,
        }
        # tw institution (when present) counts toward the OVERALL status so a report that
        # only has three-institutional-investor data still surfaces fundamentals (consumers key off the top-level
        # status). Futu capital_flow / boards count the same way when they are available.
        status_values = list(active_statuses.values())
        if institution_status == "ok":
            status_values.append("ok")
        if all(value == "not_supported" for value in status_values):
            result_ctx["status"] = "not_supported"
            result_ctx["data_quality"] = "unavailable"
        elif "failed" in status_values or "partial" in status_values:
            result_ctx["status"] = "partial"
            result_ctx["data_quality"] = "partial"
        else:
            result_ctx["status"] = "ok"
            result_ctx["data_quality"] = "ok"
        result_ctx["missing_fields"] = [
            block for block, status in active_statuses.items() if status != "ok"
        ]

        result_ctx["elapsed_ms"] = int((time.time() - start_ts) * 1000)
        if cache_ttl > 0 and self._should_cache_fundamental_context(result_ctx):
            with self._fundamental_cache_lock:
                self._fundamental_cache[cache_key] = {
                    "ts": time.time(),
                    "context": result_ctx,
                }
            self._prune_fundamental_cache(cache_ttl, cache_max_entries)
        return result_ctx

    def build_failed_fundamental_context(self, stock_code: str, reason: str) -> Dict[str, Any]:
        """Build a consistent failed-context payload for caller-side fallback."""
        market = _market_tag(stock_code)
        block_names = (
            "valuation",
            "growth",
            "earnings",
            "institution",
            "capital_flow",
            "dragon_tiger",
            "boards",
        )
        blocks = {
            block: self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                [reason],
            )
            for block in block_names
        }
        return {
            "market": market,
            "status": "failed",
            "coverage": {block: "failed" for block in block_names},
            "source_chain": [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
            "errors": [reason],
            **blocks,
        }

    def build_not_supported_fundamental_context(
        self, stock_code: str, reason: str
    ) -> Dict[str, Any]:
        """Build a consistent not-supported payload without calling providers."""
        return self._build_market_not_supported(_market_tag(stock_code), reason)

    def get_fundamental_context(
        self,
        stock_code: str,
        budget_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Aggregate fundamental blocks with fail-open semantics.
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
        is_etf = _is_etf_code(stock_code)
        if market in {"us", "hk", "jp", "kr", "tw"}:
            return self._build_offshore_fundamental_context(
                stock_code,
                market=market,
                budget_seconds=budget_seconds,
            )

        stage_timeout = float(
            budget_seconds if budget_seconds is not None else config.fundamental_stage_timeout_seconds
        )
        stage_timeout = max(0.0, stage_timeout)
        fetch_timeout = float(config.fundamental_fetch_timeout_seconds)
        fetch_timeout = max(0.0, fetch_timeout)

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

        remaining_seconds = stage_timeout
        result_ctx: Dict[str, Any] = {
            "market": market,
            "valuation": {},
            "growth": {},
            "earnings": {},
            "institution": {},
            "capital_flow": {},
            "dragon_tiger": {},
            "boards": {},
            "coverage": {},
            "source_chain": [],
            "errors": [],
        }

        start_ts = time.time()

        def _consume_budget(consumed_ms: int) -> None:
            nonlocal remaining_seconds
            remaining_seconds = max(0.0, remaining_seconds - consumed_ms / 1000.0)

        valuation_timeout = min(fetch_timeout, remaining_seconds)
        if valuation_timeout > 0:
            quote_payload, valuation_err, valuation_ms = self._run_with_retry(
                lambda: self.get_realtime_quote(stock_code),
                valuation_timeout,
                "fundamental_valuation",
            )
            _consume_budget(valuation_ms)
        else:
            quote_payload, valuation_err, valuation_ms = None, "fundamental stage timeout", 0

        valuation_payload = {
            "pe_ratio": getattr(quote_payload, "pe_ratio", None) if quote_payload else None,
            "pb_ratio": getattr(quote_payload, "pb_ratio", None) if quote_payload else None,
            "total_mv": getattr(quote_payload, "total_mv", None) if quote_payload else None,
            "circ_mv": getattr(quote_payload, "circ_mv", None) if quote_payload else None,
        }
        valuation_status = self._infer_block_status(
            valuation_payload,
            "partial" if quote_payload is not None else "not_supported",
        )
        if valuation_status == "partial" and valuation_err and not self._has_meaningful_payload(valuation_payload):
            valuation_status = "failed"
        result_ctx["valuation"] = self._build_fundamental_block(
            valuation_status,
            valuation_payload,
            self._normalize_source_chain(
                [{"provider": "realtime_quote", "result": valuation_status, "duration_ms": valuation_ms}],
                "realtime_quote",
                valuation_status,
                valuation_ms,
            ),
            [valuation_err] if valuation_err else [],
        )

        # growth / earnings / institution (one AkShare call)
        if remaining_seconds <= 0:
            bundle_status = "failed"
            bundle_payload: Dict[str, Any] = {}
            bundle_errors = ["fundamental stage timeout"]
            bundle_ms = 0
        else:
            bundle_timeout = min(fetch_timeout, remaining_seconds)
            bundle_payload, bundle_err_msg, bundle_ms = self._run_with_retry(
                lambda: self._fundamental_adapter.get_fundamental_bundle(stock_code),
                bundle_timeout,
                "fundamental_bundle",
            )
            _consume_budget(bundle_ms)
            if not isinstance(bundle_payload, dict):
                bundle_status = "failed"
                bundle_payload = {}
                bundle_errors = ["fundamental_bundle failed"]
                if bundle_err_msg:
                    bundle_errors.append(bundle_err_msg)
            else:
                bundle_status = str(bundle_payload.get("status", "not_supported"))
                bundle_errors = [bundle_err_msg] if bundle_err_msg else []

        bundle_chain = self._normalize_source_chain(
            bundle_payload.get("source_chain", []),
            "fundamental_bundle",
            bundle_status,
            bundle_ms,
        ) if isinstance(bundle_payload, dict) else self._normalize_source_chain(
            None,
            "fundamental_bundle",
            bundle_status,
            bundle_ms,
        )
        growth_payload = bundle_payload.get("growth", {}) if isinstance(bundle_payload, dict) else {}
        earnings_payload = bundle_payload.get("earnings", {}) if isinstance(bundle_payload, dict) else {}
        institution_payload = bundle_payload.get("institution", {}) if isinstance(bundle_payload, dict) else {}
        if not isinstance(growth_payload, dict):
            growth_payload = {}
        else:
            growth_payload = dict(growth_payload)
        if not isinstance(earnings_payload, dict):
            earnings_payload = {}
        else:
            earnings_payload = dict(earnings_payload)
        if not isinstance(institution_payload, dict):
            institution_payload = {}
        else:
            institution_payload = dict(institution_payload)

        # Derive TTM dividend yield from already-fetched quote price; avoid extra quote calls.
        earnings_extra_errors: List[str] = []
        dividend_payload = earnings_payload.get("dividend")
        if isinstance(dividend_payload, dict):
            dividend_payload = dict(dividend_payload)
            ttm_cash_raw = dividend_payload.get("ttm_cash_dividend_per_share")
            ttm_cash = None
            if ttm_cash_raw is not None:
                try:
                    ttm_cash = float(ttm_cash_raw)
                except (TypeError, ValueError):
                    earnings_extra_errors.append("invalid_ttm_cash_dividend_per_share")
            if isinstance(quote_payload, dict):
                latest_price_raw = quote_payload.get("price")
            else:
                latest_price_raw = getattr(quote_payload, "price", None) if quote_payload else None
            latest_price = None
            if latest_price_raw is not None:
                try:
                    latest_price = float(latest_price_raw)
                except (TypeError, ValueError):
                    latest_price = None
            ttm_yield = None
            if ttm_cash is not None:
                if latest_price is not None and latest_price > 0:
                    ttm_yield = round(ttm_cash / latest_price * 100.0, 4)
                else:
                    earnings_extra_errors.append("invalid_price_for_ttm_dividend_yield")

            dividend_payload["ttm_dividend_yield_pct"] = ttm_yield
            if ttm_yield is not None:
                dividend_payload["yield_formula"] = "ttm_cash_dividend_per_share / latest_price * 100"
            earnings_payload["dividend"] = dividend_payload

        adapter_errors = list(bundle_payload.get("errors", [])) if isinstance(bundle_payload, dict) else []
        adapter_errors.extend(bundle_errors)
        growth_errors = list(adapter_errors)
        earnings_errors = list(adapter_errors)
        earnings_errors.extend(earnings_extra_errors)
        institution_errors = list(adapter_errors)

        growth_status = self._infer_block_status(growth_payload, bundle_status)
        earnings_status = self._infer_block_status(earnings_payload, bundle_status)
        institution_status = self._infer_block_status(institution_payload, bundle_status)

        result_ctx["growth"] = self._build_fundamental_block(
            growth_status,
            growth_payload,
            bundle_chain,
            growth_errors,
        )
        result_ctx["earnings"] = self._build_fundamental_block(
            earnings_status,
            earnings_payload,
            bundle_chain,
            earnings_errors,
        )
        result_ctx["institution"] = self._build_fundamental_block(
            institution_status,
            institution_payload,
            bundle_chain,
            institution_errors,
        )

        # capital flow
        if is_etf:
            result_ctx["capital_flow"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["etf not fully supported"],
            )
            result_ctx["dragon_tiger"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["etf not fully supported"],
            )
            result_ctx["boards"] = self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["etf not fully supported"],
            )
            result_ctx["status"] = "partial"
        else:
            capital_flow_budget = min(fetch_timeout, remaining_seconds)
            capital_flow_start = time.time()
            result_ctx["capital_flow"] = self.get_capital_flow_context(
                stock_code,
                budget_seconds=capital_flow_budget,
            )
            _consume_budget(int((time.time() - capital_flow_start) * 1000))

            dragon_tiger_budget = min(fetch_timeout, remaining_seconds)
            dragon_tiger_start = time.time()
            result_ctx["dragon_tiger"] = self.get_dragon_tiger_context(
                stock_code,
                budget_seconds=dragon_tiger_budget,
            )
            _consume_budget(int((time.time() - dragon_tiger_start) * 1000))

            result_ctx["boards"] = self.get_board_context(
                stock_code,
                budget_seconds=min(fetch_timeout, remaining_seconds),
            )

        block_statuses = {
            "valuation": result_ctx["valuation"].get("status", "not_supported"),
            "growth": result_ctx["growth"].get("status", "not_supported"),
            "earnings": result_ctx["earnings"].get("status", "not_supported"),
            "institution": result_ctx["institution"].get("status", "not_supported"),
            "capital_flow": result_ctx["capital_flow"].get("status", "not_supported"),
            "dragon_tiger": result_ctx["dragon_tiger"].get("status", "not_supported"),
            "boards": result_ctx["boards"].get("status", "not_supported"),
        }
        result_ctx["coverage"] = block_statuses
        for block in (
            "valuation",
            "growth",
            "earnings",
            "institution",
            "capital_flow",
            "dragon_tiger",
            "boards",
        ):
            result_ctx["errors"].extend(result_ctx[block].get("errors", []))
            result_ctx["source_chain"].extend(result_ctx[block].get("source_chain", []))

        if is_etf:
            # Keep ETF downgrade semantics for overall status even when valuation is available.
            result_ctx["status"] = (
                "not_supported" if all(value == "not_supported" for value in block_statuses.values()) else "partial"
            )
        elif all(value == "not_supported" for value in block_statuses.values()):
            result_ctx["status"] = "not_supported"
        elif "failed" in block_statuses.values() or "partial" in block_statuses.values():
            result_ctx["status"] = "partial"
        else:
            result_ctx["status"] = "ok"

        result_ctx["elapsed_ms"] = int((time.time() - start_ts) * 1000)
        if cache_ttl > 0 and self._should_cache_fundamental_context(result_ctx):
            with self._fundamental_cache_lock:
                self._fundamental_cache[cache_key] = {
                    "ts": time.time(),
                    "context": result_ctx,
                }
            self._prune_fundamental_cache(cache_ttl, cache_max_entries)
        return result_ctx

    def get_capital_flow_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Khối dòng tiền (fail-open)."""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(stock_code) != "cn" or _is_etf_code(stock_code):
            return self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported"],
            )

        if timeout <= 0:
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                ["fundamental stage timeout"],
            )
        payload, err, cost_ms = self._run_with_retry(
            lambda: self._fundamental_adapter.get_capital_flow(stock_code),
            timeout,
            "capital_flow",
        )
        if not isinstance(payload, dict):
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": cost_ms}],
                [err or "capital_flow failed"],
            )

        stock_flow = payload.get("stock_flow") or {}
        sector_rankings = payload.get("sector_rankings") or {}
        has_stock_flow = False
        if isinstance(stock_flow, dict):
            has_stock_flow = any(v is not None for v in stock_flow.values())
        has_sector_rankings = bool(sector_rankings.get("top")) or bool(sector_rankings.get("bottom"))
        adapter_status = str(payload.get("status", "not_supported"))
        if has_stock_flow or has_sector_rankings:
            capital_flow_status = "ok"
        elif adapter_status == "not_supported":
            capital_flow_status = "not_supported"
        else:
            capital_flow_status = "partial"

        return self._build_fundamental_block(
            capital_flow_status,
            {
                "stock_flow": payload.get("stock_flow", {}),
                "sector_rankings": payload.get("sector_rankings", {}),
            },
            self._normalize_source_chain(
                payload.get("source_chain", []),
                "capital_flow",
                capital_flow_status,
                cost_ms,
            ),
            list(payload.get("errors", [])) + ([err] if err else []),
        )

    def get_dragon_tiger_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Khối bảng giao dịch khối lượng lớn - Dragon-Tiger (fail-open)."""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(stock_code) != "cn" or _is_etf_code(stock_code):
            return self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported"],
            )

        if timeout <= 0:
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                ["fundamental stage timeout"],
            )
        payload, err, cost_ms = self._run_with_retry(
            lambda: self._fundamental_adapter.get_dragon_tiger_flag(stock_code),
            timeout,
            "dragon_tiger",
        )
        if not isinstance(payload, dict):
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": cost_ms}],
                [err or "dragon_tiger failed"],
            )
        return self._build_fundamental_block(
            (payload.get("status") if isinstance(payload.get("status"), str) else "partial"),
            {
                "is_on_list": bool(payload.get("is_on_list", False)),
                "recent_count": int(payload.get("recent_count", 0)),
                "latest_date": payload.get("latest_date"),
            },
            self._normalize_source_chain(
                payload.get("source_chain", []),
                "dragon_tiger",
                str(payload.get("status", "ok")),
                cost_ms,
            ),
            list(payload.get("errors", [])) + ([err] if err else []),
        )

    def get_board_context(self, stock_code: str, budget_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Khối bảng xếp hạng nhóm ngành (fail-open)."""
        from src.config import get_config

        config = get_config()
        stock_code = normalize_stock_code(stock_code)
        timeout = float(budget_seconds if budget_seconds is not None else config.fundamental_fetch_timeout_seconds)
        if _market_tag(stock_code) != "cn" or _is_etf_code(stock_code):
            return self._build_fundamental_block(
                "not_supported",
                {},
                [{"provider": "fundamental_pipeline", "result": "not_supported", "duration_ms": 0}],
                ["not supported"],
            )

        if timeout <= 0:
            return self._build_fundamental_block(
                "failed",
                {},
                [{"provider": "fundamental_pipeline", "result": "failed", "duration_ms": 0}],
                ["fundamental stage timeout"],
            )

        def task() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], str]:
            return self._get_sector_rankings_with_meta(5)

        rankings, err, cost_ms = self._run_with_retry(task, timeout, "boards")
        if isinstance(rankings, tuple) and len(rankings) == 4:
            top, bottom, chain, chain_error = rankings
            if chain_error and not err:
                err = chain_error
            if not top and not bottom:
                return self._build_fundamental_block(
                    "failed",
                    {},
                    chain if chain else [{"provider": "sector_rankings", "result": "failed", "duration_ms": cost_ms}],
                    [err or "boards empty from all sources"],
                )
            board_status = "ok" if top and bottom else "partial"
            return self._build_fundamental_block(
                board_status,
                {"top": top or [], "bottom": bottom or []},
                chain if chain else self._normalize_source_chain(
                    ["sector_rankings"],
                    "boards",
                    board_status,
                    cost_ms,
                ),
                [err] if err else [],
            )

        return self._build_fundamental_block(
            "failed",
            {},
            [{"provider": "sector_rankings", "result": "failed", "duration_ms": cost_ms}],
            [err or "boards failed"],
        )

    def _get_sector_rankings_with_meta(
            self,
            n: int = 5,
        ) -> Tuple[List[Dict], List[Dict], List[Dict[str, Any]], str]:
            """Get sector rankings with ordered fallback chain metadata."""
            source_chain: List[Dict[str, Any]] = []
            last_error = ""

            # Duyệt thẳng danh sách nguồn mà manager đã sắp xếp sẵn theo priority
            for fetcher in self._fetchers:
                if not hasattr(fetcher, 'get_sector_rankings'):
                    continue

                start = time.time()
                try:
                    data = fetcher.get_sector_rankings(n)
                    duration_ms = int((time.time() - start) * 1000)
                    if data and data[0] is not None and data[1] is not None:
                        source_chain.append(
                            {
                                "provider": fetcher.name,
                                "result": "ok",
                                "duration_ms": duration_ms,
                            }
                        )
                        logger.info(f"[{fetcher.name}] 获取板块排行成功")
                        return data[0], data[1], source_chain, ""

                    last_error = f"{fetcher.name}返回空结果"
                    source_chain.append(
                        {
                            "provider": fetcher.name,
                            "result": "empty",
                            "duration_ms": duration_ms,
                            "error": last_error,
                        }
                    )
                except Exception as e:
                    error_type, error_reason = summarize_exception(e)
                    last_error = f"{fetcher.name} ({error_type}) {error_reason}"
                    duration_ms = int((time.time() - start) * 1000)
                    source_chain.append(
                        {
                            "provider": fetcher.name,
                            "result": "failed",
                            "duration_ms": duration_ms,
                            "error": error_reason,
                        }
                    )
                    logger.warning(f"[{fetcher.name}] 获取板块排行失败: {error_reason}")

            return [], [], source_chain, last_error

    def get_sector_rankings(self, n: int = 5) -> Tuple[List[Dict], List[Dict]]:
        """Lấy bảng xếp hạng tăng/giảm theo nhóm ngành (tự động chuyển nguồn)"""
        # Thứ tự dự phòng cố định theo yêu cầu: Akshare(EM) -> Akshare(Sina) -> Tushare -> Efinance
        top, bottom, _, last_error = self._get_sector_rankings_with_meta(n)
        if top or bottom:
            return top, bottom
        logger.warning(f"[板块排行] 所有数据源均失败，最终错误: {last_error}")
        return [], []

    @staticmethod
    def _copy_ranking_rows(rows: List[Dict]) -> List[Dict]:
        return [dict(row) if isinstance(row, dict) else row for row in rows or []]

    @classmethod
    def clear_concept_rankings_cache_for_tests(cls) -> None:
        with cls._concept_rankings_cache_lock:
            cls._concept_rankings_cache.clear()

    def get_concept_rankings(self, n: int = 5) -> Tuple[List[Dict], List[Dict]]:
        """Lấy bảng xếp hạng tăng/giảm theo nhóm khái niệm/chủ đề (tự động chuyển nguồn)."""
        try:
            normalized_n = int(n)
        except (TypeError, ValueError):
            normalized_n = 5
        if normalized_n <= 0:
            normalized_n = 5

        last_error = ""
        now = time.monotonic()

        with self.__class__._concept_rankings_cache_lock:
            cached = self.__class__._concept_rankings_cache.get(normalized_n)
            if cached and cached[0] > now:
                logger.debug("[概念排行] 命中共享缓存 n=%s", normalized_n)
                return self._copy_ranking_rows(cached[1]), self._copy_ranking_rows(cached[2])

            top: List[Dict] = []
            bottom: List[Dict] = []
            for fetcher in self._get_fetchers_snapshot():
                try:
                    data = fetcher.get_concept_rankings(normalized_n)
                    if data and (data[0] or data[1]):
                        top = data[0] or []
                        bottom = data[1] or []
                        logger.info(f"[{fetcher.name}] 获取概念排行成功")
                        break
                    last_error = f"{fetcher.name}返回空结果"
                except Exception as e:
                    error_type, error_reason = summarize_exception(e)
                    last_error = f"{fetcher.name} ({error_type}) {error_reason}"
                    logger.warning(f"[{fetcher.name}] 获取概念排行失败: {error_reason}")

            if not top and not bottom and last_error:
                logger.warning(f"[概念排行] 所有数据源均失败，最终错误: {last_error}")

            ttl = (
                self.__class__._CONCEPT_RANKINGS_CACHE_TTL_SECONDS
                if top or bottom
                else self.__class__._CONCEPT_RANKINGS_EMPTY_CACHE_TTL_SECONDS
            )
            cached_top = self._copy_ranking_rows(top)
            cached_bottom = self._copy_ranking_rows(bottom)
            self.__class__._concept_rankings_cache[normalized_n] = (
                time.monotonic() + ttl,
                cached_top,
                cached_bottom,
            )
            return self._copy_ranking_rows(cached_top), self._copy_ranking_rows(cached_bottom)

    def get_hot_stocks(self, n: int = 10) -> List[Dict[str, Any]]:
        """Lấy bảng xếp hạng cổ phiếu được quan tâm nhiều nhất (tự động chuyển nguồn)."""
        last_error = ""
        for fetcher in self._fetchers:
            try:
                data = fetcher.get_hot_stocks(n)
                if data:
                    logger.info(f"[{fetcher.name}] 获取人气股成功")
                    return data[:n]
                last_error = f"{fetcher.name}返回空结果"
            except Exception as e:
                error_type, error_reason = summarize_exception(e)
                last_error = f"{fetcher.name} ({error_type}) {error_reason}"
                logger.warning(f"[{fetcher.name}] 获取人气股失败: {error_reason}")
        if last_error:
            logger.warning(f"[人气股] 所有数据源均失败，最终错误: {last_error}")
        return []

    def get_limit_up_pool(
        self,
        date: Optional[str] = None,
        n: int = 20,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách mã trần và nhóm mã trần nhiều phiên liên tiếp (tự động chuyển nguồn)."""
        last_error = ""
        for fetcher in self._fetchers:
            try:
                data = fetcher.get_limit_up_pool(date=date, n=n)
                if data:
                    logger.info(f"[{fetcher.name}] 获取涨停池成功")
                    return data[:n]
                last_error = f"{fetcher.name}返回空结果"
            except Exception as e:
                error_type, error_reason = summarize_exception(e)
                last_error = f"{fetcher.name} ({error_type}) {error_reason}"
                logger.warning(f"[{fetcher.name}] 获取涨停池失败: {error_reason}")
        if last_error:
            logger.warning(f"[涨停池] 所有数据源均失败，最终错误: {last_error}")
        return []
