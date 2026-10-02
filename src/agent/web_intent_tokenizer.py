# -*- coding: utf-8 -*-
"""
Web Chat intent recognition layer — six-step tokenizer pipeline (Intent Tokenizer).

Splits a user message into a sequence of ``Token`` objects carrying semantic tags for
``web_intent_resolver.WebIntentResolver`` to classify by rules; this module makes no
intent decisions and only "identifies what is in the message".

== Six-step pipeline (_preprocess_text, in execution order) ==
  Step 1   Multi-stock entity scan (exact full-name match only, 8~2 char windows, including old names after renames; the entry
           extend_AkShare merges the full AkShare list into stockDB — the extension point is pinned at the very front
           of the pipeline)
  Step 2   Split on special punctuation and whitespace (excluding * - . which may appear in stock names/codes; only
           applies to the gaps left by Step 1)
  Step 3   Code-shaped string extraction (unknown_code; bare number of any length → unknown_number)
  Step 4   Market keyword extraction (with "股"+"份" disambiguation)
  Step 5   Unambiguous keyword tokenization (clean word pool)
  Step 6   Multi-strategy matching on remaining gaps (keyword/exact/substring/pinyin/fuzzy DFS)

== Core principle ==
Better to do nothing than do it wrong; drop every low-confidence match. Steps 1-5 only do exact matching;
Step 6 does hybrid matching but only emits when the whole segment is fully TAG-covered (cross-validation); if any
fragment has no tag, the whole segment is returned as-is for the downstream LLM to handle.

== Key invariants ==
  - The entity scan (Step 1) comes before every splitting step: once a full name is torn apart it cannot be restored,
    and full names containing code-shaped/keyword substrings ("TCL科技"/"恒生电子") must be consumed whole first.
    AkShare extension is therefore pinned at the front, so Steps 1~6 all see the same merged DB view.
  - NFKC width normalization on the input side + removing input spaces during window matching: "贵 州 茅 台"
    hits the full name just like regular writing (DB-side source normalization belongs to the resolver layer, added in a later PR).
  - Single definition point for the canonical spelling of token-layer code identity is ``_canonical_stock_code`` (a=6
    bare digits, hk=HK+5 digits, us=uppercase ticker): name and code paths share the same spelling, and downstream
    must reuse it when building stocks from other sources (LLM output/session inheritance), otherwise one stock
    ends up with multiple code identities.
  - The three sector-family tag word pools are in web_intent_types (generic/industry name/industry-also-stock-name);
    the whole pipeline does no stock-name matching for them; an industry name adjacent to a generic word is a high-confidence sector combination.

unknown_code is identified by ``_identify_stock_codes``: DB hit → stock_code (with the
triple); invalid shape or miss in a full market DB → wrong_{market}_code; miss in a non-full DB
→ unknown_{market}_code (uncertain, left to the downstream LLM).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from src.agent.stock_scope import extract_stock_codes  # Code format validation (no DB lookup)
from src.agent.web_intent_types import (
    Market,
    TAG_FILLER,
    TAG_SECTOR,
    TAG_SECTOR_NAME,
    TAG_SECTOR_N_STOCK,
    TAG_STOCK_CODE,
    TAG_STOCK_NAME,
    TAG_SUBJECT_INDEX,
    TAG_SUBJECT_MARKET,
    TAG_SUBJECT_MARKET_BROAD,
    TAG_UNKNOWN_CODE,
    TAG_UNKNOWN_NUMBER,
    Token,
    _ASCII_KEYWORD_UPPER,
    _CLEAN_KEYWORDS_PATTERN,
    _DIGIT_KEYWORDS_RE,
    _HAS_CONTENT_PATTERN,
    _KW_TO_MARKET,
    _SPECIAL_PUNCT_RE,
    _TAG_KEYWORD_LISTS_EXTEND,
    _classify_keyword,
    _compile_kw_pattern,
    unknown_code_tag,
    wrong_code_tag,
)
from src.services.name_to_code_resolver import (
    Stock,  # (code/name/market)
    US_stock_code_match,  # US stock code matching
    _db_lock,  # stockDB read lock: serialized with concurrent merges by downstream extend_AkShare
    extend_AkShare,  # Called at Step 1 entry: merge the full AkShare list into stockDB (idempotent)
    is_known_stock_name,  # Local name-table membership check (offline)
    is_market_db_complete,  # Market DB completeness check (basis for the wrong/unknown split)
    lookup_stock_by_code,  # Code DB lookup (source of the stock_code triple)
    resolver_name_to_code_list,
    stockAliases,  # Old names after renames (code→{old names}): entity index aligned with the resolver
    stockDB,  # Global name DB (code→name, may be enriched in place by AkShare)
)
from src.services.stock_code_utils import (  # Authoritative exchange inference (no reverse dependency on agent)
    _infer_cn_exchange,
)


def _extract_markets_from_tokens(tokens: List[Token]) -> List[Market]:
    """Extract the market enum from tokens (single extraction point): TAG_SUBJECT_MARKET → _KW_TO_MARKET,
    plus the ASCII abbreviations "us"/"hk". Disambiguation is already done in _split_market_tokens; this only maps.

    Lowercase abbreviations only apply when the message contains CJK: a standalone lowercase "us" in an English-only
    message is almost always a pronoun; uppercase US/HK is deliberate and not context-restricted."""
    has_cjk = any("\u3400" <= ch <= "\u9fff" for t in tokens for ch in t.text)
    markets: List[Market] = []
    seen: set = set()
    for t in tokens:
        if t.tag == TAG_SUBJECT_MARKET:
            mkt = _KW_TO_MARKET.get(t.text.lower())
            if mkt and mkt not in seen:
                markets.append(mkt)
                seen.add(mkt)
        else:
            stripped = t.text.strip()
            shorthand = stripped.lower()
            if shorthand in ("us", "hk") and (has_cjk or shorthand != stripped):
                mkt = Market(shorthand)
                if mkt not in seen:
                    markets.append(mkt)
                    seen.add(mkt)
    return markets


def _recognition_rate(tokens: List[Token]) -> float:
    """Ratio of tagged tokens to all tokens; a low ratio escalates to the LLM fallback."""
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if t.tag) / len(tokens)


def _market_of_code(code: str) -> str:
    """Infer market from code shape: 6 digits→a, 5 digits→hk, letter ticker→us (same semantics as
    name_to_code_resolver._infer_code_market)."""
    c = (code or "").strip().upper()
    if not c:
        return ""
    if c.isdigit():
        if len(c) == 5:
            return "hk"
        if len(c) == 6:
            return "a"
        return ""
    if re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", c):
        return "us"
    return ""


def _canonical_stock_code(code: str, market: str) -> str:
    """Canonical spelling of token-layer stock codes: hk → HK+5 digits, a/us keep the stockDB key as-is
    (6 bare digits / uppercase ticker). The single normalization point for name-path output — without it
    the code path and name path would give the same stock two identities."""
    if market == "hk" and code.isdigit():
        return f"HK{code}"
    return code


def _canonical_stocks(stocks: List[Stock]) -> List[Stock]:
    """Apply _canonical_stock_code in bulk (Stock is frozen, so it must be rebuilt)."""
    return [
        Stock(code=_canonical_stock_code(s.code, s.market), name=s.name, market=s.market)
        for s in stocks
    ]


# =========================================================================
# Step 1 — multi-stock entity scan (exact full-name match only, first step of the pipeline)
# =========================================================================

def _build_entity_index() -> Tuple[List[str], Dict[str, List[Tuple[str, str, str]]]]:
    """Build the entity scan index: full-name list + name → [(code, market, canonical name)].

    Includes stockAliases old names after renames (aligned with the resolver); alias hits also display the current
    canonical name. Rebuilt once per message (stockDB may change via AkShare enrichment, so it cannot be cached across
    messages); only reads the current stockDB and never initiates an extension itself.
    """
    names: List[str] = []
    name_codes: Dict[str, List[Tuple[str, str, str]]] = {}
    # Iterate under the lock: serialized with in-place extend_AkShare merges from concurrent requests, preventing dict iteration crashes
    with _db_lock:
        for code, name in stockDB.items():
            if not name or not code:
                continue
            if name not in name_codes:
                names.append(name)
                name_codes[name] = []
            market = _market_of_code(code)
            if market:
                name_codes[name].append((code, market, name))
        for code, aliases in stockAliases.items():
            market = _market_of_code(code)
            canonical = stockDB.get(code) or ""
            for alias in aliases:
                if not alias:
                    continue
                if alias not in name_codes:
                    names.append(alias)
                    name_codes[alias] = []
                entry = (code, market, canonical or alias)
                if market and entry not in name_codes[alias]:
                    name_codes[alias].append(entry)
    return names, name_codes


# Upper bound on compressed length for the entity scan: counted after removing spaces, the min of the longest full name in the DB and 8 (no CJK
# stock names of 9+ chars exist; longer references go to Step 6/the downstream LLM); goes down to 2 chars (short HK names
# such as 美团/快手) — the window must equal a full name exactly, so the mis-split risk is bounded
_MAX_ENTITY_LEN = 8

# Union of sector-family word pools: when clashing with stock names, sector semantics win and the whole pipeline does no stock-name matching.
# Includes words that are exactly full names in the DB ("机器人"=300024) — Step 1 lets them through and Step 6 tags them
_SECTOR_KEYWORD_SET = frozenset(
    kw
    for tag in (TAG_SECTOR, TAG_SECTOR_NAME, TAG_SECTOR_N_STOCK)
    for kw in _TAG_KEYWORD_LISTS_EXTEND.get(tag, [])
)

# ST risk-warning prefixes ("ST德豪"/"*ST美丽"/"ST*美丽"): the three prefix forms are treated as equivalent, and the CJK AB
# part must equal a full name in the DB; Step 3 does not treat their ASCII prefix as a code candidate (see _split_by_codes)
_ST_PREFIX_RE = re.compile(r"^(\*ST|ST\*|ST)([\u3400-\u9fff]+)$")


def _st_prefix_variants(name: str) -> set:
    """Set of the three equivalent ST-prefix spellings: STAB / *STAB / ST*AB are mutually recognized (what follows the prefix
    must be CJK, excluding ordinary English words like "STO"); returns {name} itself for other forms."""
    m = _ST_PREFIX_RE.match(name)
    if not m:
        return {name}
    tail = m.group(2)
    return {f"ST{tail}", f"*ST{tail}", f"ST*{tail}"}


def _split_by_stock_entities(text: str) -> List[Token]:
    """Step 1 helper (first pipeline step): multi-stock entity scan (exact full-name match only, longest first,
    non-overlapping).

    On entry whitespace (including tab/newline) is collapsed to single spaces, then each position tries windows from
    "widest span ~ 2 chars" (after collapsing, a max_len-char full name spans at most 2*max_len-1 raw characters, so
    the span upper bound is statically complete); after removing spaces the window must equal a full name in the DB
    (including old names after renames); substring/pinyin/fuzzy are forbidden ("贵 州 茅 台"/"中 国 海 洋 石 油"→full-name hit).
    When a full name is exactly an enumerated industry word ("机器人") it is let through for Step 6
    to tag as sector; abbreviations ("茅台") are not full names and are handled by Step 6 multi-strategy matching. A unique hit
    → TAG_STOCK_NAME as a definite entity; several same-named stocks across markets ("阿里巴巴") → carry multiple candidates
    for downstream ambiguity confirmation. Unmatched fragments are kept as empty-tag tokens for later steps.
    """
    if not text:
        return []
    # Collapse whitespace (including tab/newline) to single spaces: after collapsing, a max_len-char full name spans at most
    # 2*max_len-1 raw characters, so the window span upper bound can be statically capped; this also unifies this step's
    # removal of ' ' with Step 2's whitespace splitting (tab-separated references can hit too).
    # Gap text is normalized along with it — whitespace is split and filtered by later steps anyway, so no information is lost
    text = re.sub(r"\s+", " ", text)
    if not any("\u3400" <= ch <= "\u9fff" for ch in text):
        return [Token(text)]  # Pure-English segments are handled by the Step 6 DFS fallback (pinyin/US codes)
    names, name_codes = _build_entity_index()
    max_len = min(_MAX_ENTITY_LEN, max((len(n) for n in names), default=0))
    max_span = 2 * max_len - 1  # Widest raw span of the longest full name after collapsing (statically complete)
    tokens: List[Token] = []
    i, gap_start, n = 0, 0, len(text)
    while i < n:
        matched = False
        for length in range(max_span, 1, -1):  # Windows from the widest span down to 2 chars (full names with spaces also hit whole)
            if i + length > n:
                continue
            window = text[i:i + length]
            if not any("\u3400" <= ch <= "\u9fff" for ch in window):
                continue  # Windows without CJK are not scanned (codes/ASCII are handled by other steps)
            window = window.replace(' ', '')  # Remove spaces on the input side before comparing with full names
            if len(window) > max_len:
                continue  # Longer than the longest full name in the DB after compression (long-span window over unspaced text), cannot hit
            pairs = name_codes.get(window)
            if not pairs:
                for variant in _st_prefix_variants(window):
                    if variant == window:
                        continue
                    pairs = name_codes.get(variant)
                    if pairs:
                        break
            if pairs and window not in _SECTOR_KEYWORD_SET:
                stocks = [
                    Stock(code=_canonical_stock_code(code, market),
                          name=canonical, market=market)
                    for code, market, canonical in pairs
                ]
                if gap_start < i:
                    tokens.append(Token(text[gap_start:i]))
                tokens.append(Token(window, TAG_STOCK_NAME, stocks=stocks))
                i += length
                gap_start = i
                matched = True
                break
        if not matched:
            i += 1
    if gap_start < n:
        tokens.append(Token(text[gap_start:]))
    return tokens


def _is_identified_token(token: Token) -> bool:
    """Whether the token was already recognized by an earlier step (has a tag or is a known stock name/code)."""
    if token.tag:
        return True
    if is_known_stock_name(token.text):
        return True
    return False


def _extract_full_names_first(normalized: str) -> List[Token]:
    """Step 1: multi-stock exact full-name scan over the whole normalized message (first pipeline step).

    The entity scan precedes every splitting step (a torn full name cannot be restored, see the module docstring).
    On entry ``extend_AkShare`` (idempotent) merges the full AkShare list into stockDB — so the first
    cold message produces the same output as the warm state. Scans the whole message directly (no per-token wrapping),
    building the entity index only once per message.
    """
    if any("\u3400" <= ch <= "\u9fff" for ch in normalized):
        extend_AkShare()
    return _split_by_stock_entities(normalized)


# =========================================================================
# Step 4 — market keyword extraction ("股"+"份" disambiguation)
# =========================================================================

# Union of market-related tag keywords; for "股"+"份" disambiguation see _split_market_tokens
# (prevents the "港股" substring in "大港股份" from being wrongly extracted)
_MARKET_TOKEN_PATTERN = _compile_kw_pattern(
    TAG_SUBJECT_MARKET, TAG_SUBJECT_MARKET_BROAD, TAG_SUBJECT_INDEX,
)


def _split_market_tokens(text: str) -> List[Token]:
    """Step 4 helper: extract and tag market keywords. "股" followed by "份" (a stock-name suffix) is skipped and not reported."""
    if not text:
        return []
    tokens: List[Token] = []
    pos = 0
    for m in _MARKET_TOKEN_PATTERN.finditer(text):
        s, e = m.start(), m.end()
        matched = m.group()
        if matched.endswith("股") and e < len(text) and text[e] == "份":
            continue
        if pos < s:
            gap = text[pos:s].strip()
            if gap:
                tokens.append(Token(gap))
        tag = _classify_keyword(matched)
        tokens.append(Token(matched, tag))
        pos = e
    tail = text[pos:].strip()
    if tail:
        tokens.append(Token(tail))
    return tokens


def _apply_market_extraction(tokens: List[Token]) -> List[Token]:
    """Step 4: market keyword extraction + 股份 disambiguation on non-code/non-full-name tokens."""
    result: List[Token] = []
    for t in tokens:
        if _is_identified_token(t):
            result.append(t)
        else:
            result.extend(_split_market_tokens(t.text))
    return result


# =========================================================================
# Step 5 — unambiguous keyword tokenization (clean word pool)
# =========================================================================

def _tokenize_by_clean_keywords(text: str) -> List[Token]:
    """Step 5 helper: unambiguous keyword tokenization; gaps are kept as empty-tag tokens for Step 6."""
    tokens: List[Token] = []
    pos = 0
    for m in _CLEAN_KEYWORDS_PATTERN.finditer(text):
        gap = text[pos:m.start()].strip()
        if gap:
            tokens.append(Token(gap))
        tokens.append(Token(m.group(), _classify_keyword(m.group())))
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        tokens.append(Token(tail))
    return tokens


def _apply_clean_keyword_extraction(tokens: List[Token]) -> List[Token]:
    """Step 5: tokenize unrecognized tokens with unambiguous keywords (e.g. "分析" "走势" "怎样")."""
    result: List[Token] = []
    for t in tokens:
        if _is_identified_token(t):
            result.append(t)
        else:
            result.extend(_tokenize_by_clean_keywords(t.text))
    return result


# =========================================================================
# Step 6 — multi-strategy smart matching (DFS full match: sector words/keywords/stock names)
# =========================================================================

def _isAlpha(s: str) -> bool:
    """Check whether the string consists only of English letters (a-z, A-Z)."""
    return bool(s) and all(c.isascii() and c.isalpha() for c in s)


# Single-char filler set: fragments made up entirely of these characters never trigger a full stock-name DB scan — otherwise
# spam like "和"*200 would trigger a full DB scan at every DFS position (measured at seconds on a 5500-name DB)
_SINGLE_CHAR_FILLERS = frozenset(
    kw for kw in _TAG_KEYWORD_LISTS_EXTEND[TAG_FILLER] if len(kw) == 1
)


def _is_filler_only(segment: str) -> bool:
    """Whether the fragment consists entirely of single-char filler keyword characters (e.g. "和和" "的的")."""
    return bool(segment) and all(ch in _SINGLE_CHAR_FILLERS for ch in segment)


def _dfs_match(text: str) -> Optional[List[Token]]:
    """Depth-first full match: match keywords/stock names from position 0 and recurse on the remaining text; return None
    if a full match is impossible (better to do nothing than do it wrong). A full match is itself the cross-validation.

    "XX板块"-style compounds have no dedicated regex: the industry name and the generic word each hit their own pool, and DFS
    naturally yields the adjacent high-confidence [sector_name/sector_n_stock]+[sector] combination; non-enumerated
    industry words ("预制菜板块") cannot fully match and are returned as-is for the downstream LLM.
    """
    if len(text) == 0:
        return []

    # Starts with a letter: extract the word made of consecutive English letters
    if _isAlpha(text[0]):
        # Take the maximal run of letters directly, e.g. "Alibaba的基本面" → "Alibaba", "的基本面"
        i = 1
        while i < len(text) and _isAlpha(text[i]):
            i += 1
        segment = text[:i]
        # Pure English goes through stock-name pinyin detection + US code detection; both sources are deduplicated by (code, market)
        # (names equal to the ticker, like AMD/Meta, come out once from each source as the same entity); normalize to
        # the canonical spelling first so the dedupe key matches the output
        stock_list = _canonical_stocks(resolver_name_to_code_list(segment))
        seen = {(s.code, s.market) for s in stock_list}
        stock_list += [
            s for s in US_stock_code_match(segment)
            if (s.code, s.market) not in seen
        ]
        if stock_list:
            rest = _dfs_match(text[i:])
            if rest is not None:
                return [Token(segment, TAG_STOCK_NAME, stocks=stock_list)] + rest

    # Try runs of 2~4 CJK characters (longer matches first)
    for length in (4, 3, 2, 1):
        if length > len(text):
            continue
        segment = text[:length]

        # Do not split consecutive letters
        if _isAlpha(segment[-1]) and length < len(text) and _isAlpha(text[length]):
            continue

        # Recursive search, same strategy
        tag = _classify_keyword(segment)  # All keywords (clean + extend)
        stock_list = []
        # Runs of filler chars cannot form a stock name: skip the full DB scan to prevent runaway per-position scanning;
        # generic company suffixes (公司/集团…) get the corp_suffix tag in the extend pool, and the tag
        # path skips scanning too (the word pool is the single source of truth, see web_intent_types)
        if not tag and not _is_filler_only(segment):
            stock_list = _canonical_stocks(resolver_name_to_code_list(segment))
        if tag or len(stock_list) > 0:
            rest = _dfs_match(text[length:])
            if rest is not None:
                return [Token(segment, tag or TAG_STOCK_NAME, stocks=stock_list or None)] + rest
            # An "exact" full-name hit in the DB (not counted when the alias's canonical name != input) is self-evident as an entity:
            # confirm and return directly, leaving the rest as empty tags for the LLM; keyword/substring/pinyin/fuzzy hits
            # do not get this treatment and still require full-coverage cross-validation
            if not tag and stock_list and all(s.name == segment for s in stock_list):
                tail = text[length:]
                return [Token(segment, TAG_STOCK_NAME, stocks=stock_list or None)] + (
                    [Token(tail)] if tail else []
                )

    return None


# Step 6 per-token length cap: 50 consecutive chars without punctuation and without signals from earlier steps is not normal
# conversation (spam/pasted long text), so give up entirely and pass it to the LLM. Technically this caps per-character recursion depth
# (RecursionError measured at ~1500 chars) and the worst case of linear × full DB scan, keeping it bounded; normal
# chat fragments are far shorter than this cap after Step 2 punctuation splitting
_MAX_MULTI_MATCH_TEXT_LEN = 50


# Text for dynamic hybrid matching
def _multi_match(text: str) -> List[Token]:
    """Step 6 helper: multi-strategy smart matching (keywords/name DB exact/substring/pinyin/fuzzy);
    returned as-is if it cannot fully match (better to do nothing than do it wrong).

    Precondition: AkShare extension and exact full-name consumption are owned exclusively by pipeline Step 1 — calling
    this function directly with long text on a cold DB re-exposes the exponential backtracking of the unmemoized DFS; callers should
    go through ``_preprocess_text``."""
    if not text:
        return []
    if len(text) > _MAX_MULTI_MATCH_TEXT_LEN: # DFS complexity control; overly long tokens are left to the LLM
        return [Token(text)]
    result = _dfs_match(text)
    return result if result is not None else [Token(text)]


def _apply_multi_extraction(tokens: List[Token]) -> List[Token]:
    """Step 6: multi-strategy smart matching on each empty-tag token; already-tagged tokens are protected."""
    result: List[Token] = []
    for t in tokens:
        if t.tag:
            result.append(t)
        else:
            result.extend(_multi_match(t.text))
    return result


# =========================================================================
# Step 2 — special punctuation splitting
# =========================================================================

def _split_by_special_punct(text: str) -> List[Token]:
    """Step 2: split on special punctuation and whitespace boundaries (excluding * - .; whitespace is also a word boundary,
    so "tsla aapl" and "tsla,aapl" are handled the same). Only applies to Step 1 gaps; separators
    are kept as empty-tag tokens and filtered by _HAS_CONTENT_PATTERN at the end."""
    if not text:
        return []
    tokens: List[Token] = []
    pos = 0
    for m in _SPECIAL_PUNCT_RE.finditer(text):
        if pos < m.start():
            gap = text[pos:m.start()].strip()
            if gap:
                tokens.append(Token(gap))
        tokens.append(Token(m.group()))
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        tokens.append(Token(tail))
    return tokens


# =========================================================================
# Step 3 — code-shaped string extraction
# =========================================================================

# ---- _CODE_CANDIDATE_PATTERNS — candidate regexes for code-shaped strings (used only by _split_by_codes) ----
# Loose matching (prefer over-matching to missing): fragments that "look like codes" get TAG_UNKNOWN_CODE, validity is left to
# _identify_stock_codes. Pure text matching: no DB lookup, longest regex wins at the same position, no market inference.
_CODE_CANDIDATE_PATTERNS: List[Tuple[str, int]] = [
    # 1. Exchange prefix + digits of any length: SH600519, HK88888, SZ123
    (r'(?<![a-zA-Z])(?:SH|SZ|BJ|HK)\d{1,}(?!\d)', re.IGNORECASE),
    # 2. Digits.exchange suffix: 123456.HK, 235454354.sh
    (r'(?<!\d)\d{1,}\.(?:SH|SZ|BJ|HK)(?!\d)', re.IGNORECASE),
    # 3. Bare digits of any length: 600519, 12 (ambiguous shape → unknown_number, no guessing by digit count)
    (r'(?<!\d)\d{1,}(?!\d)', 0),
    # 4. Uppercase US ticker (not directly adjacent to letters/digits), with optional class suffix BRK.B —
    #    the suffix and body form one span so "BRK" and ".B" are not torn into two
    (r'(?<![A-Za-z0-9.])([A-Z]{2,5}(?:\.[A-Z]{1,2})?)(?![A-Za-z0-9])', 0),
    # 5. Letters + .us suffix (case-insensitive): aapl.us, BABA.US
    (r'(?<![A-Za-z0-9.])([A-Za-z]{1,5}\.us)(?![A-Za-z0-9])', re.IGNORECASE),
]


def _split_by_codes(text: str) -> List[Token]:
    """Step 3 helper: broad code-shaped string extraction.

    Fragments that "look like codes" under the 5 regex classes → TAG_UNKNOWN_CODE; bare digits of any length →
    TAG_UNKNOWN_NUMBER (ambiguous shape, left to the downstream LLM); gaps are kept as empty-tag
    tokens. Only does shape matching and maximal contiguous merging, with no validity checks and no market inference.
    Does not reuse extract_stock_codes: its leading-digit whitelist would drop invalid codes like 777777,
    which this function keeps for downstream confirmation.
    """
    if not text:
        return []

    # Phase 1: each regex scans independently to collect spans; overlaps/nesting are merged in phase 2. Two pass-through rules
    # keep keyword semantics intact: pure-ASCII words in the keyword pool ("AI"/"PK" are not code candidates even in
    # uppercase; they go to keyword classification); digit parts inside enumerated index words with digits ("沪深300") (let through only when
    # fully contained in the keyword span; partial overlaps like "沪深3000" do not qualify)
    digit_keyword_spans: List[Tuple[int, int]] = (
        [(m.start(), m.end()) for m in _DIGIT_KEYWORDS_RE.finditer(text)]
        if _DIGIT_KEYWORDS_RE is not None
        else []
    )
    spans: List[Tuple[int, int]] = []
    for pattern, flags in _CODE_CANDIDATE_PATTERNS:
        for m in re.finditer(pattern, text, flags):
            if m.group().upper() in _ASCII_KEYWORD_UPPER:
                continue
            # ST prefix pass-through: when followed by CJK / preceded by * / followed by *+CJK, "ST" is not a code
            # candidate and the whole name goes to Step 1 (so ST names outside the DB do not get their prefix torn off)
            if m.group() == "ST" and (
                (m.end() < len(text) and "\u3400" <= text[m.end()] <= "\u9fff")
                or (m.start() > 0 and text[m.start() - 1] == "*")
                or (m.end() + 1 < len(text) and text[m.end()] == "*"
                    and "\u3400" <= text[m.end() + 1] <= "\u9fff")
            ):
                continue
            if any(ps <= m.start() and m.end() <= pe for ps, pe in digit_keyword_spans):
                continue
            spans.append((m.start(), m.end()))

    # No hits → return the whole text as one empty-tag token
    if not spans:
        return [Token(text)]

    # Phase 2: maximal contiguous merge (overlapping spans take the longest, never cut by substrings)
    spans.sort()
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    # Phase 3: split by merged spans: candidates → unknown_code; gaps → empty-tag tokens
    tokens: List[Token] = []
    pos = 0
    for s, e in merged:
        if pos < s:
            gap = text[pos:s].strip()
            if gap:
                tokens.append(Token(gap))
        span_text = text[s:e]
        if span_text.isdigit():
            # Bare digits are shape-ambiguous (code/index/price/year/date…) → unknown_number,
            # skipped from code validation and left to the downstream LLM
            tokens.append(Token(span_text, TAG_UNKNOWN_NUMBER))
        else:
            tokens.append(Token(span_text, TAG_UNKNOWN_CODE))
        pos = e
    tail = text[pos:].strip()
    if tail:
        tokens.append(Token(tail))
    return tokens


def _apply_code_extraction(tokens: List[Token]) -> List[Token]:
    """Step 3: code-shape extraction on empty-tag tokens (recognized tokens are protected)."""
    result: List[Token] = []
    for t in tokens:
        if _is_identified_token(t):
            result.append(t)
        else:
            result.extend(_split_by_codes(t.text))
    return result


# =========================================================================
# _preprocess_text — entry point of the six-step pipeline
# =========================================================================

def _preprocess_text(text: str) -> Tuple[str, List[Token]]:
    """Six-step pipeline tokenization; returns (original text, token list). Steps and invariants are in the module docstring.

    On entry the working copy gets NFKC width normalization (the returned original text is unchanged): full-width digits/letters
    ("６００５１９"/"Ａ股") take part in every step just like their half-width forms. unknown_code is then identified by
    _identify_stock_codes as stock_code / wrong_{market}_code /
    unknown_{market}_code.
    """
    # ---- Six-step pipeline ----
    normalized = unicodedata.normalize("NFKC", text)  # Width normalization (working copy; original text returned as-is)
    tokens: List[Token] = []
    for t in _extract_full_names_first(normalized):   # Step 1: full-name entity scan
        if t.tag:
            tokens.append(t)                # Recognized tokens are not split further
        else:
            tokens.extend(_split_by_special_punct(t.text))  # Step 2: punctuation/whitespace splits gaps only
    tokens = _apply_code_extraction(tokens)           # Step 3: code-shape extraction
    tokens = _apply_market_extraction(tokens)         # Step 4: market keywords
    tokens = _apply_clean_keyword_extraction(tokens)  # Step 5: unambiguous keywords
    tokens = _apply_multi_extraction(tokens)          # Step 6: multi-strategy matching

    # Filter out pure punctuation/whitespace tokens
    tokens = [t for t in tokens if t.text.strip() and _HAS_CONTENT_PATTERN.search(t.text)]

    return text, tokens


# =========================================================================
# Code identification — unknown_code → stock_code / wrong_{market}_code / unknown_{market}_code
# =========================================================================

def _is_valid_canonical_numeric_code(code: str) -> bool:
    """Whether the numeric code normalized by extract_stock_codes satisfies the bare-format constraints (6-digit A-share
    leading-digit whitelist 0/3/6/4/8 or the 92 range, HK+5 digits). extract only strips prefixes without checking the whitelist,
    so normalized results must pass this gate too, otherwise prefixed invalid codes would bypass confirmation and execute directly.
    """
    c = (code or "").strip().upper()
    if c.startswith("HK"):
        digits = c[2:]
        return digits.isdigit() and len(digits) == 5
    if c.isdigit():
        return len(c) == 6 and (c[0] in "03648" or c.startswith("92"))
    return False


def _shape_market(text: str, canonical: str) -> str:
    """Code market inference: prefer the normalized shape (HK prefix → hk, 6 digits → a, letters → us);
    when the shape is invalid (canonical is empty) fall back to the prefix/suffix shape of the raw text; default a."""
    if canonical:
        if canonical.startswith("HK"):
            return "hk"
        m = _market_of_code(canonical)
        if m:
            return m
    t = (text or "").upper()
    if t.startswith("HK") or t.endswith(".HK"):
        return "hk"
    if _isAlpha(t.split(".", 1)[0]) and not any(ch.isdigit() for ch in t):
        return "us"
    return "a"


# Explicit exchange annotation parsing: prefix SH/SZ/BJ/HK or suffix .SH/.SZ/.BJ/.HK (case-
# insensitive), suffix wins. Authoritative parsing at the decision layer; does not reuse extract_stock_codes (that is a
# candidate collector that silently passes digit-count mismatches, unsuitable for decisions); annotated market contradicting
# the digit shape → wrong_{annotated market}, never silently re-parsed under another market
_EXPLICIT_MARKER_RE = re.compile(
    r"^(?P<pfx>SH|SZ|BJ|HK)?(?P<digits>[0-9]+)(?:[.](?P<sfx>SH|SZ|BJ|HK))?$",
    re.IGNORECASE,
)
_MARKER_MARKET = {"SH": "a", "SZ": "a", "BJ": "a", "HK": "hk"}


def _identify_one_code(t: Token) -> Token:
    """Identification of a single TAG_UNKNOWN_CODE token (helper of _identify_stock_codes):
    deterministic decision with explicit annotations taking precedence.

    Annotated market/exchange contradicting the digit shape → wrong_{annotated market} (HK600519 / SH00700 /
    SH000001 are not silently re-parsed under another market or exchange); a valid shape is looked up → stock_code,
    and misses are split into wrong/unknown by whether that market DB is full. Pure letters → US ticker path;
    unannotated defensive shapes go through the extract fallback (Step 3 never produces that shape under the pipeline contract)."""
    text = t.text

    # Pure letters → US ticker: accepted only on a local DB hit; any miss is uncertain and left to the LLM
    if not any(ch.isdigit() for ch in text):
        ticker = text.split(".", 1)[0].upper()
        matched = US_stock_code_match(ticker)
        if matched:
            # Normalize to the canonical uppercase spelling (aapl.us → AAPL) so the same stock shares one code identity
            return Token(ticker, TAG_STOCK_CODE, stocks=tuple(matched))
        return Token(text, unknown_code_tag("us"))

    m = _EXPLICIT_MARKER_RE.match(text)
    pfx, sfx = (m.group("pfx"), m.group("sfx")) if m else (None, None)
    if pfx and sfx and pfx.upper() != sfx.upper():
        # Prefix and suffix annotate mutually exclusive markets (HK600519.SH): invalid by static rules, judged by the suffix market
        return Token(text, wrong_code_tag(_MARKER_MARKET[sfx.upper()]))
    marker = (sfx or pfx) if m else None
    if marker is not None:
        market = _MARKER_MARKET[marker.upper()]
        digits = m.group("digits")
        # Zero-pad HK short codes before validation (1810.HK/HK700 → HK01810/HK00700, aligned with
        # stock_code_utils zfill(5) normalization; zfill is a no-op for ≥5 digits, so the
        # too-many-digits decision is unaffected)
        canonical = f"HK{digits.zfill(5)}" if marker.upper() == "HK" else digits
        # Shape gate for the annotated market (HK=5 digits, A-share=6 digits+leading-digit whitelist): any mismatch is
        # wrong_{annotated market}, even if the digits happen to be a valid code in another market
        if not _is_valid_canonical_numeric_code(canonical):
            return Token(text, wrong_code_tag(market))
        if marker.upper() != "HK" and _infer_cn_exchange(digits) != marker.upper():
            # The exchange implied by the digit shape contradicts the annotation (SH000001/BJ600519):
            # wrong_{annotated market}; must not be silently resolved to the same-numbered code on another exchange
            return Token(text, wrong_code_tag(market))
        stock = lookup_stock_by_code(canonical)
        if stock is not None:
            # Normalized spelling (hk00700 → HK00700, 600519.SH → 600519)
            return Token(canonical, TAG_STOCK_CODE, stocks=(stock,))
        if is_market_db_complete(market):
            # Missed even though the market DB is full (e.g. A-share with AkShare merged): definitely nonexistent
            return Token(text, wrong_code_tag(market))
        # DB not full (A-share not extended / hk/us curated local DBs): uncertain, left to the downstream LLM
        return Token(text, unknown_code_tag(market))

    # Unannotated defensive path (does not occur under the pipeline contract: Step 3 bare digits → unknown_number)
    extracted = [c.upper() for c in extract_stock_codes(text)]
    canonical = extracted[0] if extracted else ""
    if not canonical or not all(_is_valid_canonical_numeric_code(c) for c in extracted):
        return Token(text, wrong_code_tag(_shape_market(text, canonical)))
    stock = lookup_stock_by_code(canonical)
    if stock is not None:
        return Token(canonical, TAG_STOCK_CODE, stocks=(stock,))
    market = _shape_market(text, canonical)
    if is_market_db_complete(market):
        return Token(text, wrong_code_tag(market))
    return Token(text, unknown_code_tag(market))


def _identify_stock_codes(tokens: List[Token]) -> List[Token]:
    """Identify each TAG_UNKNOWN_CODE token, with three possible outputs:

    - DB hit → ``stock_code``: ``stocks`` carries the full triple and the text uses the normalized spelling
      (hk00700 → HK00700, 600519.SH → 600519, normalized from the same source as the name path's
      ``_canonical_stock_code`` so the same stock shares one code identity);
    - invalid shape or miss in a full market DB → ``wrong_{market}_code``: definitely nonexistent;
    - miss in a non-full market DB → ``unknown_{market}_code``: uncertain, left to the downstream LLM.
    """
    result: List[Token] = []
    for t in tokens:
        if t.tag != TAG_UNKNOWN_CODE:
            result.append(t)
            continue
        result.append(_identify_one_code(t))
    return result
