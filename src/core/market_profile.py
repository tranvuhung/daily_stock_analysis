# -*- coding: utf-8 -*-
"""
Market review region configuration

Defines metadata for each market region (indices, news search terms, prompt hints, etc.)
so MarketAnalyzer can switch review behavior by region across A-share/HK/US/Japan-Korea/Vietnam.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class MarketProfile:
    """Market review region configuration"""

    region: str  # "cn" | "hk" | "us" | "jp" | "kr" | "vn"
    # Index code used to judge the overall trend: cn uses SSE 000001, us uses S&P SPX
    mood_index_code: str
    # News search keywords
    news_queries: List[str]
    # Prompt hint for index commentary
    prompt_index_hint: str
    # Whether the market overview includes advance/decline counts and limit-up/limit-down (available for A-share, not US)
    has_market_stats: bool
    # Whether the market overview includes sector performance (available for A-share, not yet for US)
    has_sector_rankings: bool


CN_PROFILE = MarketProfile(
    region="cn",
    mood_index_code="000001",
    news_queries=[
        "A股 大盘 复盘",
        "股市 行情 分析",
        "A股 市场 热点 板块",
    ],
    prompt_index_hint="分析上证、深证、创业板等各指数走势特点",
    has_market_stats=True,
    has_sector_rankings=True,
)

US_PROFILE = MarketProfile(
    region="us",
    mood_index_code="SPX",
    news_queries=[
        "美股 大盘",
        "US stock market",
        "S&P 500 NASDAQ",
    ],
    prompt_index_hint="分析标普500、纳斯达克、道指等各指数走势特点",
    has_market_stats=False,
    has_sector_rankings=False,
)

HK_PROFILE = MarketProfile(
    region="hk",
    mood_index_code="HSI",
    news_queries=[
        "港股 大盘 复盘",
        "Hong Kong stock market",
        "恒生指数 行情",
    ],
    prompt_index_hint="分析恒生指数、恒生科技指数、国企指数等各指数走势特点",
    has_market_stats=False,
    has_sector_rankings=False,
)

JP_PROFILE = MarketProfile(
    region="jp",
    mood_index_code="N225",
    news_queries=[
        "日本股市 日经225",
        "Japan stock market Nikkei TOPIX",
        "日经225 东证指数 行情",
    ],
    prompt_index_hint="分析日经225、东证指数等日本主要指数走势特点",
    has_market_stats=False,
    has_sector_rankings=False,
)

KR_PROFILE = MarketProfile(
    region="kr",
    mood_index_code="KS11",
    news_queries=[
        "韩国股市 KOSPI",
        "Korea stock market KOSPI KOSDAQ",
        "KOSPI KOSDAQ 行情",
    ],
    prompt_index_hint="分析 KOSPI、KOSDAQ 等韩国主要指数走势特点",
    has_market_stats=False,
    has_sector_rankings=False,
)


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
    """Return the MarketProfile for the given region"""
    if region == "us":
        return US_PROFILE
    if region == "hk":
        return HK_PROFILE
    if region == "jp":
        return JP_PROFILE
    if region == "kr":
        return KR_PROFILE
    if region == "vn":
        return VN_PROFILE
    return CN_PROFILE
