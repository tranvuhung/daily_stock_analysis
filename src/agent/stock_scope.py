# -*- coding: utf-8 -*-
"""Xác định phạm vi mã cổ phiếu cho các lượt hỏi tiếp trong chat (chỉ mã Việt Nam).

Stock-scope helpers for ask-stock follow-up chat turns (Vietnam codes only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set


SWITCH_CLEANUP_KEYS = {
    "stock_name",
    "previous_analysis_summary",
    "previous_strategy",
    "previous_price",
    "previous_change_pct",
    "realtime_quote",
    "daily_history",
    "trend_result",
    "news_context",
    "fundamental_context",
    "analysis_context_pack_summary",
    "market_phase_context",
}

# Từ khoá tiếng Việt / tiếng Anh cho ý định chuyển mã và so sánh. Chỉ dùng để
# quyết định switch/compare và bật trích mã viết thường.
# Vietnamese/English intent keywords for switch/compare decisions.
_SWITCH_PATTERN = re.compile(
    r"phân tích|đánh giá|nghiên cứu|nhận định|chuyển sang|đổi sang|\bxem\b|\bsoi\b|"
    r"có nên|thế nào|ra sao|\banaly[sz]e\b|\blook at\b|\bswitch to\b",
    re.IGNORECASE,
)
_STRONG_COMPARE_PATTERN = re.compile(
    r"so sánh|so với|đối chiếu|\bcompare\b|\bversus\b|\bvs\b",
    re.IGNORECASE,
)
_WEAK_COMPARE_HINT_PATTERN = re.compile(
    r"khác nhau|khác biệt|chênh lệch|\bdifference\b",
    re.IGNORECASE,
)
_CHOICE_COMPARE_PATTERN = re.compile(
    r"mã nào|con nào|cái nào|nên chọn|\bwhich\b",
    re.IGNORECASE,
)

# Token viết hoa có thể là mã VN: mã 3 ký tự, chỉ số (VNINDEX/VN30/HNXIndex…),
# chứng chỉ quỹ (FUEVN100) và hậu tố ``.VN``. Mỗi token được kiểm lại bằng parser VN.
# Uppercase candidate tokens; each is validated by the Vietnam parser.
_UPPERCASE_CODE_PATTERN = re.compile(
    r"(?<![\w.])([A-Z][A-Za-z0-9]{1,12}(?:\.VN)?)(?![\w])"
)
# Mã viết thường (``hpg``) chỉ được trích khi câu có ý định switch/compare, với ranh giới
# từ Unicode (``đang`` không được cắt thành ``ang``) và bắt buộc mã có trong stock index,
# tránh nhận nhầm từ tiếng Việt như "xem", "sao", "qua", "mua" thành ticker.
# Lowercase codes: Unicode word boundaries and must exist in the bundled stock index.
_LOWERCASE_CODE_PATTERN = re.compile(r"(?<![\w.])([a-z][a-z0-9]{2})(?![\w])")
_CONTEXTUAL_INDICATOR_TOKENS = {"MA", "EMA", "SMA", "RSI", "KDJ", "MACD", "ATR", "OBV"}


def _is_switch_intent(text: str) -> bool:
    return bool(_SWITCH_PATTERN.search(text))


def _is_strong_compare(text: str) -> bool:
    return bool(_STRONG_COMPARE_PATTERN.search(text))


def _is_weak_compare_hint(text: str) -> bool:
    return bool(_WEAK_COMPARE_HINT_PATTERN.search(text))


def _is_choice_compare(text: str) -> bool:
    return bool(_CHOICE_COMPARE_PATTERN.search(text))


@dataclass(frozen=True)
class StockScope:
    """Runtime stock-scope contract for one chat turn."""

    expected_stock_code: str = ""
    allowed_stock_codes: Set[str] = field(default_factory=set)
    mode: str = "maintain"

    def as_log_payload(self) -> Dict[str, Any]:
        return {
            "expected_stock_code": self.expected_stock_code,
            "allowed_stock_codes": sorted(self.allowed_stock_codes),
            "mode": self.mode,
        }


@dataclass(frozen=True)
class StockScopeResolution:
    """Result produced before a chat turn enters the agent loop."""

    effective_context: Dict[str, Any]
    stock_scope: Optional[StockScope]


def normalize_vn_stock_code(value: Any) -> str:
    """Quy chuẩn một mã về dạng trần viết hoa; chuỗi rỗng nếu không phải mã VN.

    Normalize to a bare uppercase Vietnam code, or ``""`` when not a Vietnam code.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    from src.services.stock_list_parser import ParseStatus, parse_analysis_target

    target = parse_analysis_target(text)
    if target.asset_type != ParseStatus.STOCK:
        return ""
    return target.canonical_id.upper()


def _is_denied_candidate(candidate: str) -> bool:
    token = candidate.strip().upper()
    if token in _CONTEXTUAL_INDICATOR_TOKENS:
        return True
    try:
        from src.agent.orchestrator import _COMMON_WORDS

        return token in _COMMON_WORDS
    except Exception:
        return False


