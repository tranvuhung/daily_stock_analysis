# -*- coding: utf-8 -*-
"""
===================================
Market review analysis module
===================================

Responsibilities (chỉ thị trường Việt Nam / Vietnam market only):
1. Fetch market index data (VN-Index, VN30, HNX-Index, UPCoM-Index)
2. Search market news to build review intelligence
3. Use an LLM to generate the daily market review report
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from inspect import getattr_static
from typing import Optional, Dict, Any, List

from src.agent.provider_trace import resolved_model_provider_identity
from src.config import get_config
from src.report_language import normalize_report_language
from src.search_service import SearchService
from src.core.market_profile import get_profile, MarketProfile
from src.core.market_strategy import get_market_strategy_blueprint
from src.llm.backend_registry import (
    LOCAL_CLI_GENERATION_BACKEND_IDS,
    LITELLM_BACKEND_ID,
    resolve_generation_backend_id,
    resolve_generation_fallback_backend_id,
)
from src.llm.generation_backend import GenerationError, GenerationResult
from src.schemas.market_light import MARKET_LIGHT_REGIONS, MarketLightSnapshot
from src.services.run_diagnostics import record_llm_run, record_llm_run_started
from src.services.intelligence_service import IntelligenceService
from data_provider.base import DataFetcherManager

logger = logging.getLogger(__name__)

_LEGACY_ANALYZER_BACKEND_ID = "legacy_analyzer"

# Nhận diện mục theo số thứ tự: LLM dịch tiêu đề sang tiếng Việt và thêm emoji
# ("### 1. 📝 Tóm tắt Thị trường"), nên khớp theo tên tiếng Anh sẽ trượt và bảng số liệu
# không bao giờ được chèn. Tên tiếng Anh cũ vẫn khớp.
# Match sections by number: the model translates headings and adds emoji, so English-name
# matching missed every Vietnamese report. Legacy English names still match.
_SECTION_PATTERNS = {
    "market_summary": r"(?im)^###\s*(?:1\.[^\n]*|Market Summary[^\n]*)$",
    "index_commentary": r"(?im)^###\s*(?:2\.[^\n]*|(?:Index Commentary|Major Indices)[^\n]*)$",
}

# Nhãn khối số liệu chèn vào báo cáo. / Labels for data blocks injected into the report.
_DATA_BLOCK_LABELS = {
    "vi": {
        "market_signal": "Tín hiệu thị trường",
        "drivers": "Động lực",
        "guidance": "Khuyến nghị",
        "breadth": "Độ rộng",
        "advancers": "Tăng",
        "decliners": "Giảm",
        "flat": "Đứng giá",
        "limit_up": "Trần",
        "limit_down": "Sàn",
        "turnover": "Giá trị giao dịch",
        "index_header": "| Chỉ số | Đóng cửa | Thay đổi % | Mở cửa | Cao | Thấp | Biên độ | GTGD ({unit}) |",
    },
    "en": {
        "market_signal": "Market Signal",
        "drivers": "Drivers",
        "guidance": "Guidance",
        "breadth": "Breadth",
        "advancers": "Advancers",
        "decliners": "Decliners",
        "flat": "Flat",
        "limit_up": "Limit-up",
        "limit_down": "Limit-down",
        "turnover": "Turnover",
        "index_header": "| Index | Last | Change % | Open | High | Low | Amplitude | Turnover ({unit}) |",
    },
}

# Bản tiếng Việt của nhãn/khuyến nghị market light khi hiển thị trong báo cáo; payload
# `market_light` giữ giá trị tiếng Anh (share image đọc chúng).
# Vietnamese display text for market-light labels; the `market_light` payload stays English.
_MARKET_LIGHT_VI_TEXT = {
    "temperature": {
        "risk-on": "hưng phấn",
        "constructive": "tích cực",
        "mixed": "phân hoá",
        "defensive": "phòng thủ",
    },
    "label": {
        "risk-on": "ưa rủi ro",
        "balanced": "cân bằng",
        "risk-off": "né rủi ro",
    },
    "guidance": {
        "green": "Khẩu vị rủi ro ổn; tập trung nhóm dẫn dắt và giữ kỷ luật tỷ trọng.",
        "yellow": "Tín hiệu trái chiều; giữ tỷ trọng vừa phải và chờ xác nhận.",
        "red": "Rủi ro cao; ưu tiên kiểm soát sụt giảm, tránh mua đuổi các nhịp hồi yếu.",
    },
}

# Ngân sách token đầu ra cho điểm lại thị trường. Model có suy luận (vd Gemini 3) tính token
# suy luận vào trần này; khi chạm trần, văn bản bị đứt giữa câu nên gọi lại một lần với ngân
# sách lớn hơn. Output-token budget for the market review; thinking models count reasoning
# tokens against it, so a capped response is retried once with a larger budget.
_MARKET_REVIEW_MAX_TOKENS = 8192
_MARKET_REVIEW_RETRY_MAX_TOKENS = 32768
# Coi là chạm trần khi completion_tokens còn cách trần không quá ngưỡng này.
_MARKET_REVIEW_TRUNCATION_MARGIN = 64

_TRUNCATED_REVIEW_NOTE = {
    "vi": "> ⚠️ Báo cáo có thể chưa đầy đủ do mô hình chạm giới hạn độ dài đầu ra.",
    "en": "> ⚠️ This report may be incomplete because the model hit its output length limit.",
}


@dataclass
class MarketIndex:
    """Market index data"""
    code: str                    # Index code
    name: str                    # Index name
    current: float = 0.0         # Current level
    change: float = 0.0          # Point change
    change_pct: float = 0.0      # Change percentage (%)
    open: float = 0.0            # Open level
    high: float = 0.0            # High level
    low: float = 0.0             # Low level
    prev_close: float = 0.0      # Previous close level
    volume: float = 0.0          # Volume (lots)
    amount: float = 0.0          # Giá trị giao dịch (VND) / Turnover (VND)
    amplitude: float = 0.0       # Amplitude (%)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'current': self.current,
            'change': self.change,
            'change_pct': self.change_pct,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'volume': self.volume,
            'amount': self.amount,
            'amplitude': self.amplitude,
        }


@dataclass
class MarketOverview:
    """Market overview data"""
    date: str                           # Date
    indices: List[MarketIndex] = field(default_factory=list)  # Major indices
    up_count: int = 0                   # Number of advancing stocks
    down_count: int = 0                 # Number of declining stocks
    flat_count: int = 0                 # Number of unchanged stocks
    limit_up_count: int = 0             # Number of limit-up stocks
    limit_down_count: int = 0           # Number of limit-down stocks
    total_amount: float = 0.0           # Tổng giá trị giao dịch (tỷ đồng) / Total turnover (VND bn)
    put_through_amount: float = 0.0     # Giá trị thoả thuận (tỷ đồng) / Put-through value (VND bn)
    foreign_net_amount: Optional[float] = None  # Khối ngoại mua ròng 3 sàn (tỷ) / Foreign net (VND bn)
    # Dòng tiền khối ngoại / tự doanh theo kỳ (xem FiinTradeFetcher.get_market_money_flow)
    # Foreign / proprietary flows by period (see FiinTradeFetcher.get_market_money_flow)
    money_flow: Optional[Dict[str, Any]] = None
    # Mã dẫn dắt chỉ số, đột biến KL, diễn biến trong phiên (FiinTradeFetcher.get_market_drivers)
    # Index contributors, volume breakouts, intraday path (see FiinTradeFetcher.get_market_drivers)
    market_drivers: Optional[Dict[str, Any]] = None


@dataclass
class MarketLightReviewResult:
    """Internal market-review parts built from one overview fetch."""

    overview: MarketOverview
    report: str
    market_light_snapshot: Optional[Dict[str, Any]]
    structured_payload: Dict[str, Any] = field(default_factory=dict)


class MarketAnalyzer:
    """
    Market review analyzer
    
    Features:
    1. Fetch real-time market index quotes
    2. Fetch market advance/decline statistics
    3. Search market news
    4. Generate the market review report

    Chỉ hỗ trợ thị trường Việt Nam ('vn'). / Only the Vietnam market ('vn') is supported.
    """
    
    def __init__(
        self,
        search_service: Optional[SearchService] = None,
        analyzer=None,
        region: str = "vn",
        config: Optional[Any] = None,
    ):
        """
        Initialize the market analyzer

        Args:
            search_service: Search service instance
            analyzer: AI analyzer instance (used to call the LLM)
            region: Vùng thị trường; mọi giá trị đều được quy về 'vn'. / Market region; always normalized to 'vn'.
            config: Config used for this review; reads the global config when not provided
        """
        self.config = config or get_config()
        self.search_service = search_service
        self.analyzer = analyzer
        self.data_manager = DataFetcherManager()
        # Chỉ còn thị trường Việt Nam: bỏ qua giá trị đầu vào và luôn dùng 'vn'.
        # Only Vietnam remains: ignore the input value and always use 'vn'.
        self.region = "vn"
        self.profile: MarketProfile = get_profile(self.region)
        self.strategy = get_market_strategy_blueprint(self.region)

    def _log_context(self) -> str:
        return f"component=market_review region={self.region}"

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

    def _resolve_recorded_provider(
        self,
        *,
        provider: str,
        model: str,
        backend: str,
        usage_provider: str = "",
        response_model: str = "",
    ) -> str:
        """Resolve LiteLLM router aliases before persisting diagnostics."""
        normalized_backend = str(backend or "").strip().lower()
        normalized_model = str(model or "").strip()
        normalized_usage_provider = str(usage_provider or "").strip()
        normalized_response_model = str(response_model or "").strip()
        normalized_provider = str(provider or "").strip()
        resolved_route_provider = ""
        if normalized_backend == "litellm" and normalized_model:
            resolved_route_model, resolved_route_provider = resolved_model_provider_identity(
                normalized_model,
                getattr(self.config, "llm_model_list", None) or [],
            )
            normalized_route = str(resolved_route_model or normalized_model).strip().lower()
            if normalized_route.startswith("openai/~") or "openrouter" in normalized_route:
                resolved_route_provider = "openrouter"
        resolved_response_provider = ""
        if normalized_response_model:
            resolved_response_provider = self._resolve_configured_response_provider(
                normalized_model,
                normalized_response_model,
                getattr(self.config, "llm_model_list", None) or [],
            )
            if resolved_response_provider == "openrouter":
                return resolved_response_provider
        if normalized_usage_provider:
            return normalized_usage_provider
        if normalized_response_model:
            if resolved_response_provider:
                return resolved_response_provider
            if resolved_route_provider == "openrouter":
                return resolved_route_provider
            _wire_model, resolved_provider = resolved_model_provider_identity(
                normalized_response_model,
            )
            if resolved_provider:
                return resolved_provider
        if normalized_backend != "litellm" or not normalized_model:
            return normalized_provider
        return resolved_route_provider or normalized_provider or "openai"

    def _resolve_recorded_error_model(
        self,
        *,
        error: Any,
        fallback_model: str = "",
    ) -> str:
        """Preserve route/model diagnostics for LiteLLM configuration failures."""
        details = getattr(error, "details", None)
        if isinstance(details, dict):
            visited: set[int] = set()

            def _find_error_model(payload: Dict[str, Any]) -> str:
                payload_id = id(payload)
                if payload_id in visited:
                    return ""
                visited.add(payload_id)
                for key in ("last_model", "route_name"):
                    candidate = str(payload.get(key) or "").strip()
                    if candidate:
                        return candidate
                fallback_error = payload.get("fallback_error")
                if isinstance(fallback_error, dict):
                    nested_details = fallback_error.get("details")
                    if isinstance(nested_details, dict):
                        candidate = _find_error_model(nested_details)
                        if candidate:
                            return candidate
                    return _find_error_model(fallback_error)
                return ""

            candidate = _find_error_model(details)
            if candidate:
                return candidate
        backend = str(getattr(error, "backend", "") or "").strip()
        configured_model = str(getattr(self.config, "litellm_model", "") or "").strip()
        normalized_fallback_model = str(fallback_model or "").strip()
        if backend == "litellm":
            return normalized_fallback_model or configured_model or backend
        return normalized_fallback_model or backend

    def _get_output_language(self) -> str:
        """Ngôn ngữ báo cáo thực tế: 'en' hoặc 'vi' (mọi giá trị khác quy về 'vi').

        Truthful report language: 'en' or 'vi' (anything else falls back to 'vi').
        """
        language = normalize_report_language(
            getattr(getattr(self, "config", None), "report_language", "vi")
        )
        return "en" if language == "en" else "vi"

    def _get_review_language(self) -> str:
        # Ngôn ngữ khung cấu trúc; vi và en dùng chung khung tiếng Anh,
        # chỉ thị ngôn ngữ đầu ra được áp dụng trong prompt.
        # Structural/template language; vi and en share the English scaffolding.
        return self._get_output_language()

    def _get_template_review_language(self) -> str:
        return self._get_review_language()

    def _get_market_scope_name(self, review_language: str | None = None) -> str:
        return "Vietnam market"

    def _get_turnover_unit_label(self) -> str:
        """Đơn vị giá trị giao dịch: tỷ đồng. / Turnover unit label: VND bn."""
        return "VND bn"

    def _get_report_turnover_unit(self, language: str) -> str:
        """Đơn vị hiển thị trong báo cáo (prompt vẫn dùng nhãn tiếng Anh). / Report-facing unit label."""
        return "tỷ đồng" if language == "vi" else self._get_turnover_unit_label()

    def _format_turnover_value(self, amount_raw: float) -> str:
        """Định dạng giá trị giao dịch thô (VND) sang tỷ đồng. / Format raw VND turnover as VND bn."""
        if amount_raw == 0.0:
            return "N/A"
        # VND -> tỷ đồng, số nguyên có phân tách hàng nghìn (ví dụ 21,345)
        return f"{amount_raw / 1e9:,.0f}"

    def _get_index_change_arrow(self, change_pct: float) -> str:
        if change_pct == 0:
            return "⚪"
        color_scheme = getattr(getattr(self, "config", None), "market_review_color_scheme", "green_up")
        if color_scheme == "red_up":
            return "🔴" if change_pct > 0 else "🟢"
        return "🟢" if change_pct > 0 else "🔴"

    def _get_review_title(self, date: str) -> str:
        return f"## {date} Vietnam Market Recap"

    def _get_index_hint(self) -> str:
        return (
            "Analyze the key moves in the VN-Index, VN30, HNX-Index, UPCoM-Index, "
            "and how aligned the three exchanges are."
        )

    def _get_strategy_prompt_block(self) -> str:
        return self.strategy.to_prompt_block()

    def _get_strategy_markdown_block(self, review_language: str | None = None) -> str:
        return """### 6. Strategy Framework
