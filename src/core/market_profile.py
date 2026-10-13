# -*- coding: utf-8 -*-
"""
Market review region configuration

Defines metadata for each market region (indices, news search terms, prompt hints, etc.)
for MarketAnalyzer. Only the Vietnam region is supported.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class MarketProfile:
    """Market review region configuration"""

    region: str  # "vn"
    # Mã chỉ số dùng để đánh giá xu hướng chung (VN-Index). / Index used to judge the overall trend.
    mood_index_code: str
    # News search keywords
    news_queries: List[str]
    # Prompt hint for index commentary
    prompt_index_hint: str
    # Tổng quan có số mã tăng/giảm và trần/sàn hay không. / Whether breadth stats are available.
    has_market_stats: bool
    # Tổng quan có xếp hạng ngành hay không (VN chưa có nguồn). / Whether sector rankings are available.
    has_sector_rankings: bool


# Việt Nam: chỉ số từ VCI, độ rộng thị trường (tăng/giảm/trần/sàn) tính từ
# snapshot SSI ba sàn; chưa có nguồn dữ liệu ngành nên tắt xếp hạng ngành.
VN_PROFILE = MarketProfile(
    region="vn",
    mood_index_code="VNINDEX",
    news_queries=[
        "chứng khoán VN-Index hôm nay",
        "thị trường chứng khoán Việt Nam khối ngoại",
        "Vietnam stock market VN-Index",
    ],
    prompt_index_hint="Phân tích diễn biến VN-Index, VN30, HNX-Index, UPCoM-Index và mức độ đồng thuận giữa các sàn",
    has_market_stats=True,
    has_sector_rankings=False,
)


def get_profile(region: str) -> MarketProfile:
    """Trả về MarketProfile của vùng (chỉ Việt Nam). / Return the MarketProfile (Vietnam only)."""
    return VN_PROFILE
