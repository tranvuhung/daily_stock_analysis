# -*- coding: utf-8 -*-
"""
===================================
Tầng chiến lược nguồn dữ liệu - khởi tạo package
===================================

Package này áp dụng Strategy pattern để quản lý nhiều nguồn dữ liệu của thị trường
Việt Nam, cung cấp:
1. Giao diện lấy dữ liệu thống nhất
2. Tự động chuyển nguồn khi có sự cố (failover)
3. Chiến lược điều tiết tần suất để tránh bị chặn

Các nguồn dữ liệu (số ưu tiên càng nhỏ càng được chọn trước):
0. FiinTradeFetcher (Priority -1) - nến ngày HOSE/HNX/chỉ số; mặc định bật khi có
   ``FIINTRADE_ORIGIN`` (tắt bằng ``FIINTRADE_ENABLED=false``)
1. VciFetcher (Priority 0)  - Vietcap, dữ liệu công khai: nến ngày + bảng mã đầy đủ
2. SsiFetcher (Priority 1)  - SSI iBoard, snapshot realtime toàn thị trường + nến ngày dự phòng
3. VnstockFetcher (Priority 2) - tuỳ chọn, cần ``VNSTOCK_ENABLED=true`` và đã cài vnstock
4. YfinanceFetcher - dự phòng cấp 3 cho nến ngày (chỉ HOSE, không ghi vào DB)

Vietnam-only data source package (VCI, SSI, optional vnstock, Yahoo fallback).
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
from .yfinance_fetcher import YfinanceFetcher

__all__ = [
    'BaseFetcher',
    'DataFetcherManager',
    'VciFetcher',
    'SsiFetcher',
    'YfinanceFetcher',
    'VN_INDEX_SYMBOLS',
    'get_vn_index_provider_symbol',
    'is_vn_index_code',
]