- **Trend Regime**: Classify Vietnam equities as advancing, range-bound, or defensive based on VN-Index/VN30 alignment.
- **Liquidity & Breadth**: Track turnover versus recent sessions and advance/decline plus ceiling/floor structure.
- **Theme Signals**: Focus on banks, securities, real estate rotation and macro catalysts such as market upgrade or public investment.
"""

    def _get_market_mood_text(self, mood_key: str, review_language: str | None = None) -> str:
        mapping = {
            "strong_up": "strong gains",
            "mild_up": "moderate gains",
            "mild_down": "mild losses",
            "strong_down": "clear weakness",
            "range": "range-bound trading",
        }
        return mapping[mood_key]

    def get_market_overview(self) -> MarketOverview:
        """
        Fetch market overview data
        
        Returns:
            MarketOverview: Market overview data object
        """
        today = datetime.now().strftime('%Y-%m-%d')
        overview = MarketOverview(date=today)
        
        # 1. Lấy giá các chỉ số chính / Fetch major index quotes
        overview.indices = self._get_main_indices()

        # 2. Lấy độ rộng thị trường (tăng/giảm/trần/sàn) / Fetch advance/decline statistics
        if self.profile.has_market_stats:
            self._get_market_statistics(overview)
            # 3. Dòng tiền khối ngoại / tự doanh / Foreign and proprietary flows
            self._get_money_flow(overview)
            # 4. Mã dẫn dắt, đột biến KL, diễn biến trong phiên / Market drivers
            self._get_market_drivers(overview)

        return overview

    def _get_market_drivers(self, overview: MarketOverview) -> None:
        """Lấy động lực thị trường; lỗi chỉ log, không chặn điểm lại thị trường."""
        getter = getattr(self.data_manager, "get_market_drivers", None)
        if not callable(getter):
            return
        try:
            drivers = getter(region=self.region)
        except Exception as e:
            logger.error("[Thị trường] %s action=get_market_drivers status=failed error=%s", self._log_context(), e)
            return
        if isinstance(drivers, dict) and any(
            drivers.get(key) for key in ("index_contribution", "volume_breakouts", "intraday")
        ):
            overview.market_drivers = drivers
            logger.info("[Thị trường] %s action=get_market_drivers status=success", self._log_context())
        else:
            logger.info("[Thị trường] %s action=get_market_drivers status=empty", self._log_context())

    def _get_money_flow(self, overview: MarketOverview) -> None:
        """Lấy dòng tiền toàn thị trường; lỗi chỉ log, không chặn điểm lại thị trường."""
        getter = getattr(self.data_manager, "get_market_money_flow", None)
        if not callable(getter):
            return
        try:
            flow = getter(region=self.region)
        except Exception as e:
            logger.error("[Thị trường] %s action=get_money_flow status=failed error=%s", self._log_context(), e)
            return
        if isinstance(flow, dict) and (flow.get("foreign") or flow.get("proprietary")):
            overview.money_flow = flow
            logger.info("[Thị trường] %s action=get_money_flow status=success", self._log_context())
        else:
            logger.info("[Thị trường] %s action=get_money_flow status=empty", self._log_context())

    
    def _get_main_indices(self) -> List[MarketIndex]:
        """Fetch real-time quotes of major indices"""
        indices = []

        try:
            logger.info("[Thị trường] %s action=get_main_indices status=start", self._log_context())

            # Lấy giá chỉ số qua DataFetcherManager / Fetch index quotes via DataFetcherManager
            data_list = self.data_manager.get_main_indices(region=self.region)

            if data_list:
                for item in data_list:
                    index = MarketIndex(
                        code=item['code'],
                        name=item['name'],
                        current=item['current'],
                        change=item['change'],
                        change_pct=item['change_pct'],
                        open=item['open'],
                        high=item['high'],
                        low=item['low'],
                        prev_close=item['prev_close'],
                        volume=item['volume'],
                        amount=item['amount'],
                        amplitude=item['amplitude']
                    )
                    indices.append(index)

            if not indices:
                logger.warning("[Thị trường] %s action=get_main_indices status=empty", self._log_context())
            else:
                logger.info(
                    "[Thị trường] %s action=get_main_indices status=success count=%d",
                    self._log_context(),
                    len(indices),
                )

        except Exception as e:
            logger.error("[Thị trường] %s action=get_main_indices status=failed error=%s", self._log_context(), e)

        return indices

    def _get_market_statistics(self, overview: MarketOverview):
        """Fetch market advance/decline statistics"""
        try:
            logger.info("[Thị trường] %s action=get_market_stats status=start", self._log_context())

            stats = self.data_manager.get_market_stats(
                purpose=f"market_review:{self.region}",
                region=self.region,
            )

            if stats:
                overview.up_count = stats.get('up_count', 0)
                overview.down_count = stats.get('down_count', 0)
                overview.flat_count = stats.get('flat_count', 0)
                overview.limit_up_count = stats.get('limit_up_count', 0)
                overview.limit_down_count = stats.get('limit_down_count', 0)
                overview.total_amount = stats.get('total_amount', 0.0)
                overview.put_through_amount = stats.get('put_through_amount') or 0.0
                overview.foreign_net_amount = stats.get('foreign_net_amount')

                logger.info(
                    "[Thị trường] %s action=get_market_stats status=success up=%s down=%s flat=%s "
                    "limit_up=%s limit_down=%s amount=%.0f(%s)",
                    self._log_context(),
                    overview.up_count,
                    overview.down_count,
                    overview.flat_count,
                    overview.limit_up_count,
                    overview.limit_down_count,
                    overview.total_amount,
                    self._get_turnover_unit_label(),
                )
            else:
                logger.warning("[Thị trường] %s action=get_market_stats status=empty", self._log_context())

        except Exception as e:
            logger.error("[Thị trường] %s action=get_market_stats status=failed error=%s", self._log_context(), e)

    def search_market_news(self) -> List[Dict]:
        """
        Search market news
        
        Returns:
            List of news items
        """
        if not self.search_service:
            logger.warning(
                "[Thị trường] %s action=search_market_news status=skipped reason=no_search_service",
                self._log_context(),
            )
            return []
        
        all_news = []

        search_queries = self.profile.news_queries
        # Dùng mã chỉ số để SearchService nhận diện ngữ cảnh Việt Nam
        # (_is_vn_symbol) và ưu tiên nguồn tin tài chính tiếng Việt.
        # Use the index code so SearchService detects the Vietnam context.
        market_name = "VNINDEX"
        
        try:
            logger.info("[Thị trường] %s action=search_market_news status=start", self._log_context())

            for query in search_queries:
                response = self.search_service.search_stock_news(
                    stock_code="market",
                    stock_name=market_name,
                    max_results=3,
                    focus_keywords=query.split()
                )
                if response and response.results:
                    all_news.extend(response.results)
                    logger.info(
                        "[Thị trường] %s action=search_market_news status=query_success count=%d",
                        self._log_context(),
                        len(response.results),
                    )
            
            logger.info(
                "[Thị trường] %s action=search_market_news status=success count=%d",
                self._log_context(),
                len(all_news),
            )
            
        except Exception as e:
            logger.error("[Thị trường] %s action=search_market_news status=failed error=%s", self._log_context(), e)
        
        return all_news
    
    def generate_market_review(self, overview: MarketOverview, news: List) -> str:
        """
        Use an LLM to generate the market review report
        
        Args:
            overview: Market overview data
            news: Market news list (list of SearchResult objects)
            
        Returns:
            Market review report text
        """
        backend_error = self._get_analyzer_generation_backend_config_error()
        if backend_error is not None:
            logger.error(
                "[Thị trường] %s action=generate_review status=failed error_type=%s error=%s",
                self._log_context(),
                type(backend_error).__name__,
                backend_error,
            )
            record_llm_run(
                success=False,
                provider=backend_error.provider or backend_error.backend,
                model=self._resolve_recorded_error_model(error=backend_error),
                call_type="market_review",
                error_type=type(backend_error).__name__,
                error_message=backend_error,
            )
            raise backend_error

        if not self.analyzer or not self.analyzer.is_available():
            logger.warning(
                "[Thị trường] %s action=generate_review status=fallback_template reason=no_analyzer",
                self._log_context(),
            )
            return self._generate_template_review(overview, news)

        # Build the prompt
        prompt = self._build_review_prompt(overview, news)

        logger.info("[Thị trường] %s action=generate_review status=start", self._log_context())
        # Use public analyzer APIs so diagnostics reflect the actual execution backend.
        llm_started_at = time.perf_counter()
        provider, model = self._get_analyzer_generation_backend_identity()
        try:
            record_llm_run_started(
                provider=provider,
                model=model,
                call_type="market_review",
            )
            generation_result = self._generate_market_review_with_metadata(
                prompt,
                provider=provider,
                model=model,
            )
            generation_result, review_truncated = self._retry_truncated_market_review(
                generation_result,
                prompt,
                provider=provider,
                model=model,
            )
            review = generation_result.text if generation_result else None
            generation_diagnostics = (
                getattr(generation_result, "diagnostics", None) if generation_result is not None else None
            )
            if generation_result is not None:
                model = generation_result.model or model
                usage_payload = getattr(generation_result, "usage", None)
                provider = self._resolve_recorded_provider(
                    provider=generation_result.provider or generation_result.backend or provider,
                    model=model,
                    backend=generation_result.backend or "",
                    usage_provider=(
                        usage_payload.get("provider") if isinstance(usage_payload, dict) else ""
                    ),
                    response_model=(
                        usage_payload.get("response_model") if isinstance(usage_payload, dict) else ""
                    ),
                )
        except Exception as exc:
            error_provider = getattr(exc, "provider", None) or getattr(exc, "backend", None) or provider
            error_model = self._resolve_recorded_error_model(
                error=exc,
                fallback_model=model,
            )
            record_llm_run(
                success=False,
                provider=error_provider,
                model=error_model,
                call_type="market_review",
                duration_ms=int((time.perf_counter() - llm_started_at) * 1000),
                error_type=type(exc).__name__,
                error_message=exc,
            )
            raise

        failed_all_models = (
            isinstance(generation_diagnostics, dict)
            and generation_diagnostics.get("reason") == "all_models_failed"
        )
        record_llm_run(
            success=bool(review),
            provider=provider,
            model=model,
            call_type="market_review",
            duration_ms=int((time.perf_counter() - llm_started_at) * 1000),
            error_type=None if review else ("AllModelsFailed" if failed_all_models else "EmptyResponse"),
            error_message=(
                None
                if review
                else (
                    generation_diagnostics
                    if failed_all_models
                    else "empty market review response"
                )
            ),
        )

        if review:
            logger.info(
                "[Thị trường] %s action=generate_review status=success length=%d truncated=%s",
                self._log_context(),
                len(review),
                review_truncated,
            )
            if review_truncated:
                review = f"{review.rstrip()}\n\n{_TRUNCATED_REVIEW_NOTE[self._get_output_language()]}\n"
            # Inject structured data tables into LLM prose sections
            return self._inject_data_into_review(review, overview, news)

        logger.warning(
            "[Thị trường] %s action=generate_review status=fallback_template reason=empty_llm_response",
            self._log_context(),
        )
        return self._generate_template_review(overview, news)

    def _get_analyzer_generation_backend_config_error(self) -> Optional[GenerationError]:
        """Return analyzer backend config errors without relying on dynamic mock attributes."""
        if self.analyzer is None:
            try:
                resolve_generation_backend_id(self.config)
                resolve_generation_fallback_backend_id(self.config)
            except GenerationError as exc:
                return exc
            return None
        missing = object()
        if getattr_static(self.analyzer, "get_generation_backend_config_error", missing) is missing:
            return None
        method = getattr(self.analyzer, "get_generation_backend_config_error", None)
        if not callable(method):
            return None
        error = method()
        return error if isinstance(error, GenerationError) else None

    def _get_configured_generation_backend_identity(self) -> tuple[str, str]:
        """Best-effort backend identity for legacy analyzers without metadata APIs."""
        backend_id = str(getattr(self.config, "generation_backend", "") or "").strip().lower()
        if not backend_id:
            backend_id = LITELLM_BACKEND_ID
        if backend_id in LOCAL_CLI_GENERATION_BACKEND_IDS:
            return backend_id, backend_id
        return backend_id, str(getattr(self.config, "litellm_model", "") or "")

    def _get_legacy_analyzer_generation_backend_identity(self) -> tuple[str, str]:
        """Return a neutral identity for injected analyzers that expose no metadata APIs."""
        return _LEGACY_ANALYZER_BACKEND_ID, _LEGACY_ANALYZER_BACKEND_ID

    def _get_analyzer_generation_backend_identity(self) -> tuple[str, str]:
        """Use analyzer metadata API when available, otherwise fall back to configured identity."""
        if self.analyzer is None:
            return self._get_configured_generation_backend_identity()
        missing = object()
        if getattr_static(self.analyzer, "get_generation_backend_identity", missing) is not missing:
            method = getattr(self.analyzer, "get_generation_backend_identity", None)
            if callable(method):
                return method()
        return self._get_legacy_analyzer_generation_backend_identity()

    @staticmethod
    def _is_generation_truncated(result: Optional[GenerationResult], max_tokens: int) -> bool:
        """Chạm trần token đầu ra theo usage; không có usage thì coi như không chạm.

        Hit the output-token cap according to usage; no usage means not truncated.
        """
        if result is None or not getattr(result, "text", ""):
            return False
        usage = getattr(result, "usage", None)
        if not isinstance(usage, dict):
            return False
        try:
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            return False
        return completion_tokens >= max_tokens - _MARKET_REVIEW_TRUNCATION_MARGIN

    def _retry_truncated_market_review(
        self,
        result: Optional[GenerationResult],
        prompt: str,
        *,
        provider: str,
        model: str,
    ) -> tuple[Optional[GenerationResult], bool]:
        """Gọi lại một lần với ngân sách lớn hơn khi bản đầu chạm trần.

        Trả về (kết quả dùng tiếp, còn bị cắt hay không). Lần gọi lại lỗi hoặc vẫn chạm trần
        thì giữ bản dài hơn và báo còn bị cắt để caller gắn cảnh báo, không coi là đầy đủ.
        Returns (result to use, still truncated); a failed or still-capped retry keeps the
        longer text and reports truncation instead of passing it off as complete.
        """
        if not self._is_generation_truncated(result, _MARKET_REVIEW_MAX_TOKENS):
            return result, False

        usage = result.usage if isinstance(result.usage, dict) else {}
        logger.warning(
            "[Thị trường] %s action=generate_review status=truncated completion_tokens=%s "
            "max_tokens=%d retry_max_tokens=%d",
            self._log_context(),
            usage.get("completion_tokens"),
            _MARKET_REVIEW_MAX_TOKENS,
            _MARKET_REVIEW_RETRY_MAX_TOKENS,
        )
        try:
            retry_result = self._generate_market_review_with_metadata(
                prompt,
                provider=provider,
                model=model,
                max_tokens=_MARKET_REVIEW_RETRY_MAX_TOKENS,
            )
        except Exception as exc:
            logger.warning(
                "[Thị trường] %s action=generate_review status=retry_failed error=%s",
                self._log_context(),
                exc,
            )
            return result, True

        if retry_result is None or not retry_result.text:
            return result, True
        if self._is_generation_truncated(retry_result, _MARKET_REVIEW_RETRY_MAX_TOKENS):
            longer = retry_result if len(retry_result.text) >= len(result.text) else result
            return longer, True
        logger.info(
            "[Thị trường] %s action=generate_review status=retry_success length=%d",
            self._log_context(),
            len(retry_result.text),
        )
        return retry_result, False

    def _generate_market_review_with_metadata(
        self,
        prompt: str,
        *,
        provider: str,
        model: str,
        max_tokens: int = _MARKET_REVIEW_MAX_TOKENS,
    ) -> Optional[GenerationResult]:
        """Support legacy analyzers that only implement the public generate_text() contract."""
        if self.analyzer is None:
            return None
        missing = object()
        if getattr_static(self.analyzer, "generate_text_with_metadata", missing) is not missing:
            method = getattr(self.analyzer, "generate_text_with_metadata", None)
            if callable(method):
                return method(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=0.7,
                )

        if getattr_static(self.analyzer, "generate_text", missing) is missing:
            raise AttributeError(
                "analyzer must implement generate_text_with_metadata() or generate_text()"
            )
        legacy_method = getattr(self.analyzer, "generate_text", None)
        if not callable(legacy_method):
            raise AttributeError(
                "analyzer must implement generate_text_with_metadata() or generate_text()"
            )
        review = legacy_method(
            prompt,
            max_tokens=max_tokens,
            temperature=0.7,
        )
        if review is None:
            return None
        return GenerationResult(
            text=review,
            provider=provider,
            model=model,
            backend=provider,
            usage={},
        )

    def build_market_review_payload(
        self,
        overview: MarketOverview,
        news: List,
        report: str,
        market_light_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build the structured market-review contract consumed by API, Web, and notifications."""
        language = self._get_output_language()
        sections = self._split_report_sections(report)
        title = self._extract_report_title(report) or self._get_review_title(overview.date).lstrip("# ").strip()
        light = (
            market_light_snapshot or self.build_market_light_snapshot(overview)
            if self._supports_market_light()
            else None
        )
        breadth_dimensions = None
        if isinstance(light, dict):
            dimensions = light.get("dimensions")
            if isinstance(dimensions, dict):
                breadth_dimensions = dimensions.get("breadth")

        breadth_supported = bool(self.profile.has_market_stats)
        if breadth_supported and isinstance(breadth_dimensions, dict) and "available" in breadth_dimensions:
            breadth_supported = bool(breadth_dimensions.get("available"))

        has_breadth_data = False
        if breadth_supported:
            if isinstance(breadth_dimensions, dict) and "available" in breadth_dimensions:
                has_breadth_data = bool(breadth_dimensions.get("available"))
            else:
                breadth_available = overview.up_count + overview.down_count + overview.flat_count > 0
                limit_available = overview.limit_up_count + overview.limit_down_count > 0
                has_breadth_data = bool(breadth_available or limit_available)

        payload = {
            "version": 1,
            "kind": "market_review",
            "region": self.region,
            "language": language,
            "color_scheme": getattr(
                getattr(self, "config", None),
                "market_review_color_scheme",
                "green_up",
            ),
            "title": title,
            "generated_at": datetime.now().isoformat(),
            "date": overview.date,
            "market_scope": self._get_market_scope_name(language),
            "indices": [idx.to_dict() for idx in overview.indices],
            "news": [self._normalize_news_item(item) for item in (news or [])[:8]],
            "sections": sections,
            "markdown_report": report,
        }

        if light is not None:
            payload["market_light"] = light

        if has_breadth_data:
            payload["breadth"] = {
                "up_count": overview.up_count,
                "down_count": overview.down_count,
                "flat_count": overview.flat_count,
                "limit_up_count": overview.limit_up_count,
                "limit_down_count": overview.limit_down_count,
                "total_amount": overview.total_amount,
                "turnover_unit": self._get_turnover_unit_label(),
            }
            # Trường bổ sung (chỉ thêm, giao diện chưa đọc) / Additive fields
            if overview.put_through_amount:
                payload["breadth"]["put_through_amount"] = overview.put_through_amount
            if overview.foreign_net_amount is not None:
                payload["breadth"]["foreign_net_amount"] = overview.foreign_net_amount

        if overview.money_flow:
            payload["money_flow"] = overview.money_flow
        if overview.market_drivers:
            payload["market_drivers"] = overview.market_drivers

        return payload

    def _supports_market_light(self) -> bool:
        return self.region in MARKET_LIGHT_REGIONS

    @staticmethod
    def _extract_report_title(report: str) -> str:
        for line in (report or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    @classmethod
    def _split_report_sections(cls, report: str) -> List[Dict[str, str]]:
        text = (report or "").strip()
        if not text:
            return []
        matches = list(re.finditer(r"^(#{2,3})\s+(.+?)\s*$", text, flags=re.MULTILINE))
        if not matches:
            return [{"key": "full_review", "title": "Review", "markdown": text}]

        sections: List[Dict[str, str]] = []
        first_match = matches[0]
        starts_with_report_title = first_match.start() == 0 and first_match.group(1) == "##"
        content_start_index = 1 if starts_with_report_title else 0
        intro_start = first_match.end() if starts_with_report_title else 0
        intro_end = (
            matches[1].start()
            if starts_with_report_title and len(matches) > 1
            else (len(text) if starts_with_report_title else matches[0].start())
        )
        intro = text[intro_start:intro_end].strip()
        if intro:
            sections.append({"key": "overview", "title": "Overview", "markdown": intro})

        for index, match in enumerate(matches[content_start_index:], start=content_start_index):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            title = match.group(2).strip()
            markdown = text[start:end].strip()
            if not markdown:
                continue
            key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", title).strip("_").lower()
            sections.append({
                "key": key or f"section_{index + 1}",
                "title": title,
                "markdown": markdown,
            })
        return sections

    @classmethod
    def _normalize_news_item(cls, item: Any) -> Dict[str, str]:
        return {
            "title": cls._compact_news_text(cls._get_news_field(item, "title"), limit=120),
            "snippet": cls._compact_news_text(cls._get_news_field(item, "snippet"), limit=260),
            "source": cls._compact_news_text(cls._get_news_field(item, "source"), limit=80),
            "published_date": cls._compact_news_text(cls._get_news_field(item, "published_date"), limit=40),
            "url": cls._compact_news_text(cls._get_news_field(item, "url"), limit=240),
        }
    
    def _inject_data_into_review(
        self,
        review: str,
        overview: MarketOverview,
        news: Optional[List] = None,
    ) -> str:
        """Inject structured data tables into the corresponding LLM prose sections."""
        # Build data blocks
        stats_block = self._build_stats_block(overview)
        indices_block = self._build_indices_block(overview)
        patterns = _SECTION_PATTERNS

        if stats_block:
            review = self._insert_after_section(
                review,
                patterns["market_summary"],
                stats_block,
            )

        if indices_block:
            review = self._insert_after_section(
                review,
                patterns["index_commentary"],
                indices_block,
            )

        return review

    @staticmethod
    def _insert_after_section(text: str, heading_pattern: str, block: str) -> str:
        """Insert a data block at the end of a markdown section (before the next ### heading)."""
        import re
        # Find the heading
        match = re.search(heading_pattern, text)
        if not match:
            return text
        start = match.end()
        # Find the next ### heading after this one
        next_heading = re.search(r'\n###\s', text[start:])
        if next_heading:
            insert_pos = start + next_heading.start()
        else:
            # No next heading — append at end
            insert_pos = len(text)
        # Insert the block before the next heading, with spacing
        return text[:insert_pos].rstrip() + '\n\n' + block + '\n\n' + text[insert_pos:].lstrip('\n')

    def _build_stats_block(self, overview: MarketOverview, language: Optional[str] = None) -> str:
        """Build market statistics block (nhãn theo ngôn ngữ báo cáo / labels follow report language)."""
        has_stats = overview.up_count or overview.down_count or overview.total_amount
        has_market_signal = bool(self._supports_market_light() and (overview.indices or has_stats))
        if not has_stats and not has_market_signal:
            return ""
        lang = language or self._get_output_language()
        labels = _DATA_BLOCK_LABELS["vi" if lang == "vi" else "en"]
        light = self.build_market_light_snapshot(overview) if has_market_signal else None
        lines = []
        if isinstance(light, dict):
            temperature = light["temperature_label"]
            label = light["label"]
            guidance = light["guidance"]
            reasons = light["reasons"]
            if lang == "vi":
                temperature = _MARKET_LIGHT_VI_TEXT["temperature"].get(temperature, temperature)
                label = _MARKET_LIGHT_VI_TEXT["label"].get(label, label)
                guidance = _MARKET_LIGHT_VI_TEXT["guidance"].get(light.get("status"), guidance)
                reasons = self._build_market_light_reasons_vi(overview)
            lines.extend(
                [
                    f"- **{labels['market_signal']}**: {light['score']}/100 ({temperature}, {label})",
                    f"- **{labels['drivers']}**: {'; '.join(reasons)}",
                    f"- **{labels['guidance']}**: {guidance}",
                ]
            )
        if has_stats:
            if lines:
                lines.append("")
            lines.append(
                f"- **{labels['breadth']}**: {labels['advancers']} {overview.up_count} / "
                f"{labels['decliners']} {overview.down_count} / "
                f"{labels['flat']} {overview.flat_count}; "
                f"{labels['limit_up']} {overview.limit_up_count} / {labels['limit_down']} {overview.limit_down_count}; "
                f"{labels['turnover']} {overview.total_amount:.0f} ({self._get_report_turnover_unit(lang)})"
            )
        return "\n".join(lines)

    def build_market_light_snapshot(self, overview: MarketOverview) -> Dict[str, Any]:
        """Build a deterministic market-light snapshot from structured breadth data."""
        scores = self._build_market_light_scores(overview)
        score = int(scores["score"])
        temperature_label = str(scores["temperature_label"])
        if score >= 60:
            status = "green"
        elif score >= 40:
            status = "yellow"
        else:
            status = "red"

        label_map = {
            "green": "risk-on",
            "yellow": "balanced",
            "red": "risk-off",
        }
        guidance_map = {
            "green": "Risk appetite is acceptable; focus on leading themes and position discipline.",
            "yellow": "Signals are mixed; keep position sizing moderate and wait for confirmation.",
            "red": "Risk is elevated; prioritize drawdown control and avoid chasing weak rebounds.",
        }
        reasons = self._build_market_light_reasons_en(overview, score)

        snapshot = MarketLightSnapshot(
            region=self.region,
            trade_date=overview.date,
            status=status,
            label=label_map[status],
            score=score,
            temperature_label=temperature_label,
            reasons=reasons,
            guidance=guidance_map[status],
            dimensions=scores["dimensions"],
            data_quality=str(scores["data_quality"]),
        )
        return snapshot.model_dump()

    def _build_market_light_reasons_vi(self, overview: MarketOverview) -> List[str]:
        """Bản tiếng Việt của ``_build_market_light_reasons_en`` cho phần hiển thị báo cáo."""
        participation = overview.up_count + overview.down_count
        up_ratio = overview.up_count / participation if participation else None
        reasons: List[str] = []
        if up_ratio is not None:
            if up_ratio >= 0.6:
                reasons.append(f"tỷ lệ mã tăng {up_ratio:.0%}, độ rộng mở rộng")
            elif up_ratio <= 0.4:
                reasons.append(f"tỷ lệ mã tăng {up_ratio:.0%}, áp lực giảm chiếm ưu thế")
            else:
                reasons.append(f"tỷ lệ mã tăng {up_ratio:.0%}, độ rộng phân hoá")
        index_changes = [idx.change_pct for idx in overview.indices if idx.change_pct is not None]
        if index_changes:
            avg_change = sum(index_changes) / len(index_changes)
            reasons.append(f"biến động bình quân các chỉ số chính {avg_change:+.2f}%")
        if overview.limit_up_count or overview.limit_down_count:
            reasons.append(f"chênh lệch trần/sàn {overview.limit_up_count - overview.limit_down_count:+d}")
        if not reasons and overview.total_amount:
            reasons.append(f"giá trị giao dịch {overview.total_amount:.0f} (tỷ đồng)")
        if not reasons:
            reasons.append("dữ liệu độ rộng còn hạn chế; dùng các dữ liệu thị trường sẵn có")
        return reasons[:4]

    def _build_market_light_reasons_en(self, overview: MarketOverview, score: int) -> List[str]:
        participation = overview.up_count + overview.down_count
        up_ratio = overview.up_count / participation if participation else None
        reasons: List[str] = []
        if up_ratio is not None:
            if up_ratio >= 0.6:
                reasons.append(f"advancers ratio {up_ratio:.0%}, breadth is expanding")
            elif up_ratio <= 0.4:
                reasons.append(f"advancers ratio {up_ratio:.0%}, downside pressure dominates")
            else:
                reasons.append(f"advancers ratio {up_ratio:.0%}, breadth is mixed")
        index_changes = [idx.change_pct for idx in overview.indices if idx.change_pct is not None]
        if index_changes:
            avg_change = sum(index_changes) / len(index_changes)
            reasons.append(f"average major-index change {avg_change:+.2f}%")
        if overview.limit_up_count or overview.limit_down_count:
            reasons.append(f"limit-up/down spread {overview.limit_up_count - overview.limit_down_count:+d}")
        if not reasons and overview.total_amount:
            reasons.append(f"turnover {overview.total_amount:.0f} ({self._get_turnover_unit_label()})")
        if not reasons:
            reasons.append("limited structured breadth data; using available market inputs")
        return reasons[:4]

    def _build_indices_block(self, overview: MarketOverview, language: Optional[str] = None) -> str:
        """Build the index quotes table (header theo ngôn ngữ báo cáo / header follows report language)."""
        if not overview.indices:
            return ""
        lang = language or self._get_output_language()
        header = _DATA_BLOCK_LABELS["vi" if lang == "vi" else "en"]["index_header"]
        lines = [
            header.format(unit=self._get_report_turnover_unit(lang)),
            "|-------|------|----------|------|------|-----|-----------|-----------------|",
        ]
        for idx in overview.indices:
            arrow = self._get_index_change_arrow(idx.change_pct)
            amount_raw = idx.amount or 0.0
            amount_str = self._format_turnover_value(amount_raw)
            lines.append(
                f"| {idx.name} | {idx.current:.2f} | {arrow} {idx.change_pct:+.2f}% | "
                f"{self._format_optional_number(idx.open)} | {self._format_optional_number(idx.high)} | "
                f"{self._format_optional_number(idx.low)} | {self._format_optional_pct(idx.amplitude)} | {amount_str} |"
            )
        return "\n".join(lines)

    def _build_news_block(self, news: List) -> str:
        """Build a compact source-aware news catalyst list for the rendered report."""
        if not news:
            return ""
        language = self._get_review_language()
        lines = ["#### News Catalysts"]

        for idx, item in enumerate(news[:5], 1):
            lines.append(self._format_news_catalyst_line(idx, item, language=language))
        return "\n".join(lines)

    @staticmethod
    def _get_news_field(item: Any, field: str) -> str:
        if hasattr(item, field):
            value = getattr(item, field, "") or ""
        elif isinstance(item, dict):
            value = item.get(field, "") or ""
        else:
            value = ""
        return str(value).strip()

    @classmethod
    def _format_news_catalyst_line(cls, idx: int, item: Any, *, language: str = "vi") -> str:
        fallback_title = "Untitled catalyst"
        title = cls._compact_news_text(cls._get_news_field(item, "title"), limit=90) or fallback_title
        source = cls._compact_news_text(cls._get_news_field(item, "source"), limit=40)
        date_text = cls._compact_news_text(cls._get_news_field(item, "published_date"), limit=24)
        url = cls._compact_news_text(cls._get_news_field(item, "url"), limit=0)
        title_text = cls._escape_markdown_link_label(title)
        if url:
            title_text = f"[{title_text}]({url})"
        meta_parts = [part for part in (source, date_text) if part]
        meta = f" ({' / '.join(meta_parts)})" if meta_parts else ""
        return f"- {idx}. {title_text}{meta}"

    @staticmethod
    def _compact_news_text(value: str, *, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if limit <= 0 or len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _format_optional_number(value: float) -> str:
        return "N/A" if value in (None, 0, 0.0) else f"{value:.2f}"

    @staticmethod
    def _format_optional_pct(value: float) -> str:
        return "N/A" if value in (None, 0, 0.0) else f"{value:.2f}%"

    @staticmethod
    def _escape_markdown_link_label(value: str) -> str:
        return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")

    def _build_market_light_scores(self, overview: MarketOverview) -> Dict[str, Any]:
        """Build the canonical Market Light scores used by reports and alerts."""

        participants = overview.up_count + overview.down_count
        breadth_available = bool(self.profile.has_market_stats and participants > 0)
        breadth_score = 50
        if breadth_available:
            breadth_score = int(overview.up_count / participants * 100)

        index_changes = [idx.change_pct for idx in overview.indices if idx.change_pct is not None]
        index_available = bool(overview.indices and index_changes)
        index_score = 50
        if index_available:
            avg_change = sum(index_changes) / len(index_changes)
            index_score = int(max(0, min(100, 50 + avg_change * 12)))

        limit_total = overview.limit_up_count + overview.limit_down_count
        limit_available = bool(self.profile.has_market_stats and limit_total > 0)
        limit_score = 50
        if limit_available:
            limit_score = int(overview.limit_up_count / limit_total * 100)

        dimensions = {
            "breadth": {"score": breadth_score, "available": breadth_available},
            "index": {"score": index_score, "available": index_available},
            "limit": {"score": limit_score, "available": limit_available},
        }

        if not index_available:
            data_quality = "unavailable"
        elif all(dimension["available"] for dimension in dimensions.values()):
            data_quality = "ok"
        else:
            data_quality = "partial"

        score = int(round(breadth_score * 0.45 + index_score * 0.35 + limit_score * 0.20))
        if score >= 70:
            label = "risk-on"
        elif score >= 55:
            label = "constructive"
        elif score >= 40:
            label = "mixed"
        else:
            label = "defensive"
        return {
            "score": score,
            "temperature_label": label,
            "dimensions": dimensions,
            "data_quality": data_quality,
        }

    def _build_market_temperature(self, overview: MarketOverview) -> tuple[int, str]:
        scores = self._build_market_light_scores(overview)
        score = int(scores["score"])
        label = str(scores["temperature_label"])
        return score, label

    def _build_output_template_sections(self, review_language: str) -> str:
        """Dựng các mục đầu ra của LLM theo khả năng dữ liệu (không có xếp hạng ngành).

        Build LLM output sections according to market data capabilities (no sector rankings).
        """
        section_number = 3
        sections: List[str] = []
        if self.profile.has_market_stats:
            sections.append(f"""### {section_number}. Fund Flows
(Interpret only the provided turnover, participation, breadth, and flow signals.)""")
            section_number += 1
        sections.extend([
            f"""### {section_number}. News Catalysts
(Connect recent news to index price action and macro/external-market clues. Do not infer unsupported breadth, fund-flow, or sector-ranking data.)""",
            f"""### {section_number + 1}. Outlook
(Provide the near-term outlook based on index price action and the available news.)""",
            f"""### {section_number + 2}. Risk Alerts
(List the main risks to monitor.)""",
            f"""### {section_number + 3}. Strategy Plan
(Provide an offensive/balanced/defensive stance, a position-sizing guideline, one invalidation trigger, and end with "For reference only, not investment advice.")""",
        ])
        return "\n\n".join(sections)

    def _build_review_prompt(self, overview: MarketOverview, news: List) -> str:
        """Build the review report prompt"""
        # Tiếng Việt và tiếng Anh dùng chung khung tiếng Anh; với tiếng Việt,
        # model được yêu cầu viết toàn bộ nội dung bằng tiếng Việt có dấu.
        # vi and en share the English scaffolding; for vi the model is told to
        # write the whole report in Vietnamese.
        output_language = self._get_output_language()
        if output_language == "vi":
            shell_language_label = (
                "Vietnamese (Tiếng Việt) with correct diacritics and standard Vietnamese "
                "securities terminology"
            )
        else:
            shell_language_label = "English"

        # Index quote info (compact format, no emoji)
        indices_text = ""
        for idx in overview.indices:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            # Giá trị GD (khớp + thoả thuận) khi nguồn có / Turnover when the source has it
            turnover = (
                f", turnover {idx.amount / 1e9:,.0f} VND bn"
                if isinstance(idx.amount, (int, float)) and idx.amount > 0
                else ""
            )
            indices_text += (
                f"- {idx.name}: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%{turnover})\n"
            )

        # News info - supports SearchResult objects or dicts
        news_text = ""
        for i, n in enumerate(news[:6], 1):
            # Compatible with both SearchResult objects and dicts
            title = self._compact_news_text(self._get_news_field(n, "title"), limit=90)
            snippet = self._compact_news_text(self._get_news_field(n, "snippet"), limit=220)
            source = self._compact_news_text(self._get_news_field(n, "source"), limit=60)
            published_date = self._compact_news_text(self._get_news_field(n, "published_date"), limit=30)
            url = self._compact_news_text(self._get_news_field(n, "url"), limit=180)
            meta_parts = [part for part in (source, published_date) if part]
            meta = f" ({' / '.join(meta_parts)})" if meta_parts else ""
            url_line = f"\n   URL: {url}" if url else ""
            news_text += f"{i}. {title}{meta}\n   {snippet or '-'}{url_line}\n"

        stats_block = ""
        if self.profile.has_market_stats:
            stats_block = f"""## Market Breadth
- Advancers: {overview.up_count} | Decliners: {overview.down_count} | Flat: {overview.flat_count}
- Limit-up: {overview.limit_up_count} | Limit-down: {overview.limit_down_count}
- Turnover: {overview.total_amount:.0f} ({self._get_turnover_unit_label()})"""
            if overview.put_through_amount:
                stats_block += (
                    f"\n- Put-through (negotiated, excluded from turnover above): "
                    f"{overview.put_through_amount:,.0f} ({self._get_turnover_unit_label()})"
                )
            if overview.foreign_net_amount is not None:
                stats_block += (
                    f"\n- Foreign net, all exchanges today: {overview.foreign_net_amount:+,.0f} "
                    f"({self._get_turnover_unit_label()})"
                )

        flow_block = self._build_money_flow_prompt_block(overview.money_flow)
        drivers_block = self._build_market_drivers_prompt_block(overview.market_drivers)

        # Chưa có dữ liệu xếp hạng ngành cho Việt Nam. / No sector ranking data for Vietnam.
        data_limit_lines = []
        if not self.profile.has_market_stats:
            data_limit_lines.append(
                "- Market breadth, aggregate turnover, participation, and fund-flow signals are not available for this market."
            )
        data_limit_lines.append("- Sector/theme ranking data is not available for this market.")
        data_limits_block = "## Data Limits\n" + "\n".join(data_limit_lines)

        data_no_indices_hint = (
            "Note: Market data fetch failed. Rely mainly on [Market News] for qualitative analysis. Do not invent index levels."
            if not indices_text
            else ""
        )
        indices_placeholder = indices_text if indices_text else "No index data (API error)"
        news_placeholder = news_text if news_text else "No relevant news"
        if flow_block or drivers_block:
            provided = [
                name
                for name, block in (("Fund Flows (quote their date ranges)", flow_block), ("Market Drivers", drivers_block))
                if block
            ]
            data_boundary_requirement = (
                f"- Respect Data Limits: use only the figures given in {' and '.join(provided)}; "
                "do not invent other flow, turnover, participation, contributor, or sector-ranking data.\n"
            )
        else:
            data_boundary_requirement = (
                "- Respect Data Limits: do not invent or over-interpret unsupported breadth, fund-flow, turnover, participation, or sector-ranking data.\n"
            )
        market_summary_hint = (
            "2-3 sentences summarizing overall market tone, index moves, and liquidity."
            if self.profile.has_market_stats
            else "2-3 sentences summarizing overall market tone, index moves, and available news context."
        )

        output_template_sections = self._build_output_template_sections(output_language)
        report_title = self._get_review_title(overview.date).removeprefix("## ").strip()
        return f"""You are a professional {self._get_market_scope_name('en')} analyst. Please produce a concise market recap report based on the data below.

[Requirements]
- Output pure Markdown only
- No JSON
- No code blocks
- Use emoji sparingly in headings (at most one per heading)
- The entire fixed shell, headings, guidance, and conclusion must be in {shell_language_label}
{data_boundary_requirement}

---

# Today's Market Data

## Date
{overview.date}

## Major Indices
{indices_placeholder}

{stats_block}

{flow_block}

{drivers_block}

{data_limits_block}

## Market News
{news_placeholder}

{data_no_indices_hint}

{self._get_strategy_prompt_block()}

---

# Output Template (follow this structure)

## {report_title}

### 1. Market Summary
({market_summary_hint})

### 2. Index Commentary
({self._get_index_hint()})

{output_template_sections}

---

Output the report content directly, no extra commentary.
"""

    @staticmethod
    def _build_money_flow_prompt_block(flow: Optional[Dict[str, Any]]) -> str:
        """Mục "Fund Flows" cho prompt; rỗng khi không có dữ liệu.

        Mỗi kỳ ghi rõ khoảng ngày nguồn báo: tự doanh công bố sau phiên và khối
        "today" có thể gộp hai phiên, nên không được gán cho "hôm nay".
        Each period carries the provider's own date range.
        """
        if not isinstance(flow, dict):
            return ""
        labels = (("today", "Latest"), ("one_week", "1 week"), ("one_month", "1 month"), ("ytd", "Year to date"))

        def _period_lines(side: Dict[str, Any], with_top: tuple) -> List[str]:
            lines: List[str] = []
            for key, label in labels:
                period = side.get(key)
                if not isinstance(period, dict) or period.get("net") is None:
                    continue
                lines.append(
                    f"- {label} ({period.get('from')} → {period.get('to')}): net {period['net']:+,.0f} "
                    f"(buy {period.get('buy') or 0:,.0f} / sell {period.get('sell') or 0:,.0f})"
                )
                if key in with_top:
                    for title, top_key in (("top net buy", "top_net_buy"), ("top net sell", "top_net_sell")):
                        top = period.get(top_key) or []
                        if top:
                            items = ", ".join(f"{row['ticker']} {row['net']:+,.0f}" for row in top)
                            lines.append(f"  - {title}: {items}")
            return lines

        scope = flow.get("scope") or "market"
        unit = flow.get("unit") or "VND bn"
        parts: List[str] = []
        foreign = flow.get("foreign")
        if isinstance(foreign, dict):
            lines = _period_lines(foreign, ("today", "one_week"))
            if lines:
                parts.append("### Foreign investors\n" + "\n".join(lines))
        proprietary = flow.get("proprietary")
        if isinstance(proprietary, dict):
            lines = _period_lines(proprietary, ("today", "one_week"))
            if lines:
                parts.append(
                    "### Proprietary trading (securities firms)\n"
                    "Published after the session; the latest period may cover the previous "
                    "session or two sessions, so cite its date range instead of calling it today.\n"
                    + "\n".join(lines)
                )
        if not parts:
            return ""
        return f"## Fund Flows ({scope}, {unit})\n" + "\n\n".join(parts)

    @staticmethod
    def _build_market_drivers_prompt_block(drivers: Optional[Dict[str, Any]]) -> str:
        """Mục "Market Drivers" cho prompt; rỗng khi không có dữ liệu.

        Gồm mã kéo/đè điểm VN-Index, mã đột biến khối lượng và diễn biến trong phiên.
        Index point contributors, volume breakouts and the intraday path.
        """
        if not isinstance(drivers, dict):
            return ""
        parts: List[str] = []

        contribution = drivers.get("index_contribution")
        if isinstance(contribution, dict) and isinstance(contribution.get("periods"), dict):
            lines: List[str] = []
            index_change = contribution.get("index_change")
            for key, label in (("1d", "Today"), ("5d", "Last 5 sessions"), ("20d", "Last 20 sessions")):
                period = contribution["periods"].get(key)
                if not isinstance(period, dict):
                    continue
                for title, top_key in (("lifting", "top_gainers"), ("dragging", "top_losers")):
                    rows = period.get(top_key) or []
                    if not rows:
                        continue
                    items = ", ".join(
                        f"{row['ticker']} {row['points']:+.2f}"
                        + (f" ({row['change_pct']:+.2f}%)" if row.get("change_pct") is not None else "")
                        for row in rows
                    )
                    lines.append(f"- {label}, {title}: {items}")
            if lines:
                header = "### VN-Index point contributors (index points)"
                if isinstance(index_change, (int, float)):
                    header += f"\nVN-Index change today: {index_change:+.2f} points."
                parts.append(header + "\n" + "\n".join(lines))

        breakouts = drivers.get("volume_breakouts")
        if isinstance(breakouts, dict) and breakouts.get("items"):
            lines = []
            for row in breakouts["items"]:
                change = f"{row['change_pct']:+.2f}%, " if row.get("change_pct") is not None else ""
                sector = f" [{row['sector']}]" if row.get("sector") else ""
                value = f", value {row['value']:,.0f} VND bn" if row.get("value") is not None else ""
                lines.append(f"- {row['ticker']}{sector}: {change}volume {row['volume_ratio']:.2f}x{value}")
            parts.append(
                f"### Volume breakouts (session volume >= {breakouts.get('min_ratio', 1.5)}x the "
                f"{breakouts.get('baseline') or '1-week average volume'}, by traded value)\n"
                + "\n".join(lines)
            )

        intraday = drivers.get("intraday")
        if isinstance(intraday, dict) and intraday.get("last"):
            lines = []
            for key, label in (("first", "First print"), ("morning_close", "Morning close"),
                               ("high", "Intraday high"), ("low", "Intraday low"), ("last", "Latest")):
                point = intraday.get(key)
                if isinstance(point, dict) and point.get("value") is not None:
                    lines.append(f"- {label}: {point['value']:,.2f} at {point.get('time')}")
            share = intraday.get("closing_window_value_share")
            if isinstance(share, (int, float)):
                lines.append(f"- Matched value from 14:30 to the close (incl. ATC): {share:.1f}% of the session")
            parts.append(f"### VN-Index intraday path ({intraday.get('date')}, per-minute)\n" + "\n".join(lines))

        if not parts:
            return ""
        return "## Market Drivers\n" + "\n\n".join(parts)

    def _generate_template_review(self, overview: MarketOverview, news: List) -> str:
        """Generate the review report from a template (fallback when no LLM is available)"""
        template_language = self._get_template_review_language()
        mood_code = self.profile.mood_index_code
        # Tìm chỉ số theo mood_index_code (VNINDEX) / Find the mood index by code
        mood_index = next(
            (
                idx
                for idx in overview.indices
                if idx.code == mood_code or idx.code.endswith(mood_code)
            ),
            None,
        )
        if mood_index:
            if mood_index.change_pct > 1:
                market_mood = self._get_market_mood_text("strong_up", template_language)
            elif mood_index.change_pct > 0:
                market_mood = self._get_market_mood_text("mild_up", template_language)
            elif mood_index.change_pct > -1:
                market_mood = self._get_market_mood_text("mild_down", template_language)
            else:
                market_mood = self._get_market_mood_text("strong_down", template_language)
        else:
            market_mood = self._get_market_mood_text("range", template_language)

        # Index quotes (compact format)
        indices_text = ""
        for idx in overview.indices[:4]:
            marker = self._get_index_change_arrow(idx.change_pct)
            indices_text += f"- **{idx.name}**: {idx.current:.2f} ({marker} {idx.change_pct:+.2f}%)\n"

        stats_section = ""
        if self._supports_market_light() or self.profile.has_market_stats:
            # Mẫu dự phòng dùng khung tiếng Anh nên giữ nhãn tiếng Anh. / Template shell is English.
            stats_block = self._build_stats_block(overview, language="en")
            if stats_block:
                stats_section = f"""
### 3. Breadth & Liquidity
{stats_block}
"""
        return f"""{self._get_review_title(overview.date)}

### 1. Market Summary
Today's {self._get_market_scope_name(template_language)} showed **{market_mood}**.

### 2. Major Indices
{indices_text or "- No index data available"}
{stats_section}
### 5. Risk Alerts
Market conditions can change quickly. The data above is for reference only and does not constitute investment advice.

{self._get_strategy_markdown_block(template_language)}

---
*Review Time: {datetime.now().strftime('%H:%M')}*
"""

    def _run_daily_review_parts(self) -> MarketLightReviewResult:
        """Run market review once and keep report/snapshot on the same overview."""
        logger.info("========== Bắt đầu điểm lại thị trường ==========")

        # 1. Fetch market overview
        overview = self.get_market_overview()

        # 2. Search market news
        news = self.search_market_news()
        news = self._merge_persisted_market_intelligence(news)

        # 3. Generate the review report
        report = self.generate_market_review(overview, news)
        snapshot = self.build_market_light_snapshot(overview) if self._supports_market_light() else None
        structured_payload = self.build_market_review_payload(
            overview,
            news,
            report,
            snapshot,
        )

        logger.info("========== Hoàn tất điểm lại thị trường ==========")

        return MarketLightReviewResult(
            overview=overview,
            report=report,
            market_light_snapshot=snapshot,
            structured_payload=structured_payload,
        )

    def _merge_persisted_market_intelligence(self, news: List) -> List:
        """Merge local persisted market intelligence and search news with bounded prompt/payload slot preservation."""
        search_news = list(news or [])
        merged_local = []
        seen_urls = {
            self._get_news_field(item, "url")
            for item in search_news
            if self._get_news_field(item, "url")
        }
        try:
            service = IntelligenceService(config=self.config)
            service.refresh_auto_sources()
            payload = service.list_items(
                scope_type="market",
                market=self.region,
                published_days=max(1, int(self.config.get_effective_news_window_days() or 1)),
                page=1,
                page_size=6,
            )
            for item in payload.get("items", []):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                if url and url in seen_urls:
                    continue
                seen_urls.add(url)
                merged_local.append({
                    "title": item.get("title") or "Tin chưa có tiêu đề",
                    "snippet": item.get("summary") or "",
                    "source": item.get("source") or item.get("source_name") or "local-intel",
                    "published_date": item.get("published_at") or "",
                    "url": "" if url.startswith("no-url:intel:") else url,
                })
        except Exception as exc:
            logger.debug("[Thị trường] %s action=load_local_intelligence status=failed error=%s", self._log_context(), exc)
        merged_news = []
        merged_local_index = 0
        merged_search_index = 0
        while merged_local_index < len(merged_local) or merged_search_index < len(search_news):
            if merged_local_index < len(merged_local):
                merged_news.append(merged_local[merged_local_index])
                merged_local_index += 1
            if merged_search_index < len(search_news):
                merged_news.append(search_news[merged_search_index])
                merged_search_index += 1
        return merged_news

    def run_daily_review(self) -> str:
        """
        Run the daily market review workflow

        Returns:
            Market review report text
        """
        return self.run_daily_review_with_snapshot().report

    def run_daily_review_with_snapshot(self) -> MarketLightReviewResult:
        """Run daily review and return the report plus its structured Market Light snapshot."""
        return self._run_daily_review_parts()


# Test entry point
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    )

    analyzer = MarketAnalyzer()

    # Thử lấy tổng quan thị trường / Test fetching market overview
    overview = analyzer.get_market_overview()
    print("\n=== Tổng quan thị trường ===")
    print(f"Ngày: {overview.date}")
    print(f"Số chỉ số: {len(overview.indices)}")
    for idx in overview.indices:
        print(f"  {idx.name}: {idx.current:.2f} ({idx.change_pct:+.2f}%)")
    print(f"Tăng: {overview.up_count} | Giảm: {overview.down_count}")
    print(f"Giá trị giao dịch: {overview.total_amount:.0f} tỷ đồng")

    # Thử sinh báo cáo theo template / Test generating the template report
    report = analyzer._generate_template_review(overview, [])
    print("\n=== Báo cáo điểm lại ===")
    print(report)
