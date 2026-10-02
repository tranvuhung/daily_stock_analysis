# -*- coding: utf-8 -*-
"""
Web Chat intent recognition layer — types and constants for the tokenizer module (Intent Tokenizer Types).

== Module role ==
This module is the "data dictionary" of the six-step tokenizer pipeline in ``web_intent_tokenizer.py``: the Token
structure plus tag/keyword/regex constants. Intent-layer definitions (intent enum, intent recognition result,
confidence/session constants) arrive with ``web_intent_resolver.py`` in a later PR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from src.services.name_to_code_resolver import Stock

__all__ = [
    "Market",
    "Token",
    "TAG_UNKNOWN_CODE",
    "TAG_UNKNOWN_NUMBER",
    "TAG_STOCK_CODE",
    "unknown_code_tag",
    "wrong_code_tag",
    "TAG_STOCK_NAME",
    "TAG_SUBJECT_RESEARCH",
    "TAG_SUBJECT_PORTFOLIO",
    "TAG_SUBJECT_MARKET",
    "TAG_SUBJECT_MARKET_BROAD",
    "TAG_SUBJECT_INDEX",
    "TAG_REQUEST",
    "TAG_ACTION_RESEARCH",
    "TAG_ACTION_PORTFOLIO",
    "TAG_FOLLOWUP",
    "TAG_QUESTION",
    "TAG_COMPARISON",
    "TAG_SECTOR",
    "TAG_SECTOR_NAME",
    "TAG_SECTOR_N_STOCK",
    "TAG_TIME",
    "TAG_FILLER",
    "TAG_CORP_SUFFIX",
]


# =========================================================================
# Token semantic tag constants — 21 tags in 5 groups; synonyms share one tag; used for regex matching
# =========================================================================

# --- Stock recognition ---
TAG_UNKNOWN_CODE = "unknown_code"      # Code-shaped string pending validation: intermediate state extracted in Step 3, identified by _identify_stock_codes
TAG_UNKNOWN_NUMBER = "unknown_number"  # Bare number of any length (ambiguous shape: code/index/price/year/quantity/date…); tagged directly in Step 3, skips code validation
TAG_STOCK_CODE = "stock_code"          # Stock code found in the DB (stocks carries the full code/name/market triple)

# Code identification failure states, split by market (produced by _identify_stock_codes):
#   wrong_{market}_code   — definitely nonexistent: invalid shape (static exchange rules), or the market DB is
#                           full (A-share AkShare merged) and still missed
#   unknown_{market}_code — uncertain: missed in a non-full market DB (hk/us curated local DBs),
#                           left to the downstream LLM, never guessed


def wrong_code_tag(market: str) -> str:
    """Tag for a definitely invalid code (market ∈ a/hk/us → wrong_a_code / wrong_hk_code / wrong_us_code)."""
    return f"wrong_{market}_code"


def unknown_code_tag(market: str) -> str:
    """Tag for an uncertain code (market ∈ a/hk/us → unknown_a_code / unknown_hk_code / unknown_us_code)."""
    return f"unknown_{market}_code"

TAG_STOCK_NAME = "stock_name"          # Stock entity (full name or one-to-one abbreviation); Step 1 only exact full-name match, Step 6 multi-strategy extraction

# --- Intent subjects ---
TAG_SUBJECT_RESEARCH = "subject_research"       # Research subject (price action/trend/technicals/fundamentals/chip distribution)
TAG_SUBJECT_PORTFOLIO = "subject_portfolio"     # Portfolio subject (holdings/position/watchlist/P&L)
TAG_SUBJECT_MARKET = "subject_market"           # Market identifier (A-share/HK/US/Shanghai market…)
TAG_SUBJECT_MARKET_BROAD = "subject_market_broad"  # Generic market concept (broad market/quotes/index/both exchanges)
TAG_SUBJECT_INDEX = "subject_index"             # Specific index (SSE/Hang Seng/Nasdaq/CSI 300…)

# --- Actions ---
TAG_REQUEST = "request"                       # Analysis action words (analyze/take a look/research/diagnose/check/evaluate)
TAG_ACTION_RESEARCH = "action_research"       # Research decision (can buy/may buy/stop loss/target price/buy/sell)
TAG_ACTION_PORTFOLIO = "action_portfolio"     # Position operation (add/reduce/rebalance/full position/flat)

# --- Auxiliary ---
TAG_FOLLOWUP = "followup"     # Follow-up continuation (continue/just now/this one/it/above/last time/this stock)
TAG_QUESTION = "question"     # Question (what do you think/how is it/up or down/how/why/question particles)
TAG_COMPARISON = "comparison"  # Comparison (compare/contrast/which is better/vs/pk/pick one of two)
TAG_SECTOR = "sector"          # Generic sector words (sector/industry/track/concept/theme/leader) + XX+suffix compound forms
TAG_SECTOR_NAME = "sector_name"        # Industry names (finance/construction/securities/agriculture…); used bare means sector intent
TAG_SECTOR_N_STOCK = "sector_n_stock"  # Industry name that is also a full stock name (机器人=300024); bare use is ambiguous and left to the downstream LLM
TAG_TIME = "time"             # Time indicators (today/this week/trading day/realtime)
TAG_FILLER = "filler"         # Reply filler words (single-char standalone matches: 的/买/卖/选/那/这…)
TAG_CORP_SUFFIX = "corp_suffix"  # Generic company suffixes (company/group/holdings/shares/international): zero discriminative power; an exact hit skips stock-name matching

class Market(str, Enum):
    """Market identifier enum (str subclass; members compare directly with strings)."""

    A = "a"    # A-share
    HK = "hk"  # HK stocks
    US = "us"  # US stocks


# =========================================================================
# Tag → keyword list — the single definition point for all Chinese keywords
# =========================================================================
# All later regexes are compiled from here, never hand-written Chinese keywords; TAG_SUBJECT_MARKET is derived from _MARKET_KEYWORD_MAP.
# Exclusions: multi-token cross-word patterns (和.*比…) → extra param; "值得" → conflicts with the stock name "值得买", removed

_MARKET_KEYWORD_MAP: Dict[Market, tuple] = {
    Market.A: ("a股", "大a", "沪市", "深市", "沪深"),
    Market.HK: ("港股", "h股", "香港"),
    Market.US: ("美股", "美国"),
}

# Keywords are split into two pools by whether they affect hybrid stock-entity matching: clean is unambiguous and tokenized directly, with higher confidence;
#       extend may be confused with stock names; it adds tokenization variety but has lower confidence and needs cross-validation, otherwise it is better dropped.
# _TAG_KEYWORD_LISTS merges both; downstream logic is unchanged

_TAG_KEYWORD_LISTS_CLEAN: Dict[str, List[str]] = {
    TAG_REQUEST: [
        "分析", "看看", "研究", "诊断", "评估", "查一下", "查下",
        "analyze", "analyse", "research",
    ],
    TAG_SUBJECT_RESEARCH: [
        "走势", "走向", "涨势", "趋势", "技术面", "基本面", "筹码", "后市", "trend",
    ],
    TAG_ACTION_RESEARCH: [
        "能买", "可以买", "目标价", "止损", "买点", "卖点", "抄底", "buy", "sell",
    ],
    TAG_QUESTION: [
        "涨还是跌", "怎么看", "怎么样", "怎么", "是否",
        "为何", "为什么", "还会", "要不要",
        "能否", "能不能", "如何", "吗", "？", "?",
    ],
    TAG_SUBJECT_PORTFOLIO: [
        "持仓", "仓位", "我的股票", "自选股", "盈亏", "成本价",
        "portfolio", "position",
    ],
    TAG_ACTION_PORTFOLIO: [
        "加仓", "减仓", "调仓", "满仓", "空仓",
    ],
    TAG_SUBJECT_MARKET_BROAD: [
        "大盘", "行情", "两市", "北向", "股市", "market", "sector", "指数",
    ],
    TAG_SUBJECT_INDEX: [
        "上证", "深证", "创业板", 
        "恒指", "纳斯达克", "纳指",
        "标普", "道琼斯", "道指", 
        "沪指", "深成指", "深证成指",
        "科创", "科创板", "index", "indices",
        # Index words containing digits (CJK+digit compounds): the digit part has the same shape as bare numbers, so Step 3
        # lets them through via the _DIGIT_KEYWORDS_RE protected zone, otherwise the whole word could never match in Step 4/5
        "中证A500", "中证1000", "中证2000", "中证500", "中证800", "中证100",
        "上证50", "北证50", "深证100", "创业板50", "沪深300", "科创50",
    ],
    TAG_FOLLOWUP: [
        "继续", "接着", "刚才", "上面", "上次", "这只", "该股", "它", "他",
        "然后",
    ],
    TAG_COMPARISON: [
        "哪个好", "哪个", "哪只", "谁更", "二选一",
        "差别", "区别", "优劣", "pk", "vs",
    ],
    TAG_TIME: [
        "今天", "本周", "交易日", "实时", "最近",
    ],
    TAG_FILLER: [
        "那个", "这个", "一只", "一下", "帮我", "我要", "我想", "以及",
        "其他", "其它",
    ],
}

_TAG_KEYWORD_LISTS_EXTEND: Dict[str, List[str]] = {
    TAG_SUBJECT_INDEX: [
        "恒生",  # Confused with the stock name "恒生电子"
    ],
    TAG_COMPARISON: [
        "对比", "比较", "多选", "选哪",
    ],
    TAG_SECTOR: [
        # Generic words: suffixes of XX industry names (sector/industry/track/concept/theme) + leader.
        # Step 6 DFS splits "建筑板块" into an adjacent [sector_name]+[sector] combination,
        # and that adjacent combination is a high-confidence sector signal (consumed downstream); "leader" is not a suffix, it refers to
        # a leading stock ("白酒龙头") and does not take part in the adjacent-combination semantics
        "板块", "行业", "赛道", "概念", "题材", "龙头",
    ],
    TAG_SECTOR_NAME: [
        # Industry names: bare use means sector intent. Includes words that clash with stock names (verified on the full DB: "证券"⊂
        # 中信证券, "农业"⊂农业银行…) — name-DB substring hits have very low confidence,
        # so sector semantics take precedence and the whole pipeline does no stock-name matching for them
        "新能源", "半导体", "消费", "医药", "白酒",
        "军工", "银行", "地产", "保险", "券商",
        "煤炭", "有色", "钢铁", "汽车", "光伏", "锂电",
        "芯片", "人工智能", "互联网", "金融", "科技", "AI",
        "证券", "农业", "电力", "建筑", "传媒", "教育",
        "航空", "软件", "通信", "旅游",
    ],
    TAG_SECTOR_N_STOCK: [
        # Industry names that are also full stock names in the DB ("机器人"=300024): exact full-name hit, both sector and stock
        # readings are plausible — bare use gets the ambiguity tag for downstream LLM/confirmation to resolve, not directly
        # stock_name, nor arbitrarily sector_name
        "机器人",
    ],
    TAG_FILLER: [
        "和", "下", "是", "再",
        "的", "买", "卖", "选", "了", "吧", "呢",
        "那", "这", "只", "支", "啊", "呀", "咯",
        "哦", "嘛", "么", "就", "请", "第", "个",
    ],
    TAG_CORP_SUFFIX: [
        # Generic company suffixes (zero discriminative power): an exact hit gets the corp_suffix tag and skips
        # stock-name scanning — substring matching on the name DB always hits full names with a head word ("公司"⊂中微
        # 公司, "集团"⊂上汽集团), so a hit is noise, not signal; they also serve as coverable fragments
        # in DFS full coverage ("腾讯公司"→腾讯+公司). Names with a discriminative head word such as
        # "苹果公司"/"中芯国际" are unaffected. Unlike filler: the semantics is a company-name
        # suffix rather than a filler word, so downstream can recognize it separately. Only in extend, not clean
        # (the clean regex would tear the suffix out of the middle of a token in Step 5)
        "公司", "集团", "控股", "股份", "国际",
    ],
}

_TAG_KEYWORD_LISTS: Dict[str, List[str]] = {}
for _tag in set(_TAG_KEYWORD_LISTS_CLEAN) | set(_TAG_KEYWORD_LISTS_EXTEND):
    _TAG_KEYWORD_LISTS[_tag] = (
        _TAG_KEYWORD_LISTS_CLEAN.get(_tag, []) +
        _TAG_KEYWORD_LISTS_EXTEND.get(_tag, [])
    )

# Market identifier keyword → tag (derived from _MARKET_KEYWORD_MAP)
_MARKET_KW_TAG_MAP: Dict[str, str] = {
    kw: TAG_SUBJECT_MARKET
    for keywords in _MARKET_KEYWORD_MAP.values()
    for kw in keywords
}

# market keyword → Market enum (inverted, O(1) lookup)
_KW_TO_MARKET: Dict[str, Market] = {
    kw: mkt for mkt, keywords in _MARKET_KEYWORD_MAP.items() for kw in keywords
}

# keyword → tag (inverted + market words merged, for O(1) lookup in _classify_keyword)
_KEYWORD_TAG_MAP: Dict[str, str] = {
    kw: tag for tag, kws in _TAG_KEYWORD_LISTS.items() for kw in kws
}
_KEYWORD_TAG_MAP.update(_MARKET_KW_TAG_MAP)

# Lowercase-normalized map: fallback lookup for case-insensitive keyword classification. The word pools store mixed forms
# (most ASCII words lowercase, "AI"/"中证A500" uppercase); a lower() fallback on input alone would make
# lowercase forms of uppercase-stored words ("ai赛道"/"中证a500") fail entirely, so the keyword side is
# also normalized to lowercase. The current pools have no lowercase homographs; if one appears it would be silently merged here,
# so the pools must stay mutually exclusive.
_KEYWORD_TAG_MAP_LOWER: Dict[str, str] = {
    kw.lower(): tag for kw, tag in _KEYWORD_TAG_MAP.items()
}

# Enumerated keywords containing digits (CJK+digit compounds such as "沪深300"/"中证1000"): their digit part
# has the same shape as bare numbers in text; if Step 3 extracted them as bare numbers, the word would be split into "prefix+number"
# and never match in Step 4/5. _split_by_codes uses this regex to mark keyword spans as
# protected zones; digit candidate spans fully inside a protected zone are let through (partial overlaps like "沪深3000"
# do not qualify and keep bare-number behavior), and the whole word goes to Step 4/5 keyword tokenization.
_DIGIT_KEYWORDS: Tuple[str, ...] = tuple(sorted(
    (
        kw
        for kws in _TAG_KEYWORD_LISTS.values()
        for kw in kws
        if any(ch.isdigit() for ch in kw) and any("\u3400" <= ch <= "\u9fff" for ch in kw)
    ),
    key=len,
    reverse=True,
))
# Compiled case-insensitively: "中证a500" and "中证A500" are equally protected for the digit part (case
# normalization on the tag classification side is in _KEYWORD_TAG_MAP_LOWER).
_DIGIT_KEYWORDS_RE = (
    re.compile("|".join(re.escape(kw) for kw in _DIGIT_KEYWORDS), re.IGNORECASE)
    if _DIGIT_KEYWORDS
    else None
)

# Uppercase set of all pure-ASCII keywords: the pass-through predicate for Step 3 code candidates (case-
# insensitive) — keyword forms like "PK"/"Buy"/"AI" must not be grabbed by the US ticker regex, otherwise
# keyword semantics are lost and they wrongly enter code identification (this includes "AI", previously let through by exact match on the sector pool,
# whose role this takes over; "HK"/"US" are not keywords and still act as code candidates)
_ASCII_KEYWORD_UPPER: frozenset = frozenset(
    kw.upper() for kw in _KEYWORD_TAG_MAP if kw.isascii() and kw.isalpha()
)


# =========================================================================
# Regex compilation helpers — everything is compiled from _TAG_KEYWORD_LISTS
# =========================================================================

def _compile_kw_fragment(kw: str) -> str:
    """Single keyword → regex fragment: ASCII wrapped in (?i:), CJK escaped directly."""
    if re.search(r"[A-Za-z]", kw):
        return f"(?i:{re.escape(kw)})"
    return re.escape(kw)


def _compile_kw_pattern(*tags: str, extra: str = "") -> re.Pattern:
    """Extract all keywords from the given tag list and compile them into a single regex.

    Sorted by length descending so longer keywords win ("沪深300" is not cut by "沪深").
    The extra param can append raw regex fragments (e.g. vs word boundaries, multi-token cross-word patterns).
    """
    kws: List[str] = []
    for tag in tags:
        if tag == TAG_SUBJECT_MARKET:
            kws.extend(_MARKET_KW_TAG_MAP.keys())
        else:
            kws.extend(_TAG_KEYWORD_LISTS.get(tag, []))
    fragments = sorted(
        ({_compile_kw_fragment(k) for k in kws} |
         ({extra} if extra else set())),
        key=len, reverse=True,
    )
    return re.compile("|".join(fragments))


# Step 5 keyword tokenization regex (market tags excluded and handled separately in Step 4, so "大港股份" disambiguation keeps working)
_NON_MARKET_TAGS = frozenset({
    TAG_REQUEST, TAG_SUBJECT_RESEARCH, TAG_ACTION_RESEARCH, TAG_QUESTION,
    TAG_SUBJECT_PORTFOLIO, TAG_ACTION_PORTFOLIO,
    TAG_FOLLOWUP, TAG_COMPARISON, TAG_SECTOR, TAG_SECTOR_NAME,
    TAG_SECTOR_N_STOCK, TAG_TIME, TAG_FILLER,
})


def _compile_clean_kw_pattern(*tags: str) -> re.Pattern:
    """Compile a regex from _TAG_KEYWORD_LISTS_CLEAN only, excluding extended keywords that may be confused with stock names."""
    kws: List[str] = []
    for tag in tags:
        kws.extend(_TAG_KEYWORD_LISTS_CLEAN.get(tag, []))
    fragments = sorted({_compile_kw_fragment(k) for k in kws}, key=len, reverse=True)
    return re.compile("|".join(fragments))


# Step 5 unambiguous keyword tokenization regex (words like "分析" "走势" "持仓" that cannot be confused with stock names)
_CLEAN_KEYWORDS_PATTERN = _compile_clean_kw_pattern(*_NON_MARKET_TAGS)


# Pure punctuation/whitespace filter: must contain at least one CJK char/letter/digit
_HAS_CONTENT_PATTERN = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")

# Special punctuation set (Step 1 token boundaries). Excludes * - . because they may appear in stock names/codes (ST*, 600519.SH)
_SPECIAL_PUNCT_CHARS = (
    "\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3001\u201c\u201d\u2018\u2019"
    "\uff08\uff09\u3010\u3011\u300a\u300b\u2026\uff5e\u2014\uff0f\uff20"
    "\uff03\uff04\uff05\uff3e\uff06\uff0b\uff1d"
    ",!?;:\"'()[]{}<>/@#$%^&+=~`|\\\\"
)
# Whitespace (regular/full-width spaces, tabs, newlines) is also a word boundary: separation between ASCII words and codes
# ("tsla aapl") previously relied only on Step 3 uppercase or punctuation fallback, so lowercase multi-word runs leaked whole to
# the LLM; whitespace tokens have no content and are dropped by _HAS_CONTENT_PATTERN at the end of the pipeline
_SPECIAL_PUNCT_RE = re.compile("[" + re.escape(_SPECIAL_PUNCT_CHARS) + r"|\s]")


def _classify_keyword(token_text: str) -> str:
    """Look up the semantic tag in _KEYWORD_TAG_MAP; without an exact hit fall back to the lowercase-normalized map
    (case-insensitivity mirrors the (?i:) compile flag, see the _KEYWORD_TAG_MAP_LOWER comment);
    return an empty string on a miss."""
    tag = _KEYWORD_TAG_MAP.get(token_text)
    if tag:
        return tag
    return _KEYWORD_TAG_MAP_LOWER.get(token_text.lower(), "")


# =========================================================================
# Token — tokenizer struct, text + tag
# =========================================================================


@dataclass(frozen=True)
class Token:
    """Tokenizer struct: text + semantic tag + optional resolved stock entities.

    frozen=True makes Token hashable; an empty tag means unrecognized; stocks passes resolved entities through
    so downstream does not re-resolve. A list passed for stocks is normalized to a tuple in __post_init__
    (frozen field values must be hashable); downstream consumes it as a sequence unaffected.
    """
    text: str
    tag: str = ""
    stocks: Optional[Tuple["Stock", ...]] = None

    def __post_init__(self) -> None:
        if isinstance(self.stocks, list):
            object.__setattr__(self, "stocks", tuple(self.stocks))