def _append_candidate(candidates: List[str], raw: str) -> None:
    normalized = normalize_vn_stock_code(raw)
    if not normalized or _is_denied_candidate(normalized):
        return
    if normalized not in candidates:
        candidates.append(normalized)


def extract_stock_codes(text: str) -> List[str]:
    """Trích các mã Việt Nam xuất hiện trong câu, theo thứ tự xuất hiện.

    Extract Vietnam codes from free text in order of appearance.
    """
    if not text:
        return []

    candidates: List[str] = []
    for match in _UPPERCASE_CODE_PATTERN.finditer(text):
        raw = match.group(1)
        # Chỉ nhận token toàn chữ hoa (``HPG``) hoặc chỉ số viết hoa-thường
        # (``HNXIndex``); bỏ qua từ thường viết hoa chữ đầu như ``Hello``.
        if not (raw.isupper() or raw.upper().startswith(("HNX", "VN", "UPCOM"))):
            continue
        _append_candidate(candidates, raw)

    if (
        _is_switch_intent(text)
        or _is_strong_compare(text)
        or _is_weak_compare_hint(text)
        or _is_choice_compare(text)
    ):
        from src.data.stock_index_loader import resolve_index_stock_code

        for match in _LOWERCASE_CODE_PATTERN.finditer(text):
            if resolve_index_stock_code(match.group(1)) is None:
                continue
            _append_candidate(candidates, match.group(1))

    return candidates


def _is_compare_message(message: str, candidates: List[str], current_code: str) -> bool:
    if _is_strong_compare(message):
        return True
    new_candidates = {code for code in candidates if code != current_code}
    if len(new_candidates) >= 2:
        return True
    if _is_choice_compare(message) and len(candidates) >= 2:
        return True
    if not _is_weak_compare_hint(message):
        return False
    return len(candidates) >= 2


def _with_skills(context: Dict[str, Any], skills: Optional[Iterable[str]]) -> Dict[str, Any]:
    if skills is None:
        return context
    next_context = dict(context)
    next_context["skills"] = list(skills)
    return next_context


def _switch_context(context: Dict[str, Any], stock_code: str) -> Dict[str, Any]:
    next_context = {
        key: value
        for key, value in context.items()
        if key not in SWITCH_CLEANUP_KEYS and key != "allowed_stock_codes"
    }
    next_context["stock_code"] = stock_code
    next_context["stock_name"] = ""
    return next_context


def resolve_stock_scope(
    message: str,
    context: Optional[Dict[str, Any]],
    *,
    skills: Optional[Iterable[str]] = None,
    strict_initial_scope: bool = False,
) -> StockScopeResolution:
    """Xác định phạm vi mã cho một lượt chat. / Resolve the stock scope for one turn."""
    original_context = dict(context or {})
    message_text = message or ""
    current_code = normalize_vn_stock_code(original_context.get("stock_code"))
    raw_context_code = str(original_context.get("stock_code") or "").strip()
    invalid_context_code = bool(raw_context_code) and (
        not current_code or _is_denied_candidate(current_code)
    )
    original_context.pop("allowed_stock_codes", None)
    if invalid_context_code:
        original_context.pop("stock_code", None)
        original_context.pop("stock_name", None)
        current_code = ""

    if not current_code:
        if invalid_context_code or strict_initial_scope:
            candidates = extract_stock_codes(message_text)
            if strict_initial_scope and not invalid_context_code and not candidates:
                return StockScopeResolution(
                    effective_context=_with_skills(original_context, skills),
                    stock_scope=None,
                )
            allowed = set(candidates)
            expected = candidates[0] if len(candidates) == 1 else ""
            effective_context = dict(original_context)
            mode = "switch" if expected else ("compare" if len(candidates) > 1 else "maintain")
            if expected:
                effective_context["stock_code"] = expected
                effective_context["stock_name"] = ""
            return StockScopeResolution(
                effective_context=_with_skills(effective_context, skills),
                stock_scope=StockScope(
                    expected_stock_code=expected,
                    allowed_stock_codes=allowed,
                    mode=mode,
                ),
            )
        return StockScopeResolution(
            effective_context=_with_skills(original_context, skills),
            stock_scope=None,
        )

    candidates = extract_stock_codes(message_text)
    new_candidates = [code for code in candidates if code != current_code]
    mode = "maintain"
    effective_context = dict(original_context)
    expected = current_code
    allowed = {current_code}

    if _is_compare_message(message_text, candidates, current_code):
        mode = "compare"
        allowed.update(candidates)
    elif _is_switch_intent(message_text) and len(new_candidates) == 1:
        mode = "switch"
        expected = new_candidates[0]
        allowed = {expected}
        effective_context = _switch_context(original_context, expected)

    effective_context["stock_code"] = expected if mode == "switch" else current_code
    effective_context = _with_skills(effective_context, skills)

    return StockScopeResolution(
        effective_context=effective_context,
        stock_scope=StockScope(
            expected_stock_code=expected,
            allowed_stock_codes=allowed,
            mode=mode,
        ),
    )
