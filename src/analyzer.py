# -*- coding: utf-8 -*-
"""
===================================
Tầng phân tích AI / AI analysis layer
===================================

Trách nhiệm / Responsibilities:
1. Đóng gói việc gọi LLM (qua LiteLLM: Gemini/Anthropic/OpenAI...)
2. Kết hợp phân tích kỹ thuật và tin tức để sinh báo cáo
3. Parse phản hồi LLM thành ``AnalysisResult`` có cấu trúc

Lưu ý: thân prompt gửi cho LLM luôn là tiếng Anh cho mọi thị trường; ngôn ngữ
đầu ra do mục "Output Language" quyết định (xem ``_get_analysis_system_prompt``).
Note: the prompt body is always English; the output language is set separately.
"""

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple, Callable, Union

import litellm
from json_repair import repair_json
from litellm import Router

from src.agent.llm_adapter import (
    get_thinking_extra_body,
    resolve_fallback_litellm_wire_models,
    register_fallback_model_pricing,
)
from src.agent.provider_trace import resolved_model_provider_identity
from src.agent.skills.defaults import CORE_TRADING_SKILL_POLICY_EN
from src.config import (
    Config,
    extra_litellm_params,
    get_api_keys_for_model,
    get_config,
    get_configured_llm_models,
    get_explicit_llm_channel_model_provider,
    resolve_news_window_days,
)
from src.llm.hermes import (
    HERMES_CHANNEL_NAME,
    build_hermes_redaction_values,
    canonicalize_hermes_model_ref,
    filter_non_hermes_deployments,
    hermes_blocked_route_candidates,
    is_masked_secret_placeholder,
    open_hermes_no_proxy_client,
    route_deployment_origins,
    route_has_hermes,
    sanitize_hermes_error_text,
)
from src.llm.generation_params import apply_litellm_generation_params
from src.llm.errors import call_litellm_with_param_recovery
from src.llm.backend_registry import (
    LOCAL_CLI_GENERATION_BACKEND_IDS,
    LITELLM_BACKEND_ID,
    resolve_generation_backend_id,
    resolve_generation_fallback_backend_id,
)
from src.llm.backend_factory import create_generation_backend
from src.llm.generation_backend import (
    GenerationBackend,
    GenerationError,
    GenerationErrorCode,
    GenerationResult,
)
from src.llm.usage import (
    attach_legacy_message_stability_audit,
    attach_message_hmacs,
    extract_usage_payload,
    normalize_litellm_usage,
    should_persist_usage_telemetry,
)
from src.llm.local_cli_backend import redact_diagnostic_text
from src.llm.provider_cache import (
    apply_prompt_cache_hints,
    build_provider_cache_route_context,
    filter_prompt_cache_telemetry,
)
from src.llm.response_content import strip_leading_think_wrapper
from src.storage import persist_llm_usage
from src.data.stock_mapping import PLACEHOLDER_NAME_PREFIXES, STOCK_NAME_MAP
from src.report_language import (
    get_signal_level,
    get_placeholder_text,
    get_unknown_text,
    infer_decision_type_from_advice,
    is_placeholder_value,
    get_localized_stock_name,
    get_pipeline_progress_text,
    localize_confidence_level,
    localize_operation_advice,
    localize_trend_prediction,
    localize_volume_status,
    normalize_report_language,
)
from src.schemas.decision_action import build_action_fields
from src.schemas.decision_scale import (
    CANONICAL_DECISION_SCALE_PROMPT_EN,
    score_band_metadata,
)
from src.schemas.report_schema import AnalysisReportSchema
from src.market_context import detect_market, get_market_role, get_market_guidelines
from src.services.daily_market_context import format_daily_market_context_prompt_section
from src.market_phase_prompt import format_market_phase_prompt_section

logger = logging.getLogger(__name__)


def _localized_text(language: Any, *, en: str, vi: Optional[str] = None) -> str:
    """Pick a deterministic fallback string for the report language (vi/en).

    Khi không truyền ``vi`` thì cả hai ngôn ngữ đều dùng chuỗi tiếng Anh.
    When ``vi`` is not given, both languages use the English string.
    """
    normalized = normalize_report_language(language)
    if normalized == "vi" and vi:
        return vi
    return en


