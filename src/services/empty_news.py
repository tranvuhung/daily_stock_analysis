# -*- coding: utf-8 -*-
"""Report disclosure text for when news search did not run or returned zero hits.

Single source of truth: the string-concatenation renderer (src/notification.py) and the template
rendering chain (src/services/report_renderer.py + templates/*.j2) share this module, so the same
analysis result is not disclosed on some channels and silent on others.

The disclosure asserts "did this conclusion use any news evidence", so the primary signal is
whether the news evidence actually received by the analysis (news_context) is non-empty, not how
many search hits there were. news_context may come from realtime search, social sentiment, or the
locally persisted news pool; the latter two also feed the model input without producing search
hits, so looking only at the count would misreport such analyses as "no news evidence included"
(review OR-COR-2e4b9d61).

news_result_count is therefore secondary and only explains why there is truly no evidence:
    None  search did not run (no search channel configured)
    0     search ran but got zero hits (rate limiting, all failed, etc.)
    > 0   realtime search had hits (evidence necessarily exists)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, Tuple

from src.report_language import SUPPORTED_REPORT_LANGUAGES

_EN_NOT_CONFIGURED = (
    "⚠️ No news search channel is configured; "
    "this analysis does not incorporate news-based evidence."
)
_EN_ZERO_RESULTS = (
    "⚠️ No news data could be retrieved for this run; "
    "the conclusions below do not incorporate news-based evidence."
)

_VI_NOT_CONFIGURED = (
    "⚠️ Chưa cấu hình kênh tìm kiếm tin tức; "
    "phân tích này không sử dụng bằng chứng từ tin tức."
)
_VI_ZERO_RESULTS = (
    "⚠️ Lần chạy này không lấy được dữ liệu tin tức nào; "
    "các kết luận bên dưới không dựa trên bằng chứng tin tức."
)

_DISCLOSURES = {
    "vi": (_VI_NOT_CONFIGURED, _VI_ZERO_RESULTS),
    "en": (_EN_NOT_CONFIGURED, _EN_ZERO_RESULTS),
}

if set(_DISCLOSURES) != set(SUPPORTED_REPORT_LANGUAGES):
    raise RuntimeError(
        "Empty-news disclosures must cover every SUPPORTED_REPORT_LANGUAGES value"
    )


def persisted_news_result_state(
    raw_result: Any,
    context_snapshot: Any = None,
) -> Tuple[Optional[int], bool]:
    """Recover the count and its reliability from the persisted payload.

    New records store the three-state value explicitly in raw_result; old records may leave a
    0 / >0 count in context_snapshot. When neither field exists it can only be treated as legacy
    unknown; a missing field must not be taken as an explicit None.
    """
    if isinstance(raw_result, Mapping):
        if raw_result.get("news_result_count_known") is False:
            return None, False
        if "news_result_count" in raw_result:
            return raw_result.get("news_result_count"), True

    if isinstance(context_snapshot, Mapping) and "news_result_count" in context_snapshot:
        return context_snapshot.get("news_result_count"), True

    return None, False


def news_evidence_present(*sources: Any) -> bool:
    """Whether this analysis actually received news evidence. True if any source is truthy.

    Each source is either a real item count (int) or a content string **with placeholder text
    already removed**. Realtime search and the local news pool each count as one
    source; both pipeline paths use this function — do not write a separate check elsewhere.

    **Do not pass in the whole news_context assembled by the pipeline.**
    `format_intel_report()` in `src/search_service.py` still emits the
    per-dimension result header and a "not found" placeholder per dimension on zero hits, so
    the whole block is never empty; using it would turn "searched but got nothing" into "has
    evidence", swallowing exactly the disclosure this module adds (review OR-COR-8f4c2d1b). The
    check must register each source individually, never sniff strings.
    """
    for source in sources:
        if source is None or isinstance(source, bool):
            if source:
                return True
            continue
        if isinstance(source, (int, float)):
            if source > 0:
                return True
            continue
        if str(source).strip():
            return True
    return False


def _disclosure_for_state(
    news_result_count: Optional[int],
    *,
    known: bool,
    evidence_present: bool,
    language: str,
) -> Optional[str]:
    try:
        not_configured, zero_results = _DISCLOSURES[language]
    except KeyError as exc:
        raise ValueError(f"Unsupported report language for empty-news disclosure: {language}") from exc

    if not known:
        return None
    # If evidence exists, no warning regardless of its source; the count only explains why there is no evidence.
    if evidence_present:
        return None
    if news_result_count is None:
        return not_configured
    if news_result_count == 0:
        return zero_results
    return None


def empty_news_disclosure(result: Any, language: str = "vi") -> Optional[str]:
    """Return the matching notice when search did not run or got zero hits; return None on normal hits.

    The decision must be independent of whether the model produced news-related text: the
    analyzer's output schema requires market_sentiment / hot_topics even when there is no news, so
    deciding based on whether those fields are empty would produce the worst combination —
    showing model-generated sentiment while hiding that there was no news evidence.
    """
    if isinstance(result, Mapping):
        news_result_count, known = persisted_news_result_state(result)
        evidence_present = persisted_news_evidence_present(result, news_result_count)
    else:
        news_result_count = getattr(result, "news_result_count", None)
        known = getattr(result, "news_result_count_known", True)
        evidence_present = bool(getattr(result, "news_evidence_present", False))
    return _disclosure_for_state(
        news_result_count,
        known=known,
        evidence_present=evidence_present,
        language=language,
    )


def empty_news_disclosure_from_stored(
    raw_result: Any,
    context_snapshot: Any,
    language: str = "vi",
) -> Optional[str]:
    """Build the disclosure from the persisted payload for history/API entry points; stay silent when old records lack fields."""
    news_result_count, known = persisted_news_result_state(raw_result, context_snapshot)
    return _disclosure_for_state(
        news_result_count,
        known=known,
        evidence_present=persisted_news_evidence_present(raw_result, news_result_count),
        language=language,
    )


def persisted_news_evidence_present(raw_result: Any, news_result_count: Optional[int]) -> bool:
    """Recover "did this analysis use news evidence" from the persisted payload.

    Records written before this PR lack the field, so fall back to inferring from the count: >0
    means evidence existed, anything else is treated as no evidence. This matches how the report
    looked at the time and never retroactively changes old reports.
    """
    if isinstance(raw_result, Mapping) and "news_evidence_present" in raw_result:
        return bool(raw_result.get("news_evidence_present"))
    return bool(news_result_count)
