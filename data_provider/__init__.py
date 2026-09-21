# -*- coding: utf-8 -*-
"""
===================================
Tầng chiến lược nguồn dữ liệu - khởi tạo package
===================================

Package này áp dụng Strategy pattern để quản lý nhiều nguồn dữ liệu, cung cấp:
1. Giao diện lấy dữ liệu thống nhất
2. Tự động chuyển nguồn khi có sự cố (failover)
3. Chiến lược điều tiết tần suất để tránh bị chặn

Mặc định chỉ bật thị trường Việt Nam (``SUPPORTED_MARKETS``, xem
``src/core/markets.py``). Các nguồn dữ liệu khi bật Việt Nam:
1. VciFetcher (Priority 0)  - Vietcap, dữ liệu công khai: nến ngày + bảng mã đầy đủ
2. SsiFetcher (Priority 1)  - SSI iBoard, snapshot realtime toàn thị trường + nến ngày dự phòng
3. VnstockFetcher (Priority 2) - tuỳ chọn, cần ``VNSTOCK_ENABLED=true`` và đã cài vnstock

Các nguồn A-share / HK / US dưới đây chỉ được khởi tạo khi ``SUPPORTED_MARKETS``
bật lại thị trường tương ứng.

Thứ tự ưu tiên nguồn dữ liệu (điều chỉnh động):
[Khi đã cấu hình TUSHARE_TOKEN]
1. TushareFetcher (Priority 0) - ưu tiên cao nhất (được nâng động)
2. EfinanceFetcher (Priority 0) - cùng mức ưu tiên
3. AkshareFetcher (Priority 1) - từ thư viện akshare
4. PytdxFetcher (Priority 2) - từ thư viện pytdx (Tongdaxin)
5. BaostockFetcher (Priority 3) - từ thư viện baostock
6. YfinanceFetcher (Priority 4) - từ thư viện yfinance
7. TencentFetcher (Priority 5) - Tencent, nến ngày trực tiếp, lớp dự phòng cuối

[Khi chưa cấu hình TUSHARE_TOKEN]
1. EfinanceFetcher (Priority 0) - ưu tiên cao nhất, từ thư viện efinance
2. AkshareFetcher (Priority 1) - từ thư viện akshare
3. PytdxFetcher (Priority 2) - từ thư viện pytdx (Tongdaxin)
4. TushareFetcher (Priority 2) - từ thư viện tushare (không khả dụng)
5. BaostockFetcher (Priority 3) - từ thư viện baostock
6. YfinanceFetcher (Priority 4) - từ thư viện yfinance
7. TencentFetcher (Priority 5) - Tencent, nến ngày trực tiếp, lớp dự phòng cuối
8. LongbridgeFetcher (Priority 5) - Longbridge OpenAPI (dự phòng cho US/HK,
   không chồng lấn thị trường với Tencent)

Lưu ý: số ưu tiên càng nhỏ càng được chọn trước; cùng mức ưu tiên thì xếp theo
thứ tự khởi tạo.
"""

from .base import (
    BaseFetcher,
    DataFetcherManager,
    VN_INDEX_SYMBOLS,
    get_vn_index_provider_symbol,
    is_vn_index_code,
)
from .vci_fetcher import VciFetcher
from .ssi_fetcher import SsiFetcher
from .efinance_fetcher import EfinanceFetcher
from .tencent_fetcher import TencentFetcher
from .akshare_fetcher import AkshareFetcher, is_hk_stock_code
from .tushare_fetcher import TushareFetcher
from .pytdx_fetcher import PytdxFetcher
from .baostock_fetcher import BaostockFetcher
from .yfinance_fetcher import YfinanceFetcher
from .longbridge_fetcher import LongbridgeFetcher
from .finnhub_fetcher import FinnhubFetcher
from .alphavantage_fetcher import AlphaVantageFetcher
from .us_index_mapping import is_us_index_code, is_us_stock_code, get_us_index_yf_symbol, US_INDEX_MAPPING

__all__ = [
    'BaseFetcher',
    'DataFetcherManager',
    'VciFetcher',
    'SsiFetcher',
    'VN_INDEX_SYMBOLS',
    'get_vn_index_provider_symbol',
    'is_vn_index_code',
    'EfinanceFetcher',
    'TencentFetcher',
    'AkshareFetcher',
    'TushareFetcher',
    'PytdxFetcher',
    'BaostockFetcher',
    'YfinanceFetcher',
    'LongbridgeFetcher',
    'FinnhubFetcher',
    'AlphaVantageFetcher',
    'is_us_index_code',
    'is_us_stock_code',
    'is_hk_stock_code',
    'get_us_index_yf_symbol',
    'US_INDEX_MAPPING',
]