def _normalize_risk_warning_values(value: Any) -> List[str]:
    """Normalize arbitrary risk_warning values into a flat list of text alerts."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        normalized: List[str] = []
        for item in value:
            normalized.extend(_normalize_risk_warning_values(item))
        return normalized
    if isinstance(value, dict):
        if not value:
            return []
        try:
            dumped = json.dumps(value, ensure_ascii=False)
            text = dumped.strip()
        except (TypeError, ValueError):
            text = str(value).strip()
        return [text] if text else []
    text = str(value).strip()
    return [text] if text else []


def _today_has_realtime_overlay(today: Any) -> bool:
    if not isinstance(today, dict):
        return False
    data_source = today.get("data_source") or today.get("dataSource")
    if isinstance(data_source, str) and data_source.startswith("realtime:"):
        return True
    if today.get("is_partial_bar") is True or today.get("isPartialBar") is True:
        return True
    if today.get("is_estimated") is True or today.get("isEstimated") is True:
        return True
    return bool(today.get("estimated_fields") or today.get("estimatedFields"))


def _today_looks_complete_daily_bar(
    context: Dict[str, Any],
    phase_context: Dict[str, Any],
) -> bool:
    today = context.get("today")
    if (
        not isinstance(today, dict)
        or today.get("close") in (None, "")
        or _today_has_realtime_overlay(today)
    ):
        return False

    effective_date = phase_context.get("effective_daily_bar_date")
    today_date = today.get("date") or today.get("trade_date") or context.get("date")
    if effective_date and today_date and str(today_date) != str(effective_date):
        return False
    return True


def _phase_aware_quote_labels(context: Dict[str, Any]) -> Tuple[str, str]:
    """Chọn nhãn bảng giá không mâu thuẫn với phiên giao dịch hiện tại.

    Choose quote-table labels that do not conflict with the session phase.
    Các nhãn này chỉ đi vào prompt gửi LLM, không hiển thị cho người dùng.
    These labels only feed the LLM prompt; they are never shown to the user.
    """
    phase_context = context.get("market_phase_context")
    if not isinstance(phase_context, dict):
        return "Today's Quote", "Close"

    phase = str(phase_context.get("phase") or "").strip()
    if phase in {"premarket", "non_trading"}:
        today = context.get("today")
        if _today_looks_complete_daily_bar(context, phase_context):
            return "Last Complete Trading Day", "Last complete session close"
        if _today_has_realtime_overlay(today):
            return "Latest Quote", "Realtime estimated price"
        if isinstance(today, dict) and today.get("close") not in (None, ""):
            return "Latest Quote", "Latest price"
        return "Today's Quote", "Close"

    if (
        phase in {"intraday", "lunch_break", "closing_auction"}
        and phase_context.get("is_partial_bar") is True
    ):
        return "Latest Quote", "Intraday estimated price"

    return "Today's Quote", "Close"


def _should_hide_regular_session_ohlc(context: Dict[str, Any]) -> bool:
    phase_context = context.get("market_phase_context")
    if not isinstance(phase_context, dict):
        return False

    phase = str(phase_context.get("phase") or "").strip()
    return phase in {"premarket", "non_trading"} and not _today_looks_complete_daily_bar(
        context,
        phase_context,
    )


def _market_currency(stock_code: Any) -> str:
    """Đơn vị yết giá theo thị trường của mã. / Quote currency for the stock's market.

    Trước đây prompt in cứng đơn vị CNY cho mọi thị trường — sai với mã Việt Nam
    (VND). Tái dùng bảng thị trường sẵn có thay vì tự khai báo lần nữa.
    The prompt used to hardcode CNY for every market, which is wrong for
    Vietnamese tickers (VND). Reuse the existing market registry instead.
    """
    # Import trễ theo đúng khuôn đã có trong ``src/core/pipeline.py``.
    from data_provider.base import _market_tag
    from src.core.markets import get_spec

    spec = get_spec(_market_tag(str(stock_code or "")))
    return spec.currency if spec else ""


def _legacy_market_group(stock_code: Any) -> str:
    code = str(stock_code or "").strip()
    if not code or code.lower() == "unknown":
        return "unknown"
    return detect_market(code)


def _legacy_audit_marker_specs(
    context: Dict[str, Any],
    *,
    code: str,
    stock_name: str,
    report_language: str,
    news_context: Optional[str],
    analysis_context_pack_summary: Optional[str],
) -> List[Dict[str, Any]]:
    markers: List[Dict[str, Any]] = []

    def add(marker_name: str, value: Any) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        markers.append(
            {
                "marker_name": marker_name,
                "message_role": "user",
                "text": text,
            }
        )

    add("stock_code", code)
    add("stock_name", stock_name)
    add("analysis_date", context.get("date"))
    add("market_phase", "## Market Phase Context")
    add("daily_market_context", "## Daily Market Context")
    add("analysis_context_pack", analysis_context_pack_summary)
    add("quote", "## 📈 Technical Data")
    add("news_context", "## 📰 News Intelligence" if news_context else None)
    return markers


class _LiteLLMStreamError(RuntimeError):
    """Internal error wrapper that records whether any text was streamed."""

    def __init__(self, message: str, *, partial_received: bool = False):
        super().__init__(message)
        self.partial_received = partial_received


class _AllModelsFailedError(Exception):
    """Raised when every model in the fallback chain fails.

    This includes both LLM call errors and JSON parse errors (when a
    ``response_validator`` is provided to :meth:`GeminiAnalyzer._call_litellm`).

    The ``last_response_text`` attribute holds the raw text from the last model
    that *did* return a response (but whose JSON could not be validated), so
    callers can still attempt a best-effort text fallback.

    ``last_model``, ``last_provider`` and ``last_usage`` record the resolved
    route identity and token usage from the last attempt so callers can persist
    diagnostics even on fallback.
    """

    def __init__(
        self,
        message: str,
        *,
        last_response_text: Optional[str] = None,
        last_model: Optional[str] = None,
        last_provider: Optional[str] = None,
        last_usage: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.last_response_text = last_response_text
        self.last_model = last_model
        self.last_provider = last_provider
        self.last_usage = last_usage or {}


from src.utils.data_processing import normalize_report_signal_attribution


def check_content_integrity(
    result: "AnalysisResult",
    *,
    require_phase_decision: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Check mandatory fields for report content integrity.
    Returns (pass, missing_fields). Module-level for use by pipeline (agent weak mode).

    Note:
    - Required fields: missing → pass=False, added to missing_fields
    - Optional fields (e.g., signal_attribution): missing → pass=True and are not added to missing_fields
    """
    missing: List[str] = []

    def _is_blank_text(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        return True

    def _is_invalid_risk_alerts(value: Any) -> bool:
        return not isinstance(value, list)

    def _is_invalid_stop_loss(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, (list, tuple, dict)):
            return True
        if isinstance(value, str):
            return not value.strip()
        return False

    if result.sentiment_score is None:
        missing.append("sentiment_score")
    advice = result.operation_advice
    if not advice or not isinstance(advice, str) or _is_blank_text(advice):
        missing.append("operation_advice")
    summary = result.analysis_summary
    if not summary or not isinstance(summary, str) or _is_blank_text(summary):
        missing.append("analysis_summary")
    dash = result.dashboard if isinstance(result.dashboard, dict) else {}
    core = dash.get("core_conclusion")
    core = core if isinstance(core, dict) else {}
    if _is_blank_text(core.get("one_sentence")):
        missing.append("dashboard.core_conclusion.one_sentence")
    intel = dash.get("intelligence")
    intel = intel if isinstance(intel, dict) else None
    if intel is None or _is_invalid_risk_alerts(intel.get("risk_alerts")):
        missing.append("dashboard.intelligence.risk_alerts")
    if result.decision_type in ("buy", "hold"):
        battle = dash.get("battle_plan")
        battle = battle if isinstance(battle, dict) else {}
        sp = battle.get("sniper_points")
        sp = sp if isinstance(sp, dict) else {}
        stop_loss = sp.get("stop_loss")
        if _is_invalid_stop_loss(stop_loss):
            missing.append("dashboard.battle_plan.sniper_points.stop_loss")
    if require_phase_decision:
        phase_decision = dash.get("phase_decision")
        phase_decision = phase_decision if isinstance(phase_decision, dict) else {}
        if not isinstance(phase_decision.get("phase_context"), dict):
            missing.append("dashboard.phase_decision.phase_context")
        if _is_blank_text(phase_decision.get("action_window")):
            missing.append("dashboard.phase_decision.action_window")
        if _is_blank_text(phase_decision.get("immediate_action")):
            missing.append("dashboard.phase_decision.immediate_action")
        if not isinstance(phase_decision.get("watch_conditions"), list):
            missing.append("dashboard.phase_decision.watch_conditions")
        if _is_blank_text(phase_decision.get("next_check_time")):
            missing.append("dashboard.phase_decision.next_check_time")
        if _is_blank_text(phase_decision.get("confidence_reason")):
            missing.append("dashboard.phase_decision.confidence_reason")
        if not isinstance(phase_decision.get("data_limitations"), list):
            missing.append("dashboard.phase_decision.data_limitations")
    return len(missing) == 0, missing


def apply_placeholder_fill(result: "AnalysisResult", missing_fields: List[str]) -> None:
    """Fill missing mandatory fields with placeholders (in-place). Module-level for pipeline."""

    def _is_blank_text(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        return True

    def _is_invalid_risk_alerts(value: Any) -> bool:
        return not isinstance(value, list)

    def _is_invalid_stop_loss(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, (list, tuple, dict)):
            return True
        if isinstance(value, str):
            return not value.strip()
        return False

    report_language = normalize_report_language(getattr(result, "report_language", "vi"))
    placeholder = get_placeholder_text(report_language)
    phase_decision_placeholders = {
        "dashboard.phase_decision.action_window": _localized_text(
            report_language,
            en="Model did not provide a phase action window",
        ),
        "dashboard.phase_decision.immediate_action": _localized_text(
            report_language,
            en="Model did not provide a phase-aware immediate action",
        ),
        "dashboard.phase_decision.next_check_time": _localized_text(
            report_language,
            en="Model did not provide a next check point",
        ),
        "dashboard.phase_decision.confidence_reason": _localized_text(
            report_language,
            en="Model did not provide a phase confidence rationale",
        ),
    }
    for field in missing_fields:
        if field == "sentiment_score":
            result.sentiment_score = 50
        elif field == "operation_advice":
            if _is_blank_text(result.operation_advice):
                result.operation_advice = placeholder
        elif field == "analysis_summary":
            if _is_blank_text(result.analysis_summary):
                result.analysis_summary = placeholder
        elif field == "dashboard.core_conclusion.one_sentence":
            if not result.dashboard:
                result.dashboard = {}
            core = result.dashboard.get("core_conclusion")
            if not isinstance(core, dict):
                core = {}
                result.dashboard["core_conclusion"] = core
            fallback_sentence = (
                result.analysis_summary
                or result.operation_advice
                or placeholder
            )
            if _is_blank_text(core.get("one_sentence")):
                result.dashboard["core_conclusion"]["one_sentence"] = fallback_sentence
        elif field == "dashboard.intelligence.risk_alerts":
            if not result.dashboard:
                result.dashboard = {}
            intelligence = result.dashboard.get("intelligence")
            if not isinstance(intelligence, dict):
                intelligence = {}
                result.dashboard["intelligence"] = intelligence
            if _is_invalid_risk_alerts(intelligence.get("risk_alerts")):
                risk_warning_values = _normalize_risk_warning_values(result.risk_warning)
                intelligence["risk_alerts"] = risk_warning_values
        elif field == "dashboard.battle_plan.sniper_points.stop_loss":
            if not result.dashboard:
                result.dashboard = {}
            battle_plan = result.dashboard.get("battle_plan")
            if not isinstance(battle_plan, dict):
                battle_plan = {}
                result.dashboard["battle_plan"] = battle_plan
            sniper_points = battle_plan.get("sniper_points")
            if not isinstance(sniper_points, dict):
                sniper_points = {}
                battle_plan["sniper_points"] = sniper_points
            if _is_invalid_stop_loss(sniper_points.get("stop_loss")):
                sniper_points["stop_loss"] = placeholder
        elif field.startswith("dashboard.phase_decision."):
            if not result.dashboard:
                result.dashboard = {}
            phase_decision = result.dashboard.get("phase_decision")
            if not isinstance(phase_decision, dict):
                phase_decision = {}
                result.dashboard["phase_decision"] = phase_decision
            if field == "dashboard.phase_decision.phase_context":
                if not isinstance(phase_decision.get("phase_context"), dict):
                    phase_decision["phase_context"] = {}
            elif field == "dashboard.phase_decision.watch_conditions":
                if not isinstance(phase_decision.get("watch_conditions"), list):
                    phase_decision["watch_conditions"] = []
            elif field == "dashboard.phase_decision.data_limitations":
                if not isinstance(phase_decision.get("data_limitations"), list):
                    phase_decision["data_limitations"] = []
            elif field in phase_decision_placeholders:
                if _is_blank_text(phase_decision.get(field.rsplit(".", 1)[-1])):
                    phase_decision[field.rsplit(".", 1)[-1]] = phase_decision_placeholders[field]


def _is_value_placeholder(v: Any) -> bool:
    """True if value is empty or placeholder (N/A, missing data, etc.)."""
    return is_placeholder_value(v)


_RISK_WARNING_PLACEHOLDER_TEXTS = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "tbd",
    # Giá trị giữ chỗ tiếng Việt / Vietnamese placeholders
    "chưa có",
    "đang cập nhật",
    "thiếu dữ liệu",
    "không rõ",
    "không có",
}

_STRUCTURAL_RISK_PHRASE_HINTS = (
    # Tiếng Việt (LLM viết báo cáo bằng tiếng Việt) / Vietnamese phrases
    "rủi ro trọng yếu",
    "rủi ro lớn",
    "rủi ro then chốt",
    "tin xấu lớn",
    "thoái vốn",
    "cổ đông lớn bán ra",
    "hủy niêm yết",
    "huỷ niêm yết",
    "đình chỉ giao dịch",
    "tạm ngừng giao dịch",
    "diện kiểm soát",
    "diện cảnh báo",
    "xử phạt",
    "vi phạm",
    "kiện tụng",
    "thanh tra",
    "điều tra",
    "ý kiến kiểm toán",
    "kiểm toán ngoại trừ",
    "vỡ nợ",
    "chậm trả",
    "khủng hoảng thanh khoản",
    "giải chấp",
    "thanh lý",
    "phá sản",
    "lỗ đột biến",
    "major risk",
    "material adverse",
    "suspension",
    "delisting",
    "regulatory",
    "downgrade",
    "liquidity",
    "default",
)


def _is_meaningful_text(value: Any) -> bool:
    text = str(value).strip() if value is not None else ""
    if not text:
        return False
    lowered = text.strip().lower()
    return lowered not in _RISK_WARNING_PLACEHOLDER_TEXTS


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Safely convert to float; return default on failure. Private helper for numeric fills."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        try:
            return default if math.isnan(float(v)) else float(v)
        except (ValueError, TypeError):
            return default
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


_BULLISH_TREND_HINTS: Tuple[str, ...] = (
    "xếp tăng",
    "tăng liên tục",
    "xu hướng tăng",
    "mở rộng lên",
    "bullish",
    "uptrend",
)
_WEAK_BULLISH_TREND_HINTS: Tuple[str, ...] = ("tăng yếu",)
_BEARISH_TREND_HINTS: Tuple[str, ...] = (
    "xếp giảm",
    "giảm liên tục",
    "xu hướng giảm",
    "mở rộng xuống",
    "bearish",
    "downtrend",
)
_WEAK_BEARISH_TREND_HINTS: Tuple[str, ...] = ("giảm yếu",)
_NEGATION_TOKENS: Tuple[str, ...] = (
    "không",
    "chưa",
    "chẳng",
    "not ",
    "no ",
)
_NEGATION_BREAK_CHARS: Tuple[str, ...] = (",", ".", ";", ":", "!", "?", "，", "。", "；", "：", "！", "？", "\n")
# Cửa sổ tính theo ký tự; từ tiếng Việt dài hơn chữ Hán nên cửa sổ rộng hơn bản cũ (16/8).
# Character windows; Vietnamese words are longer than CJK, so wider than the legacy 16/8.
_NEGATION_LOOKBACK_CHARS = 24
_NEGATION_MAX_GAP_CHARS = 12
_NEGATION_SCOPE_BREAK_TOKENS: Tuple[str, ...] = (
    "nhưng",
    "mà là",
    "thay vào",
    "ngược lại",
    "chuyển sang",
    "đổi thành",
    " but ",
    " instead ",
    " rather ",
)
# Từ phủ định ngắn, dễ nhập nhằng ("không", "chưa"): chỉ tính là phủ định khi phần
# chen giữa bắt đầu bằng một động từ/liên từ trong danh sách này.
# Short ambiguous negations only count when the gap starts with one of these words.
_GAP_RESTRICTED_NEGATION_TOKENS: Tuple[str, ...] = ("không", "chưa")
_SINGLE_CHAR_NEGATION_GAP_PREFIXES: Tuple[str, ...] = (
    "phải",
    "hình thành",
    "xuất hiện",
    "vào",
    "chuyển",
    "là",
    "có",
    "còn",
    "thấy",
    "thuộc",
    "giữ",
    "duy trì",
    "phá",
)


def _normalize_prompt_reason_items(items: Any) -> List[str]:
    """Normalize prompt reason/risk items into a clean string list."""
    if not isinstance(items, list):
        return []
    normalized: List[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _contains_trend_hint(text: str, hints: Tuple[str, ...]) -> bool:
    """Return True when text contains a non-negated strong trend hint."""
    lowered = text.strip().lower()

    def _has_negation_scope_break(gap: str) -> bool:
        normalized_gap = gap.lower()
        for token in _NEGATION_SCOPE_BREAK_TOKENS:
            token_index = normalized_gap.find(token)
            if token_index > 0:
                return True
        return False

    def _is_valid_negation_gap(token: str, gap: str) -> bool:
        if not gap:
            return True
        if token not in _GAP_RESTRICTED_NEGATION_TOKENS:
            return True
        return any(gap.startswith(prefix) for prefix in _SINGLE_CHAR_NEGATION_GAP_PREFIXES)

    def _is_negated_match(index: int) -> bool:
        prefix = lowered[max(0, index - _NEGATION_LOOKBACK_CHARS):index]
        for token in _NEGATION_TOKENS:
            token_index = prefix.rfind(token)
            if token_index < 0:
                continue
            gap = prefix[token_index + len(token):]
            if any(char in gap for char in _NEGATION_BREAK_CHARS):
                continue
            stripped_gap = gap.strip()
            if len(stripped_gap) > _NEGATION_MAX_GAP_CHARS:
                continue
            if _has_negation_scope_break(stripped_gap):
                continue
            if not _is_valid_negation_gap(token, stripped_gap):
                continue
            return True
        return False

    for hint in hints:
        keyword = hint.lower()
        start = 0
        while True:
            index = lowered.find(keyword, start)
            if index < 0:
                break
            if not _is_negated_match(index):
                return True
            start = index + len(keyword)
    return False


def _infer_trend_direction(trend: Dict[str, Any]) -> str:
    """Infer the final trend direction from trend_status and ma_alignment."""
    combined = " ".join(
        str(trend.get(key, "")).strip()
        for key in ("trend_status", "ma_alignment")
        if str(trend.get(key, "")).strip()
    )
    if not combined:
        return "neutral"
    lowered = combined.lower()
    normalized = lowered.replace(" ", "")
    has_bullish = (
        _contains_trend_hint(combined, _BULLISH_TREND_HINTS + _WEAK_BULLISH_TREND_HINTS)
        or "ma5>ma10>ma20" in normalized
        or (
            "ma5>ma10" in normalized
            and any(pattern in normalized for pattern in ("ma10≤ma20", "ma10<=ma20"))
        )
    )
    has_bearish = (
        _contains_trend_hint(combined, _BEARISH_TREND_HINTS + _WEAK_BEARISH_TREND_HINTS)
        or "ma5<ma10<ma20" in normalized
        or (
            "ma5<ma10" in normalized
            and any(pattern in normalized for pattern in ("ma10≥ma20", "ma10>=ma20"))
        )
    )
    if has_bullish and not has_bearish:
        return "bullish"
    if has_bearish and not has_bullish:
        return "bearish"
    return "neutral"


def _filter_conflicting_trend_items(items: List[str], conflict_hints: Tuple[str, ...]) -> List[str]:
    """Drop reasons that directly conflict with the final trend direction."""
    return [item for item in items if not _contains_trend_hint(item, conflict_hints)]


def _sanitize_trend_analysis_for_prompt(
    trend: Any,
    *,
    volume_change_ratio: Any = None,
) -> Dict[str, Any]:
    """Clean prompt-only trend hints on a derived copy without touching runtime/provider config."""
    trend_dict = dict(trend) if isinstance(trend, dict) else {}
    signal_reasons = _normalize_prompt_reason_items(trend_dict.get("signal_reasons"))
    risk_factors = _normalize_prompt_reason_items(trend_dict.get("risk_factors"))
    prompt_notes: List[str] = []
    trend_direction = _infer_trend_direction(trend_dict)

    if trend_direction == "bearish":
        filtered_signal_reasons = _filter_conflicting_trend_items(
            signal_reasons,
            _BULLISH_TREND_HINTS + _WEAK_BULLISH_TREND_HINTS,
        )
        if len(filtered_signal_reasons) != len(signal_reasons):
            prompt_notes.append("Technical structure is bearish; bullish-structure reasons that directly conflict with the bearish view were removed.")
        signal_reasons = filtered_signal_reasons
        prompt_notes.append(
            "If news, earnings or policy catalysts are bullish, phrase them only as 'event-led, technicals unconfirmed' or 'fundamentals bullish, technicals not yet confirmed'; never present them as a definite buy point."
        )
    elif trend_direction == "bullish":
        filtered_signal_reasons = _filter_conflicting_trend_items(
            signal_reasons,
            _BEARISH_TREND_HINTS + _WEAK_BEARISH_TREND_HINTS,
        )
        if len(filtered_signal_reasons) != len(signal_reasons):
            prompt_notes.append("Technical structure is bullish; bearish-structure reasons that directly conflict with the bullish view were removed.")
        signal_reasons = filtered_signal_reasons
        filtered_risk_factors = _filter_conflicting_trend_items(
            risk_factors,
            _BEARISH_TREND_HINTS + _WEAK_BEARISH_TREND_HINTS,
        )
        if len(filtered_risk_factors) != len(risk_factors):
            prompt_notes.append("Technical structure is bullish; bearish-structure risk statements that directly conflict with the bullish view were removed.")
        risk_factors = filtered_risk_factors

    parsed_volume_change = _safe_float(volume_change_ratio, default=math.nan)
    if math.isfinite(parsed_volume_change) and parsed_volume_change > 10:
        prompt_notes.append(
            f"Volume changed about {parsed_volume_change:.2f}x versus yesterday, which may be abnormal data or a one-off spike; down-weight the volume signal and do not treat it mechanically as strong confirmation."
        )

    trend_dict["signal_reasons"] = signal_reasons
    trend_dict["risk_factors"] = risk_factors
    trend_dict["prompt_consistency_notes"] = prompt_notes
    trend_dict["prompt_trend_direction"] = trend_direction
    return trend_dict


_PRICE_POS_KEYS = ("ma5", "ma10", "ma20", "bias_ma5", "bias_status", "current_price", "support_level", "resistance_level")


def fill_price_position_if_needed(
    result: "AnalysisResult",
    trend_result: Any = None,
    realtime_quote: Any = None,
) -> None:
    """Fill missing price_position fields from trend_result / realtime data (in-place)."""
    if not result:
        return
    try:
        if not result.dashboard:
            result.dashboard = {}
        dash = result.dashboard
        dp = dash.get("data_perspective") or {}
        dash["data_perspective"] = dp
        pp = dp.get("price_position") or {}

        computed: Dict[str, Any] = {}
        if trend_result:
            tr = trend_result if isinstance(trend_result, dict) else (
                trend_result.__dict__ if hasattr(trend_result, "__dict__") else {}
            )
            computed["ma5"] = tr.get("ma5")
            computed["ma10"] = tr.get("ma10")
            computed["ma20"] = tr.get("ma20")
            computed["bias_ma5"] = tr.get("bias_ma5")
            computed["current_price"] = tr.get("current_price")
            support_levels = tr.get("support_levels") or []
            resistance_levels = tr.get("resistance_levels") or []
            if support_levels:
                computed["support_level"] = support_levels[0]
            if resistance_levels:
                computed["resistance_level"] = resistance_levels[0]
        if realtime_quote:
            rq = realtime_quote if isinstance(realtime_quote, dict) else (
                realtime_quote.to_dict() if hasattr(realtime_quote, "to_dict") else {}
            )
            if _is_value_placeholder(computed.get("current_price")):
                computed["current_price"] = rq.get("price")

        filled = False
        for k in _PRICE_POS_KEYS:
            if _is_value_placeholder(pp.get(k)) and not _is_value_placeholder(computed.get(k)):
                pp[k] = computed[k]
                filled = True
        if filled:
            dp["price_position"] = pp
            logger.info("[price_position] Filled placeholder fields from computed data")
    except Exception as e:
        logger.warning("[price_position] Fill failed, skipping: %s", e)


def stabilize_decision_with_structure(
    result: "AnalysisResult",
    trend_result: Any = None,
    fundamental_context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Hiệu chỉnh khuyến nghị mua/bán quá đà dựa trên vùng giá hỗ trợ/kháng cự.

    Calibrate aggressive buy/sell advice with support/resistance levels. The LLM
    can overreact to one-day price movement; this guard keeps the public
    `decision_type` enum stable while allowing neutral wording (range-bound /
    shakeout watch) when price structure does not confirm an immediate action.
    """
    if not result:
        return

    try:
        language = normalize_report_language(getattr(result, "report_language", "vi"))
        dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
        data_perspective = dashboard.get("data_perspective") if isinstance(dashboard, dict) else {}
        if not isinstance(data_perspective, dict):
            data_perspective = {}
        price_position = data_perspective.get("price_position")
        if not isinstance(price_position, dict):
            price_position = {}

        trend_dict = _as_dict_for_decision_guard(trend_result)
        current_price = _first_numeric_value(
            getattr(result, "current_price", None),
            price_position.get("current_price"),
            trend_dict.get("current_price"),
        )
        support = _first_numeric_value(
            price_position.get("support_level"),
            _first_list_value(trend_dict.get("support_levels")),
        )
        resistance = _first_numeric_value(
            price_position.get("resistance_level"),
            _first_list_value(trend_dict.get("resistance_levels")),
        )
        decision_type = infer_decision_type_from_advice(
            getattr(result, "decision_type", ""),
            default=getattr(result, "decision_type", "hold") or "hold",
        )
        decision_type = decision_type if decision_type in {"buy", "hold", "sell"} else "hold"

        if current_price is None:
            return

        broke_support = support is not None and current_price < support * 0.985
        near_support = support is not None and not broke_support and current_price <= support * 1.03
        breakout = resistance is not None and current_price > resistance * 1.01
        near_resistance = (
            resistance is not None
            and not breakout
            and current_price >= resistance * 0.97
        )
        mid_range = (
            support is not None
            and resistance is not None
            and support * 1.03 < current_price < resistance * 0.97
        )

        has_significant_risk = _has_structural_risk_alert(result)

        # Chỉ hiệu chỉnh theo cấu trúc giá (hỗ trợ/kháng cự); thị trường Việt Nam không
        # có dữ liệu dòng tiền chủ lực nên không dùng tín hiệu dòng tiền.
        # Structure-only calibration: Vietnam has no main-force capital-flow data.
        if decision_type == "buy":
            if near_resistance or mid_range:
                _downgrade_to_structural_hold(
                    result,
                    language,
                    advice_key="range",
                    reason_key="buy_near_resistance" if near_resistance else "hold_mid_range",
                    current_price=current_price,
                    support=support,
                    resistance=resistance,
                )
        elif decision_type == "sell":
            if near_support and not has_significant_risk:
                _downgrade_to_structural_hold(
                    result,
                    language,
                    advice_key="shakeout",
                    reason_key="sell_near_support",
                    current_price=current_price,
                    support=support,
                    resistance=resistance,
                )
        elif decision_type == "hold":
            change_pct = _first_numeric_value(getattr(result, "change_pct", None))
            if change_pct is not None and change_pct < 0 and near_support:
                _set_structural_hold_wording(
                    result,
                    language,
                    advice_key="shakeout",
                    reason_key="hold_shakeout",
                    current_price=current_price,
                    support=support,
                    resistance=resistance,
                )
            elif mid_range:
                _set_structural_hold_wording(
                    result,
                    language,
                    advice_key="range",
                    reason_key="hold_mid_range",
                    current_price=current_price,
                    support=support,
                    resistance=resistance,
                )
        _sync_stability_dashboard_fields(result)
    except Exception as exc:
        logger.warning("[decision_stability] skipped: %s", exc)


def _has_structural_risk_alert(result: "AnalysisResult") -> bool:
    dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}

    risk_text = getattr(result, "risk_warning", "")
    if _is_significant_structural_risk(risk_text):
        return True

    intelligence = dashboard.get("intelligence") if isinstance(dashboard, dict) else None
    if isinstance(intelligence, dict):
        risk_alerts = intelligence.get("risk_alerts")
        if isinstance(risk_alerts, str):
            if _is_significant_structural_risk(risk_alerts):
                return True
        elif isinstance(risk_alerts, (list, tuple, set)):
            if any(_is_significant_structural_risk(item) for item in risk_alerts):
                return True

    core_conclusion = dashboard.get("core_conclusion") if isinstance(dashboard, dict) else None
    if isinstance(core_conclusion, dict):
        signal_type = str(core_conclusion.get("signal_type", "")).strip()
        if _is_significant_structural_risk(signal_type):
            return True
    return False


def _is_significant_structural_risk(value: Any) -> bool:
    text = str(value or "").strip()
    if not _is_meaningful_text(text):
        return False

    normalized = text.lower()
    if any(keyword in normalized for keyword in _STRUCTURAL_RISK_PHRASE_HINTS):
        return True

    return ("trọng yếu" in normalized or "nghiêm trọng" in normalized) and "rủi ro" in normalized


def _sync_stability_dashboard_fields(result: "AnalysisResult") -> None:
    dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
    result.dashboard = dashboard
    dashboard["sentiment_score"] = getattr(result, "sentiment_score", None)
    dashboard["operation_advice"] = getattr(result, "operation_advice", None)
    dashboard["decision_type"] = getattr(result, "decision_type", None)


def _as_dict_for_decision_guard(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            converted = value.to_dict()
            return converted if isinstance(converted, dict) else {}
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _first_list_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


def _coerce_numeric_value(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    text = str(value).replace(",", "").strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _first_numeric_value(*values: Any) -> Optional[float]:
    for value in values:
        if isinstance(value, (list, tuple)):
            nested = _first_numeric_value(*value)
            if nested is not None:
                return nested
            continue
        numeric = _coerce_numeric_value(value)
        if numeric is not None:
            return numeric
    return None


def _record_decision_score_calibration(
    result: "AnalysisResult",
    *,
    raw_score: int,
    adjusted_score: int,
    final_action: str,
    guardrail_reason: Optional[str],
) -> None:
    dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
    result.dashboard = dashboard
    calibration = score_band_metadata(raw_score)
    calibration.update(
        {
            "raw_score": raw_score,
            "adjusted_score": adjusted_score,
            "final_action": final_action,
        }
    )
    if guardrail_reason:
        calibration["guardrail_reason"] = guardrail_reason
    dashboard["decision_score_calibration"] = calibration


def _bound_hold_watch_sentiment_score(
    result: "AnalysisResult",
    *,
    reason: Optional[str] = None,
    final_action: str = "watch",
) -> None:
    try:
        score = int(getattr(result, "sentiment_score", 50))
    except (TypeError, ValueError):
        score = 50
    adjusted_score = min(59, max(45, score))
    result.sentiment_score = adjusted_score
    _record_decision_score_calibration(
        result,
        raw_score=score,
        adjusted_score=adjusted_score,
        final_action=final_action,
        guardrail_reason=reason,
    )


def _apply_hold_watch_dashboard(
    result: "AnalysisResult",
    language: str,
    *,
    advice: str,
    reason: str,
    current_price: Optional[float],
    support: Optional[float],
    resistance: Optional[float],
    no_position: str,
    has_position: str,
) -> None:
    result.operation_advice = advice

    dashboard = result.dashboard if isinstance(result.dashboard, dict) else {}
    result.dashboard = dashboard
    core = dashboard.get("core_conclusion")
    if not isinstance(core, dict):
        core = {}
        dashboard["core_conclusion"] = core
    core["signal_type"] = "🟡 Hold / Watch"
    core["one_sentence"] = f"{advice}: {reason}"

    position_advice = core.get("position_advice")
    if not isinstance(position_advice, dict):
        position_advice = {}
        core["position_advice"] = position_advice
    position_advice["no_position"] = no_position
    position_advice["has_position"] = has_position

    stability = {
        "applied": True,
        "reason": reason,
        "current_price": current_price,
        "support": support,
        "resistance": resistance,
    }
    score_calibration = dashboard.get("decision_score_calibration")
    if isinstance(score_calibration, dict):
        stability["raw_score"] = score_calibration.get("raw_score")
        stability["adjusted_score"] = score_calibration.get("adjusted_score")
        stability["final_action"] = score_calibration.get("final_action")
    dashboard["decision_stability"] = stability

    if reason and reason not in str(result.risk_warning or ""):
        sep = "; "
        result.risk_warning = f"{result.risk_warning}{sep}{reason}" if result.risk_warning else reason
    result.buy_reason = reason or result.buy_reason


def _downgrade_to_structural_hold(
    result: "AnalysisResult",
    language: str,
    *,
    advice_key: str,
    reason_key: str,
    current_price: float,
    support: Optional[float],
    resistance: Optional[float],
) -> None:
    result.decision_type = "hold"
    _set_structural_hold_wording(
        result,
        language,
        advice_key=advice_key,
        reason_key=reason_key,
        current_price=current_price,
        support=support,
        resistance=resistance,
        calibrate_score=True,
    )


def _set_structural_hold_wording(
    result: "AnalysisResult",
    language: str,
    *,
    advice_key: str,
    reason_key: str,
    current_price: float,
    support: Optional[float],
    resistance: Optional[float],
    calibrate_score: bool = False,
) -> None:
    advice_map = {
        "vi": {
            "range": "Quan sát vùng dao động",
            "shakeout": "Quan sát rũ bỏ",
            "hold": "Nắm giữ và quan sát",
        },
        "en": {
            "range": "Range-bound watch",
            "shakeout": "Shakeout watch",
            "hold": "Hold and watch",
        },
    }
    advice_default = {
        "vi": "Nắm giữ và quan sát",
        "en": "Hold and watch",
    }.get(language, "Nắm giữ và quan sát")
    advice = advice_map.get(language, advice_map["vi"]).get(advice_key, advice_default)
    reason_templates = {
        "vi": {
            "buy_near_resistance": "Giá đang sát vùng kháng cự và chưa bứt phá rõ ràng, nên việc mua đuổi nhịp hồi là không khả thi.",
            "sell_near_support": "Giá đang sát vùng hỗ trợ và chưa thủng hỗ trợ, một phiên giảm chưa đủ cơ sở để bán.",
            "hold_shakeout": "Giá lùi về sát hỗ trợ nhưng chưa thủng hỗ trợ, phù hợp xử lý như một nhịp rũ bỏ.",
            "hold_mid_range": "Giá nằm giữa hỗ trợ và kháng cự, chưa có tín hiệu rõ ràng, nên quan sát vùng dao động sẽ khả thi hơn.",
        },
        "en": {
            "buy_near_resistance": "Price is near resistance without a confirmed breakout, so chasing the rebound is not actionable.",
            "sell_near_support": "Price is near support and has not broken it, so a one-day drop is not enough to sell.",
            "hold_shakeout": "Price pulled back near support without breaking it, which is better treated as a shakeout watch.",
            "hold_mid_range": "Price is between support and resistance without a clear signal, so range-bound watch is more actionable.",
        },
    }
    reason = reason_templates.get(language, reason_templates["vi"]).get(reason_key, "")
    if calibrate_score:
        final_action = "watch" if advice_key in {"range", "shakeout"} else "hold"
        _bound_hold_watch_sentiment_score(result, reason=reason, final_action=final_action)
    result.operation_advice = advice
    if advice_key == "range":
        result.trend_prediction = "Sideways"

    # Khuyến nghị theo vị thế phải theo ngôn ngữ báo cáo như advice/reason ở trên.
    # Position advice follows the report language, like advice/reason above.
    position_templates = {
        "vi": (
            "Không mua đuổi, không bán hoảng loạn; chờ hỗ trợ được xác nhận hoặc bứt phá hợp lệ.",
            "Lấy vùng hỗ trợ chính làm ngưỡng rủi ro và quản trị tỷ trọng, trừ khi hỗ trợ bị thủng.",
        ),
        "en": (
            "Do not chase or panic; wait for support confirmation or a valid breakout.",
            "Use key support as the risk line and manage position size unless support fails.",
        ),
    }
    no_position, has_position = position_templates.get(language, position_templates["vi"])
    _apply_hold_watch_dashboard(
        result,
        language,
        advice=advice,
        reason=reason,
        current_price=current_price,
        support=support,
        resistance=resistance,
        no_position=no_position,
        has_position=has_position,
    )
    logger.info("[decision_stability] Applied structural hold calibration: %s", reason_key)


def get_stock_name_multi_source(
    stock_code: str,
    context: Optional[Dict] = None,
    data_manager = None
) -> str:
    """Lấy tên cổ phiếu từ nhiều nguồn. / Resolve the stock name from several sources.

    Thứ tự ưu tiên / Lookup order:
    1. Từ ``context`` được truyền vào (dữ liệu realtime)
    2. Từ bảng ánh xạ tĩnh ``STOCK_NAME_MAP``
    3. Từ ``DataFetcherManager`` (các nguồn dữ liệu)
    4. Trả về tên mặc định (tiền tố + mã)

    Args:
        stock_code: mã cổ phiếu
        context: ngữ cảnh phân tích (tuỳ chọn)
        data_manager: instance ``DataFetcherManager`` (tuỳ chọn)

    Returns:
        Tên cổ phiếu. / The stock name.
    """
    # 1. Lấy từ context (dữ liệu realtime). / From the context (realtime data).
    if context:
        # Ưu tiên trường stock_name. / Prefer the stock_name field.
        if context.get('stock_name'):
            name = context['stock_name']
            # Bỏ qua tên giữ chỗ (tiền tố trong PLACEHOLDER_NAME_PREFIXES).
            # Skip placeholder names (prefixes in PLACEHOLDER_NAME_PREFIXES).
            if name and not name.startswith(PLACEHOLDER_NAME_PREFIXES):
                return name

        # Kế đến là dữ liệu realtime. / Then the realtime payload.
        if 'realtime' in context and context['realtime'].get('name'):
            return context['realtime']['name']

    # 2. Lấy từ bảng ánh xạ tĩnh. / From the static mapping table.
    if stock_code in STOCK_NAME_MAP:
        return STOCK_NAME_MAP[stock_code]

    # 3. Lấy từ nguồn dữ liệu. / From the data sources.
    if data_manager is None:
        try:
            from data_provider.base import DataFetcherManager
            data_manager = DataFetcherManager()
        except Exception as e:
            logger.debug(f"Could not initialise DataFetcherManager: {e}")

    if data_manager:
        try:
            name = data_manager.get_stock_name(stock_code)
            if name:
                # Cập nhật cache. / Update the cache.
                STOCK_NAME_MAP[stock_code] = name
                return name
        except Exception as e:
            logger.debug(f"Failed to get the stock name from the data source: {e}")

    # 4. Tên mặc định. / Default placeholder name.
    return f'Cổ phiếu {stock_code}'


@dataclass
class AnalysisResult:
    """Dataclass kết quả phân tích AI - bản Decision Dashboard.

    Đóng gói kết quả LLM trả về, gồm decision dashboard và phần phân tích chi tiết.
    AI analysis result, wrapping the LLM output: decision dashboard plus detail.
    """
    code: str
    name: str

    # ========== Chỉ số cốt lõi / Core metrics ==========
    sentiment_score: int  # Điểm tổng hợp 0-100 (>70 rất tích cực, >60 tích cực, 40-60 đi ngang, <40 tiêu cực)
    trend_prediction: str  # Dự báo xu hướng / trend prediction
    operation_advice: str  # Khuyến nghị hành động / operation advice
    decision_type: str = "hold"  # Loại quyết định: buy/hold/sell (dùng cho thống kê)
    # Giá trị mặc định là nhãn tiếng Việt mà `src/report_language.py` nhận ra.
    # Default is a Vietnamese label recognized by `src/report_language.py`.
    confidence_level: str = "Trung bình"  # Độ tin cậy / confidence: Cao/Trung bình/Thấp (High/Medium/Low)
    report_language: str = "vi"  # Ngôn ngữ báo cáo / report output language
    action: Optional[str] = None  # Taxonomy hành động: buy/add/hold/reduce/sell/watch/avoid/alert
    action_label: Optional[str] = None  # Nhãn hành động đã bản địa hoá / localized action label

    # ========== Decision dashboard ==========
    dashboard: Optional[Dict[str, Any]] = None  # Toàn bộ dữ liệu dashboard / full dashboard payload

    # ========== Phân tích xu hướng / Trend analysis ==========
    trend_analysis: str = ""  # Phân tích hình thái giá (hỗ trợ, kháng cự, đường xu hướng...)
    short_term_outlook: str = ""  # Triển vọng ngắn hạn (1-3 phiên) / short-term outlook
    medium_term_outlook: str = ""  # Triển vọng trung hạn (1-2 tuần) / medium-term outlook

    # ========== Phân tích kỹ thuật / Technical analysis ==========
    technical_analysis: str = ""  # Phân tích tổng hợp chỉ báo kỹ thuật
    ma_analysis: str = ""  # Phân tích đường trung bình (sắp xếp MA, giao cắt vàng/tử)
    volume_analysis: str = ""  # Phân tích thanh khoản (tăng/giảm khối lượng, dòng tiền lớn)
    pattern_analysis: str = ""  # Phân tích mẫu hình nến / candlestick pattern analysis

    # ========== Phân tích cơ bản / Fundamental analysis ==========
    fundamental_analysis: str = ""  # Phân tích cơ bản tổng hợp
    sector_position: str = ""  # Vị thế ngành và xu hướng ngành / sector position and trend
    company_highlights: str = ""  # Điểm nhấn và rủi ro của doanh nghiệp

    # ========== Tin tức và tâm lý / News and sentiment ==========
    news_summary: str = ""  # Tóm tắt tin tức và công bố quan trọng gần đây
    market_sentiment: str = ""  # Phân tích tâm lý thị trường / market sentiment
    hot_topics: str = ""  # Chủ đề nóng liên quan / related hot topics

    # ========== Tổng hợp / Summary ==========
    analysis_summary: str = ""  # Tóm tắt phân tích tổng hợp / overall analysis summary
    key_points: str = ""  # Các điểm cốt lõi (3-5 ý) / key points
    risk_warning: str = ""  # Cảnh báo rủi ro / risk warning
    buy_reason: str = ""  # Lý do mua/bán / reasoning for the call

    # ========== Metadata ==========
    market_snapshot: Optional[Dict[str, Any]] = None  # Ảnh chụp giá trong phiên (để hiển thị)
    raw_response: Optional[str] = None  # Phản hồi gốc (để gỡ lỗi) / raw response for debugging
    search_performed: bool = False  # Có thực hiện tìm kiếm trực tuyến hay không
    # Số tin tức thực sự tìm được. ``None`` = CHƯA từng tìm (ví dụ chưa cấu hình kênh
    # tìm kiếm), ``0`` = đã tìm nhưng không có kết quả nào. Báo cáo dùng hai câu công bố
    # khác nhau cho hai nguyên nhân này.
    # Number of news hits. ``None`` = never searched; ``0`` = searched, zero hits.
    news_result_count: Optional[int] = None
    # Bản ghi lịch sử cũ không lưu ``news_result_count``; khi dựng lại phải phân biệt
    # với ``None`` tường minh, nếu không sẽ báo nhầm dữ liệu cũ thành "chưa cấu hình
    # kênh tìm kiếm". Phân tích thời gian thực luôn tin được giá trị mặc định.
    news_result_count_known: bool = True
    # Lần phân tích này có thực sự nhận được bằng chứng tin tức (``news_context``) hay không.
    # ``news_result_count`` chỉ trả lời "tìm kiếm trực tuyến được mấy tin", còn phần công bố
    # khẳng định "kết luận có dùng bằng chứng tin tức hay không" — hai mệnh đề khác nhau:
    # ``news_context`` còn có thể đến từ tâm lý mạng xã hội hoặc kho tin đã lưu cục bộ, vốn
    # cũng vào đầu vào của mô hình mà không tạo ra lượt tìm kiếm nào. Chỉ nhìn con số đếm sẽ
    # báo nhầm những lần phân tích đó thành "không dùng bằng chứng tin tức".
    news_evidence_present: bool = False
    data_sources: str = ""  # Mô tả nguồn dữ liệu / data source description
    success: bool = True
    error_message: Optional[str] = None

    # ========== Dữ liệu giá (ảnh chụp lúc phân tích) / Price snapshot at analysis time ==========
    current_price: Optional[float] = None  # Giá tại thời điểm phân tích
    change_pct: Optional[float] = None     # Biến động (%) tại thời điểm phân tích

    # ========== Dấu vết mô hình (Issue #528) / Model marker ==========
    model_used: Optional[str] = None  # Mô hình LLM đã dùng (tên đầy đủ, vd gemini/gemini-2.0-flash)

    # ========== So sánh lịch sử (Report Engine P0) / History comparison ==========
    query_id: Optional[str] = None  # query_id của lần phân tích này, để loại chính nó khi so sánh

    # ========== Ngữ cảnh cơ bản (chỉ runtime, dùng ghép thông báo; KHÔNG lưu vào to_dict) ==========
    fundamental_context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Chuyển thành dict. / Convert to a dict."""
        return {
            'code': self.code,
            'name': self.name,
            'sentiment_score': self.sentiment_score,
            'trend_prediction': self.trend_prediction,
            'operation_advice': self.operation_advice,
            'decision_type': self.decision_type,
            'confidence_level': self.confidence_level,
            'report_language': self.report_language,
            'action': self.action,
            'action_label': self.action_label,
            'dashboard': self.dashboard,  # decision dashboard data
            'trend_analysis': self.trend_analysis,
            'short_term_outlook': self.short_term_outlook,
            'medium_term_outlook': self.medium_term_outlook,
            'technical_analysis': self.technical_analysis,
            'ma_analysis': self.ma_analysis,
            'volume_analysis': self.volume_analysis,
            'pattern_analysis': self.pattern_analysis,
            'fundamental_analysis': self.fundamental_analysis,
            'sector_position': self.sector_position,
            'company_highlights': self.company_highlights,
            'news_summary': self.news_summary,
            'market_sentiment': self.market_sentiment,
            'hot_topics': self.hot_topics,
            'analysis_summary': self.analysis_summary,
            'key_points': self.key_points,
            'risk_warning': self.risk_warning,
            'buy_reason': self.buy_reason,
            'market_snapshot': self.market_snapshot,
            'search_performed': self.search_performed,
            'news_result_count': self.news_result_count,
            'news_result_count_known': self.news_result_count_known,
            'news_evidence_present': self.news_evidence_present,
            'success': self.success,
            'error_message': self.error_message,
            'current_price': self.current_price,
            'change_pct': self.change_pct,
            'model_used': self.model_used,
        }

    def get_core_conclusion(self) -> str:
        """Lấy kết luận cốt lõi (một câu). / Core conclusion, one sentence."""
        if self.dashboard and 'core_conclusion' in self.dashboard:
            return self.dashboard['core_conclusion'].get('one_sentence', self.analysis_summary)
        return self.analysis_summary

    def get_position_advice(self, has_position: bool = False) -> str:
        """Lấy khuyến nghị theo trạng thái nắm giữ. / Position-based advice."""
        if self.dashboard and 'core_conclusion' in self.dashboard:
            pos_advice = self.dashboard['core_conclusion'].get('position_advice', {})
            if has_position:
                return pos_advice.get('has_position', self.operation_advice)
            return pos_advice.get('no_position', self.operation_advice)
        return self.operation_advice

    def get_sniper_points(self) -> Dict[str, str]:
        """Lấy các mốc giá vào/ra. / Entry, stop-loss and target levels."""
        if self.dashboard and 'battle_plan' in self.dashboard:
            return self.dashboard['battle_plan'].get('sniper_points', {})
        return {}

    def get_checklist(self) -> List[str]:
        """Lấy checklist kiểm tra. / The action checklist."""
        if self.dashboard and 'battle_plan' in self.dashboard:
            return self.dashboard['battle_plan'].get('action_checklist', [])
        return []

    def get_risk_alerts(self) -> List[str]:
        """Lấy danh sách cảnh báo rủi ro. / The risk alerts."""
        if self.dashboard and 'intelligence' in self.dashboard:
            return self.dashboard['intelligence'].get('risk_alerts', [])
        return []

    def get_emoji(self) -> str:
        """Trả emoji tương ứng khuyến nghị. / Emoji for the operation advice."""
        _, emoji, _ = get_signal_level(
            self.operation_advice,
            self.sentiment_score,
            self.report_language,
        )
        return emoji

    def get_confidence_stars(self) -> str:
        """Trả số sao theo độ tin cậy. / Star rating for the confidence level."""
        star_map = {
            "cao": "⭐⭐⭐",
            "high": "⭐⭐⭐",
            "trung bình": "⭐⭐",
            "medium": "⭐⭐",
            "thấp": "⭐",
            "low": "⭐",
        }
        return star_map.get(str(self.confidence_level or "").strip().lower(), "⭐⭐")


def populate_decision_action_fields(
    result: AnalysisResult,
    *,
    explicit_action: Any = None,
    report_type: Any = None,
    use_existing_action: bool = True,
    align_with_score: bool = True,
) -> AnalysisResult:
    """Populate optional decision action fields without changing legacy advice."""

    action_source = explicit_action
    if action_source is None and use_existing_action:
        action_source = getattr(result, "action", None)

    fields = build_action_fields(
        operation_advice=getattr(result, "operation_advice", None),
        explicit_action=action_source,
        report_type=report_type,
        report_language=getattr(result, "report_language", "vi"),
        sentiment_score=getattr(result, "sentiment_score", None),
        guardrail_reason=getattr(result, "guardrail_reason", None),
        align_with_score=align_with_score,
    )
    result.action = fields["action"]
    result.action_label = fields["action_label"]
    return result


class GeminiAnalyzer:
    """Bộ phân tích bằng LLM. / LLM-backed analyzer.

    Trách nhiệm / Responsibilities:
    1. Gọi API LLM để phân tích cổ phiếu
    2. Kết hợp tin tức đã tìm sẵn với dữ liệu kỹ thuật để sinh báo cáo
    3. Parse kết quả JSON mà mô hình trả về

    Cách dùng / Usage:
        analyzer = GeminiAnalyzer()
        result = analyzer.analyze(context, news_context)
    """

    # ========================================
    # Prompt hệ thống - Decision Dashboard v2.0 / System prompts.
    # ========================================
    # Định dạng đầu ra: từ tín hiệu đơn giản nâng lên thành decision dashboard.
    # Khối chính: kết luận cốt lõi + soi dữ liệu + tin tức + kế hoạch hành động.
    # ========================================

    # Prompt mặc định (bản cũ) — dùng khi không có skill nào được chọn tường minh.
    # Legacy default prompt, used when no skill is explicitly selected.
    # Ngôn ngữ đầu ra KHÔNG do prompt này quyết định: xem `_get_analysis_system_prompt`.
    # Output language is NOT decided here; see `_get_analysis_system_prompt`.
    LEGACY_DEFAULT_SYSTEM_PROMPT = """You are a trend-trading {market_placeholder} investment analyst. You produce a professional [Decision Dashboard] analysis report.

{guidelines_placeholder}

""" + CORE_TRADING_SKILL_POLICY_EN + """

""" + CANONICAL_DECISION_SCALE_PROMPT_EN + """

## Output Format: Decision Dashboard JSON

Follow the JSON structure below exactly. It is one complete [Decision Dashboard]:

```json
{
    "stock_name": "Full company name",
    "sentiment_score": integer 0-100,
    "trend_prediction": "Strong Bullish/Bullish/Sideways/Bearish/Strong Bearish",
    "operation_advice": "Buy/Add Position/Hold/Reduce/Sell/Watch",
    "decision_type": "buy/hold/sell",
    "action": "buy/add/hold/reduce/sell/watch/avoid/alert",
    "guardrail_reason": "Why the score band was downgraded or upgraded when it disagrees with the final action; leave empty otherwise",
    "confidence_level": "High/Medium/Low",

    "dashboard": {
        "core_conclusion": {
            "one_sentence": "One-sentence core conclusion (under 30 words, tell the user exactly what to do)",
            "signal_type": "🟢 Buy Signal/🟡 Hold and Watch/🔴 Sell Signal/⚠️ Risk Alert",
            "time_sensitivity": "Act now/Today/This week/Not urgent",
            "position_advice": {
                "no_position": "Advice for a flat position: concrete instructions",
                "has_position": "Advice for an existing position: concrete instructions"
            }
        },

        "data_perspective": {
            "trend_status": {
                "ma_alignment": "Description of the moving-average alignment",
                "is_bullish": true/false,
                "trend_score": 0-100
            },
            "price_position": {
                "current_price": current price value,
                "ma5": MA5 value,
                "ma10": MA10 value,
                "ma20": MA20 value,
                "bias_ma5": bias percentage value,
                "bias_status": "Safe/Caution/Danger",
                "support_level": support price,
                "resistance_level": resistance price
            },
            "volume_analysis": {
                "volume_ratio": volume ratio value,
                "volume_status": "Expanding volume/Shrinking volume/Normal volume",
                "turnover_rate": turnover rate percentage,
                "volume_meaning": "What the volume means (e.g. a low-volume pullback means selling pressure is easing)"
            }
        },

        "intelligence": {
            "latest_news": "Summary of recent important news",
            "risk_alerts": ["Risk 1: concrete description", "Risk 2: concrete description"],
            "positive_catalysts": ["Catalyst 1: concrete description", "Catalyst 2: concrete description"],
            "earnings_outlook": "Earnings outlook (based on guidance, preliminary results, etc.)",
            "sentiment_summary": "One-sentence summary of news sentiment"
        },

        "battle_plan": {
            "sniper_points": {
                "ideal_buy": "Ideal entry: XX (near MA5)",
                "secondary_buy": "Secondary entry: XX (near MA10)",
                "stop_loss": "Stop loss: XX (below MA20 or X%)",
                "take_profit": "Target: XX (prior high / round number)"
            },
            "position_strategy": {
                "suggested_position": "Suggested position size: X0% of capital",
                "entry_plan": "Scaled entry plan",
                "risk_control": "Risk-control plan"
            },
            "action_checklist": [
                "✅/⚠️/❌ Check 1: bullish alignment",
                "✅/⚠️/❌ Check 2: bias is reasonable (may be relaxed in a strong trend)",
                "✅/⚠️/❌ Check 3: volume confirms",
                "✅/⚠️/❌ Check 4: no major negative news",
                "✅/⚠️/❌ Check 5: PE valuation is reasonable"
            ]
        },

        "phase_decision": {
            "phase_context": {"phase": "premarket/intraday/lunch_break/closing_auction/postmarket/non_trading/unknown"},
            "action_window": "Pre-market plan/Intraday tracking/Midday confirmation/Pre-close risk control/Post-close review/Non-trading-day watch",
            "immediate_action": "Act now/Wait for confirmation/Watch/Stop-loss or take-profit alert/Do not chase/No intraday action",
            "watch_conditions": ["Watch condition 1", "Watch condition 2"],
            "next_check_time": "Next checkpoint, or the market's local time",
            "confidence_reason": "Why this confidence level, covering session-phase and data-quality limits",
            "data_limitations": ["Phase or data-quality limitation 1", "Phase or data-quality limitation 2"]
        },

        "signal_attribution": {
            "technical_indicators": technical-indicator contribution (0-100),
            "news_sentiment": news-sentiment contribution (0-100),
            "fundamentals": fundamentals contribution (0-100),
            "market_conditions": market-environment contribution (0-100),
            "strongest_bullish_signal": "Name of the strongest bullish signal",
            "strongest_bearish_signal": "Name of the strongest bearish signal"
        }
    },

    "analysis_summary": "Around 100 words of overall analysis",
    "key_points": "3-5 key points, comma separated",
    "risk_warning": "Risk warning",
    "buy_reason": "Reasoning for the call, citing the trading principles",

    "trend_analysis": "Price-structure analysis",
    "short_term_outlook": "Short-term outlook (1-3 days)",
    "medium_term_outlook": "Medium-term outlook (1-2 weeks)",
    "technical_analysis": "Overall technical analysis",
    "ma_analysis": "Moving-average analysis",
    "volume_analysis": "Volume analysis",
    "pattern_analysis": "Candlestick pattern analysis",
    "fundamental_analysis": "Fundamental analysis",
    "sector_position": "Sector and industry analysis",
    "company_highlights": "Company highlights and risks",
    "news_summary": "News summary",
    "market_sentiment": "Market sentiment",
    "hot_topics": "Related hot topics",

    "search_performed": true/false,
    "data_sources": "Description of the data sources"
}
```

## Scoring Rubric

### Strong Buy (80-100):
- ✅ Bullish alignment: MA5 > MA10 > MA20
- ✅ Low bias: <2%, the ideal entry
- ✅ Low-volume pullback or high-volume breakout
- ✅ Positive news catalyst

### Buy (60-79):
- ✅ Bullish alignment, or weak bullish
- ✅ Bias <5%
- ✅ Normal volume
- ⚪ One secondary condition may be unmet

### Watch (40-59):
- ⚠️ Bias >5% (risk of chasing)
- ⚠️ Moving averages are tangled, trend unclear
- ⚠️ A risk event is present

### Reduce (20-39):
- ⚠️ Trend weakening or a key moving average broken
- ⚠️ Capital flow / volume turning weak, risk clearly outweighs reward
- ⚠️ Focus on cutting exposure and protecting gains

### Sell (0-19):
- ❌ Bearish alignment or a clearly deteriorating trend
- ❌ Key support or the stop-loss level is broken
- ❌ High-volume decline or major negative news

## Core Principles of the Decision Dashboard

1. **Conclusion first**: say plainly whether to buy or sell
2. **Split by position**: give different advice to holders and non-holders
3. **Precise entry points**: always give concrete prices, never vague wording
4. **Visual checklist**: use ✅⚠️❌ to show the result of every check
5. **Risk priority**: make risk items from the news stand out

## Actionability and Stability Constraints

- Do not swing violently between "Buy" and "Sell" merely because of a single day's move or a score crossing a band boundary.
- The recommendation must consider price position (support/resistance), volume, and risk events together.
- When price sits between support and resistance and capital flow is unclear, prefer an actionable neutral call such as "Hold / Sideways / Watch / Shakeout watch"; `decision_type` stays `hold`.
- Only call a buy when price is near confirmed support or has broken resistance decisively, with capital flow and price-volume confirming. Never chase near resistance while capital is flowing out.
- Only call a sell or reduce when key support breaks, main-force capital keeps flowing out, or risk clearly escalates.
- You must output all seven `dashboard.phase_decision` fields. Intraday, at lunch break, and near the close, give the current action, the watch conditions, and the next checkpoint.
- You should output the six optional `dashboard.signal_attribution` fields, explaining how the recommendation breaks down across technical indicators, news sentiment, fundamentals, and market environment, plus the strongest bullish and bearish signals.
- Never fabricate today's intraday move during pre-market, a non-trading day, or an unknown phase. When quote/daily_bars/technical are stale, fallback, missing, fetch_failed, partial, or estimated, `confidence_level` must not be High."""

    # Prompt mặc định hiện hành — dùng khi có skill được chọn tường minh.
    # Current default prompt, used when a skill is explicitly selected.
    SYSTEM_PROMPT = """You are a {market_placeholder} investment analyst. You produce a professional [Decision Dashboard] analysis report.

{guidelines_placeholder}

{default_skill_policy_section}
{skills_section}

""" + CANONICAL_DECISION_SCALE_PROMPT_EN + """

## Output Format: Decision Dashboard JSON

Follow the JSON structure below exactly. It is one complete [Decision Dashboard]:

```json
{
    "stock_name": "Full company name",
    "sentiment_score": integer 0-100,
    "trend_prediction": "Strong Bullish/Bullish/Sideways/Bearish/Strong Bearish",
    "operation_advice": "Buy/Add Position/Hold/Reduce/Sell/Watch",
    "decision_type": "buy/hold/sell",
    "action": "buy/add/hold/reduce/sell/watch/avoid/alert",
    "guardrail_reason": "Why the score band was downgraded or upgraded when it disagrees with the final action; leave empty otherwise",
    "confidence_level": "High/Medium/Low",

    "dashboard": {
        "core_conclusion": {
            "one_sentence": "One-sentence core conclusion (under 30 words, tell the user exactly what to do)",
            "signal_type": "🟢 Buy Signal/🟡 Hold and Watch/🔴 Sell Signal/⚠️ Risk Alert",
            "time_sensitivity": "Act now/Today/This week/Not urgent",
            "position_advice": {
                "no_position": "Advice for a flat position: concrete instructions",
                "has_position": "Advice for an existing position: concrete instructions"
            }
        },

        "data_perspective": {
            "trend_status": {
                "ma_alignment": "Description of the moving-average alignment",
                "is_bullish": true/false,
                "trend_score": 0-100
            },
            "price_position": {
                "current_price": current price value,
                "ma5": MA5 value,
                "ma10": MA10 value,
                "ma20": MA20 value,
                "bias_ma5": bias percentage value,
                "bias_status": "Safe/Caution/Danger",
                "support_level": support price,
                "resistance_level": resistance price
            },
            "volume_analysis": {
                "volume_ratio": volume ratio value,
                "volume_status": "Expanding volume/Shrinking volume/Normal volume",
                "turnover_rate": turnover rate percentage,
                "volume_meaning": "What the volume means (e.g. a low-volume pullback means selling pressure is easing)"
            }
        },

        "intelligence": {
            "latest_news": "Summary of recent important news",
            "risk_alerts": ["Risk 1: concrete description", "Risk 2: concrete description"],
            "positive_catalysts": ["Catalyst 1: concrete description", "Catalyst 2: concrete description"],
            "earnings_outlook": "Earnings outlook (based on guidance, preliminary results, etc.)",
            "sentiment_summary": "One-sentence summary of news sentiment"
        },

        "battle_plan": {
            "sniper_points": {
                "ideal_buy": "Ideal entry: XX (meets the primary skill trigger)",
                "secondary_buy": "Secondary entry: XX (more conservative, or after confirmation)",
                "stop_loss": "Stop loss: XX (invalidation condition or X% risk)",
                "take_profit": "Target: XX (set from resistance / risk-reward ratio)"
            },
            "position_strategy": {
                "suggested_position": "Suggested position size: X0% of capital",
                "entry_plan": "Scaled entry plan",
                "risk_control": "Risk-control plan"
            },
            "action_checklist": [
                "✅/⚠️/❌ Check 1: does the current structure meet the activated skill's conditions",
                "✅/⚠️/❌ Check 2: are the entry level and risk-reward reasonable",
                "✅/⚠️/❌ Check 3: do price-volume and volatility support the call",
                "✅/⚠️/❌ Check 4: no major negative news",
                "✅/⚠️/❌ Check 5: position size and stop-loss plan are explicit",
                "✅/⚠️/❌ Check 6: valuation, earnings and catalysts match the conclusion"
            ]
        },

        "phase_decision": {
            "phase_context": {"phase": "premarket/intraday/lunch_break/closing_auction/postmarket/non_trading/unknown"},
            "action_window": "Pre-market plan/Intraday tracking/Midday confirmation/Pre-close risk control/Post-close review/Non-trading-day watch",
            "immediate_action": "Act now/Wait for confirmation/Watch/Stop-loss or take-profit alert/Do not chase/No intraday action",
            "watch_conditions": ["Watch condition 1", "Watch condition 2"],
            "next_check_time": "Next checkpoint, or the market's local time",
            "confidence_reason": "Why this confidence level, covering session-phase and data-quality limits",
            "data_limitations": ["Phase or data-quality limitation 1", "Phase or data-quality limitation 2"]
        },

        "signal_attribution": {
            "technical_indicators": technical-indicator contribution (0-100),
            "news_sentiment": news-sentiment contribution (0-100),
            "fundamentals": fundamentals contribution (0-100),
            "market_conditions": market-environment contribution (0-100),
            "strongest_bullish_signal": "Name of the strongest bullish signal",
            "strongest_bearish_signal": "Name of the strongest bearish signal"
        }
    },

    "analysis_summary": "Around 100 words of overall analysis",
    "key_points": "3-5 key points, comma separated",
    "risk_warning": "Risk warning",
    "buy_reason": "Reasoning for the call, citing the activated skill or the risk framework",

    "trend_analysis": "Price-structure analysis",
    "short_term_outlook": "Short-term outlook (1-3 days)",
    "medium_term_outlook": "Medium-term outlook (1-2 weeks)",
    "technical_analysis": "Overall technical analysis",
    "ma_analysis": "Moving-average analysis",
    "volume_analysis": "Volume analysis",
    "pattern_analysis": "Candlestick pattern analysis",
    "fundamental_analysis": "Fundamental analysis",
    "sector_position": "Sector and industry analysis",
    "company_highlights": "Company highlights and risks",
    "news_summary": "News summary",
    "market_sentiment": "Market sentiment",
    "hot_topics": "Related hot topics",

    "search_performed": true/false,
    "data_sources": "Description of the data sources"
}
```

## Scoring Rubric

### Strong Buy (80-100):
- ✅ Several activated skills support the positive conclusion at once
- ✅ Upside, trigger conditions and risk-reward are all clear
- ✅ Key risks have been checked; position size and stop loss are explicit
- ✅ The important data and the intelligence agree with each other

### Buy (60-79):
- ✅ The main signal is positive, with a few items still to confirm
- ✅ A manageable risk or a secondary entry point is acceptable
- ✅ The report must state the remaining watch conditions

### Watch (40-59):
- ⚠️ Signals disagree, or there is not enough confirmation
- ⚠️ Risk and opportunity are roughly balanced
- ⚠️ Better to wait for the trigger, or to avoid the uncertainty

### Reduce (20-39):
- ⚠️ The main conclusion is weakening; risk clearly outweighs reward
- ⚠️ Some invalidation conditions have fired; existing exposure should come down
- ⚠️ Better to protect gains than to press the attack

### Sell (0-19):
- ❌ A stop-loss / invalidation condition or major negative news has fired
- ❌ The trend or the risk picture has deteriorated significantly
- ❌ Existing positions should exit first

## Core Principles of the Decision Dashboard

1. **Conclusion first**: say plainly whether to buy or sell
2. **Split by position**: give different advice to holders and non-holders
3. **Precise entry points**: always give concrete prices, never vague wording
4. **Visual checklist**: use ✅⚠️❌ to show the result of every check
5. **Risk priority**: make risk items from the news stand out

## Actionability and Stability Constraints

- Do not swing violently between "Buy" and "Sell" merely because of a single day's move or a score crossing a band boundary.
- The recommendation must consider price position (support/resistance), volume, and risk events together.
- When price sits between support and resistance and capital flow is unclear, prefer an actionable neutral call such as "Hold / Sideways / Watch / Shakeout watch"; `decision_type` stays `hold`.
- Only call a buy when price is near confirmed support or has broken resistance decisively, with capital flow and price-volume confirming. Never chase near resistance while capital is flowing out.
- Only call a sell or reduce when key support breaks, main-force capital keeps flowing out, or risk clearly escalates.
- You must output all seven `dashboard.phase_decision` fields. Intraday, at lunch break, and near the close, give the current action, the watch conditions, and the next checkpoint.
- You should output the six optional `dashboard.signal_attribution` fields, explaining how the recommendation breaks down across technical indicators, news sentiment, fundamentals, and market environment, plus the strongest bullish and bearish signals.
- Never fabricate today's intraday move during pre-market, a non-trading day, or an unknown phase. When quote/daily_bars/technical are stale, fallback, missing, fetch_failed, partial, or estimated, `confidence_level` must not be High."""

    # Prompt cho các lượt sinh văn bản tự do (không phải JSON dashboard).
    # Prompt for free-form text generation, not the JSON dashboard path.
    TEXT_SYSTEM_PROMPT = """You are a professional stock analysis assistant.

- Every answer must be grounded in the data and context the user provides
- When the information is insufficient, state the uncertainty explicitly
- Never invent prices, financial results, or news facts
"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        config: Optional[Config] = None,
        skills: Optional[List[str]] = None,
        skill_instructions: Optional[str] = None,
        default_skill_policy: Optional[str] = None,
        use_legacy_default_prompt: Optional[bool] = None,
    ):
        """Initialize LLM Analyzer via LiteLLM.

        Args:
            api_key: Ignored (kept for backward compatibility). Keys are loaded from config.
        """
        self._config_override = config
        self._requested_skills = list(skills) if skills is not None else None
        self._skill_instructions_override = skill_instructions
        self._default_skill_policy_override = default_skill_policy
        self._use_legacy_default_prompt_override = use_legacy_default_prompt
        self._resolved_prompt_state: Optional[Dict[str, Any]] = None
        self._router = None
        self._legacy_router_model_list: List[Dict[str, Any]] = []
        self._litellm_available = False
        self._init_litellm()
        if not self._litellm_available:
            try:
                backend_id, _fallback_backend_id = self._resolve_generation_backend_config()
            except GenerationError:
                backend_id = ""
            if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
                logger.info(
                    "Analyzer generation backend: %s configured; LiteLLM API keys are not "
                    "required for stock analysis generation",
                    backend_id,
                )
            else:
                logger.warning("No LLM configured (LITELLM_MODEL / API keys), AI analysis will be unavailable")

    def _get_runtime_config(self) -> Config:
        """Return the runtime config, honoring injected overrides for tests/pipeline."""
        return getattr(self, "_config_override", None) or get_config()

    def _get_skill_prompt_sections(self) -> tuple[str, str, bool]:
        """Resolve skill instructions + default baseline + prompt mode."""
        skill_instructions = getattr(self, "_skill_instructions_override", None)
        default_skill_policy = getattr(self, "_default_skill_policy_override", None)
        use_legacy_default_prompt = getattr(self, "_use_legacy_default_prompt_override", None)

        if skill_instructions is not None and default_skill_policy is not None:
            return (
                skill_instructions,
                default_skill_policy,
                bool(use_legacy_default_prompt) if use_legacy_default_prompt is not None else False,
            )

        resolved_state = getattr(self, "_resolved_prompt_state", None)
        if resolved_state is None:
            from src.agent.factory import resolve_skill_prompt_state

            prompt_state = resolve_skill_prompt_state(
                self._get_runtime_config(),
                skills=getattr(self, "_requested_skills", None),
            )
            resolved_state = {
                "skill_instructions": prompt_state.skill_instructions,
                "default_skill_policy": prompt_state.default_skill_policy,
                "use_legacy_default_prompt": bool(getattr(prompt_state, "use_legacy_default_prompt", False)),
            }
            self._resolved_prompt_state = resolved_state

        return (
            skill_instructions if skill_instructions is not None else resolved_state.get("skill_instructions", ""),
            default_skill_policy if default_skill_policy is not None else resolved_state.get("default_skill_policy", ""),
            (
                use_legacy_default_prompt
                if use_legacy_default_prompt is not None
                else bool(resolved_state.get("use_legacy_default_prompt", False))
            ),
        )

    def _get_analysis_system_prompt(self, report_language: str, stock_code: str = "") -> str:
        """Build the analyzer system prompt with output-language guidance."""
        lang = normalize_report_language(report_language)
        market_role = get_market_role(stock_code, lang)
        market_guidelines = get_market_guidelines(stock_code, lang)
        skill_instructions, default_skill_policy, use_legacy_default_prompt = self._get_skill_prompt_sections()
        if use_legacy_default_prompt:
            base_prompt = self.LEGACY_DEFAULT_SYSTEM_PROMPT.replace(
                "{market_placeholder}", market_role
            ).replace(
                "{guidelines_placeholder}", market_guidelines
            )
        else:
            skills_section = ""
            if skill_instructions:
                skills_section = f"## Active Trading Skills\n\n{skill_instructions}\n"
            default_skill_policy_section = ""
            if default_skill_policy:
                default_skill_policy_section = f"{default_skill_policy}\n"
            base_prompt = (
                self.SYSTEM_PROMPT.replace("{market_placeholder}", market_role)
                .replace("{guidelines_placeholder}", market_guidelines)
                .replace("{default_skill_policy_section}", default_skill_policy_section)
                .replace("{skills_section}", skills_section)
            )
        if lang == "vi":
            return base_prompt + """

## Output Language (highest priority)

- Keep all JSON keys unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in Vietnamese (Tiếng Việt), with correct diacritics.
- Use standard Vietnamese securities terminology (mua / nắm giữ / bán, hỗ trợ / kháng cự, cắt lỗ, khối lượng).
- Use the company's common Vietnamese name when you are confident; otherwise keep the listed name instead of inventing one.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, nested dashboard text, checklist items, and all narrative summaries.
- The sample values inside the JSON schema above (for example `Expanding volume/Shrinking volume/Normal volume`) are illustrative English placeholders. Never copy them verbatim — write the Vietnamese equivalent instead.
"""
        if lang == "en":
            return base_prompt + """

## Output Language (highest priority)

- Keep all JSON keys unchanged.
- `decision_type` must remain `buy|hold|sell`.
- All human-readable JSON values must be written in English.
- Use the common English company name when you are confident; otherwise keep the original listed company name instead of inventing one.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, nested dashboard text, checklist items, and all narrative summaries.
"""
        # Mọi ngôn ngữ khác 'en' đã được chuẩn hoá về 'vi' ở trên.
        # Every non-'en' language is normalized to 'vi' above.
        return base_prompt

    def _has_channel_config(self, config: Config) -> bool:
        """Check if multi-channel config (channels / YAML / legacy model_list) is active."""
        return bool(config.llm_model_list) and not all(
            e.get('model_name', '').startswith('__legacy_') for e in config.llm_model_list
        )

    @staticmethod
    def _legacy_router_provider_alias(model: str) -> str:
        provider = model.split("/", 1)[0] if "/" in model else "openai"
        return f"__legacy_{provider}__"

    @staticmethod
    def _build_legacy_router_model_list_from_config(
        model: str,
        model_list: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Build legacy-router candidates from configured legacy llm_model_list entries."""
        if not model:
            return []
        target_model = model
        target_legacy_alias = GeminiAnalyzer._legacy_router_provider_alias(model)
        legacy_entries: List[Dict[str, Any]] = []
        for entry in model_list or []:
            if not isinstance(entry, dict):
                continue
            model_name = str(entry.get("model_name") or "").strip()
            if model_name != target_legacy_alias:
                continue

            params = entry.get("litellm_params")
            if not isinstance(params, dict):
                continue

            api_key = str(params.get("api_key") or "").strip()
            if not api_key or len(api_key) < 8:
                continue

            deployed_params = dict(params)
            deployed_params["model"] = target_model
            deployed_params["api_key"] = api_key
            legacy_entries.append({
                "model_name": target_model,
                "litellm_params": deployed_params,
            })

        return legacy_entries

    def _init_litellm(self) -> None:
        """Initialize litellm Router from channels / YAML / legacy keys."""
        config = self._get_runtime_config()
        if self._get_hermes_config_error(config) is not None:
            logger.error("Analyzer LLM: Hermes channel configuration blocks legacy fallback")
            return
        litellm_model = config.litellm_model
        if not litellm_model:
            backend_id = ""
            try:
                backend_id = resolve_generation_backend_id(config)
            except GenerationError:
                pass
            if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
                logger.info(
                    "Analyzer LiteLLM: LITELLM_MODEL not configured; using %s generation backend",
                    backend_id,
                )
            else:
                logger.warning("Analyzer LLM: LITELLM_MODEL not configured")
            return

        self._litellm_available = True

        # --- Channel / YAML path: build Router from pre-built model_list ---
        if self._has_channel_config(config):
            model_list = config.llm_model_list
            if self._get_mixed_hermes_route_error(config, litellm_model) is not None:
                self._litellm_available = False
                logger.error("Analyzer LLM: mixed Hermes/non-Hermes route requires deployment-level no-proxy support")
                return
            router_model_list = model_list
            if route_has_hermes(model_list, litellm_model):
                # Hermes-only routes are dispatched directly with a request-scoped
                # no-proxy OpenAI client. Keeping them out of Router prevents the
                # default proxy-aware transport from seeing the Hermes bearer key.
                router_model_list = filter_non_hermes_deployments(model_list)
                if not router_model_list:
                    self._litellm_available = True
                    logger.info("Analyzer LLM: Hermes-only route will use direct no-proxy completion")
                    return
            try:
                self._router = Router(
                    model_list=router_model_list,
                    routing_strategy="simple-shuffle",
                    num_retries=2,
                )
            except TypeError:
                logger.debug("Analyzer LLM: Router constructor signature not compatible; fallback to direct mode")
                self._router = None
            else:
                unique_models = list(dict.fromkeys(
                    e['litellm_params']['model'] for e in model_list
                ))
                logger.info(
                    f"Analyzer LLM: Router initialized from channels/YAML — "
                    f"{len(router_model_list)} deployment(s), models: {unique_models}"
                )
                return

        # --- Legacy path: build Router for multi-key, or use single key ---
        keys = get_api_keys_for_model(litellm_model, config)
        legacy_model_list = self._build_legacy_router_model_list_from_config(
            litellm_model,
            config.llm_model_list,
        )
        if len(legacy_model_list) <= 1 and keys:
            extra_params = extra_litellm_params(litellm_model, config)
            configured_model_list = [
                {
                    "model_name": litellm_model,
                    "litellm_params": {
                        "model": litellm_model,
                        "api_key": k,
                        **extra_params,
                    },
                }
                for k in keys
            ]
            if not legacy_model_list:
                legacy_model_list = configured_model_list
            elif len(legacy_model_list) < len(configured_model_list):
                legacy_model_list = configured_model_list

        if len(legacy_model_list) > 1:
            self._legacy_router_model_list = legacy_model_list
            try:
                self._router = Router(
                    model_list=legacy_model_list,
                    routing_strategy="simple-shuffle",
                    num_retries=2,
                )
            except TypeError:
                logger.debug("Analyzer LLM: Legacy Router constructor signature not compatible; using legacy model_list fallback")
                self._router = None
            else:
                logger.info(
                    f"Analyzer LLM: Legacy Router initialized with {len(legacy_model_list)} keys "
                    f"for {litellm_model}"
                )
                return

        if keys:
            logger.info(f"Analyzer LLM: litellm initialized (model={litellm_model})")
        else:
            logger.info(
                f"Analyzer LLM: litellm initialized (model={litellm_model}, "
                f"API key from environment)"
            )

    def is_available(self) -> bool:
        """Check whether the configured generation backend is available."""
        backend_error = self.get_generation_backend_config_error()
        if backend_error is not None:
            return self._can_use_generation_fallback(backend_error)
        backend_id, _fallback_backend_id = self._resolve_generation_backend_config()
        if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
            return True
        return self._litellm_runtime_available()

    def _litellm_runtime_available(self) -> bool:
        return self._router is not None or self._litellm_available

    def _can_use_generation_fallback(self, backend_error: GenerationError) -> bool:
        if not backend_error.fallbackable:
            return False
        try:
            _backend_id, fallback_backend_id = self._resolve_generation_backend_config()
        except GenerationError:
            return False
        return (
            fallback_backend_id == LITELLM_BACKEND_ID
            and self._litellm_runtime_available()
        )

    def _resolve_generation_backend_config(self) -> Tuple[str, Optional[str]]:
        """Resolve and validate generation backend ids."""
        config = self._get_runtime_config()
        backend_id = resolve_generation_backend_id(config)
        fallback_backend_id = resolve_generation_fallback_backend_id(config)
        return backend_id, fallback_backend_id

    def get_generation_backend_config_error(self) -> Optional[GenerationError]:
        """Return a structured backend config error, if the backend cannot run."""
        try:
            backend_id, _fallback_backend_id = self._resolve_generation_backend_config()
            config = self._get_runtime_config()
            hermes_error = self._get_hermes_config_error(config)
            if hermes_error is not None:
                return hermes_error
            for model in [getattr(config, "litellm_model", "")] + list(getattr(config, "litellm_fallback_models", []) or []):
                mixed_error = self._get_mixed_hermes_route_error(config, model)
                if mixed_error is not None:
                    return mixed_error
            if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
                backend = self._get_generation_backend(backend_id)
                get_config_error = getattr(backend, "get_config_error", None)
                if callable(get_config_error):
                    return get_config_error()
        except GenerationError as exc:
            return exc
        return None

    def _get_hermes_config_error(self, config: Config) -> Optional[GenerationError]:
        issues = list(getattr(config, "llm_channel_config_issues", []) or [])
        if not getattr(config, "llm_blocks_legacy_fallback", False) or not issues:
            return None
        blocked_routes = set(getattr(config, "llm_blocked_hermes_routes", []) or [])
        selected_models = [
            ("LITELLM_MODEL", getattr(config, "litellm_model", "") or ""),
            *[
                ("LITELLM_FALLBACK_MODELS", fallback_model)
                for fallback_model in list(getattr(config, "litellm_fallback_models", []) or [])
            ],
        ]
        selected_blocked_route = ""
        selected_field = ""
        for field_name, model in selected_models:
            raw_model = str(model or "").strip()
            if not raw_model:
                continue
            candidates = hermes_blocked_route_candidates(raw_model)
            candidates.add(raw_model)
            try:
                candidates.add(canonicalize_hermes_model_ref(raw_model).route_model)
            except (TypeError, ValueError) as exc:
                logger.debug("Failed to canonicalize selected Hermes route candidate %r: %s", raw_model, exc)
            matched = candidates & blocked_routes
            if matched:
                selected_blocked_route = sorted(matched)[0]
                selected_field = field_name
                break
        if blocked_routes and not selected_blocked_route and getattr(config, "llm_model_list", None):
            return None
        first = issues[0]
        code = (
            "explicit_hermes_route_invalid"
            if selected_blocked_route
            else first.get("code", "invalid_hermes_channel")
        )
        return GenerationError(
            error_code=GenerationErrorCode.UNSAFE_CONFIG,
            stage="configuration",
            retryable=False,
            fallbackable=False,
            backend=LITELLM_BACKEND_ID,
            provider=HERMES_CHANNEL_NAME,
            details={
                "field": selected_field or first.get("field", "LLM_HERMES_API_KEY"),
                "code": code,
                "reason": code,
                "message": first.get("message", "Hermes channel configuration is invalid"),
                "issues": issues,
                "route_name": selected_blocked_route or None,
            },
        )

    def _get_mixed_hermes_route_error(self, config: Config, model: str) -> Optional[GenerationError]:
        if not model:
            return None
        origins = route_deployment_origins(getattr(config, "llm_model_list", []) or [], model)
        if not origins.is_mixed:
            return None
        return GenerationError(
            error_code=GenerationErrorCode.UNSAFE_CONFIG,
            stage="configuration",
            retryable=False,
            fallbackable=False,
            backend=LITELLM_BACKEND_ID,
            provider=HERMES_CHANNEL_NAME,
            details={
                "field": "LLM_CHANNELS",
                "code": "mixed_hermes_route_unsupported",
                "reason": "router_deployment_no_proxy_unavailable",
                "route_name": model,
            },
        )

    def _hermes_redaction_values_for_model(self, config: Config, model: str = "") -> set[str]:
        redactions: set[str] = set()
        deployments = list(getattr(config, "llm_model_list", []) or [])
        selected_deployments = deployments
        if model:
            origins = route_deployment_origins(deployments, model)
            selected_deployments = list(origins.hermes_deployments or [])
            if not selected_deployments and not origins.has_hermes:
                return redactions
        for deployment in selected_deployments:
            if not isinstance(deployment, dict):
                continue
            if not route_has_hermes([deployment], str(deployment.get("model_name") or "")):
                continue
            params = deployment.get("litellm_params") or {}
            if isinstance(params, dict):
                redactions.update(build_hermes_redaction_values(params.get("api_key")))
        return redactions

    def _sanitize_hermes_exception_text(
        self,
        exc: Any,
        *,
        config: Optional[Config] = None,
        model: str = "",
    ) -> str:
        runtime_config = config or self._get_runtime_config()
        redactions = self._hermes_redaction_values_for_model(runtime_config, model)
        if not redactions:
            return str(exc)
        return sanitize_hermes_error_text(exc, redaction_values=redactions)

    def _litellm_redaction_values_for_model(self, config: Config, model: str = "") -> set[str]:
        redactions = self._hermes_redaction_values_for_model(config, model)
        try:
            redactions.update(build_hermes_redaction_values(*get_api_keys_for_model(model, config)))
        except Exception:
            pass
        origins = route_deployment_origins(getattr(config, "llm_model_list", []) or [], model)
        for deployment in (*origins.hermes_deployments, *origins.non_hermes_deployments):
            params = deployment.get("litellm_params") if isinstance(deployment, dict) else None
            if isinstance(params, dict):
                redactions.update(build_hermes_redaction_values(params.get("api_key")))
        return redactions

    def _sanitize_litellm_exception_text(
        self,
        exc: Any,
        *,
        config: Optional[Config] = None,
        model: str = "",
    ) -> str:
        runtime_config = config or self._get_runtime_config()
        redactions = self._litellm_redaction_values_for_model(runtime_config, model)
        sanitized = sanitize_hermes_error_text(exc, redaction_values=redactions)
        return redact_diagnostic_text(sanitized, limit=500)

    def _dispatch_litellm_completion(
        self,
        model: str,
        call_kwargs: Dict[str, Any],
        *,
        config: Config,
        use_channel_router: bool,
        router_model_names: set[str],
    ) -> Any:
        """Dispatch a LiteLLM completion through router or direct fallback."""
        origins = route_deployment_origins(config.llm_model_list, model)
        if origins.is_mixed:
            raise RuntimeError("Hermes/non-Hermes mixed generation route is not supported without deployment-level no-proxy client support")
        if origins.is_hermes_only:
            deployment = origins.hermes_deployments[0]
            params = dict(deployment.get("litellm_params") or {})
            api_key = str(params.get("api_key") or "").strip()
            base_url = str(params.get("api_base") or "").strip()
            if is_masked_secret_placeholder(api_key):
                raise RuntimeError("Hermes API key is a masked placeholder and cannot be used for generation")
            timeout = float(call_kwargs.get("timeout") or 30.0)
            hermes_kwargs = dict(call_kwargs)
            hermes_kwargs["model"] = str(params.get("model") or model)
            hermes_kwargs["stream"] = False
            hermes_kwargs.pop("api_key", None)
            hermes_kwargs.pop("api_base", None)
            with open_hermes_no_proxy_client(api_key=api_key, base_url=base_url, timeout=timeout) as client:
                hermes_kwargs["client"] = client
                return litellm.completion(**hermes_kwargs)

        wire_models = resolve_fallback_litellm_wire_models(model, config.llm_model_list)
        register_fallback_model_pricing(wire_models)
        effective_kwargs = dict(call_kwargs)
        if use_channel_router and self._router and model in router_model_names:
            return self._router.completion(**effective_kwargs)
        if self._router and model == config.litellm_model and not use_channel_router:
            return self._router.completion(**effective_kwargs)

        keys = get_api_keys_for_model(model, config)
        if keys:
            effective_kwargs["api_key"] = keys[0]
        effective_kwargs.update(extra_litellm_params(model, config))
        return litellm.completion(**effective_kwargs)

    def _normalize_usage(
        self,
        usage_obj: Any,
        *,
        model: str = "",
        provider: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Normalize usage objects from LiteLLM responses/chunks."""
        if not usage_obj:
            usage = attach_message_hmacs({}, messages) if messages is not None else {}
            return filter_prompt_cache_telemetry(usage, self._get_runtime_config())
        usage = normalize_litellm_usage(usage_obj, model=model, provider=provider)
        if messages is not None:
            usage = attach_message_hmacs(usage, messages)
        return filter_prompt_cache_telemetry(usage, self._get_runtime_config())

    @staticmethod
    def _get_response_field(obj: Any, key: str) -> Any:
        """Read a field from dict-like or object-like LiteLLM payloads."""
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    @staticmethod
    def _resolve_configured_response_provider(
        configured_model: str,
        response_model: str,
        model_list: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Match the actual response model against all deployments of one alias."""
        normalized_configured_model = str(configured_model or "").strip()
        normalized_response_model = str(response_model or "").strip().lower()
        if not normalized_configured_model or not normalized_response_model or not model_list:
            return ""

        for entry in model_list:
            params = entry.get("litellm_params", {}) or {}
            model_name = str(entry.get("model_name") or "").strip()
            if not model_name:
                model_name = str(params.get("model") or "").strip()
            if model_name != normalized_configured_model:
                continue

            deployment_model = str(params.get("model") or "").strip()
            if deployment_model.lower() != normalized_response_model:
                continue

            normalized_deployment_model = deployment_model.lower()
            if normalized_deployment_model.startswith("openai/~") or "openrouter" in normalized_deployment_model:
                return "openrouter"

            _resolved_model, resolved_provider = resolved_model_provider_identity(
                deployment_model,
            )
            if resolved_provider:
                return resolved_provider
        return ""

    def _resolve_response_model_provider(
        self,
        response: Any,
        *,
        fallback_provider: Optional[str] = None,
        configured_model: str = "",
        model_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[str, str]:
        """Return the actual response model/provider when LiteLLM exposes them."""
        configured_provider = str(fallback_provider or "").strip()
        normalized_configured_model = str(configured_model or "").strip()
        if normalized_configured_model:
            resolved_configured_model, _ = resolved_model_provider_identity(
                normalized_configured_model,
                model_list,
            )
            configured_route = str(resolved_configured_model or normalized_configured_model).strip().lower()
            if configured_route.startswith("openai/~") or "openrouter" in configured_route:
                configured_provider = "openrouter"
        response_model = str(self._get_response_field(response, "model") or "").strip()
        if response_model:
            if "/" not in response_model:
                return response_model, configured_provider
            matched_provider = self._resolve_configured_response_provider(
                normalized_configured_model,
                response_model,
                model_list,
            )
            if matched_provider:
                return response_model, matched_provider
            if configured_provider == "openrouter":
                return response_model, configured_provider
            response_provider = get_explicit_llm_channel_model_provider(response_model)
            if response_provider:
                return response_model, response_provider
            return response_model, configured_provider
        return "", configured_provider

    @staticmethod
    def _promote_error_identity(details: Any) -> Dict[str, str]:
        """Lift route/model diagnostics from nested GenerationError details."""
        if not isinstance(details, dict):
            return {}
        promoted: Dict[str, str] = {}
        for key in ("last_model", "route_name", "last_provider"):
            candidate = str(details.get(key) or "").strip()
            if candidate:
                promoted[key] = candidate
        return promoted

    def _resolve_router_failure_identity(
        self,
        exc: Any,
        *,
        route_name: str,
        recovery_model_list: List[Dict[str, Any]],
    ) -> Tuple[str, str]:
        """Resolve the final Router deployment identity from a transport exception."""
        normalized_route_name = str(route_name or "").strip()
        origins = route_deployment_origins(recovery_model_list, normalized_route_name)
        deployment_count = len(origins.hermes_deployments) + len(origins.non_hermes_deployments)
        candidate_models: List[str] = []
        candidate_provider = ""
        seen_payloads: set[int] = set()

        def _remember_model(value: Any) -> None:
            normalized = str(value or "").strip()
            if not normalized:
                return
            if deployment_count > 1 and normalized == normalized_route_name:
                return
            if normalized not in candidate_models:
                candidate_models.append(normalized)

        def _remember_provider(value: Any) -> None:
            nonlocal candidate_provider
            normalized = str(value or "").strip()
            if normalized and not candidate_provider:
                candidate_provider = normalized

        def _walk(payload: Any) -> None:
            if payload is None:
                return
            payload_id = id(payload)
            if payload_id in seen_payloads:
                return
            seen_payloads.add(payload_id)

            if isinstance(payload, dict):
                params = payload.get("litellm_params")
                if isinstance(params, dict):
                    _remember_model(params.get("model"))
                    _remember_provider(
                        params.get("custom_llm_provider") or params.get("provider")
                    )
                for key in (
                    "litellm_model",
                    "response_model",
                    "deployment_model",
                    "model",
                    "model_name",
                ):
                    _remember_model(payload.get(key))
                _remember_provider(
                    payload.get("llm_provider")
                    or payload.get("litellm_provider")
                    or payload.get("custom_llm_provider")
                    or payload.get("provider")
                )
                for key in ("response", "error", "details", "metadata", "body"):
                    _walk(payload.get(key))
                return

            for key in ("response", "error", "details", "metadata", "body"):
                nested = getattr(payload, key, None)
                if nested is not payload:
                    _walk(nested)
            _remember_provider(
                getattr(payload, "llm_provider", None)
                or getattr(payload, "litellm_provider", None)
                or getattr(payload, "custom_llm_provider", None)
                or getattr(payload, "provider", None)
            )
            for key in (
                "litellm_model",
                "response_model",
                "deployment_model",
                "model",
                "model_name",
            ):
                _remember_model(getattr(payload, key, None))

        _walk(exc)
        for candidate_model in candidate_models:
            resolved_model, resolved_provider = resolved_model_provider_identity(
                candidate_model,
                recovery_model_list,
            )
            normalized_route = str(resolved_model or candidate_model).strip().lower()
            explicit_provider = get_explicit_llm_channel_model_provider(candidate_model)
            route_text = f"{candidate_provider} {normalized_route}".strip().lower()
            if normalized_route.startswith("openai/~") or "openrouter" in route_text:
                return resolved_model or candidate_model, "openrouter"
            if candidate_provider and not explicit_provider:
                return resolved_model or candidate_model, candidate_provider
            if resolved_provider:
                return resolved_model or candidate_model, resolved_provider
        return "", candidate_provider

    def _extract_text_blocks(self, blocks: Any, *, strip: bool = True) -> str:
        """Extract final-answer text from OpenAI-compatible content blocks.

        Some reasoning models (including MiniMax) expose thinking and final
        answer blocks in the same list.  Thinking blocks can also carry a
        ``text`` field, so concatenating every block corrupts structured output
        by prefixing the JSON answer with chain-of-thought text.
        """
        if not blocks:
            return ""

        parts: List[str] = []
        for block in blocks:
            if isinstance(block, str):
                parts.append(block)
                continue

            block_type = ""
            text = None
            if isinstance(block, dict):
                block_type = str(block.get("type") or "").strip().lower()
                text = block.get("text")
                if text is None:
                    text = block.get("content")
            else:
                block_type = str(getattr(block, "type", "") or "").strip().lower()
                text = getattr(block, "text", None)
                if text is None:
                    text = getattr(block, "content", None)

            # Keep untyped legacy blocks for compatibility, but typed blocks
            # must explicitly represent final output text.
            if block_type and block_type not in {"text", "output_text"}:
                continue
            if isinstance(text, str) and text:
                parts.append(text)

        result = "".join(parts)
        return result.strip() if strip else result

    def _extract_completion_text(self, response: Any) -> str:
        """Extract text from non-stream LiteLLM completion responses."""
        choices = self._get_response_field(response, "choices")
        if not choices:
            return ""

        choice = choices[0]
        message = self._get_response_field(choice, "message")

        content_blocks = self._get_response_field(choice, "content_blocks")
        if content_blocks is None and message is not None:
            content_blocks = self._get_response_field(message, "content_blocks")
        block_text = self._extract_text_blocks(content_blocks)
        if block_text:
            return strip_leading_think_wrapper(block_text)

        content = None
        if message is not None:
            content = self._get_response_field(message, "content")
        if content is None:
            content = self._get_response_field(choice, "content")

        if isinstance(content, list):
            return strip_leading_think_wrapper(self._extract_text_blocks(content))
        if isinstance(content, str):
            return strip_leading_think_wrapper(content)
        return str(content).strip() if content is not None else ""

    def _extract_stream_text(self, chunk: Any) -> str:
        """Extract provider-agnostic text delta from a LiteLLM streaming chunk."""
        choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
        if not choices:
            return ""

        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else getattr(choice, "delta", None)
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)

        content: Any = None
        if isinstance(delta, dict):
            content = delta.get("content")
        elif isinstance(delta, str):
            content = delta
        elif delta is not None:
            content = getattr(delta, "content", None)

        if content is None:
            if isinstance(message, dict):
                content = message.get("content")
            elif message is not None:
                content = getattr(message, "content", None)

        if isinstance(content, list):
            return self._extract_text_blocks(content, strip=False)

        return content if isinstance(content, str) else ""

    def _consume_litellm_stream(
        self,
        stream_response: Any,
        *,
        model: str,
        usage_model: Optional[str] = None,
        provider: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Consume a LiteLLM stream into a single text payload."""
        chunks: List[str] = []
        usage: Dict[str, Any] = {}
        chars_received = 0
        next_emit_at = 1

        try:
            for chunk in stream_response:
                chunk_usage = extract_usage_payload(chunk)
                normalized_usage = self._normalize_usage(
                    chunk_usage,
                    model=usage_model or model,
                    provider=provider,
                )
                if normalized_usage:
                    usage = normalized_usage

                delta_text = self._extract_stream_text(chunk)
                if not delta_text:
                    continue

                chunks.append(delta_text)
                chars_received += len(delta_text)
                if progress_callback and chars_received >= next_emit_at:
                    progress_callback(chars_received)
                    next_emit_at = chars_received + 160
        except Exception as exc:
            raise _LiteLLMStreamError(
                f"{model} stream interrupted: {exc}",
                partial_received=chars_received > 0,
            ) from exc

        response_text = strip_leading_think_wrapper("".join(chunks))
        if not response_text:
            raise _LiteLLMStreamError(
                f"{model} stream returned empty response",
                partial_received=False,
            )

        if progress_callback and chars_received > 0:
            progress_callback(chars_received)

        return response_text, usage

    def _get_generation_backend(self, backend_id: Optional[str] = None) -> GenerationBackend:
        """Return the configured generation backend."""
        config = self._get_runtime_config()
        resolved_backend_id = backend_id or self._resolve_generation_backend_config()[0]
        return create_generation_backend(
            resolved_backend_id,
            config=config,
            litellm_completion_callable=self._call_litellm_impl,
        )

    def _call_litellm(
        self,
        prompt: str,
        generation_config: dict,
        *,
        system_prompt: Optional[str] = None,
        stream: bool = False,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
        response_validator: Optional[Callable[[str], None]] = None,
        audit_context: Optional[Dict[str, Any]] = None,
        return_generation_result: bool = False,
    ) -> Union[Tuple[str, str, Dict[str, Any]], GenerationResult]:
        """Compatibility wrapper around the configured generation backend."""
        preflight_error = self.get_generation_backend_config_error()
        if preflight_error is not None and not self._can_use_generation_fallback(preflight_error):
            raise preflight_error
        backend_id, fallback_backend_id = self._resolve_generation_backend_config()
        try:
            result = self._get_generation_backend(backend_id).generate(
                prompt,
                generation_config,
                system_prompt=system_prompt,
                stream=stream,
                stream_progress_callback=stream_progress_callback,
                response_validator=response_validator,
                audit_context=audit_context,
            )
        except GenerationError as exc:
            if not exc.fallbackable or not fallback_backend_id:
                raise
            try:
                fallback_backend = self._get_generation_backend(fallback_backend_id)
            except GenerationError as fallback_exc:
                raise GenerationError(
                    error_code=fallback_exc.error_code,
                    stage="fallback",
                    retryable=False,
                    fallbackable=False,
                    backend=fallback_backend_id,
                    provider=fallback_exc.provider,
                    details={
                        "primary_error": {
                            "error_code": exc.error_code.value,
                            "backend": exc.backend,
                            "provider": exc.provider,
                            "stage": exc.stage,
                            "details": exc.details,
                        },
                        "fallback_error": fallback_exc.details,
                    },
                ) from fallback_exc
            try:
                result = fallback_backend.generate(
                    prompt,
                    generation_config,
                    system_prompt=system_prompt,
                    stream=stream,
                    stream_progress_callback=stream_progress_callback,
                    response_validator=response_validator,
                    audit_context=audit_context,
                )
            except _AllModelsFailedError:
                raise
            except GenerationError as fallback_exc:
                fallback_identity = self._promote_error_identity(fallback_exc.details)
                raise GenerationError(
                    error_code=fallback_exc.error_code,
                    stage="fallback",
                    retryable=False,
                    fallbackable=False,
                    backend=fallback_backend_id,
                    provider=fallback_exc.provider,
                    details={
                        "reason": "fallback_backend_failed",
                        **fallback_identity,
                        "primary_error": {
                            "error_code": exc.error_code.value,
                            "backend": exc.backend,
                            "provider": exc.provider,
                            "stage": exc.stage,
                            "details": exc.details,
                        },
                        "fallback_error": {
                            "error_code": fallback_exc.error_code.value,
                            "backend": fallback_exc.backend,
                            "provider": fallback_exc.provider,
                            "stage": fallback_exc.stage,
                            "details": fallback_exc.details,
                        },
                    },
                ) from fallback_exc
            except Exception as fallback_exc:
                raise GenerationError(
                    error_code=GenerationErrorCode.UNKNOWN_BACKEND_ERROR,
                    stage="fallback",
                    retryable=False,
                    fallbackable=False,
                    backend=fallback_backend_id,
                    provider=fallback_backend_id,
                    details={
                        "reason": "fallback_backend_failed",
                        "primary_error": {
                            "error_code": exc.error_code.value,
                            "backend": exc.backend,
                            "provider": exc.provider,
                            "stage": exc.stage,
                            "details": exc.details,
                        },
                        "fallback_error": str(fallback_exc),
                    },
                ) from fallback_exc
        if return_generation_result:
            return result
        return result.text, result.model, result.usage

    def _call_litellm_impl(
        self,
        prompt: str,
        generation_config: dict,
        *,
        system_prompt: Optional[str] = None,
        stream: bool = False,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
        response_validator: Optional[Callable[[str], None]] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Call LLM via litellm with fallback across configured models.

        When channels/YAML are configured, every model goes through the Router
        (which handles per-model key selection, load balancing, and retries).
        In legacy mode, the primary model may use the Router while fallback
        models fall back to direct litellm.completion().

        Args:
            prompt: User prompt text.
            generation_config: Dict with optional keys: temperature, max_output_tokens, max_tokens.
            response_validator: Optional callable that accepts the raw response text and raises
                an exception if the response is unacceptable (e.g. not valid JSON).  When it
                raises, the current model is treated as failed and the next fallback model is
                tried.  If all models fail validation, :class:`_AllModelsFailedError` is raised
                with ``last_response_text`` set to the last raw response received.

        Returns:
            Tuple of (response text, model_used, usage). On success model_used is the full model
            name and usage is a dict with prompt_tokens, completion_tokens, total_tokens.
        """
        config = self._get_runtime_config()
        max_tokens = (
            generation_config.get('max_output_tokens')
            or generation_config.get('max_tokens')
            or 8192
        )
        requested_temperature = generation_config.get('temperature', 0.7)
        requested_timeout = generation_config.get("timeout")

        models_to_try = [config.litellm_model] + (config.litellm_fallback_models or [])
        models_to_try = [m for m in models_to_try if m]

        use_channel_router = self._has_channel_config(config)

        last_error = None
        last_response_text: Optional[str] = None
        last_model: Optional[str] = None
        last_provider: Optional[str] = None
        last_usage: Dict[str, Any] = {}
        effective_system_prompt = system_prompt or self.TEXT_SYSTEM_PROMPT
        router_model_names = set(get_configured_llm_models(config.llm_model_list))
        for model in models_to_try:
            last_model = model
            origins = route_deployment_origins(config.llm_model_list, model)
            model_stream = bool(stream and not origins.has_hermes)
            recovery_model_list = config.llm_model_list
            legacy_router_model_list = getattr(self, "_legacy_router_model_list", None) or []
            if legacy_router_model_list and model == config.litellm_model and not use_channel_router:
                recovery_model_list = legacy_router_model_list
            usage_model, usage_provider = resolved_model_provider_identity(model, recovery_model_list)
            if usage_provider:
                last_provider = usage_provider

            try:
                def _attach_usage_audit(
                    usage: Dict[str, Any],
                    messages: List[Dict[str, Any]],
                ) -> Dict[str, Any]:
                    if audit_context is None:
                        return filter_prompt_cache_telemetry(
                            attach_message_hmacs(usage, messages),
                            config,
                        )
                    effective_audit_context = dict(audit_context)
                    effective_audit_context["provider"] = (
                        usage.get("provider") or usage_provider
                    )
                    effective_audit_context["transport"] = (
                        effective_audit_context.get("transport") or "litellm"
                    )
                    return filter_prompt_cache_telemetry(
                        attach_legacy_message_stability_audit(
                            usage,
                            messages,
                            effective_audit_context,
                        ),
                        config,
                    )

                model_short = model.split("/")[-1] if "/" in model else model
                extra = get_thinking_extra_body(model_short)
                call_kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": effective_system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                }
                if requested_timeout not in (None, ""):
                    call_kwargs["timeout"] = requested_timeout
                if extra:
                    call_kwargs["extra_body"] = extra
                uses_router = (
                    (use_channel_router and self._router and model in router_model_names)
                    or (self._router and model == config.litellm_model and not use_channel_router)
                )
                if not uses_router:
                    try:
                        keys = get_api_keys_for_model(model, config)
                    except AttributeError:
                        keys = []
                    if keys:
                        call_kwargs["api_key"] = keys[0]
                    try:
                        call_kwargs.update(extra_litellm_params(model, config))
                    except AttributeError:
                        pass
                call_kwargs = apply_litellm_generation_params(
                    call_kwargs,
                    model,
                    requested_temperature,
                    model_list=recovery_model_list,
                )
                route_context = build_provider_cache_route_context(
                    model=model,
                    provider=usage_provider,
                    call_kwargs=call_kwargs,
                    model_list=recovery_model_list,
                    call_type="analysis",
                )
                hint_result = apply_prompt_cache_hints(call_kwargs, route_context, config)
                call_kwargs = hint_result.call_kwargs
                if requested_timeout not in (None, ""):
                    call_kwargs["timeout"] = requested_timeout
                if hint_result.diagnostics:
                    logger.debug("[PromptCache] %s", hint_result.diagnostics)

                _stream_text: Optional[str] = None
                _stream_usage: Dict[str, Any] = {}

                if model_stream:
                    try:
                        stream_response = call_litellm_with_param_recovery(
                            lambda kwargs: self._dispatch_litellm_completion(
                                model,
                                kwargs,
                                config=config,
                                use_channel_router=use_channel_router,
                                router_model_names=router_model_names,
                            ),
                            model=model,
                            call_kwargs={**call_kwargs, "stream": True},
                            model_list=recovery_model_list,
                            cache_recovery=False,
                            logger=logger,
                        )
                        _stream_text, _stream_usage = self._consume_litellm_stream(
                            stream_response,
                            model=model,
                            usage_model=usage_model,
                            provider=usage_provider,
                            progress_callback=stream_progress_callback,
                        )
                    except _LiteLLMStreamError as exc:
                        safe_error = self._sanitize_litellm_exception_text(exc, config=config, model=model)
                        if exc.partial_received:
                            logger.warning(
                                "[LiteLLM] %s stream failed after partial output, retrying non-stream for same model: %s",
                                model,
                                safe_error,
                            )
                        else:
                            logger.warning(
                                "[LiteLLM] %s stream unavailable before first chunk, falling back to non-stream: %s",
                                model,
                                safe_error,
                            )
                        last_error = RuntimeError(f"{type(exc).__name__}: {safe_error}")
                    except Exception as exc:
                        safe_error = self._sanitize_litellm_exception_text(exc, config=config, model=model)
                        logger.warning(
                            "[LiteLLM] %s stream request failed before first chunk, falling back to non-stream: %s",
                            model,
                            safe_error,
                        )

                if _stream_text is not None:
                    last_response_text = _stream_text
                    last_model = model
                    if usage_provider:
                        _stream_usage["provider"] = usage_provider
                    _stream_usage = _attach_usage_audit(_stream_usage, call_kwargs["messages"])
                    if usage_provider:
                        _stream_usage.setdefault("provider", usage_provider)
                    last_usage = _stream_usage
                    if response_validator is not None:
                        response_validator(_stream_text)
                    return _stream_text, model, _stream_usage

                response = call_litellm_with_param_recovery(
                    lambda kwargs: self._dispatch_litellm_completion(
                        model,
                        kwargs,
                        config=config,
                        use_channel_router=use_channel_router,
                        router_model_names=router_model_names,
                    ),
                    model=model,
                    call_kwargs=call_kwargs,
                    model_list=recovery_model_list,
                    logger=logger,
                )

                response_model, response_provider = self._resolve_response_model_provider(
                    response,
                    fallback_provider=usage_provider,
                    configured_model=model,
                    model_list=recovery_model_list,
                )
                actual_model = response_model or model
                if response_model:
                    last_model = actual_model
                if response_provider:
                    last_provider = response_provider
                content = self._extract_completion_text(response)
                if content:
                    usage_messages = None if audit_context is not None else call_kwargs["messages"]
                    usage = self._normalize_usage(
                        extract_usage_payload(response),
                        model=response_model or usage_model or model,
                        provider=response_provider or usage_provider,
                        messages=usage_messages,
                    )
                    if response_provider or usage_provider:
                        usage["provider"] = response_provider or usage_provider
                    if audit_context is not None:
                        usage = _attach_usage_audit(usage, call_kwargs["messages"])
                    if response_model:
                        usage.setdefault("response_model", response_model)
                    if response_provider or usage_provider:
                        usage.setdefault("provider", response_provider or usage_provider)
                    last_response_text = content
                    last_model = actual_model
                    if response_provider:
                        last_provider = response_provider
                    last_usage = usage
                    if response_validator is not None:
                        response_validator(content)
                    return (content, actual_model, usage)
                raise ValueError("LLM returned empty response")

            except Exception as e:
                if uses_router:
                    router_model, router_provider = self._resolve_router_failure_identity(
                        e,
                        route_name=model,
                        recovery_model_list=recovery_model_list,
                    )
                    if router_model:
                        last_model = router_model
                    if router_provider:
                        last_provider = router_provider
                safe_error = self._sanitize_litellm_exception_text(e, config=config, model=model)
                logger.warning("[LiteLLM] %s failed: %s", model, safe_error)
                last_error = RuntimeError(f"{type(e).__name__}: {safe_error}")
                continue

        raise _AllModelsFailedError(
            f"All LLM models failed (tried {len(models_to_try)} model(s)). Last error: {last_error}",
            last_response_text=last_response_text,
            last_model=last_model,
            last_provider=last_provider,
            last_usage=last_usage,
        )

    def generate_text(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """Public entry point for free-form text generation.

        External callers (e.g. MarketAnalyzer) must use this method instead of
        calling _call_litellm() directly or accessing private attributes such as
        _litellm_available, _router, _model, _use_openai, or _use_anthropic.

        Args:
            prompt:      Text prompt to send to the LLM.
            max_tokens:  Maximum tokens in the response (default 2048).
            temperature: Sampling temperature (default 0.7).

        Returns:
            Response text, or None if the LLM call fails (error is logged).
        """
        try:
            result = self._call_litellm(
                prompt,
                generation_config={"max_tokens": max_tokens, "temperature": temperature},
            )
            if isinstance(result, tuple):
                text, model_used, usage = result
                if should_persist_usage_telemetry(usage):
                    persist_llm_usage(usage, model_used, call_type="market_review")
                return text
            return result
        except GenerationError:
            raise
        except Exception as exc:
            logger.error("[generate_text] LLM call failed: %s", exc)
            return None

    def get_generation_backend_identity(self) -> Tuple[str, str]:
        """Return the configured primary backend identity for live diagnostics."""
        backend_id, _fallback_backend_id = self._resolve_generation_backend_config()
        if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
            return backend_id, backend_id
        config = self._get_runtime_config()
        return backend_id, str(getattr(config, "litellm_model", "") or "")

    def generate_text_with_metadata(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> Optional[GenerationResult]:
        """Generate text and return the actual backend/model used for diagnostics."""
        try:
            result = self._call_litellm(
                prompt,
                generation_config={"max_tokens": max_tokens, "temperature": temperature},
                return_generation_result=True,
            )
            if not isinstance(result, GenerationResult):
                raise TypeError("generation backend returned an invalid result")
            if should_persist_usage_telemetry(result.usage):
                persist_llm_usage(result.usage, result.model, call_type="market_review")
            return result
        except GenerationError:
            raise
        except _AllModelsFailedError as exc:
            backend_id, fallback_backend_id = self._resolve_generation_backend_config()
            if not fallback_backend_id and backend_id == LITELLM_BACKEND_ID:
                logger.warning(
                    "[generate_text_with_metadata] Primary LiteLLM exhausted all configured models; "
                    "returning empty GenerationResult so caller fallback can continue"
                )
                usage = dict(exc.last_usage or {})
                if exc.last_provider:
                    usage.setdefault("provider", exc.last_provider)
                return GenerationResult(
                    text="",
                    model=exc.last_model or str(getattr(self._get_runtime_config(), "litellm_model", "") or ""),
                    provider=exc.last_provider or backend_id,
                    backend=backend_id,
                    usage=usage,
                    diagnostics={
                        "reason": "all_models_failed",
                        "configured_primary_backend": backend_id,
                        "configured_fallback_backend": fallback_backend_id,
                        "last_model": exc.last_model,
                        "template_fallback": True,
                    },
                )
            failed_backend = fallback_backend_id or backend_id
            raise GenerationError(
                error_code=GenerationErrorCode.UNKNOWN_BACKEND_ERROR,
                stage="fallback" if fallback_backend_id else "generation",
                retryable=False,
                fallbackable=False,
                backend=failed_backend,
                provider=exc.last_provider or failed_backend,
                details={
                    "reason": "all_models_failed",
                    "configured_primary_backend": backend_id,
                    "configured_fallback_backend": fallback_backend_id,
                    "last_model": exc.last_model,
                },
            ) from exc
        except Exception as exc:
            logger.error("[generate_text_with_metadata] LLM call failed: %s", exc)
            raise

    def analyze(
        self, 
        context: Dict[str, Any],
        news_context: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
        analysis_context_pack_summary: Optional[str] = None,
    ) -> AnalysisResult:
        """Phân tích một mã cổ phiếu. / Analyze a single stock.

        Luồng xử lý / Flow:
        1. Dựng prompt đầu vào (kỹ thuật + tin tức)
        2. Gọi API LLM (có retry và chuyển đổi mô hình)
        3. Parse phản hồi JSON
        4. Trả về kết quả có cấu trúc

        Args:
            context: dữ liệu ngữ cảnh từ ``storage.get_analysis_context()``
            news_context: nội dung tin tức đã tìm sẵn (tuỳ chọn)

        Returns:
            Đối tượng ``AnalysisResult``.
        """
        def _emit_progress(progress: int, message: str) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(progress, message)
            except Exception as exc:
                logger.debug("[analyzer] progress callback skipped: %s", exc)

        def _progress_text(stage: str, subject: Any = None, **params: Any) -> str:
            """Thông báo tiến độ theo ngôn ngữ báo cáo.

            Các chuỗi này đi chung ``progress_callback`` với pipeline
            (``src/core/pipeline.py:932``) nên cũng lọt thẳng ra panel Run Flow qua
            ``task.message`` — không được để nguyên tiếng Trung.
            These share the pipeline's progress callback and reach the Run Flow panel.
            """
            # ``get_localized_stock_name`` coi tên trùng mã là giữ chỗ, nên chỉ gọi khi
            # thực sự có tên cổ phiếu; truyền mã trần sẽ biến "FPT" thành "Cổ phiếu chưa
            # xác định". Cùng cái bẫy đã gặp ở ``pipeline._progress_text``.
            # Only localize when a real name was supplied; passing the bare code would
            # replace the ticker with the generic placeholder.
            display = get_localized_stock_name(subject, code, report_language) if subject else str(code or "")
            return get_pipeline_progress_text(stage, report_language, name=display, **params)

        code = context.get('code', 'Unknown')
        config = self._get_runtime_config()
        report_language = normalize_report_language(getattr(config, "report_language", "vi"))
        system_prompt = self._get_analysis_system_prompt(report_language, stock_code=code)
        skill_instructions, default_skill_policy, use_legacy_default_prompt = self._get_skill_prompt_sections()
        
        # Chờ trước khi gọi để tránh bị giới hạn tần suất. / Delay to avoid rate limiting.
        request_delay = config.gemini_request_delay
        if request_delay > 0:
            logger.debug(f"[LLM] Waiting {request_delay:.1f}s before the request...")
            _emit_progress(65, _progress_text("llm_wait", seconds=f"{request_delay:.1f}"))
            time.sleep(request_delay)
        
        # Ưu tiên tên cổ phiếu từ context (do main.py truyền vào).
        name = context.get('stock_name')
        if not name or name.startswith(PLACEHOLDER_NAME_PREFIXES):
            # Dự phòng: lấy từ realtime. / Fallback: the realtime payload.
            if 'realtime' in context and context['realtime'].get('name'):
                name = context['realtime']['name']
            else:
                # Cuối cùng là bảng ánh xạ. / Last resort: the mapping table.
                name = STOCK_NAME_MAP.get(code, f'Cổ phiếu {code}')

        backend_error = self.get_generation_backend_config_error()
        if backend_error is not None and not self._can_use_generation_fallback(backend_error):
            details = backend_error.details or {}
            field = str(details.get("field") or "GENERATION_BACKEND")
            requested_backend = str(details.get("requested_backend") or backend_error.backend)
            reason = str(details.get("reason") or backend_error.error_code.value)
            summary = (
                "AI analysis is unavailable because the generation backend "
                f"cannot start: {backend_error.error_code.value}."
            )
            risk_warning = (
                f"Check {field}={requested_backend} ({reason}) or set a valid "
                "backend/fallback before retrying."
            )
            return AnalysisResult(
                code=code,
                name=name,
                sentiment_score=50,
                trend_prediction=localize_trend_prediction('sideways', report_language),
                operation_advice=localize_operation_advice('hold', report_language),
                confidence_level=localize_confidence_level('low', report_language),
                analysis_summary=summary,
                risk_warning=risk_warning,
                success=False,
                error_message=(
                    f"{backend_error.error_code.value}: {field}={requested_backend}"
                ),
                model_used=None,
                report_language=report_language,
            )

        # Mô hình không khả dụng thì trả kết quả mặc định. / Return a default result.
        if not self.is_available():
            return AnalysisResult(
                code=code,
                name=name,
                sentiment_score=50,
                trend_prediction=localize_trend_prediction('sideways', report_language),
                operation_advice=localize_operation_advice('hold', report_language),
                confidence_level=localize_confidence_level('low', report_language),
                analysis_summary=_localized_text(
                    report_language,
                    en='AI analysis is unavailable because no API key is configured.',
                ),
                risk_warning=_localized_text(
                    report_language,
                    en='Configure an LLM API key (GEMINI_API_KEY/ANTHROPIC_API_KEY/OPENAI_API_KEY) and retry.',
                ),
                success=False,
                error_message=_localized_text(
                    report_language,
                    en='LLM API key is not configured',
                ),
                model_used=None,
                report_language=report_language,
            )
        
        try:
            # Dựng prompt đầu vào (dữ liệu kỹ thuật + tin tức).
            prompt = self._format_prompt(
                context,
                name,
                news_context,
                report_language=report_language,
                analysis_context_pack_summary=analysis_context_pack_summary,
            )
            legacy_audit_context = {
                "language": report_language,
                "market_group": _legacy_market_group(code),
                "analysis_mode": "stock_analysis",
                "legacy_prompt_mode": "legacy_default" if use_legacy_default_prompt else "skill_aware",
                "skill_config": {
                    "skill_instructions": skill_instructions,
                    "default_skill_policy": default_skill_policy,
                    "use_legacy_default_prompt": use_legacy_default_prompt,
                },
                "transport": "litellm",
                "dynamic_markers": _legacy_audit_marker_specs(
                    context,
                    code=code,
                    stock_name=name,
                    report_language=report_language,
                    news_context=news_context,
                    analysis_context_pack_summary=analysis_context_pack_summary,
                ),
            }
            
            config = self._get_runtime_config()
            backend_id, _fallback_backend_id = self._resolve_generation_backend_config()
            model_name = config.litellm_model or "unknown"
            if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
                model_name = backend_id
                legacy_audit_context["transport"] = backend_id
            logger.info(f"========== AI analysis {name}({code}) ==========")
            logger.info(f"[LLM config] model: {model_name}")
            logger.info(f"[LLM config] prompt length: {len(prompt)} chars")
            logger.info(f"[LLM config] news included: {'yes' if news_context else 'no'}")

            # Backend CLI cục bộ là khả năng chạy tiến trình, không ghi full prompt.
            if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
                prompt_preview = redact_diagnostic_text(prompt, limit=500)
            else:
                prompt_preview = prompt[:500] + "..." if len(prompt) > 500 else prompt
            logger.info(f"[LLM prompt preview]\n{prompt_preview}")
            if backend_id not in LOCAL_CLI_GENERATION_BACKEND_IDS:
                logger.debug(f"=== Full prompt ({len(prompt)} chars) ===\n{prompt}\n=== End Prompt ===")

            # Thiết lập cấu hình sinh. / Set the generation config.
            generation_config = {
                "temperature": config.llm_temperature,
                "max_output_tokens": 8192,
            }

            logger.info(f"[LLM call] calling {model_name}...")
            _emit_progress(68, _progress_text("llm_accepted", name))

            # Gọi qua litellm (có retry khi thiếu trường bắt buộc).
            current_prompt = prompt
            retry_count = 0
            max_retries = config.report_integrity_retry if config.report_integrity_enabled else 0

            while True:
                start_time = time.time()
                try:
                    response_text, model_used, llm_usage = self._call_litellm(
                        current_prompt,
                        generation_config,
                        system_prompt=system_prompt,
                        stream=True,
                        stream_progress_callback=stream_progress_callback,
                        response_validator=self._validate_json_response,
                        audit_context=legacy_audit_context,
                    )
                except _AllModelsFailedError as exc:
                    if exc.last_response_text is not None:
                        logger.warning(
                            "[LLM JSON] %s(%s): all models returned invalid JSON, using text fallback",
                            name,
                            code,
                        )
                        response_text = exc.last_response_text
                        model_used = exc.last_model
                        llm_usage = exc.last_usage
                    else:
                        raise
                elapsed = time.time() - start_time

                # Ghi nhận thông tin phản hồi. / Record the response info.
                logger.info(
                    f"[LLM response] {model_name} succeeded in {elapsed:.2f}s, {len(response_text)} chars"
                )
                if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
                    response_preview = redact_diagnostic_text(response_text, limit=300)
                else:
                    response_preview = response_text[:300] + "..." if len(response_text) > 300 else response_text
                logger.info(f"[LLM response preview]\n{response_preview}")
                if backend_id not in LOCAL_CLI_GENERATION_BACKEND_IDS:
                    logger.debug(
                        f"=== {model_name} full response ({len(response_text)} chars) ===\n{response_text}\n=== End Response ==="
                    )
                # Keep parser/retry progress monotonic so task progress/message never "goes backward".
                parse_progress = min(99, 93 + retry_count * 2)
                _emit_progress(parse_progress, _progress_text("llm_parsing", name))

                # Parse phản hồi. / Parse the response.
                result = self._parse_response(response_text, code, name)
                result.raw_response = response_text
                result.search_performed = bool(news_context)
                result.market_snapshot = self._build_market_snapshot(context, report_language)
                result.model_used = model_used
                result.report_language = report_language

                # Kiểm tra tính đầy đủ của nội dung (tuỳ chọn).
                if not config.report_integrity_enabled:
                    break
                require_phase_decision = isinstance(context.get("market_phase_context"), dict)
                pass_integrity, missing_fields = self._check_content_integrity(
                    result,
                    require_phase_decision=require_phase_decision,
                )
                if pass_integrity:
                    break
                if retry_count < max_retries:
                    current_prompt = self._build_integrity_retry_prompt(
                        prompt,
                        response_text,
                        missing_fields,
                        report_language=report_language,
                    )
                    retry_count += 1
                    logger.info(
                        "[LLM integrity] required fields missing %s, completion retry #%d",
                        missing_fields,
                        retry_count,
                    )
                    retry_progress = min(99, 92 + retry_count * 2)
                    _emit_progress(
                        retry_progress,
                        _progress_text("llm_retry", name, attempt=retry_count, total=max_retries),
                    )
                else:
                    self._apply_placeholder_fill(result, missing_fields)
                    logger.warning(
                        "[LLM integrity] required fields missing %s, filled with placeholders; not blocking the flow",
                        missing_fields,
                    )
                    break

            if should_persist_usage_telemetry(llm_usage):
                persist_llm_usage(llm_usage, model_used, call_type="analysis", stock_code=code)

            logger.info(f"[LLM parse] {name}({code}) analysis done: {result.trend_prediction}, score {result.sentiment_score}")

            return result
            
        except Exception as e:
            safe_error = self._sanitize_hermes_exception_text(e)
            logger.error("AI analysis of %s(%s) failed: %s", name, code, safe_error)
            return AnalysisResult(
                code=code,
                name=name,
                sentiment_score=50,
                trend_prediction=localize_trend_prediction('sideways', report_language),
                operation_advice=localize_operation_advice('hold', report_language),
                confidence_level=localize_confidence_level('low', report_language),
                analysis_summary=_localized_text(
                    report_language,
                    en=f'Analysis failed: {safe_error[:100]}',
                ),
                risk_warning=_localized_text(
                    report_language,
                    en='Analysis failed. Please retry later or review manually.',
                ),
                success=False,
                error_message=safe_error,
                model_used=None,
                report_language=report_language,
            )
    
    def _format_prompt(
        self, 
        context: Dict[str, Any], 
        name: str,
        news_context: Optional[str] = None,
        report_language: str = "vi",
        analysis_context_pack_summary: Optional[str] = None,
    ) -> str:
        """Dựng prompt phân tích (Decision Dashboard v2.0).

        Gồm: chỉ báo kỹ thuật, giá realtime (volume ratio, thanh khoản), phân tích xu
        hướng và tin tức.

        Build the analysis prompt (Decision Dashboard v2.0): technical indicators,
        realtime quote, trend analysis and news.

        Thân prompt luôn là tiếng Anh cho mọi thị trường; ngôn ngữ đầu ra do mục
        "Output language requirements" ở cuối quyết định.
        The prompt body is always English; the output language is set by the
        "Output language requirements" section at the end.

        Args:
            context: ngữ cảnh dữ liệu kỹ thuật (đã bổ sung dữ liệu mở rộng)
            name: tên cổ phiếu mặc định, có thể bị context ghi đè
            news_context: nội dung tin tức đã tìm sẵn
        """
        code = context.get('code', 'Unknown')
        report_language = normalize_report_language(report_language)
        _, _, use_legacy_default_prompt = self._get_skill_prompt_sections()
        # Thị trường không nhận diện được (hoặc chưa bật) thì bỏ hẳn đơn vị thay vì
        # để lại khoảng trắng thừa trong bảng. Pipeline đã lọc theo thị trường đang bật
        # (`src/core/pipeline.py:409`) nên nhánh rỗng gần như không xảy ra khi chạy thật.
        # Drop the unit entirely for an unknown/disabled market instead of leaving a
        # dangling space in the table.
        currency = _market_currency(code)
        price_unit = f" {currency}" if currency else ""

        # Ưu tiên tên cổ phiếu trong context (lấy từ realtime_quote).
        # Prefer the stock name carried in the context (from realtime_quote).
        # Tên giữ chỗ (tiền tố trong PLACEHOLDER_NAME_PREFIXES, kể cả bản ghi cũ) được thay
        # bằng tên trong bảng ánh xạ nếu có.
        # Placeholder names (PLACEHOLDER_NAME_PREFIXES, incl. legacy ones) fall back to the map.
        stock_name = context.get('stock_name', name)
        if not stock_name or stock_name.startswith(PLACEHOLDER_NAME_PREFIXES):
            stock_name = STOCK_NAME_MAP.get(code, f'Cổ phiếu {code}')
            
        today = context.get('today', {})
        unknown_text = get_unknown_text(report_language)
        quote_section_title, close_price_label = _phase_aware_quote_labels(context)
        hide_regular_session_ohlc = _should_hide_regular_session_ohlc(context)
        realtime_overlay_quote = hide_regular_session_ohlc and _today_has_realtime_overlay(today)
        pct_chg_label = "Realtime change" if realtime_overlay_quote else "Change"
        volume_label = "Realtime volume" if realtime_overlay_quote else "Volume"
        amount_label = "Realtime turnover" if realtime_overlay_quote else "Turnover"
        quote_rows = [
            f"| {close_price_label} | {today.get('close', 'N/A')}{price_unit} |",
        ]
        if not hide_regular_session_ohlc:
            quote_rows.extend(
                [
                    f"| Open | {today.get('open', 'N/A')}{price_unit} |",
                    f"| High | {today.get('high', 'N/A')}{price_unit} |",
                    f"| Low | {today.get('low', 'N/A')}{price_unit} |",
                ]
            )
        quote_rows.extend(
            [
                f"| {pct_chg_label} | {today.get('pct_chg', 'N/A')}% |",
                f"| {volume_label} | {self._format_volume(today.get('volume'), report_language)} |",
                f"| {amount_label} | {self._format_amount(today.get('amount'), report_language)} |",
            ]
        )
        quote_rows_text = "\n".join(quote_rows)
        
        # ========== Dựng đầu vào theo định dạng Decision Dashboard ==========
        prompt = f"""# Decision Dashboard Analysis Request

## 📊 Stock Basics
| Item | Value |
|------|------|
| Ticker | **{code}** |
| Name | **{stock_name}** |
| Analysis date | {context.get('date', unknown_text)} |

---
"""
        prompt += format_market_phase_prompt_section(
            context.get("market_phase_context"),
            report_language=report_language,
        )
        daily_market_context_section = format_daily_market_context_prompt_section(
            context.get("daily_market_context"),
            report_language=report_language,
        )
        if daily_market_context_section:
            prompt += daily_market_context_section
        if isinstance(analysis_context_pack_summary, str) and analysis_context_pack_summary:
            prompt += analysis_context_pack_summary
        prompt += f"""

## 📈 Technical Data

### {quote_section_title}
| Metric | Value |
|------|------|
{quote_rows_text}

### Moving Averages (key decision input)
| MA | Value | Meaning |
|------|------|------|
| MA5 | {today.get('ma5', 'N/A')} | Short-term trend line |
| MA10 | {today.get('ma10', 'N/A')} | Short-to-medium trend line |
| MA20 | {today.get('ma20', 'N/A')} | Medium-term trend line |
| MA alignment | {context.get('ma_status', unknown_text)} | Bullish / bearish / tangled |
"""
        
        # Bổ sung dữ liệu realtime (volume ratio, thanh khoản...).
        # Add realtime quote extras (volume ratio, turnover rate, ...).
        if 'realtime' in context:
            rt = context['realtime']
            prompt += f"""
### Realtime Quote Extras
| Metric | Value | Reading |
|------|------|------|
| Current price | {rt.get('price', 'N/A')}{price_unit} | |
| **Volume ratio** | **{rt.get('volume_ratio', 'N/A')}** | {rt.get('volume_ratio_desc', '')} |
| **Turnover rate** | **{rt.get('turnover_rate', 'N/A')}%** | |
| PE (trailing) | {rt.get('pe_ratio', 'N/A')} | |
| PB | {rt.get('pb_ratio', 'N/A')} | |
| Market cap | {self._format_amount(rt.get('total_mv'), report_language)} | |
| Free-float cap | {self._format_amount(rt.get('circ_mv'), report_language)} | |
| 60-day change | {rt.get('change_60d', 'N/A')}% | Medium-term performance |
"""
            vn_rows = self._build_vn_quote_rows(rt, price_unit, report_language)
            if vn_rows:
                prompt += f"""
### Vietnam Session Data (exchange, foreign flow, order book)
| Metric | Value | Reading |
|------|------|------|
{vn_rows}
"""

        # Bổ sung báo cáo tài chính và cổ tức (góc nhìn đầu tư giá trị).
        # Add financial results and dividends (value-investing lens).
        fundamental_context = context.get("fundamental_context") if isinstance(context, dict) else None
        earnings_block = (
            fundamental_context.get("earnings", {})
            if isinstance(fundamental_context, dict)
            else {}
        )
        earnings_data = (
            earnings_block.get("data", {})
            if isinstance(earnings_block, dict)
            else {}
        )
        financial_report = (
            earnings_data.get("financial_report", {})
            if isinstance(earnings_data, dict)
            else {}
        )
        dividend_metrics = (
            earnings_data.get("dividend", {})
            if isinstance(earnings_data, dict)
            else {}
        )
        if isinstance(financial_report, dict) or isinstance(dividend_metrics, dict):
            financial_report = financial_report if isinstance(financial_report, dict) else {}
            dividend_metrics = dividend_metrics if isinstance(dividend_metrics, dict) else {}
            ttm_yield = dividend_metrics.get("ttm_dividend_yield_pct", "N/A")
            ttm_cash = dividend_metrics.get("ttm_cash_dividend_per_share", "N/A")
            ttm_count = dividend_metrics.get("ttm_event_count", "N/A")
            report_date = financial_report.get("report_date", "N/A")
            prompt += f"""
### Financials and Dividends (value-investing lens)
| Metric | Value | Note |
|------|------|------|
| Latest reporting period | {report_date} | From structured financial fields |
| Revenue | {financial_report.get('revenue', 'N/A')} | |
| Net profit attributable to parent | {financial_report.get('net_profit_parent', 'N/A')} | |
| Operating cash flow | {financial_report.get('operating_cash_flow', 'N/A')} | |
| ROE | {financial_report.get('roe', 'N/A')} | |
| Cash dividend per share (TTM) | {ttm_cash} | Cash dividends only, pre-tax |
| Dividend yield (TTM) | {ttm_yield} | Formula: TTM cash dividend per share / current price x 100% |
| Dividend events (TTM) | {ttm_count} | |

> If any field above is N/A or missing, state plainly that the data is missing and no judgement can be made. Never make it up.
"""

        corporate_events = self._build_corporate_events_section(fundamental_context)
        if corporate_events:
            prompt += corporate_events

        # Bổ sung kết quả phân tích xu hướng (chỉ nhánh bull_trend mặc định ngầm
        # mới giữ cách diễn đạt cũ).
        if 'trend_analysis' in context:
            trend = _sanitize_trend_analysis_for_prompt(
                context['trend_analysis'],
                volume_change_ratio=context.get('volume_change_ratio'),
            )
            consistency_notes = trend.get('prompt_consistency_notes', [])
            # Chỉ dịch ở BƯỚC RENDER, sau khi `_sanitize_trend_analysis_for_prompt`
            # và `_infer_trend_direction` đã khớp trên chuỗi tiếng Trung gốc. Dịch
            # sớm hơn là làm tê liệt toàn bộ bộ lọc mâu thuẫn xu hướng.
            # Localize at RENDER TIME only, after the sanitizer and
            # `_infer_trend_direction` have matched on the original Chinese.
            trend_status_text = localize_trend_prediction(
                trend.get('trend_status', unknown_text), report_language
            )
            volume_status_text = localize_volume_status(
                trend.get('volume_status', unknown_text), report_language
            )
            buy_signal_text = localize_operation_advice(
                trend.get('buy_signal', unknown_text), report_language
            )
            if use_legacy_default_prompt:
                bias_warning = "🚨 Above 5% — do not chase!" if trend.get('bias_ma5', 0) > 5 else "✅ Safe range"
                prompt += f"""
### Trend Pre-read (from the trading principles)
| Metric | Value | Verdict |
|------|------|------|
| Trend status | {trend_status_text} | |
| MA alignment | {trend.get('ma_alignment', unknown_text)} | MA5>MA10>MA20 is bullish |
| Trend strength | {trend.get('trend_strength', 0)}/100 | |
| **Bias (MA5)** | **{trend.get('bias_ma5', 0):+.2f}%** | {bias_warning} |
| Bias (MA10) | {trend.get('bias_ma10', 0):+.2f}% | |
| Volume status | {volume_status_text} | {trend.get('volume_trend', '')} |
| System signal | {buy_signal_text} | |
| System score | {trend.get('signal_score', 0)}/100 | |

#### System Reasoning
**Reasons to buy**:
{chr(10).join('- ' + r for r in trend.get('signal_reasons', ['N/A'])) if trend.get('signal_reasons') else '- N/A'}

**Risk factors**:
{chr(10).join('- ' + r for r in trend.get('risk_factors', ['N/A'])) if trend.get('risk_factors') else '- N/A'}
"""
                if consistency_notes:
                    prompt += f"""

**Consistency constraints**:
{chr(10).join('- ' + note for note in consistency_notes)}
"""
            else:
                bias_warning = (
                    "🚨 Stretched — assess the risk of chasing carefully"
                    if trend.get('bias_ma5', 0) > 5
                    else "✅ Position is relatively manageable"
                )
                prompt += f"""
### Technical and Structural Analysis (input for the activated skill)
| Metric | Value | Note |
|------|------|------|
| Trend status | {trend_status_text} | |
| MA alignment | {trend.get('ma_alignment', unknown_text)} | Judge structural strength together with the activated skill |
| Trend strength | {trend.get('trend_strength', 0)}/100 | |
| **Price position (MA5)** | **{trend.get('bias_ma5', 0):+.2f}%** | {bias_warning} |
| Price position (MA10) | {trend.get('bias_ma10', 0):+.2f}% | |
| Volume status | {volume_status_text} | {trend.get('volume_trend', '')} |
| System signal | {buy_signal_text} | |
| System score | {trend.get('signal_score', 0)}/100 | |

#### System Reasoning
**Supporting factors**:
{chr(10).join('- ' + r for r in trend.get('signal_reasons', ['N/A'])) if trend.get('signal_reasons') else '- N/A'}

**Risk factors**:
{chr(10).join('- ' + r for r in trend.get('risk_factors', ['N/A'])) if trend.get('risk_factors') else '- N/A'}
"""
                if consistency_notes:
                    prompt += f"""

**Consistency constraints**:
{chr(10).join('- ' + note for note in consistency_notes)}
"""
        
        # Bổ sung so sánh với phiên hôm qua.
        # Add the day-over-day comparison.
        if 'yesterday' in context:
            volume_change = context.get('volume_change_ratio', 'N/A')
            prompt += f"""
### Price-Volume Change
- Volume vs yesterday: {volume_change}x
- Price vs yesterday: {context.get('price_change_ratio', 'N/A')}%
"""
            parsed_volume_change = _safe_float(volume_change, default=math.nan)
            if math.isfinite(parsed_volume_change) and parsed_volume_change > 10:
                prompt += """
- ⚠️ Volume anomaly: volume is more than 10x yesterday's. This may come from bad data or a one-off surge, so weight it down. Do not treat it mechanically as strong confirmation.
"""
        
        # Bổ sung kết quả tìm kiếm tin tức (khu vực trọng điểm).
        # Add news search results (the key section).
        news_window_days: Optional[int] = None
        context_window = context.get("news_window_days")
        try:
            if context_window is not None:
                parsed_window = int(context_window)
                if parsed_window > 0:
                    news_window_days = parsed_window
        except (TypeError, ValueError):
            news_window_days = None

        if news_window_days is None:
            prompt_config = self._get_runtime_config()
            news_window_days = resolve_news_window_days(
                news_max_age_days=getattr(prompt_config, "news_max_age_days", 3),
                news_strategy_profile=getattr(prompt_config, "news_strategy_profile", "short"),
            )
        prompt += """
---

## 📰 News Intelligence
"""
        if news_context:
            prompt += f"""
Below are news search results for **{stock_name}({code})** from the last {news_window_days} days. Focus on extracting:
1. 🚨 **Risk alerts**: share reductions, penalties, negative news
2. 🎯 **Positive catalysts**: earnings, contracts, policy
3. 📊 **Earnings outlook**: guidance, preliminary results
4. 🕒 **Date rules (mandatory)**:
   - Every item written into `risk_alerts` / `positive_catalysts` / `latest_news` must carry a concrete date (YYYY-MM-DD)
   - Ignore any news outside the last {news_window_days} days
   - Ignore any news whose publication date cannot be determined

```
{news_context}
```
"""
        else:
            prompt += """
No recent news was found for this stock. Base the analysis mainly on the technical data.
"""

        # Chèn cảnh báo thiếu dữ liệu.
        # Inject the missing-data warning.
        if context.get('data_missing'):
            prompt += """
⚠️ **Missing data warning**
Because of upstream limits, the full realtime quote and technical indicators are unavailable.
**Ignore the N/A values in the tables above** and base the fundamental and sentiment analysis on the **[📰 News Intelligence]** section.
When answering technical questions (moving averages, bias, and so on), say plainly that the data is missing and no judgement can be made. **Never invent data.**
"""

        # Yêu cầu đầu ra rõ ràng.
        # Explicit output requirements.
        prompt += f"""
---

## ✅ Analysis Task

Produce the [Decision Dashboard] for **{stock_name}({code})**, strictly in the JSON format above.
"""
        if context.get('is_index_etf'):
            prompt += """
> ⚠️ **Index / ETF constraints**: this instrument is an index-tracking ETF or a market index.
> - Risk analysis covers only: **index trend, tracking error, market liquidity**
> - Never put the fund manager's litigation, reputation, or executive changes into the risk alerts
> - The earnings outlook comes from the **overall performance of the index constituents**, not the fund manager's financials
> - `risk_alerts` must not contain corporate risks about the fund manager

"""
        prompt += """
### ⚠️ Important: output the stock name in the correct format
The correct format is "Name (Ticker)", for example "Hoa Phat Group (HPG)".
If the name shown above is a generic placeholder built from the ticker, or is otherwise wrong, state the correct full company name at the start of the analysis, written in the report's output language.
"""
        if use_legacy_default_prompt:
            prompt += f"""

### Must answer explicitly:
1. ❓ Is the bullish alignment MA5>MA10>MA20 satisfied?
2. ❓ Is the current bias within the safe range (<5%)? Above 5% you must flag "do not chase"
3. ❓ Does volume confirm (low-volume pullback / high-volume breakout)?
4. ❓ Is there major negative news? (share reductions, penalties, earnings reversals, and so on)
"""
        else:
            prompt += f"""

### Must answer explicitly:
1. ❓ Does the current structure meet the activated skill's key trigger conditions?
2. ❓ Are the entry level and risk-reward reasonable? If the price is too stretched, state the conditions to wait for
3. ❓ Do volume and volatility support the current conclusion?
4. ❓ Is there major negative news, or anything that conflicts with the skill's conclusion?
5. ❓ If the conclusion holds, what exactly are the trigger conditions, the stop loss, and the checkpoints?
"""
        prompt += f"""

### Decision Dashboard requirements:
- **Stock name**: output the correct full company name (for example "Hoa Phat Group"), never a generic placeholder built from the ticker
- **Core conclusion**: one sentence saying whether to buy, sell, or wait
- **Advice by position**: what a flat holder should do vs what an existing holder should do
- **Concrete entry levels**: buy price, stop-loss price, target price (to the cent)
- **Checklist**: mark every item with ✅/⚠️/❌
- **News recency**: `latest_news`, `risk_alerts` and `positive_catalysts` must not contain anything older than {news_window_days} days or of unknown date
- **Technical consistency**: never treat mutually exclusive conclusions such as "bearish alignment" and "bullish alignment" as valid at the same time. If the fundamental or event picture conflicts with the technical one, write explicitly "event first, technicals yet to confirm" or "fundamentals lean bullish, but technicals have not confirmed"
 
Output the complete Decision Dashboard in JSON."""

        if report_language == "vi":
            prompt += """

### Output language requirements (highest priority)
- Keep every JSON key exactly as defined above; do not translate keys.
- `decision_type` must remain `buy`, `hold`, or `sell`.
- All human-readable JSON values must be in Vietnamese (Tiếng Việt), with correct diacritics.
- Use standard Vietnamese securities terminology (mua / nắm giữ / bán, hỗ trợ / kháng cự, cắt lỗ, khối lượng).
- The sample values inside the JSON schema above (for example `Expanding volume/Shrinking volume/Normal volume`) are illustrative English placeholders. Never copy them verbatim — write the Vietnamese equivalent instead.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, all nested dashboard text, checklist items, and every summary field.
- Use the company's common Vietnamese name when you are confident. If not, keep the listed company name rather than inventing one.
- When data is missing, explain it in Vietnamese instead of Chinese or English.
"""
        elif report_language == "en":
            prompt += """

### Output language requirements (highest priority)
- Keep every JSON key exactly as defined above; do not translate keys.
- `decision_type` must remain `buy`, `hold`, or `sell`.
- All human-readable JSON values must be in English.
- This includes `stock_name`, `trend_prediction`, `operation_advice`, `confidence_level`, all nested dashboard text, checklist items, and every summary field.
- Use the common English company name when you are confident. If not, keep the listed company name rather than inventing one.
- When data is missing, explain it in English instead of Chinese.
"""
        
        return prompt
    
    def _format_volume(self, volume: Optional[float], language: Optional[str] = None) -> str:
        """Định dạng khối lượng giao dịch. / Format the traded volume.

        Báo cáo tiếng Việt dùng bậc thập phân tiếng Việt (nghìn / triệu / tỷ) chứ không
        dùng bậc vạn/ức kiểu Trung Quốc. Báo cáo tiếng Anh dùng K / M / B.
        Vietnamese reports use thousand/million/billion units; English uses K/M/B.
        """
        if volume is None:
            return 'N/A'
        lang = normalize_report_language(
            language if language is not None else getattr(self, "report_language", None)
        )
        if lang == "vi":
            if volume >= 1e9:
                return f"{volume / 1e9:.2f} tỷ cp"
            if volume >= 1e6:
                return f"{volume / 1e6:.2f} triệu cp"
            if volume >= 1e3:
                return f"{volume / 1e3:.2f} nghìn cp"
            return f"{volume:.0f} cp"
        if volume >= 1e9:
            return f"{volume / 1e9:.2f}B shares"
        if volume >= 1e6:
            return f"{volume / 1e6:.2f}M shares"
        if volume >= 1e3:
            return f"{volume / 1e3:.2f}K shares"
        return f"{volume:.0f} shares"

    def _format_amount(self, amount: Optional[float], language: Optional[str] = None) -> str:
        """Định dạng giá trị giao dịch (tiếng Việt dùng triệu / tỷ). / Format turnover."""
        if amount is None:
            return 'N/A'
        lang = normalize_report_language(
            language if language is not None else getattr(self, "report_language", None)
        )
        if lang == "vi":
            if amount >= 1e9:
                return f"{amount / 1e9:.2f} tỷ VND"
            if amount >= 1e6:
                return f"{amount / 1e6:.2f} triệu VND"
            return f"{amount:.0f} VND"
        if amount >= 1e9:
            return f"{amount / 1e9:.2f}B VND"
        if amount >= 1e6:
            return f"{amount / 1e6:.2f}M VND"
        return f"{amount:.0f} VND"

    def _format_signed_amount(self, amount: Optional[float], language: Optional[str] = None) -> str:
        """Giá trị có dấu (mua/bán ròng). / Signed amount (net buy/sell)."""
        if amount is None:
            return 'N/A'
        sign = "-" if amount < 0 else "+"
        return f"{sign}{self._format_amount(abs(amount), language)}"

    def _build_vn_quote_rows(
        self, rt: Dict[str, Any], price_unit: str, language: Optional[str]
    ) -> str:
        """Các dòng dữ liệu phiên đặc thù Việt Nam; chỉ in dòng có giá trị.

        Vietnam-specific session rows; only rows with values are emitted so sources
        without these fields (SSI) do not add N/A noise to the prompt.
        """
        rows: List[str] = []
        price = rt.get('price')
        ref_price, ceiling, floor = rt.get('ref_price'), rt.get('ceiling'), rt.get('floor')
        if ref_price is not None:
            rows.append(f"| Reference price | {ref_price}{price_unit} | Exchange reference for today's change |")
        if ceiling is not None and floor is not None:
            reading = ""
            if isinstance(price, (int, float)) and price > 0:
                reading = (
                    f"{(ceiling - price) / price * 100:.2f}% to ceiling, "
                    f"{(price - floor) / price * 100:.2f}% above floor"
                )
            rows.append(f"| Ceiling / Floor | {ceiling} / {floor}{price_unit} | {reading} |")
        if rt.get('eps_ttm') is not None:
            rows.append(f"| EPS (TTM) | {rt['eps_ttm']:.0f}{price_unit} | Basis of the trailing PE |")
        if rt.get('roe') is not None:
            rows.append(f"| ROE | {rt['roe']}% | |")
        if rt.get('beta') is not None:
            rows.append(f"| Beta | {rt['beta']:.2f} | Sensitivity to the market (provider estimate) |")
        if rt.get('foreign_net_value') is not None:
            rows.append(
                f"| Foreign net (session) | {self._format_signed_amount(rt['foreign_net_value'], language)} | "
                f"Buy {self._format_amount(rt.get('foreign_buy_value'), language)} / "
                f"Sell {self._format_amount(rt.get('foreign_sell_value'), language)} |"
            )
        if rt.get('foreign_ownership_pct') is not None:
            room = rt.get('foreign_room_remaining')
            room_text = f"room left {room:,.0f} shares" if isinstance(room, (int, float)) else ""
            rows.append(f"| Foreign ownership | {rt['foreign_ownership_pct']}% | {room_text} |")
        if rt.get('put_through_value'):
            volume = rt.get('put_through_volume')
            volume_text = f"{volume:,.0f} shares" if isinstance(volume, (int, float)) else ""
            rows.append(
                f"| Put-through (negotiated) | {self._format_amount(rt['put_through_value'], language)} | "
                f"{volume_text}; excluded from matched volume |"
            )
        if rt.get('bid_ask_imbalance') is not None:
            rows.append(
                f"| Top-3 bid share | {rt['bid_ask_imbalance'] * 100:.1f}% | "
                f"Bid volume / (bid + ask) on the best 3 levels; > 50% = bids dominate |"
            )
        if rt.get('avg_volume_1m') is not None:
            rows.append(f"| Avg volume (1 month) | {rt['avg_volume_1m']:,} shares | Compare with today's volume |")
        return "\n".join(rows)

    @staticmethod
    def _build_corporate_events_section(fundamental_context: Any) -> str:
        """Mục sự kiện doanh nghiệp (giao dịch nội bộ, phát hành, KQKD) cho prompt.

        Corporate events section; empty when the blocks carry no event data.
        """
        if not isinstance(fundamental_context, dict):
            return ""

        def _block_data(name: str) -> Dict[str, Any]:
            block = fundamental_context.get(name)
            data = block.get("data") if isinstance(block, dict) else None
            return data if isinstance(data, dict) else {}

        earnings = _block_data("earnings")
        institution = _block_data("institution")
        if "insider_transaction_count" not in institution and not earnings.get("share_issuances") \
                and not earnings.get("earnings_releases"):
            return ""

        lines = ["", "### Corporate Events (exchange disclosures)"]
        if "insider_transaction_count" in institution:
            window = institution.get("insider_window_days", 90)
            count = institution.get("insider_transaction_count", 0)
            net = institution.get("insider_net_shares") or 0
            lines.append(
                f"- Insider trades, last {window} days: {count} disclosure(s); "
                f"net executed shares {net:+,.0f}"
            )
            for row in (institution.get("insider_transactions") or [])[:5]:
                executed = row.get("shares_executed")
                registered = row.get("shares_registered")
                lines.append(
                    f"  - {row.get('public_date')}: {row.get('name')} {row.get('action')} "
                    f"executed {executed or 0:,.0f} / registered {registered or 0:,.0f} shares"
                )
        for row in earnings.get("share_issuances") or []:
            ratio = row.get("ratio")
            ratio_text = f", ratio {ratio * 100:.2f}%" if isinstance(ratio, (int, float)) else ""
            volume = row.get("volume")
            volume_text = f"{volume:,.0f} shares" if isinstance(volume, (int, float)) else "volume N/A"
            lines.append(
                f"- Share issuance: {row.get('method_en') or row.get('method')} "
                f"(ex-date {row.get('ex_date')}, {volume_text}{ratio_text})"
            )
        releases = earnings.get("earnings_releases") or []
        if releases:
            latest = releases[0]
            lines.append(
                f"- Latest earnings release: Q{latest.get('quarter')}/{latest.get('year')} "
                f"published {latest.get('public_date')}"
            )
        lines.append(
            "> Ex-dates are derived as the trading day before the record date. "
            "Use these disclosures as context only; if a section is absent, say the data is missing."
        )
        return "\n".join(lines) + "\n"

    def _format_percent(self, value: Optional[float]) -> str:
        """Định dạng phần trăm. / Format a percentage."""
        if value is None:
            return 'N/A'
        try:
            return f"{float(value):.2f}%"
        except (TypeError, ValueError):
            return 'N/A'

    def _format_price(self, value: Optional[float]) -> str:
        """Định dạng giá. / Format a price."""
        if value is None:
            return 'N/A'
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return 'N/A'

    def _build_market_snapshot(
        self, context: Dict[str, Any], report_language: Optional[str] = None
    ) -> Dict[str, Any]:
        """Dựng ảnh chụp giá trong phiên (để hiển thị). / Build the quote snapshot for display."""
        today = context.get('today', {}) or {}
        realtime = context.get('realtime', {}) or {}
        yesterday = context.get('yesterday', {}) or {}

        prev_close = yesterday.get('close')
        close = today.get('close')
        high = today.get('high')
        low = today.get('low')

        amplitude = None
        change_amount = None
        if prev_close not in (None, 0) and high is not None and low is not None:
            try:
                amplitude = (float(high) - float(low)) / float(prev_close) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                amplitude = None
        if prev_close is not None and close is not None:
            try:
                change_amount = float(close) - float(prev_close)
            except (TypeError, ValueError):
                change_amount = None

        snapshot = {
            "date": context.get('date', 'Không rõ'),
            "close": self._format_price(close),
            "open": self._format_price(today.get('open')),
            "high": self._format_price(high),
            "low": self._format_price(low),
            "prev_close": self._format_price(prev_close),
            "pct_chg": self._format_percent(today.get('pct_chg')),
            "change_amount": self._format_price(change_amount),
            "amplitude": self._format_percent(amplitude),
            "volume": self._format_volume(today.get('volume'), report_language),
            "amount": self._format_amount(today.get('amount'), report_language),
        }

        if realtime:
            snapshot.update({
                "price": self._format_price(realtime.get('price')),
                "volume_ratio": realtime.get('volume_ratio', 'N/A'),
                "turnover_rate": self._format_percent(realtime.get('turnover_rate')),
                "source": getattr(realtime.get('source'), 'value', realtime.get('source', 'N/A')),
            })

        return snapshot

    def _check_content_integrity(
        self,
        result: AnalysisResult,
        *,
        require_phase_decision: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Delegate to module-level check_content_integrity."""
        return check_content_integrity(result, require_phase_decision=require_phase_decision)

    def _build_integrity_complement_prompt(self, missing_fields: List[str], report_language: str = "vi") -> str:
        """Build complement instruction for missing mandatory fields."""
        report_language = normalize_report_language(report_language)
        lines = ["### Completion requirements: fill the missing mandatory fields below and output the full JSON again:"]
        for f in missing_fields:
            if f == "sentiment_score":
                lines.append("- sentiment_score: integer score from 0 to 100")
            elif f == "operation_advice":
                lines.append("- operation_advice: localized action advice")
            elif f == "analysis_summary":
                lines.append("- analysis_summary: concise analysis summary")
            elif f == "dashboard.core_conclusion.one_sentence":
                lines.append("- dashboard.core_conclusion.one_sentence: one-line decision")
            elif f == "dashboard.intelligence.risk_alerts":
                lines.append("- dashboard.intelligence.risk_alerts: risk alert list (can be empty)")
            elif f == "dashboard.battle_plan.sniper_points.stop_loss":
                lines.append("- dashboard.battle_plan.sniper_points.stop_loss: stop-loss level")
            elif f == "dashboard.phase_decision.phase_context":
                lines.append("- dashboard.phase_decision.phase_context: public market phase summary subset")
            elif f == "dashboard.phase_decision.action_window":
                lines.append("- dashboard.phase_decision.action_window: phase-aware action window")
            elif f == "dashboard.phase_decision.immediate_action":
                lines.append("- dashboard.phase_decision.immediate_action: act now / wait / watch / no intraday action")
            elif f == "dashboard.phase_decision.watch_conditions":
                lines.append("- dashboard.phase_decision.watch_conditions: list of watch conditions")
            elif f == "dashboard.phase_decision.next_check_time":
                lines.append("- dashboard.phase_decision.next_check_time: next check point or market-local time")
            elif f == "dashboard.phase_decision.confidence_reason":
                lines.append("- dashboard.phase_decision.confidence_reason: confidence rationale and data limits")
            elif f == "dashboard.phase_decision.data_limitations":
                lines.append("- dashboard.phase_decision.data_limitations: list of phase/data quality limitations")
        return "\n".join(lines)

    def _build_integrity_retry_prompt(
        self,
        base_prompt: str,
        previous_response: str,
        missing_fields: List[str],
        report_language: str = "vi",
    ) -> str:
        """Build retry prompt using the previous response as the complement baseline."""
        complement = self._build_integrity_complement_prompt(missing_fields, report_language=report_language)
        previous_output = previous_response.strip()
        prefix = "### The previous output is below. Complete the missing fields based on that output and return the full JSON again. Do not omit existing fields:"
        return "\n\n".join([
            base_prompt,
            prefix,
            previous_output,
            complement,
        ])

    def _apply_placeholder_fill(self, result: AnalysisResult, missing_fields: List[str]) -> None:
        """Delegate to module-level apply_placeholder_fill."""
        apply_placeholder_fill(result, missing_fields)

    def _extract_analysis_json_object(self, response_text: str) -> Tuple[str, Dict[str, Any]]:
        """Extract the single allowed JSON object from an LLM response."""

        text = response_text or ""
        stripped = text.strip()
        if not stripped:
            raise ValueError("empty_response")

        fence_pattern = re.compile(
            r"```[ \t]*(?P<lang>[A-Za-z0-9_-]*)[ \t]*\n?(?P<body>.*?)```",
            flags=re.DOTALL,
        )
        fenced_matches = list(fence_pattern.finditer(text))
        if len(fenced_matches) > 1:
            raise ValueError("ambiguous_json")
        if len(fenced_matches) == 1:
            match = fenced_matches[0]
            outside = (text[:match.start()] + text[match.end():]).strip()
            if outside:
                raise ValueError("ambiguous_json")
            fence_lang = (match.group("lang") or "").strip().lower()
            if fence_lang not in {"", "json"}:
                raise ValueError("ambiguous_json")
            json_str = match.group("body").strip()
            data = self._load_analysis_json_candidate(json_str)
            return json_str, data
        if "```" in text:
            raise ValueError("ambiguous_json")

        try:
            data = self._load_analysis_json_candidate(stripped)
        except json.JSONDecodeError as exc:
            if self._contains_embedded_json_object(text):
                raise ValueError("ambiguous_json") from exc
            raise
        return stripped, data

    def _load_analysis_json_candidate(self, json_str: str) -> Dict[str, Any]:
        """Parse one already-selected JSON candidate, repairing common LLM JSON drift."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            stripped = (json_str or "").strip()
            try:
                _obj, end = json.JSONDecoder().raw_decode(stripped)
            except json.JSONDecodeError:
                pass
            else:
                if stripped[end:].strip():
                    raise
            if not (stripped.startswith("{") and stripped.endswith("}")):
                raise
            repaired = self._fix_json_string(stripped)
            data = json.loads(repaired)
        if not isinstance(data, dict):
            raise TypeError("json_root_not_object")
        return data

    @staticmethod
    def _contains_embedded_json_object(text: str) -> bool:
        decoder = json.JSONDecoder()
        count = 0
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                _obj, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            count += 1
            before = text[:index].strip()
            after = text[index + end:].strip()
            if count > 1 or before or after:
                return True
        return False

    def _validate_analysis_minimal_contract(self, data: Dict[str, Any]) -> None:
        try:
            AnalysisReportSchema.model_validate(data)
        except Exception as exc:
            logger.warning(
                "AnalysisReportSchema validation failed; continuing with raw parser contract: %s",
                str(exc)[:200],
            )
        minimal_keys = {
            "sentiment_score",
            "trend_prediction",
            "operation_advice",
            "analysis_summary",
            "dashboard",
        }
        if not any(key in data for key in minimal_keys):
            raise self._generation_validation_error(
                GenerationErrorCode.SCHEMA_VALIDATION_FAILED,
                reason="minimal_contract_failed",
                message="analysis JSON does not contain any minimal parser field",
            )
        if "sentiment_score" in data:
            try:
                int(data.get("sentiment_score", 50))
            except (TypeError, ValueError) as exc:
                raise self._generation_validation_error(
                    GenerationErrorCode.SCHEMA_VALIDATION_FAILED,
                    reason="parser_contract_failed",
                    message="sentiment_score must be integer-compatible",
                ) from exc

    def _generation_validation_error(
        self,
        error_code: GenerationErrorCode,
        *,
        reason: str,
        message: str,
    ) -> GenerationError:
        try:
            backend_id, _fallback_backend_id = self._resolve_generation_backend_config()
        except GenerationError:
            backend_id = "generation_backend"
        return GenerationError(
            error_code=error_code,
            stage="validation",
            retryable=True,
            fallbackable=True,
            backend=backend_id,
            provider=backend_id,
            details={
                "reason": reason,
                "message": message,
            },
        )

    def _parse_response(
        self, 
        response_text: str, 
        code: str, 
        name: str
    ) -> AnalysisResult:
        """
        Parse phản hồi LLM (bản decision dashboard).

        Cố trích kết quả phân tích dạng JSON, gồm cả trường ``dashboard``. Nếu parse
        thất bại thì thử trích thông minh hoặc trả về kết quả mặc định.
        Parse the LLM response; fall back to best-effort extraction on failure.
        """
        try:
            report_language = normalize_report_language(
                getattr(self._get_runtime_config(), "report_language", "vi")
            )
            try:
                _json_str, data = self._extract_analysis_json_object(response_text)
                self._validate_analysis_minimal_contract(data)
            except Exception as exc:
                logger.warning("Could not extract a single valid JSON object from the response; marking as parse failure: %s", exc)
                return self._parse_text_response(response_text, code, name)

            # Trích dữ liệu dashboard. / Extract the dashboard payload.
            dashboard = data.get('dashboard', None)
            guardrail_reason = data.get("guardrail_reason") or data.get("downgrade_reason")
            if guardrail_reason and isinstance(dashboard, dict):
                score_calibration = dashboard.get("decision_score_calibration")
                if not isinstance(score_calibration, dict):
                    score_calibration = {}
                    dashboard["decision_score_calibration"] = score_calibration
                score_calibration.setdefault("guardrail_reason", str(guardrail_reason).strip())
            # Chuẩn hoá signal_attribution (LLM có thể trả chuỗi, số âm, hoặc tổng khác 100).
            normalize_report_signal_attribution(dashboard)

            # Ưu tiên tên do AI trả về nếu tên gốc không hợp lệ hoặc chứa mã.
            ai_stock_name = data.get('stock_name')
            if ai_stock_name and (name.startswith(PLACEHOLDER_NAME_PREFIXES) or name == code or 'Unknown' in name):
                name = ai_stock_name

            # Parse mọi trường, có giá trị mặc định để tránh thiếu.
            # Parse decision_type; thiếu thì suy ra từ operation_advice.
            decision_type = data.get('decision_type', '')
            if not decision_type:
                op = data.get('operation_advice', localize_operation_advice('hold', report_language))
                decision_type = infer_decision_type_from_advice(op, default='hold')

            explicit_action = data.get("action")
            if explicit_action is None and isinstance(dashboard, dict):
                explicit_action = dashboard.get("action")

            result = AnalysisResult(
                code=code,
                name=name,
                # Chỉ số cốt lõi / core metrics
                sentiment_score=int(data.get('sentiment_score', 50)),
                trend_prediction=data.get('trend_prediction', localize_trend_prediction('sideways', report_language)),
                operation_advice=data.get('operation_advice', localize_operation_advice('hold', report_language)),
                decision_type=decision_type,
                confidence_level=localize_confidence_level(
                    data.get('confidence_level', localize_confidence_level('medium', report_language)),
                    report_language,
                ),
                report_language=report_language,
                # Decision dashboard
                dashboard=dashboard,
                # Phân tích xu hướng / trend analysis
                trend_analysis=data.get('trend_analysis', ''),
                short_term_outlook=data.get('short_term_outlook', ''),
                medium_term_outlook=data.get('medium_term_outlook', ''),
                # Kỹ thuật / technical
                technical_analysis=data.get('technical_analysis', ''),
                ma_analysis=data.get('ma_analysis', ''),
                volume_analysis=data.get('volume_analysis', ''),
                pattern_analysis=data.get('pattern_analysis', ''),
                # Cơ bản / fundamental
                fundamental_analysis=data.get('fundamental_analysis', ''),
                sector_position=data.get('sector_position', ''),
                company_highlights=data.get('company_highlights', ''),
                # Tâm lý và tin tức / sentiment and news
                news_summary=data.get('news_summary', ''),
                market_sentiment=data.get('market_sentiment', ''),
                hot_topics=data.get('hot_topics', ''),
                # Tổng hợp / summary
                analysis_summary=data.get('analysis_summary', _localized_text(
                    report_language, en='Analysis completed')),
                key_points=data.get('key_points', ''),
                risk_warning=data.get('risk_warning', ''),
                buy_reason=data.get('buy_reason', ''),
                # Metadata
                search_performed=data.get('search_performed', False),
                data_sources=data.get('data_sources', _localized_text(
                    report_language, en='Technical data')),
                success=True,
            )
            return populate_decision_action_fields(
                result,
                explicit_action=explicit_action,
                align_with_score=False,
            )
                
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed: {e}; marking as parse failure")
            return self._parse_text_response(response_text, code, name)
    
    def _fix_json_string(self, json_str: str) -> str:
        """Sửa các lỗi định dạng JSON thường gặp. / Fix common JSON format issues."""
        import re
        
        # Bỏ comment. / Strip comments.
        json_str = re.sub(r'//.*?\n', '\n', json_str)
        json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)
        
        # Sửa dấu phẩy thừa cuối. / Fix trailing commas.
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        
        # Ép boolean về chữ thường. / Force booleans to lowercase.
        json_str = json_str.replace('True', 'true').replace('False', 'false')
        
        # fix by json-repair
        json_str = repair_json(json_str)
        
        return json_str

    def _validate_json_response(self, text: str) -> None:
        """Validate that *text* contains one parser-compatible JSON object.

        Used as the ``response_validator`` argument to :meth:`_call_litellm` so
        that a JSON-less or unparseable reply from the primary model is treated
        as a model failure and triggers fallback to the next configured model.

        Raises:
            GenerationError: if the response has no unique parser-compatible
                JSON object, the selected JSON candidate cannot be parsed, or
                the parsed object cannot satisfy the minimal parser contract.
        """
        try:
            _json_str, data = self._extract_analysis_json_object(text)
        except ValueError as exc:
            reason = str(exc) or "invalid_json"
            if reason == "ambiguous_json":
                message = "JSON source is ambiguous"
            else:
                message = "No unique JSON object found in LLM response"
            raise self._generation_validation_error(
                GenerationErrorCode.INVALID_JSON,
                reason=reason,
                message=message,
            ) from exc
        except json.JSONDecodeError as exc:
            raise self._generation_validation_error(
                GenerationErrorCode.INVALID_JSON,
                reason="invalid_json",
                message=str(exc)[:200],
            ) from exc
        except Exception as exc:
            raise self._generation_validation_error(
                GenerationErrorCode.INVALID_JSON,
                reason="invalid_json",
                message=str(exc)[:200],
            ) from exc

        self._validate_analysis_minimal_contract(data)
    
    def _parse_text_response(
        self, 
        response_text: str, 
        code: str, 
        name: str
    ) -> AnalysisResult:
        """Cố trích thông tin phân tích từ phản hồi văn bản thuần.

        Best-effort extraction from a plain-text (non-JSON) response.
        """
        report_language = normalize_report_language(
            getattr(self._get_runtime_config(), "report_language", "vi")
        )
        # Nhận diện từ khoá để đoán tâm lý. / Keyword-based sentiment guess.
        sentiment_score = 50
        trend = localize_trend_prediction('sideways', report_language)
        advice = localize_operation_advice('hold', report_language)
        
        text_lower = response_text.lower()
        
        # Nhận diện tâm lý đơn giản. / Simple sentiment detection.
        positive_keywords = ['tích cực', 'mua', 'tăng giá', 'bứt phá', 'tăng mạnh', 'tin tốt', 'mua thêm', 'bullish', 'buy']
        negative_keywords = ['tiêu cực', 'bán', 'giảm giá', 'thủng', 'suy yếu', 'tin xấu', 'giảm tỷ trọng', 'bearish', 'sell']
        
        positive_count = sum(1 for kw in positive_keywords if kw in text_lower)
        negative_count = sum(1 for kw in negative_keywords if kw in text_lower)
        
        if positive_count > negative_count + 1:
            sentiment_score = 65
            trend = localize_trend_prediction('bullish', report_language)
            advice = localize_operation_advice('buy', report_language)
            decision_type = 'buy'
        elif negative_count > positive_count + 1:
            sentiment_score = 35
            trend = localize_trend_prediction('bearish', report_language)
            advice = localize_operation_advice('sell', report_language)
            decision_type = 'sell'
        else:
            decision_type = 'hold'
        
        # Lấy 500 ký tự đầu làm tóm tắt. / First 500 chars as the summary.
        summary = response_text[:500] if response_text else _localized_text(
            report_language, en='No analysis result')
        
        result = AnalysisResult(
            code=code,
            name=name,
            sentiment_score=sentiment_score,
            trend_prediction=trend,
            operation_advice=advice,
            decision_type=decision_type,
            confidence_level=localize_confidence_level('low', report_language),
            analysis_summary=summary,
            key_points=_localized_text(
                report_language,
                en='JSON parsing failed; treat this as best-effort output.',
            ),
            risk_warning=_localized_text(
                report_language,
                en='The result may be inaccurate. Cross-check with other information.',
            ),
            raw_response=response_text,
            success=False,
            error_message='LLM response is not valid JSON; analysis result will not be persisted',
            report_language=report_language,
        )
        return populate_decision_action_fields(result, align_with_score=False)
    
    def batch_analyze(
        self, 
        contexts: List[Dict[str, Any]],
        delay_between: float = 2.0
    ) -> List[AnalysisResult]:
        """
        Phân tích hàng loạt nhiều mã. / Analyze several stocks in a batch.

        Lưu ý: có độ trễ giữa các lần phân tích để tránh bị giới hạn tần suất API.

        Args:
            contexts: danh sách dữ liệu ngữ cảnh
            delay_between: độ trễ giữa hai lần phân tích (giây)

        Returns:
            Danh sách ``AnalysisResult``.
        """
        results = []
        
        for i, context in enumerate(contexts):
            if i > 0:
                logger.debug(f"Waiting {delay_between}s before continuing...")
                time.sleep(delay_between)
            
            result = self.analyze(context)
            results.append(result)
        
        return results


# Hàm tiện lợi / convenience helper.
def get_analyzer() -> GeminiAnalyzer:
    """Lấy instance bộ phân tích LLM. / Get the LLM analyzer instance."""
    return GeminiAnalyzer()


if __name__ == "__main__":
    # Mã thử nghiệm / demo code.
    logging.basicConfig(level=logging.DEBUG)
    
    # Dữ liệu context giả lập / mock context data.
    test_context = {
        'code': '600519',
        'date': '2026-01-09',
        'today': {
            'open': 1800.0,
            'high': 1850.0,
            'low': 1780.0,
            'close': 1820.0,
            'volume': 10000000,
            'amount': 18200000000,
            'pct_chg': 1.5,
            'ma5': 1810.0,
            'ma10': 1800.0,
            'ma20': 1790.0,
            'volume_ratio': 1.2,
        },
        'ma_status': 'MA xếp tăng 📈',
        'volume_change_ratio': 1.3,
        'price_change_ratio': 1.5,
    }
    
    analyzer = GeminiAnalyzer()
    
    if analyzer.is_available():
        print("=== Kiểm thử phân tích AI ===")
        result = analyzer.analyze(test_context)
        print(f"Kết quả phân tích: {result.to_dict()}")
    else:
        print("Chưa cấu hình Gemini API, bỏ qua kiểm thử")
