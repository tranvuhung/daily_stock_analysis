# -*- coding: utf-8 -*-
"""
===================================
Hệ thống phân tích danh mục theo dõi - luồng phân tích lõi
===================================

Trách nhiệm:
1. Quản lý toàn bộ quy trình phân tích
2. Điều phối các module lấy dữ liệu, lưu trữ, tìm kiếm, phân tích, thông báo
3. Hiện thực phần điều khiển đồng thời và xử lý ngoại lệ
4. Cung cấp các chức năng lõi để phân tích cổ phiếu
"""

import logging
import inspect
import threading
import time
from pathlib import Path
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Dict, Any, Optional, Set, Tuple, Callable

import pandas as pd

from src.config import FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT, get_config, Config
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import (
    normalize_stock_code,
    should_persist_daily_data,
)
from src.analyzer import (
    GeminiAnalyzer,
    AnalysisResult,
    fill_price_position_if_needed,
    populate_decision_action_fields,
    stabilize_decision_with_structure,
)
from src.notification import NotificationService, NotificationChannel
from src.schemas.decision_action import normalize_decision_action
from src.report_language import (
    get_localized_stock_name,
    get_pipeline_progress_text,
    get_placeholder_text,
    get_unknown_text,
    infer_decision_type_from_advice,
    localize_confidence_level,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.search_service import SearchService
from src.analysis_context_pack_prompt import format_analysis_context_pack_prompt_section
from src.analysis_context_pack_overview import render_analysis_context_pack_overview
from src.market_phase_summary import MARKET_PHASE_SUMMARY_KEY, render_market_phase_summary
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail
from src.agent.final_explanation import (
    PipelineActionAdjustment,
    build_pipeline_final_explanation,
    capture_pipeline_action_adjustment,
)
from src.agent.news_evidence import (
    activate_news_evidence_scope,
    get_current_news_evidence,
    reset_news_evidence_scope,
)
from src.services.empty_news import news_evidence_present
from src.formatters import strip_hidden_markdown_metadata
from src.phase_decision_guardrail import apply_phase_decision_guardrails
from src.services.daily_market_context import (
    DailyMarketContext,
    DailyMarketContextService,
    format_daily_market_context_prompt_section,
)
from src.services.intelligence_service import IntelligenceService
from src.services.analysis_context_builder import (
    AnalysisContextBuilder,
    PipelineAnalysisArtifacts,
)
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    current_diagnostic_snapshot,
    get_current_diagnostic_context,
    record_history_run,
    record_llm_run,
    record_llm_run_started,
    record_notification_run,
    reset_run_diagnostic_context,
    sanitize_diagnostic_text,
)
from src.services.decision_signal_extractor import (
    extract_and_persist_from_analysis_result,
    resolve_decision_signal_action_fields,
)
from src.services.stock_list_parser import AnalysisTarget, ParseStatus
from src.services.decision_signal_summary import summarize_decision_signal
from src.enums import ReportType
from src.data.stock_mapping import PLACEHOLDER_NAME_PREFIXES
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.core.trading_calendar import (
    build_market_phase_context,
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    is_market_open,
)
from bot.models import BotMessage


logger = logging.getLogger(__name__)


def _analysis_metadata(*, query_id: str, trigger_source: Any) -> Dict[str, Any]:
    """Metadata của ContextPack (query_id và nguồn kích hoạt).

    ContextPack metadata (query id and trigger source).
    """
    return {"query_id": query_id, "trigger_source": trigger_source}


def _share_image_payload(result: Any) -> Optional[Dict[str, Any]]:
    """Return structured poster data when the result exposes the real contract."""

    to_dict = getattr(result, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        payload = to_dict()
    except Exception as exc:
        logger.debug("Dựng dữ liệu cấu trúc cho ảnh chia sẻ thất bại, quay về Markdown: %s", exc)
        return None
    return payload if isinstance(payload, dict) and payload else None


def _supports_explicit_keyword(callable_obj: Any, keyword: str) -> bool:
    """Avoid breaking custom notifier overrides that predate an optional kwarg."""

    try:
        return keyword in inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False


# Guard phòng thủ: khi instance được tạo mà bỏ qua __init__ (ví dụ dùng __new__
# trong test), việc double-check khởi tạo _single_stock_notify_lock vẫn an toàn
# với đa luồng.
_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()
_DAILY_MARKET_CONTEXT_SERVICE_LOCK_INIT_GUARD = threading.Lock()


def _symbol_scope_lookup_values(code: str, market: str) -> List[str]:
    """Return accepted persisted-intelligence symbol spellings for lookup."""
    raw = str(code or "").strip()
    normalized = normalize_stock_code(raw) if raw else ""
    values: List[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            values.append(text)

    def add_case_variants(value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        add(text)
        add(text.upper())
        add(text.lower())

    add_case_variants(normalized)
    add_case_variants(raw)

    # Bản ghi cũ có thể dùng hậu tố ``.VN``. / Legacy rows may use the ``.VN`` suffix.
    if normalized:
        add_case_variants(f"{normalized.upper()}.VN")
    return values


class StockAnalysisPipeline:
    """
    Bộ điều phối luồng phân tích cổ phiếu chính

    Trách nhiệm:
    1. Quản lý toàn bộ quy trình phân tích
    2. Điều phối các module lấy dữ liệu, lưu trữ, tìm kiếm, phân tích, thông báo
    3. Hiện thực phần điều khiển đồng thời và xử lý ngoại lệ
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        analysis_skills: Optional[List[str]] = None,
        analysis_phase: str = "auto",
        portfolio_context: Optional[Dict[str, Any]] = None,
        daily_market_context_enabled: Optional[bool] = None,
        daily_market_context_allow_generate: bool = True,
    ):
        """
        Khởi tạo bộ điều phối

        Args:
            config: đối tượng cấu hình (tuỳ chọn, mặc định dùng cấu hình toàn cục)
            max_workers: số luồng chạy đồng thời tối đa (tuỳ chọn, mặc định đọc từ cấu hình)
        """
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        self.source_message = source_message
        self.query_id = query_id
        self.trace_id = trace_id or query_id
        self.query_source = self._resolve_query_source(query_source)
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        self.progress_callback = progress_callback
        self.analysis_skills = list(analysis_skills) if analysis_skills is not None else None
        self.analysis_phase = analysis_phase or "auto"
        self.portfolio_context = dict(portfolio_context) if isinstance(portfolio_context, dict) else None
        self.daily_market_context_enabled = (
            bool(getattr(self.config, "daily_market_context_enabled", True))
            if daily_market_context_enabled is None
            else bool(daily_market_context_enabled)
        )
        self.daily_market_context_allow_generate = daily_market_context_allow_generate
        
        # Khởi tạo các module
        self.db = get_db()
        self.fetcher_manager = DataFetcherManager()
        # Không tạo riêng akshare_fetcher nữa, thống nhất dùng fetcher_manager để lấy
        # dữ liệu tăng cường
        self.trend_analyzer = StockTrendAnalyzer()  # Bộ phân tích kỹ thuật
        self.analyzer = GeminiAnalyzer(config=self.config, skills=self.analysis_skills)
        self.notifier = NotificationService(source_message=source_message)
        self._single_stock_notify_lock = threading.Lock()
        self._daily_market_context_service_lock = threading.Lock()
        self._last_local_report_path: Optional[str] = None
        self._last_local_report_error: Optional[str] = None
        
        # Khởi tạo dịch vụ tìm kiếm (tuỳ chọn; khởi tạo lỗi không được chặn luồng
        # phân tích chính)
        try:
            self.search_service = SearchService(
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=self.config.anspire_api_keys,
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                searxng_timeout_seconds=getattr(self.config, "searxng_timeout_seconds", None),
                news_max_age_days=self.config.news_max_age_days,
                news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
            )
        except Exception as exc:
            logger.warning("Khởi tạo dịch vụ tìm kiếm thất bại, sẽ chạy ở chế độ không tìm kiếm: %s", exc, exc_info=True)
            self.search_service = None
        
        logger.info(f"Khởi tạo bộ điều phối hoàn tất, số luồng song song tối đa: {self.max_workers}")
        logger.info("Đã bật bộ máy phân tích kỹ thuật (đường MA / xu hướng / chỉ báo giá-khối lượng)")
        # In trạng thái cấu hình giá realtime
        if self.config.enable_realtime_quote:
            logger.info(f"Đã bật báo giá realtime (thứ tự ưu tiên: {self.config.realtime_source_priority})")
        else:
            logger.info("Báo giá realtime đã tắt, sẽ dùng giá đóng cửa lịch sử")
        if self.search_service is None:
            logger.warning("Dịch vụ tìm kiếm chưa bật (khởi tạo thất bại hoặc thiếu phụ thuộc)")
        elif self.search_service.is_available:
            logger.info("Đã bật dịch vụ tìm kiếm")
        else:
            logger.warning("Dịch vụ tìm kiếm chưa bật (chưa cấu hình khả năng tìm kiếm)")

    def _progress_text(self, stage: str, *, code: Any = "", name: Any = None, **params: Any) -> str:
        """Build the progress message in the run's report language.

        These strings go straight to the Run Flow UI via ``task.message`` (see
        ``src/services/run_flow.py``), so they must not stay in Chinese.
        ``name`` may still be the Chinese "stock {code}" placeholder produced by
        ``_resolve_stock_name``, so it is passed through ``get_localized_stock_name``
        before being displayed.
        """
        # Dựng thông báo tiến độ là việc "best-effort" giống ``_emit_progress``: nó chạy
        # TRƯỚC lớp try/except của hàm đó, nên phải tự chịu được trường hợp pipeline chưa
        # gắn ``config`` (đường khởi tạo một phần / test dùng ``__new__``). Một dòng tiến độ
        # không bao giờ được làm hỏng lượt phân tích.
        # Building the message runs before ``_emit_progress``'s guard, so it must tolerate a
        # pipeline without ``config``. A progress line must never break the analysis run.
        config = getattr(self, "config", None)
        language = normalize_report_language(getattr(config, "report_language", "vi"))
        if name:
            # ``get_localized_stock_name`` coi tên trùng mã là giữ chỗ, nên chỉ gọi khi thực
            # sự có tên; nếu không sẽ biến "HPG" thành "Cổ phiếu chưa xác định" và mất mã.
            # It treats a name equal to the code as a placeholder, so only call it when a real
            # name was supplied -- otherwise the ticker itself would be replaced.
            display = get_localized_stock_name(name, code, language)
        else:
            display = str(code or "")
        return get_pipeline_progress_text(stage, language, name=display, **params)

    def _emit_progress(self, progress: int, message: str) -> None:
        """Best-effort bridge from pipeline stages to task SSE progress."""
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        try:
            callback(progress, message)
        except Exception as exc:
            query_id = getattr(self, "query_id", None)
            logger.warning(
                "[pipeline] progress callback failed: %s (progress=%s, message=%r, query_id=%s)",
                exc,
                progress,
                message,
                query_id,
                extra={
                    "progress": progress,
                    "progress_message": message,
                    "query_id": query_id,
                },
            )

    @staticmethod
    def _filter_codes_by_enabled_markets(
        stock_codes: List[str],
        analysis_targets: Optional[List[Optional[AnalysisTarget]]],
    ) -> Tuple[List[str], Optional[List[Optional[AnalysisTarget]]]]:
        """Loại bỏ mã không thuộc thị trường đang bật, giữ codes và targets tương ứng 1-1.

        Mã không nhận diện được thị trường (``_market_tag`` trả ``unknown``) cũng bị
        loại: trong bản triển khai chỉ bật Việt Nam, ``600519`` / ``AAPL`` đều rơi vào
        nhóm này.
        """

        from data_provider.base import _market_tag
        from src.core.markets import enabled_markets, is_market_enabled

        if analysis_targets is not None and len(analysis_targets) != len(stock_codes):
            raise ValueError("analysis_targets must align with stock_codes")

        kept_codes: List[str] = []
        kept_targets: List[Optional[AnalysisTarget]] = []
        dropped: List[str] = []

        for index, code in enumerate(stock_codes):
            target = analysis_targets[index] if analysis_targets is not None else None
            try:
                market = _market_tag(code)
            except Exception:  # pragma: no cover - nhận diện lỗi thì giữ lại thận trọng
                market = None

            if market is not None and not is_market_enabled(market):
                dropped.append(f"{code}({market})")
                continue

            kept_codes.append(code)
            kept_targets.append(target)

        if dropped:
            logger.warning(
                "Bỏ qua mã không thuộc thị trường đã bật: %s (SUPPORTED_MARKETS hiện tại=%s)",
                ", ".join(dropped),
                ",".join(enabled_markets()),
            )

        return kept_codes, (kept_targets if analysis_targets is not None else None)

    def fetch_and_save_stock_data(
        self, 
        code: str,
        force_refresh: bool = False,
        current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Lấy và lưu dữ liệu của một mã cổ phiếu

        Logic tiếp tục dở dang:
        1. Kiểm tra CSDL đã có dữ liệu của phiên gần nhất có thể tái dùng chưa
        2. Nếu có và không bắt buộc làm mới thì bỏ qua request mạng
        3. Nếu không thì lấy từ nguồn và lưu lại

        Args:
            code: mã cổ phiếu
            force_refresh: có bắt buộc làm mới hay không (bỏ qua cache cục bộ)
            current_time: mốc thời gian tham chiếu đã đóng băng cho lượt chạy này, dùng
                để thống nhất việc xác định phiên mục tiêu khi tiếp tục dở dang
            analysis_target: mục tiêu phân tích có cấu trúc (mục tiêu chỉ số dùng để suy
                ra market=cn và ngữ nghĩa ngày)

        Returns:
            Tuple[thành công hay không, thông tin lỗi]
        """
        stock_name = code
        try:
            # Trước hết lấy tên cổ phiếu
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            target_date = self._resolve_resume_target_date(
                code, current_time=current_time, analysis_target=analysis_target
            )

            # Kiểm tra tiếp tục dở dang: nếu dữ liệu của phiên gần nhất có thể tái dùng
            # đã tồn tại thì bỏ qua
            if not force_refresh and self.db.has_today_data(code, target_date):
                logger.info(
                    f"{stock_name}({code}) {target_date} dữ liệu đã tồn tại, bỏ qua lấy dữ liệu (chạy tiếp từ điểm dừng)"
                )
                return True, None

            # Lấy dữ liệu từ nguồn
            logger.info(f"{stock_name}({code}) bắt đầu lấy dữ liệu từ nguồn dữ liệu...")
            df, source_name = self.fetcher_manager.get_daily_data(code, days=30)

            if df is None or df.empty:
                return False, "Dữ liệu lấy về rỗng"

            # Lưu vào cơ sở dữ liệu. Nguồn lệch cơ sở điều chỉnh giá (ví dụ Yahoo
            # phục vụ cổ phiếu Việt Nam) chỉ dùng cho phiên chạy hiện tại, không
            # được ghi đè lẻ tẻ vào lịch sử theo khoá (code, date).
            if should_persist_daily_data(source_name, code):
                saved_count = self.db.save_daily_data(df, code, source_name)
                logger.info(f"{stock_name}({code}) lưu dữ liệu thành công (nguồn: {source_name}, thêm mới {saved_count} bản ghi)")
            else:
                logger.info(
                    "%s(%s) Bỏ qua ghi DB: nguồn [%s] khác cơ sở điều chỉnh giá so với "
                    "nguồn chính của thị trường, dữ liệu chỉ dùng cho phiên chạy này",
                    stock_name,
                    code,
                    source_name,
                )

            return True, None

        except Exception as e:
            error_msg = f"Lấy/lưu dữ liệu thất bại: {str(e)}"
            logger.error(f"{stock_name}({code}) {error_msg}")
            return False, error_msg
    
    def analyze_stock(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Optional[AnalysisResult]:
        """
        Phân tích một mã cổ phiếu (bản mở rộng: gồm tỷ lệ khối lượng, tỷ lệ quay vòng,
        thông tin đa chiều)

        Quy trình:
        1. Lấy giá realtime (tỷ lệ khối lượng, tỷ lệ quay vòng) - qua DataFetcherManager,
           tự động chuyển nguồn khi sự cố
        2. Phân tích xu hướng (dựa trên triết lý giao dịch)
        3. Tìm kiếm thông tin đa chiều (tin mới nhất + rà soát rủi ro + kỳ vọng kết quả)
        4. Lấy ngữ cảnh phân tích từ CSDL
        5. Gọi AI để phân tích tổng hợp

        Args:
            query_id: id liên kết chuỗi truy vấn
            code: mã cổ phiếu
            report_type: loại báo cáo
            current_time: mốc thời gian tham chiếu đã đóng băng cho lượt chạy này, dùng
                để thống nhất ngữ cảnh phiên thị trường
            analysis_target: mục tiêu phân tích có cấu trúc (mục tiêu chỉ số dùng để suy
                ra market=cn và ma trận năng lực)

        Returns:
            AnalysisResult, hoặc None nếu phân tích thất bại
        """
        stock_name = code
        try:
            portfolio_context = getattr(self, "portfolio_context", None)
            if not isinstance(portfolio_context, dict):
                portfolio_context = None
            market = get_market_for_stock(normalize_stock_code(code))
            market_phase_context = build_market_phase_context(
                market=market,
                current_time=current_time,
                trigger_source=self.query_source,
                analysis_phase=getattr(self, "analysis_phase", "auto"),
            )
            market_phase_context_dict = market_phase_context.to_dict()
            market_phase_summary = render_market_phase_summary(market_phase_context_dict)
            report_language = normalize_report_language(getattr(self.config, "report_language", "vi"))
            daily_market_target_date = self._coerce_daily_market_context_date(
                getattr(market_phase_context, "effective_daily_bar_date", None)
                or market_phase_context_dict.get("effective_daily_bar_date")
            )
            if daily_market_target_date is None:
                daily_market_target_date = get_effective_trading_date(
                    market,
                    current_time=current_time,
                )
            daily_market_context = self._load_daily_market_context(
                market,
                target_date=daily_market_target_date,
            )

            self._emit_progress(18, self._progress_text("fetch_market", code=code))
            # Lấy tên cổ phiếu (đi đường nhẹ trước; nếu realtime_quote có name thì ghi đè sau)
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            # Bước 1: lấy giá realtime (tỷ lệ khối lượng, tỷ lệ quay vòng...) - dùng điểm vào
            # thống nhất, tự động chuyển nguồn khi sự cố
            realtime_quote = None
            try:
                if self.config.enable_realtime_quote:
                    realtime_quote = self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                    if realtime_quote:
                        if realtime_quote.name:
                            stock_name = realtime_quote.name
                        # Tương thích trường dữ liệu giữa các nguồn (một số nguồn có thể không có volume_ratio)
                        volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
                        turnover_rate = getattr(realtime_quote, 'turnover_rate', None)
                        logger.info(f"{stock_name}({code}) báo giá realtime: giá={realtime_quote.price}, "
                                  f"tỷ lệ KL={volume_ratio}, tỷ lệ quay vòng={turnover_rate}% "
                                  f"(nguồn: {realtime_quote.source.value if hasattr(realtime_quote, 'source') else 'unknown'})")
                    else:
                        logger.warning(f"{stock_name}({code}) mọi nguồn báo giá realtime đều không khả dụng, đã hạ cấp sang giá đóng cửa lịch sử để tiếp tục phân tích")
                else:
                    logger.info(f"{stock_name}({code}) báo giá realtime đã tắt, dùng giá đóng cửa lịch sử để tiếp tục phân tích")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) luồng báo giá realtime gặp lỗi, đã hạ cấp sang giá đóng cửa lịch sử để tiếp tục phân tích: {e}")

            # Nếu vẫn chưa có tên thì dùng luôn mã làm tên
            if not stock_name:
                stock_name = f'Cổ phiếu {code}'

            # If agent mode is explicitly enabled, or specific agent skills are configured, use the Agent analysis pipeline.
            # NOTE: use config.agent_mode (explicit opt-in) instead of
            # config.is_agent_available() so that users who only configured an
            # API Key for the traditional analysis path are not silently
            # switched to Agent mode (which is slower and more expensive).
            use_agent = getattr(self.config, 'agent_mode', False)
            if not use_agent:
                if self.analysis_skills:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to request skills: {self.analysis_skills}")
            if not use_agent:
                # Auto-enable agent mode when specific skills are configured (e.g., scheduled task with strategy)
                configured_skills = getattr(self.config, 'agent_skills', [])
                if configured_skills and configured_skills != ['all']:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to configured skills: {configured_skills}")

            self._emit_progress(32, self._progress_text("aggregate", code=code, name=stock_name))

            # Bước 2.5: tổng hợp năng lực dữ liệu cơ bản (điểm vào thống nhất, hạ cấp khi lỗi)
            # - Thất bại thì trả partial/failed, không ảnh hưởng luồng kỹ thuật/tin tức sẵn có
            # - Khi tắt công tắc vẫn trả về cấu trúc not_supported
            fundamental_context = None
            try:
                fundamental_context = self.fetcher_manager.get_fundamental_context(
                    code,
                    budget_seconds=getattr(
                        self.config,
                        'fundamental_stage_timeout_seconds',
                        FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
                    ),
                )
            except Exception as e:
                logger.warning(f"{stock_name}({code}) tổng hợp dữ liệu cơ bản thất bại: {e}")
                fundamental_context = self.fetcher_manager.build_failed_fundamental_context(code, str(e))


            # P0: write-only snapshot, fail-open, no read dependency on this table.
            try:
                self.db.save_fundamental_snapshot(
                    query_id=query_id,
                    code=code,
                    payload=fundamental_context,
                    source_chain=fundamental_context.get("source_chain", []),
                    coverage=fundamental_context.get("coverage", {}),
                )
            except Exception as e:
                logger.debug(f"{stock_name}({code}) ghi snapshot cơ bản thất bại: {e}")

            # Bước 3: phân tích xu hướng (dựa trên triết lý giao dịch) - chạy trước nhánh Agent
            # để cả hai luồng dùng chung
            trend_result: Optional[TrendAnalysisResult] = None
            try:
                from src.services.history_loader import get_frozen_target_date
                _mkt = get_market_for_stock(normalize_stock_code(code))
                frozen = get_frozen_target_date()
                end_date = frozen if frozen else get_market_now(_mkt).date()
                start_date = end_date - timedelta(days=89)  # ~60 trading days for MA60
                historical_bars = self.db.get_data_range(code, start_date, end_date)
                if historical_bars:
                    df = pd.DataFrame([bar.to_dict() for bar in historical_bars])
                    # Issue #234: Augment with realtime for intraday MA calculation
                    if self.config.enable_realtime_quote and realtime_quote:
                        df = self._augment_historical_with_realtime(
                            df, realtime_quote, code, market=market
                        )
                    trend_result = self.trend_analyzer.analyze(df, code)
                    logger.info(f"{stock_name}({code}) phân tích xu hướng: {trend_result.trend_status.value}, "
                              f"tín hiệu mua={trend_result.buy_signal.value}, điểm={trend_result.signal_score}")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) phân tích xu hướng thất bại: {e}", exc_info=True)

            if use_agent:
                logger.info(f"{stock_name}({code}) phân tích ở chế độ Agent")
                self._emit_progress(58, self._progress_text("agent_switch", code=code, name=stock_name))
                return self._analyze_with_agent(
                    code,
                    report_type,
                    query_id,
                    stock_name,
                    realtime_quote,
                    fundamental_context,
                    trend_result,
                    market_phase_context=market_phase_context_dict,
                    market_phase_summary=market_phase_summary,
                    daily_market_context=daily_market_context,
                    portfolio_context=portfolio_context,
                    analysis_target=analysis_target,
                )

            # Bước 4: tìm kiếm thông tin đa chiều (tin mới nhất + rà soát rủi ro + kỳ vọng kết quả)
            news_context = None
            persisted_intelligence_context = self._load_persisted_intelligence_context(
                code=code,
                stock_name=stock_name,
                market=market or "vn",
            )
            news_result_count: Optional[int] = None
            self._emit_progress(46, self._progress_text("news", code=code, name=stock_name))
            if self.search_service is not None and self.search_service.is_available:
                logger.info(f"{stock_name}({code}) bắt đầu tìm kiếm tin tức đa chiều...")

                # Đã phát lệnh tìm kiếm: từ đây dù không lấy được bản ghi nào thì vẫn là "đã chạy
                # nhưng không trúng kết quả", chứ không phải "chưa chạy tìm kiếm". Nếu để nguyên None
                # thì tình huống đáng cảnh báo nhất - mọi nguồn tìm kiếm đều hỏng - lại không được
                # cảnh báo.
                news_result_count = 0

                # Dùng tìm kiếm đa chiều (tối đa 5 lượt)
                intel_stock_code = code
                intel_results = self.search_service.search_comprehensive_intel(
                    stock_code=intel_stock_code,
                    stock_name=stock_name,
                    max_searches=5
                )

                # Định dạng báo cáo thông tin
                if intel_results:
                    news_context = self.search_service.format_intel_report(intel_results, stock_name)
                    total_results = sum(
                        len(r.results) for r in intel_results.values() if r.success
                    )
                    news_result_count = total_results
                    logger.info(f"{stock_name}({code}) tìm kiếm tin tức hoàn tất: tổng {total_results} kết quả")
                    logger.debug(f"{stock_name}({code}) kết quả tìm kiếm tin tức:\n{news_context}")

                    # Lưu thông tin tin tức vào cơ sở dữ liệu (phục vụ tổng kết và tra cứu sau này)
                    try:
                        query_context = self._build_query_context(query_id=query_id)
                        for dim_name, response in intel_results.items():
                            if response and response.success and response.results:
                                self.db.save_news_intel(
                                    code=code,
                                    name=stock_name,
                                    dimension=dim_name,
                                    query=response.query,
                                    response=response,
                                    query_context=query_context
                                )
                    except Exception as e:
                        logger.warning(f"{stock_name}({code}) lưu tin tức tình báo thất bại: {e}")
            else:
                logger.info(f"{stock_name}({code}) dịch vụ tìm kiếm không khả dụng, bỏ qua tìm kiếm tin tức")

            if persisted_intelligence_context:
                news_context = (
                    f"{news_context}\n\n{persisted_intelligence_context}"
                    if news_context
                    else persisted_intelligence_context
                )

            # Bước 5: lấy ngữ cảnh phân tích (dữ liệu phân tích kỹ thuật)
            self._emit_progress(58, self._progress_text("context", code=code, name=stock_name))
            context = self._get_analysis_context_with_market_fallback(
                code, analysis_target=analysis_target
            )

            if context is None:
                logger.warning(f"{stock_name}({code}) không lấy được dữ liệu giá lịch sử, sẽ chỉ phân tích dựa trên tin tức và báo giá realtime")
                _mkt_date = get_market_now(
                    get_market_for_stock(normalize_stock_code(code))
                ).date()
                context = {
                    'code': code,
                    'stock_name': stock_name,
                    'date': _mkt_date.isoformat(),
                    'data_missing': True,
                    'today': {},
                    'yesterday': {}
                }
            
            # Bước 6: bổ sung dữ liệu ngữ cảnh (thêm giá realtime, kết quả phân tích xu
            # hướng, tên cổ phiếu)
            enhanced_context = self._enhance_context(
                context, 
                realtime_quote, 
                trend_result,
                stock_name,  # Truyền vào tên cổ phiếu
                fundamental_context,
                market_phase_context=market_phase_context_dict,
                portfolio_context=portfolio_context,
            )
            enhanced_context["market_phase_context"] = market_phase_context_dict
            self._attach_daily_market_context(
                enhanced_context,
                daily_market_context,
                report_language=report_language,
            )
            if portfolio_context is not None:
                enhanced_context["portfolio_context"] = dict(portfolio_context)
            
            # Bước 7: gọi AI phân tích (truyền vào ngữ cảnh đã bổ sung và tin tức)
            (
                analysis_context_pack_summary,
                analysis_context_pack_overview,
            ) = self._build_analysis_context_pack_outputs(
                self._build_legacy_analysis_artifacts(
                    code=code,
                    stock_name=stock_name,
                    market=market,
                    phase=market_phase_context_dict,
                    context=context,
                    enhanced_context=enhanced_context,
                    realtime_quote=realtime_quote,
                    trend_result=trend_result,
                    fundamental_context=fundamental_context,
                    news_context=news_context,
                    news_result_count=news_result_count,
                    query_id=query_id,
                    portfolio_context=portfolio_context,
                ),
                report_language=report_language,
                code=code,
                query_id=query_id,
            )
            llm_progress_state = {"last_progress": 64}

            def _on_llm_stream(chars_received: int) -> None:
                dynamic_progress = min(92, 64 + min(chars_received // 80, 28))
                if dynamic_progress <= llm_progress_state["last_progress"]:
                    return
                llm_progress_state["last_progress"] = dynamic_progress
                self._emit_progress(
                    dynamic_progress,
                    self._progress_text(
                        "llm_streaming", code=code, name=stock_name, chars=chars_received
                    ),
                )

            self._emit_progress(64, self._progress_text("llm_request", code=code, name=stock_name))
            llm_started_at = time.monotonic()
            try:
                record_llm_run_started(
                    model=getattr(self.config, "litellm_model", None),
                    call_type="analysis",
                )
                result = self.analyzer.analyze(
                    enhanced_context,
                    news_context=news_context,
                    progress_callback=self._emit_progress,
                    stream_progress_callback=_on_llm_stream,
                    analysis_context_pack_summary=analysis_context_pack_summary,
                )
                llm_duration_ms = int((time.monotonic() - llm_started_at) * 1000)
                if result is not None:
                    # Để tầng hiển thị tự phân biệt "chưa cấu hình kênh", "tìm kiếm không trúng kết quả"
                    # và "trúng kết quả bình thường". Trước đây giá trị này chỉ vào ảnh chụp chẩn đoán,
                    # tầng báo cáo không lấy được.
                    result.news_result_count = news_result_count
                    # Đăng ký từng nguồn trong hai luồng, không nhìn vào cả đoạn news_context đã ghép:
                    # format_intel_report() khi không trúng kết quả vẫn xuất văn bản placeholder nên cả
                    # đoạn không bao giờ rỗng; lấy nó để phán đoán sẽ biến "đã tìm nhưng không được bản
                    # ghi nào" thành "có bằng chứng".
                    result.news_evidence_present = news_evidence_present(
                        news_result_count,
                        persisted_intelligence_context,
                    )
                record_llm_run(
                    success=bool(result and getattr(result, "success", True)),
                    model=getattr(result, "model_used", None) if result else None,
                    call_type="analysis",
                    duration_ms=llm_duration_ms,
                    error_type=(
                        None
                        if result and getattr(result, "success", True)
                        else "AnalysisResultError"
                    ),
                    error_message=(
                        getattr(result, "error_message", None)
                        if result and not getattr(result, "success", True)
                        else ("LLM returned empty result" if result is None else None)
                    ),
                )
            except Exception as exc:
                record_llm_run(
                    success=False,
                    model=getattr(self.config, "litellm_model", None),
                    call_type="analysis",
                    duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                    error_type=type(exc).__name__,
                    error_message=exc,
                )
                raise

            # Bước 7.5: đưa thông tin giá tại thời điểm phân tích vào result
            if result:
                self._emit_progress(94, self._progress_text("validate", code=code, name=stock_name))
                result.query_id = query_id
                realtime_data = enhanced_context.get('realtime', {})
                result.current_price = realtime_data.get('price')
                result.change_pct = realtime_data.get('change_pct')

            # Step 7.7: price_position fallback
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                action_source_advice = getattr(result, "operation_advice", None)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                adjustments = apply_phase_decision_guardrails(
                    result,
                    market_phase_summary=market_phase_summary,
                    analysis_context_pack_overview=analysis_context_pack_overview,
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "vi"),
                )
                if adjustments:
                    logger.info("[phase_decision_guardrail] Applied adjustments for %s: %s", code, adjustments)
                market_context_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=enhanced_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "vi"),
                )
                if market_context_adjustments:
                    logger.info(
                        "[daily_market_context_guardrail] Applied adjustments for %s: %s",
                        code,
                        market_context_adjustments,
                    )
                if isinstance(fundamental_context, dict):
                    result.fundamental_context = fundamental_context
                result.market_phase_summary = market_phase_summary
                result.analysis_context_pack_overview = analysis_context_pack_overview
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=action_source_advice,
                )

            # Bước 8: lưu lịch sử phân tích
            if result and result.success:
                try:
                    self._emit_progress(97, self._progress_text("save_report", code=code, name=stock_name))
                    context_snapshot = self._build_context_snapshot(
                        enhanced_context=enhanced_context,
                        news_content=news_context,
                        news_result_count=news_result_count,
                        realtime_quote=realtime_quote,
                        analysis_context_pack_overview=analysis_context_pack_overview,
                        market_phase_summary=market_phase_summary,
                    )
                    result.diagnostic_context_snapshot = context_snapshot
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=news_context,
                        context_snapshot=context_snapshot,
                        save_snapshot=self.save_context_snapshot
                    )
                    valid_saved_history_id = (
                        isinstance(saved_history_id, int)
                        and not isinstance(saved_history_id, bool)
                        and saved_history_id > 0
                    )
                    record_history_run(
                        report_saved=bool(saved_history_id),
                        metadata_saved=bool(saved_history_id),
                        analysis_history_id=(
                            saved_history_id if valid_saved_history_id else None
                        ),
                    )
                    if valid_saved_history_id:
                        self._extract_decision_signal_after_history_save(
                            result=result,
                            query_id=query_id,
                            source_report_id=saved_history_id,
                            report_type=report_type.value,
                            context_snapshot=context_snapshot,
                            portfolio_context=portfolio_context,
                            analysis_target=analysis_target,
                        )
                except Exception as e:
                    record_history_run(
                        report_saved=False,
                        metadata_saved=False,
                        error_message=e,
                    )
                    logger.warning(f"{stock_name}({code}) lưu lịch sử phân tích thất bại: {e}")

            return result

        except Exception as e:
            logger.error(f"{stock_name}({code}) phân tích thất bại: {e}")
            logger.exception(f"{stock_name}({code}) chi tiết lỗi:")
            return None
    
    def _enhance_context(
        self,
        context: Dict[str, Any],
        realtime_quote,
        trend_result: Optional[TrendAnalysisResult],
        stock_name: str = "",
        fundamental_context: Optional[Dict[str, Any]] = None,
        market_phase_context: Optional[Dict[str, Any]] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Bổ sung ngữ cảnh phân tích

        Thêm giá realtime, kết quả phân tích xu hướng và tên cổ phiếu vào ngữ cảnh

        Args:
            context: ngữ cảnh gốc
            realtime_quote: dữ liệu giá realtime (UnifiedRealtimeQuote hoặc None)
            trend_result: kết quả phân tích xu hướng
            stock_name: tên cổ phiếu
            market_phase_context: ngữ cảnh phiên thị trường đã dựng sẵn, dùng để đánh dấu
                nến chưa hoàn chỉnh (partial bar) trong phiên

        Returns:
            Ngữ cảnh sau khi đã bổ sung
        """
        enhanced = context.copy()
        enhanced["report_language"] = normalize_report_language(getattr(self.config, "report_language", "vi"))
        
        # Thêm tên cổ phiếu
        if stock_name:
            enhanced['stock_name'] = stock_name
        elif realtime_quote and getattr(realtime_quote, 'name', None):
            enhanced['stock_name'] = realtime_quote.name
        if isinstance(portfolio_context, dict):
            enhanced["portfolio_context"] = dict(portfolio_context)

        # Truyền thẳng cửa sổ tìm kiếm lúc chạy sang analyzer, tránh việc đọc lại cấu hình
        # toàn cục làm hai cửa sổ lệch nhau
        enhanced['news_window_days'] = getattr(self.search_service, "news_window_days", 3)
        
        # Thêm giá realtime (tương thích khác biệt trường dữ liệu giữa các nguồn)
        if realtime_quote:
            # Dùng getattr để lấy trường an toàn; trường thiếu trả None hoặc giá trị mặc định
            volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
            quote_source = getattr(realtime_quote, 'source', None)
            quote_source_name = getattr(quote_source, 'value', quote_source)
            quote_source_name = str(quote_source_name) if quote_source_name is not None else None
            enhanced['realtime'] = {
                'name': getattr(realtime_quote, 'name', ''),
                'price': getattr(realtime_quote, 'price', None),
                'change_pct': getattr(realtime_quote, 'change_pct', None),
                'volume_ratio': volume_ratio,
                'volume_ratio_desc': self._describe_volume_ratio(volume_ratio) if volume_ratio else 'Không có dữ liệu',
                'turnover_rate': getattr(realtime_quote, 'turnover_rate', None),
                'pe_ratio': getattr(realtime_quote, 'pe_ratio', None),
                'pb_ratio': getattr(realtime_quote, 'pb_ratio', None),
                'total_mv': getattr(realtime_quote, 'total_mv', None),
                'circ_mv': getattr(realtime_quote, 'circ_mv', None),
                'change_60d': getattr(realtime_quote, 'change_60d', None),
                # Trường riêng Việt Nam (FiinTrade); SSI không có nên bị lọc bỏ ở dưới.
                # Vietnam-specific fields (FiinTrade); dropped below when None.
                'eps_ttm': getattr(realtime_quote, 'eps_ttm', None),
                'roe': getattr(realtime_quote, 'roe', None),
                'beta': getattr(realtime_quote, 'beta', None),
                'ref_price': getattr(realtime_quote, 'ref_price', None),
                'ceiling': getattr(realtime_quote, 'ceiling', None),
                'floor': getattr(realtime_quote, 'floor', None),
                'foreign_buy_value': getattr(realtime_quote, 'foreign_buy_value', None),
                'foreign_sell_value': getattr(realtime_quote, 'foreign_sell_value', None),
                'foreign_net_value': getattr(realtime_quote, 'foreign_net_value', None),
                'foreign_room_remaining': getattr(realtime_quote, 'foreign_room_remaining', None),
                'foreign_ownership_pct': getattr(realtime_quote, 'foreign_ownership_pct', None),
                'put_through_volume': getattr(realtime_quote, 'put_through_volume', None),
                'put_through_value': getattr(realtime_quote, 'put_through_value', None),
                'bid_ask_imbalance': getattr(realtime_quote, 'bid_ask_imbalance', None),
                'avg_volume_1m': getattr(realtime_quote, 'avg_volume_1m', None),
                'source': quote_source_name,
                'fetched_at': getattr(realtime_quote, 'fetched_at', None),
                'provider_timestamp': getattr(realtime_quote, 'provider_timestamp', None),
                'is_stale': getattr(realtime_quote, 'is_stale', None),
                'stale_seconds': getattr(realtime_quote, 'stale_seconds', None),
                'fallback_from': getattr(realtime_quote, 'fallback_from', None),
            }
            # Loại bỏ giá trị None để giảm kích thước ngữ cảnh
            enhanced['realtime'] = {k: v for k, v in enhanced['realtime'].items() if v is not None}
        
        # Thêm kết quả phân tích xu hướng
        if trend_result:
            enhanced['trend_analysis'] = {
                'trend_status': trend_result.trend_status.value,
                'ma_alignment': trend_result.ma_alignment,
                'trend_strength': trend_result.trend_strength,
                'bias_ma5': trend_result.bias_ma5,
                'bias_ma10': trend_result.bias_ma10,
                'volume_status': trend_result.volume_status.value,
                'volume_trend': trend_result.volume_trend,
                'buy_signal': trend_result.buy_signal.value,
                'signal_score': trend_result.signal_score,
                'signal_reasons': trend_result.signal_reasons,
                'risk_factors': trend_result.risk_factors,
            }

        # Issue #234: phân tích trong phiên dùng OHLC realtime và MA xu hướng để ghi đè today.
        # Điều kiện bảo vệ: trend_result.ma5 > 0 nghĩa là MA đã tính xong và đủ dữ liệu.
        if realtime_quote and trend_result and trend_result.ma5 > 0:
            price = getattr(realtime_quote, 'price', None)
            if price is not None and price > 0:
                yesterday_close = None
                if enhanced.get('yesterday') and isinstance(enhanced['yesterday'], dict):
                    yesterday_close = enhanced['yesterday'].get('close')
                orig_today = enhanced.get('today') or {}
                market_today = get_market_now(
                    get_market_for_stock(normalize_stock_code(enhanced.get('code', '')))
                ).date().isoformat()
                source = getattr(realtime_quote, 'source', None)
                source_name = getattr(source, 'value', source)
                source_name = str(source_name) if source_name is not None else 'unknown'
                open_p = getattr(realtime_quote, 'open_price', None) or getattr(
                    realtime_quote, 'pre_close', None
                ) or yesterday_close or orig_today.get('open') or price
                high_p = getattr(realtime_quote, 'high', None) or price
                low_p = getattr(realtime_quote, 'low', None) or price
                vol = getattr(realtime_quote, 'volume', None)
                amt = getattr(realtime_quote, 'amount', None)
                pct = getattr(realtime_quote, 'change_pct', None)
                fetched_at = getattr(realtime_quote, 'fetched_at', None)
                provider_timestamp = getattr(realtime_quote, 'provider_timestamp', None)
                fallback_from = getattr(realtime_quote, 'fallback_from', None)
                realtime_today = {
                    'close': price,
                    'open': open_p,
                    'high': high_p,
                    'low': low_p,
                    'ma5': trend_result.ma5,
                    'ma10': trend_result.ma10,
                    'ma20': trend_result.ma20,
                    'date': market_today,
                    'data_source': f"realtime:{source_name}",
                    'realtime_source': source_name,
                    'is_estimated': True,
                }
                estimated_fields = [
                    'close', 'open', 'high', 'low', 'ma5', 'ma10', 'ma20',
                ]
                if vol is not None:
                    realtime_today['volume'] = vol
                    estimated_fields.append('volume')
                if amt is not None:
                    realtime_today['amount'] = amt
                    estimated_fields.append('amount')
                if pct is not None:
                    realtime_today['pct_chg'] = pct
                    estimated_fields.append('pct_chg')
                realtime_today['estimated_fields'] = estimated_fields
                if isinstance(market_phase_context, dict) and "is_partial_bar" in market_phase_context:
                    realtime_today['is_partial_bar'] = market_phase_context.get("is_partial_bar")
                if fetched_at is not None:
                    realtime_today['fetched_at'] = fetched_at
                if provider_timestamp is not None:
                    realtime_today['provider_timestamp'] = provider_timestamp
                if fallback_from is not None:
                    realtime_today['fallback_from'] = fallback_from
                realtime_owned_fields = {
                    'open', 'high', 'low', 'close',
                    'volume', 'amount', 'pct_chg', 'pctChg',
                    'date', 'data_source', 'dataSource', 'source',
                    'realtime_source', 'realtimeSource',
                    'is_partial_bar', 'isPartialBar', 'is_estimated',
                    'isEstimated', 'estimated_fields', 'estimatedFields',
                    'fetched_at', 'fetchedAt', 'provider_timestamp',
                    'providerTimestamp', 'fallback_from', 'fallbackFrom',
                }
                for k, v in orig_today.items():
                    if k not in realtime_today and k not in realtime_owned_fields and v is not None:
                        realtime_today[k] = v
                enhanced['today'] = realtime_today
                enhanced['ma_status'] = self._compute_ma_status(
                    price, trend_result.ma5, trend_result.ma10, trend_result.ma20
                )
                enhanced['date'] = market_today
                if yesterday_close is not None:
                    try:
                        yc = float(yesterday_close)
                        if yc > 0:
                            enhanced['price_change_ratio'] = round(
                                (price - yc) / yc * 100, 2
                            )
                    except (TypeError, ValueError):
                        pass
                if vol is not None and enhanced.get('yesterday'):
                    yest_vol = enhanced['yesterday'].get('volume') if isinstance(
                        enhanced['yesterday'], dict
                    ) else None
                    if yest_vol is not None:
                        try:
                            yv = float(yest_vol)
                            if yv > 0:
                                enhanced['volume_change_ratio'] = round(
                                    float(vol) / yv, 2
                                )
                        except (TypeError, ValueError):
                            pass

        # ETF/index flag for analyzer prompt (Fixes #274)
        enhanced['is_index_etf'] = SearchService.is_index_or_etf(
            context.get('code', ''), enhanced.get('stock_name', stock_name)
        )

        # P0: append unified fundamental block; keep as additional context only
        enhanced["fundamental_context"] = (
            fundamental_context
            if isinstance(fundamental_context, dict)
            else self.fetcher_manager.build_failed_fundamental_context(
                context.get("code", ""),
                "invalid fundamental context",
            )
        )

        return enhanced

    def _ensure_agent_history(
        self,
        code: str,
        min_days: int = 240,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> None:
        """Ensure at least *min_days* of K-line history is in DB for agent tools."""
        from src.services.history_loader import get_frozen_target_date

        target = get_frozen_target_date()
        if target is None:
            target = self._resolve_resume_target_date(
                code, analysis_target=analysis_target
            )
        start = target - timedelta(days=int(min_days * 1.8))
        bars = self.db.get_data_range(code, start, target)
        if bars and len(bars) >= min(min_days, 200):
            logger.debug("[%s] Agent history: %d bars in DB, sufficient", code, len(bars))
            return
        try:
            df, source = self.fetcher_manager.get_daily_data(code, days=min_days)
            if df is not None and not df.empty:
                if should_persist_daily_data(source, code):
                    self.db.save_daily_data(df, code, source)
                    logger.info("[%s] Prefetched %d rows of history for agent (source: %s)", code, len(df), source)
                else:
                    logger.info(
                        "[%s] Bỏ qua ghi DB cho %d dòng lịch sử: nguồn [%s] khác cơ sở "
                        "điều chỉnh giá",
                        code,
                        len(df),
                        source,
                    )
        except Exception as e:
            logger.warning("[%s] Agent history prefetch failed: %s", code, e)


    def _analyze_with_agent(
        self, 
        code: str, 
        report_type: ReportType, 
        query_id: str,
        stock_name: str,
        realtime_quote: Any,
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Optional[TrendAnalysisResult] = None,
        *,
        market_phase_context: Optional[Dict[str, Any]] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
        daily_market_context: Optional[DailyMarketContext] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Optional[AnalysisResult]:
        """
        Phân tích một mã cổ phiếu ở chế độ Agent.
        """
        try:
            from src.agent.factory import build_agent_executor
            report_language = normalize_report_language(getattr(self.config, "report_language", "vi"))

            requested_skills = (
                self.analysis_skills
                if self.analysis_skills is not None
                else (getattr(self.config, 'agent_skills', None) or None)
            )
            # Build executor from shared factory (ToolRegistry and SkillManager prototype are cached)
            executor = build_agent_executor(self.config, requested_skills)

            # Build initial context to avoid redundant tool calls
            initial_context = {
                "stock_code": code,
                "stock_name": stock_name,
                "report_type": report_type.value,
                "report_language": report_language,
                "fundamental_context": fundamental_context,
            }
            if isinstance(portfolio_context, dict):
                initial_context["portfolio_context"] = dict(portfolio_context)
            if self.analysis_skills is not None:
                initial_context["skills"] = self.analysis_skills
            if market_phase_context is not None:
                initial_context["market_phase_context"] = market_phase_context
            self._attach_daily_market_context(
                initial_context,
                daily_market_context,
                report_language=report_language,
            )
            
            if realtime_quote:
                initial_context["realtime_quote"] = self._safe_to_dict(realtime_quote)
            if trend_result:
                initial_context["trend_result"] = self._safe_to_dict(trend_result)

            persisted_intelligence_context = self._load_persisted_intelligence_context(
                code=code,
                stock_name=stock_name,
                market=get_market_for_stock(normalize_stock_code(code)) or "vn",
            )
            if persisted_intelligence_context:
                existing = initial_context.get("news_context")
                initial_context["news_context"] = (
                    f"{existing}\n\n{persisted_intelligence_context}"
                    if existing
                    else persisted_intelligence_context
                )
                logger.info(f"[{code}] Agent mode: local intelligence evidence injected into news_context")

            # Issue #1066: ensure deep history is in DB before agent tools run
            if analysis_target is None:
                self._ensure_agent_history(code)
            else:
                self._ensure_agent_history(code, analysis_target=analysis_target)

            analysis_context = self._load_agent_analysis_context(
                code, stock_name, analysis_target=analysis_target
            )
            market = get_market_for_stock(normalize_stock_code(code))
            (
                analysis_context_pack_summary,
                analysis_context_pack_overview,
            ) = self._build_analysis_context_pack_outputs(
                self._build_agent_analysis_artifacts(
                    code=code,
                    stock_name=stock_name,
                    market=market,
                    phase=market_phase_context,
                    initial_context=initial_context,
                    fundamental_context=fundamental_context,
                    query_id=query_id,
                    base_context=analysis_context,
                    portfolio_context=portfolio_context,
                ),
                report_language=report_language,
                code=code,
                query_id=query_id,
            )
            if analysis_context_pack_summary:
                initial_context["analysis_context_pack_summary"] = analysis_context_pack_summary

            # Chạy Agent
            message = f"Analyze stock {code} ({stock_name}) and return the full decision dashboard JSON."
            llm_started_at = time.monotonic()
            # Agent tự gọi tool tìm kiếm để lấy bằng chứng, nên số liệu công bố chỉ lấy từ kết
            # quả thật của những tool đó; lời gọi search_stock_news() bù thêm sau khi phân tích
            # xong không liên quan tới bằng chứng mà Agent đã dùng.
            # Đối tượng bộ đếm giữ tham chiếu ở đây nên sau khi reset vẫn đọc được an toàn.
            news_evidence_token = activate_news_evidence_scope()
            news_evidence = get_current_news_evidence()
            try:
                record_llm_run_started(
                    model=getattr(self.config, "agent_litellm_model", None),
                    call_type="agent_analysis",
                )
                agent_result = executor.run(message, context=initial_context)
            except Exception as exc:
                record_llm_run(
                    success=False,
                    model=getattr(self.config, "agent_litellm_model", None),
                    call_type="agent_analysis",
                    duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                    error_type=type(exc).__name__,
                    error_message=exc,
                )
                raise
            finally:
                reset_news_evidence_scope(news_evidence_token)

            # Chuyển thành AnalysisResult
            result = self._agent_result_to_analysis_result(
                agent_result,
                code,
                stock_name,
                report_type,
                query_id,
                trend_result=trend_result,
            )

            # Bộ đếm ba trạng thái lấy từ kết quả tool tìm kiếm mà Agent thực sự dùng: kênh không
            # khả dụng thì là None (chưa chạy tìm kiếm), kênh khả dụng thì bắt đầu từ 0 và lấy
            # được bao nhiêu tính bấy nhiêu.
            if result is not None and news_evidence is not None:
                result.news_result_count = news_evidence.resolve(
                    search_available=bool(
                        self.search_service is not None
                        and self.search_service.is_available
                    ),
                )
                # Giống nhánh thường, đăng ký theo từng nguồn: số bản ghi Agent tự tìm được khi chạy
                # và kho tin tức cục bộ được tiêm vào. Nhánh này
                # không đi qua format_intel_report(), nhưng vẫn không truyền cả đoạn đã ghép, phòng
                # trường hợp sau này có người thêm một nguồn sinh văn bản placeholder.
                result.news_evidence_present = news_evidence_present(
                    result.news_result_count,
                    persisted_intelligence_context,
                )
            record_llm_run(
                success=bool(result and getattr(result, "success", True)),
                model=getattr(result, "model_used", None) if result else getattr(agent_result, "model", None),
                call_type="agent_analysis",
                duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                error_type=(
                    None
                    if result and getattr(result, "success", True)
                    else "AgentResultError"
                ),
                error_message=(
                    getattr(result, "error_message", None)
                    if result and not getattr(result, "success", True)
                    else ("Agent returned empty result" if result is None else None)
                ),
            )
            if result:
                result.query_id = query_id
            # Agent weak integrity: placeholder fill only, no LLM retry
            if result and getattr(self.config, "report_integrity_enabled", False):
                from src.analyzer import check_content_integrity, apply_placeholder_fill

                pass_integrity, missing = check_content_integrity(
                    result,
                    require_phase_decision=isinstance(market_phase_summary, dict),
                )
                if not pass_integrity:
                    apply_placeholder_fill(result, missing)
                    logger.info(
                        "[LLM integrity] integrity_mode=agent_weak thiếu trường bắt buộc %s, đã điền giá trị giữ chỗ",
                        missing,
                    )
            # price_position fallback (same as non-agent path Step 7.7)
            if result:
                pipeline_adjustments: list[PipelineActionAdjustment] = []
                runtime_facts = getattr(agent_result, "runtime_facts", None)
                pipeline_start_signal = getattr(result, "decision_type", "hold")
                risk_application = (
                    getattr(runtime_facts, "risk_override_application", None)
                    if runtime_facts is not None
                    else None
                )
                if risk_application is not None:
                    pipeline_start_signal = risk_application.post_risk_signal.value
                initial_action_advice = getattr(result, "operation_advice", None)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=initial_action_advice,
                )
                pipeline_start_action = normalize_decision_action(
                    getattr(result, "action", None)
                )
                action_chain_valid = pipeline_start_action is not None
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                realtime_data = initial_context.get("realtime_quote", {})
                if isinstance(realtime_data, dict):
                    result.current_price = realtime_data.get("price")
                    result.change_pct = realtime_data.get("change_pct")
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="structure_and_fundamentals",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                adjustments = apply_phase_decision_guardrails(
                    result,
                    market_phase_summary=market_phase_summary,
                    analysis_context_pack_overview=analysis_context_pack_overview,
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "vi"),
                )
                if adjustments:
                    logger.info("[phase_decision_guardrail] Applied agent adjustments for %s: %s", code, adjustments)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="market_phase",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                market_context_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=initial_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "vi"),
                )
                if market_context_adjustments:
                    logger.info(
                        "[daily_market_context_guardrail] Applied agent adjustments for %s: %s",
                        code,
                        market_context_adjustments,
                    )
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="daily_market_context",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                if isinstance(fundamental_context, dict):
                    result.fundamental_context = fundamental_context
                result.market_phase_summary = market_phase_summary
                result.analysis_context_pack_overview = analysis_context_pack_overview
                final_action = normalize_decision_action(getattr(result, "action", None))
                if isinstance(result.dashboard, dict):
                    result.dashboard.pop("agent_disagreement_explanation", None)
                if (
                    runtime_facts is not None
                    and action_chain_valid
                    and pipeline_start_action is not None
                    and final_action is not None
                ):
                    if not isinstance(result.dashboard, dict):
                        result.dashboard = {}
                    result.dashboard["agent_disagreement_explanation"] = (
                        build_pipeline_final_explanation(
                            runtime_facts=runtime_facts,
                            pipeline_start_signal=pipeline_start_signal,
                            pipeline_start_action=pipeline_start_action,
                            final_action=final_action,
                            pipeline_adjustments=pipeline_adjustments,
                            data_quality=(
                                analysis_context_pack_overview.get("data_quality")
                                if isinstance(analysis_context_pack_overview, dict)
                                else None
                            ),
                        )
                    )


            resolved_stock_name = result.name if result and result.name else stock_name

            # Lưu thông tin tin tức vào cơ sở dữ liệu (kết quả tool của Agent chỉ dùng cho ngữ
            # cảnh LLM, không được lưu bền, Fixes #396)
            # Dùng search_stock_news (khớp logic gọi tool của Agent), chỉ 1 lời gọi API, không
            # thêm độ trễ
            if self.search_service is not None and self.search_service.is_available:
                try:
                    news_response = self.search_service.search_stock_news(
                        stock_code=code,
                        stock_name=resolved_stock_name,
                        max_results=5
                    )
                    # Lần tra bù này chỉ để lưu bền thông tin tin tức (Fixes #396); cố ý KHÔNG ghi
                    # result.news_result_count: nó xảy ra sau khi phân tích xong, không liên quan tới
                    # bằng chứng mà Agent thực sự dùng; lấy nó để phán đoán công bố sẽ sai lệch theo cả
                    # hai hướng. Số đếm thật được thu thập trong phạm vi bằng chứng của executor.run()
                    # (xem phần trên).
                    if news_response.success and news_response.results:
                        query_context = self._build_query_context(query_id=query_id)
                        self.db.save_news_intel(
                            code=code,
                            name=resolved_stock_name,
                            dimension="latest_news",
                            query=news_response.query,
                            response=news_response,
                            query_context=query_context
                        )
                        logger.info(f"[{code}] Chế độ Agent: đã lưu {len(news_response.results)} tin tức tình báo")
                except Exception as e:
                    logger.warning(f"[{code}] Chế độ Agent lưu tin tức tình báo thất bại: {e}")

            # Lưu lịch sử phân tích
            if result and result.success:
                try:
                    agent_context_snapshot = self._build_context_snapshot(
                        enhanced_context={
                            **self._without_runtime_prompt_context(initial_context),
                            "stock_name": resolved_stock_name,
                        },
                        news_content=initial_context.get("news_context"),
                        realtime_quote=realtime_quote,
                        analysis_context_pack_overview=analysis_context_pack_overview,
                        market_phase_summary=market_phase_summary,
                    )
                    result.diagnostic_context_snapshot = agent_context_snapshot
                    agent_context_snapshot["stock_name"] = resolved_stock_name
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=None,
                        context_snapshot=agent_context_snapshot,
                        save_snapshot=self.save_context_snapshot,
                    )
                    valid_saved_history_id = (
                        isinstance(saved_history_id, int)
                        and not isinstance(saved_history_id, bool)
                        and saved_history_id > 0
                    )
                    record_history_run(
                        report_saved=bool(saved_history_id),
                        metadata_saved=bool(saved_history_id),
                        analysis_history_id=(
                            saved_history_id if valid_saved_history_id else None
                        ),
                    )
                    if valid_saved_history_id:
                        self._persist_skill_opinion_samples_after_history_save(
                            runtime_facts=getattr(agent_result, "runtime_facts", None),
                            analysis_history_id=saved_history_id,
                            stock_code=code,
                            analysis_context_pack_overview=analysis_context_pack_overview,
                        )
                        self._extract_decision_signal_after_history_save(
                            result=result,
                            query_id=query_id,
                            source_report_id=saved_history_id,
                            report_type=report_type.value,
                            context_snapshot=agent_context_snapshot,
                            portfolio_context=portfolio_context,
                            analysis_target=analysis_target,
                        )
                    latest_diagnostic_snapshot = current_diagnostic_snapshot()
                    if latest_diagnostic_snapshot is not None:
                        agent_context_snapshot["diagnostics"] = latest_diagnostic_snapshot
                        result.diagnostic_context_snapshot = agent_context_snapshot
                except Exception as e:
                    record_history_run(
                        report_saved=False,
                        metadata_saved=False,
                        error_message=e,
                    )
                    logger.warning(f"[{code}] Lưu lịch sử phân tích Agent thất bại: {e}")

            return result

        except Exception as e:
            logger.error(f"[{code}] Phân tích Agent thất bại: {e}")
            logger.exception(f"[{code}] Chi tiết lỗi Agent:")
            return None

    def _load_agent_analysis_context(
        self, code: str, stock_name: str, analysis_target: Optional[AnalysisTarget] = None
    ) -> Dict[str, Any]:
        """Load daily-bar context for Agent pack summaries without blocking analysis."""
        try:
            context = self._get_analysis_context_with_market_fallback(
                code, analysis_target=analysis_target
            )
        except Exception as exc:
            logger.warning(
                "[%s] Agent analysis context load failed; daily_bars will be marked missing: %s",
                code,
                exc,
            )
            context = None

        if isinstance(context, dict) and context:
            enriched = dict(context)
            enriched.setdefault("code", code)
            if stock_name:
                enriched.setdefault("stock_name", stock_name)
            return enriched

        return {
            "code": code,
            "stock_name": stock_name,
            "data_missing": True,
            "today": {},
            "yesterday": {},
        }

    def _get_analysis_context_with_market_fallback(
        self, code: str, analysis_target: Optional[AnalysisTarget] = None
    ) -> Optional[Dict[str, Any]]:
        """Load analysis context, fetching JP/KR/TW/VN daily bars when DB has no context.

        ``vn`` nằm ở đây vì nến ngày do Yahoo phục vụ cố tình không được ghi vào DB
        (xem ``should_persist_daily_data``). Không có nhánh này thì một lần failover
        sang Yahoo sẽ khiến phân tích chạy mà không có chút lịch sử giá nào.
        """
        context = self.db.get_analysis_context(code)
        if isinstance(context, dict) and context:
            return context

        market = get_market_for_stock(normalize_stock_code(code))
        if market != "vn":
            return context

        try:
            df, source_name = self.fetcher_manager.get_daily_data(code, days=60)
        except Exception as exc:
            logger.warning("[%s] JP/KR/VN daily fallback fetch failed: %s", code, exc)
            return context

        if df is None or df.empty:
            logger.warning("[%s] JP/KR/VN daily fallback returned empty data", code)
            return context

        try:
            if not should_persist_daily_data(source_name, code):
                logger.info(
                    "[%s] Bỏ qua ghi DB: nguồn [%s] khác cơ sở điều chỉnh giá",
                    code,
                    source_name,
                )
                return self._build_analysis_context_from_daily_df(code, df)
            self.db.save_daily_data(df, code, source_name)
            refreshed = self.db.get_analysis_context(code)
            if isinstance(refreshed, dict) and refreshed:
                return refreshed
        except Exception as exc:
            logger.warning("[%s] JP/KR/VN daily fallback persistence failed: %s", code, exc)

        return self._build_analysis_context_from_daily_df(code, df)

    def _build_analysis_context_from_daily_df(self, code: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        if df is None or df.empty:
            return None

        frame = df.copy()
        frame.columns = [str(column).lower() for column in frame.columns]
        if "date" in frame.columns:
            frame = frame.sort_values("date")
        frame = frame.tail(2)
        rows = frame.to_dict(orient="records")
        if not rows:
            return None

        def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
            normalized: Dict[str, Any] = {"code": row.get("code") or code}
            for key in ("open", "high", "low", "close", "volume", "amount", "pct_chg", "ma5", "ma10", "ma20", "volume_ratio"):
                value = row.get(key)
                if pd.notna(value):
                    normalized[key] = float(value)
            row_date = row.get("date")
            if hasattr(row_date, "date"):
                row_date = row_date.date()
            normalized["date"] = row_date.isoformat() if hasattr(row_date, "isoformat") else str(row_date)
            return normalized

        today = normalize_row(rows[-1])
        context: Dict[str, Any] = {
            "code": code,
            "date": today.get("date"),
            "today": today,
        }
        if len(rows) > 1:
            yesterday = normalize_row(rows[-2])
            context["yesterday"] = yesterday
            yesterday_volume = yesterday.get("volume")
            if yesterday_volume:
                context["volume_change_ratio"] = round(float(today.get("volume", 0)) / float(yesterday_volume), 2)
            yesterday_close = yesterday.get("close")
            if yesterday_close:
                context["price_change_ratio"] = round(
                    (float(today.get("close", 0)) - float(yesterday_close)) / float(yesterday_close) * 100,
                    2,
                )
            context["ma_status"] = self.db._analyze_ma_status(SimpleNamespace(**today))

        return context

    def _load_daily_market_context(
        self,
        market: str,
        *,
        force_refresh: bool = False,
        target_date: Optional[date] = None,
    ) -> Optional[DailyMarketContext]:
        """Load/generate today's market context when market review is explicitly enabled."""
        if getattr(self, "daily_market_context_enabled", True) is not True:
            return None
        if getattr(self.config, "daily_market_context_enabled", True) is not True:
            return None
        if getattr(self.config, "market_review_enabled", None) is not True:
            return None

        try:
            service = getattr(self, "_daily_market_context_service", None)
            if service is None:
                service_lock = self._get_daily_market_context_service_lock()
                with service_lock:
                    service = getattr(self, "_daily_market_context_service", None)
                    if service is None:
                        service = DailyMarketContextService(db_manager=self.db)
                        self._daily_market_context_service = service
            get_context_kwargs = {
                "region": market,
                "config": self.config,
                "notifier": self.notifier,
                "analyzer": self.analyzer,
                "search_service": self.search_service,
                "force_refresh": force_refresh,
                "allow_generate": getattr(self, "daily_market_context_allow_generate", True),
                "target_date": target_date,
            }
            current_query_id = getattr(self, "query_id", None)
            if isinstance(current_query_id, str) and current_query_id.strip():
                get_context_kwargs["current_query_id"] = current_query_id
            return service.get_context(**get_context_kwargs)
        except Exception as exc:
            logger.warning("Tải ngữ cảnh môi trường thị trường thất bại, phân tích từng mã vẫn tiếp tục: %s", exc, exc_info=True)
            return None

    def _get_daily_market_context_service_lock(self) -> threading.Lock:
        service_lock = getattr(self, "_daily_market_context_service_lock", None)
        if service_lock is not None:
            return service_lock
        with _DAILY_MARKET_CONTEXT_SERVICE_LOCK_INIT_GUARD:
            service_lock = getattr(self, "_daily_market_context_service_lock", None)
            if service_lock is None:
                service_lock = threading.Lock()
                self._daily_market_context_service_lock = service_lock
            return service_lock

    @staticmethod
    def _coerce_daily_market_context_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    @staticmethod
    def _attach_daily_market_context(
        target_context: Dict[str, Any],
        daily_market_context: Optional[DailyMarketContext],
        *,
        report_language: str,
    ) -> None:
        """Attach only the safe daily market summary to runtime analysis context."""
        if daily_market_context is None:
            return
        safe_context = daily_market_context.to_safe_dict()
        prompt_section = format_daily_market_context_prompt_section(
            safe_context,
            report_language=report_language,
        )
        if not prompt_section:
            return
        target_context["daily_market_context"] = safe_context
        target_context["daily_market_context_summary"] = prompt_section

    def _agent_result_to_analysis_result(
        self,
        agent_result,
        code: str,
        stock_name: str,
        report_type: ReportType,
        query_id: str,
        trend_result: Optional[TrendAnalysisResult] = None,
    ) -> AnalysisResult:
        """
        Chuyển AgentResult thành AnalysisResult.
        """
        report_language = normalize_report_language(getattr(self.config, "report_language", "vi"))
        dash = None
        result = AnalysisResult(
            code=code,
            name=stock_name,
            sentiment_score=50,
            trend_prediction=get_unknown_text(report_language),
            operation_advice=localize_operation_advice("watch", report_language),
            confidence_level=localize_confidence_level("medium", report_language),
            report_language=report_language,
            success=agent_result.success,
            error_message=agent_result.error or None,
            data_sources=f"agent:{agent_result.provider}",
            model_used=agent_result.model or None,
        )

        if agent_result.success and agent_result.dashboard:
            dash = agent_result.dashboard
            ai_stock_name = str(dash.get("stock_name", "")).strip()
            if ai_stock_name and self._is_placeholder_stock_name(stock_name, code):
                result.name = ai_stock_name

            nested_dashboard = dash.get("dashboard") if isinstance(dash, dict) else None

            raw_score = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "sentiment_score",
                scalar=True,
            )
            if self._is_agent_field_missing(raw_score, scalar=True):
                fallback_score = self._trend_score_fallback(trend_result)
                if fallback_score is not None:
                    result.sentiment_score = fallback_score
                    self._mark_trend_fallback_source(result)
            else:
                result.sentiment_score = self._safe_int(raw_score, 50)

            raw_trend = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "trend_prediction",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_trend, scalar=True, expect_text=True):
                trend_label = self._trend_label_fallback(
                    trend_result,
                    report_language,
                )
                if trend_label:
                    result.trend_prediction = trend_label
                    self._mark_trend_fallback_source(result)
            else:
                result.trend_prediction = str(raw_trend)

            raw_advice = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "operation_advice",
                scalar=True,
                allow_dict=True,
                expect_text=True,
            )
            extracted_advice = ""
            if isinstance(raw_advice, dict):
                # LLM may return {"no_position": "...", "has_position": "..."}
                extracted_advice = self._extract_advice_text_from_dict(raw_advice)
                if extracted_advice:
                    result.operation_advice = localize_operation_advice(
                        extracted_advice,
                        report_language,
                    )
                else:
                    signal_label = self._trend_signal_fallback(
                        trend_result,
                        report_language,
                    )
                    if signal_label:
                        result.operation_advice = signal_label
                        self._mark_trend_fallback_source(result)
            elif not self._is_agent_field_missing(
                raw_advice,
                scalar=True,
                allow_dict=True,
                expect_text=True,
            ):
                result.operation_advice = str(raw_advice) if raw_advice else (localize_operation_advice("watch", report_language))
            else:
                signal_label = self._trend_signal_fallback(trend_result, report_language)
                if signal_label:
                    result.operation_advice = signal_label
                    self._mark_trend_fallback_source(result)
            from src.agent.protocols import normalize_decision_signal

            raw_decision = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "decision_type",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_decision, scalar=True, expect_text=True):
                trend_decision = self._trend_decision_fallback(trend_result)
                decision_from_advice = infer_decision_type_from_advice(
                    result.operation_advice,
                    default="",
                )
                if decision_from_advice:
                    result.decision_type = decision_from_advice
                    if (
                        self._is_agent_field_missing(
                            raw_advice,
                            scalar=True,
                            allow_dict=True,
                            expect_text=True,
                        )
                        and not extracted_advice
                        and trend_decision
                    ):
                        self._mark_trend_fallback_source(result)
                else:
                    result.decision_type = trend_decision or "hold"
                    if trend_decision:
                        self._mark_trend_fallback_source(result)
            else:
                result.decision_type = normalize_decision_signal(raw_decision)
            result.confidence_level = localize_confidence_level(
                self._agent_dashboard_value(dash, nested_dashboard, "confidence_level")
                or result.confidence_level,
                report_language,
            )
            raw_summary = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "analysis_summary",
                scalar=True,
                expect_text=True,
            )
            if not self._is_agent_field_missing(raw_summary, scalar=True, expect_text=True):
                result.analysis_summary = str(raw_summary)
            else:
                result.analysis_summary = self._summary_fallback_from_result(result, report_language)
            top_level_phase_decision = dash.get("phase_decision") if isinstance(dash, dict) else None
            if isinstance(nested_dashboard, dict) and isinstance(top_level_phase_decision, dict):
                nested_dashboard = dict(nested_dashboard)
                nested_dashboard.setdefault("phase_decision", top_level_phase_decision)

            # The AI returns a top-level dict that contains a nested 'dashboard' sub-key
            # with core_conclusion / battle_plan / intelligence.  AnalysisResult's helper
            # methods (get_sniper_points, get_core_conclusion, etc.) expect that inner
            # structure, so we unwrap it here.
            result.dashboard = nested_dashboard or dash
            self._backfill_agent_dashboard_fields(result, trend_result, report_language)
        else:
            self._apply_trend_fallback(result, trend_result, report_language)
            if trend_result is not None:
                result.analysis_summary = (
                    result.analysis_summary
                    or self._summary_fallback_from_result(result, report_language)
                )
                self._backfill_agent_dashboard_fields(result, trend_result, report_language)
            if not result.error_message:
                result.error_message = "Agent failed to generate a valid decision dashboard"

        explicit_action = dash.get("action") if isinstance(dash, dict) else None
        if explicit_action is None and isinstance(getattr(result, "dashboard", None), dict):
            explicit_action = result.dashboard.get("action")
        return populate_decision_action_fields(result, explicit_action=explicit_action)

    @staticmethod
    def _refresh_decision_action_for_final_result(
        result: AnalysisResult,
        *,
        report_type: Any,
        previous_operation_advice: Any,
    ) -> AnalysisResult:
        # A guardrail may rewrite the advice after the Agent action was parsed;
        # discard that stale action before using the same resolver as the
        # downstream DecisionSignal builder.
        previous_advice = str(previous_operation_advice or "").strip()
        current_advice = str(getattr(result, "operation_advice", None) or "").strip()
        if previous_advice != current_advice:
            result.action = None
            result.action_label = None
        fields = resolve_decision_signal_action_fields(
            result,
            report_type=str(report_type or ""),
        )
        result.action = fields["action"]
        result.action_label = fields["action_label"]
        return result

    @staticmethod
    def _agent_dashboard_value(
        dash: Dict[str, Any],
        nested_dashboard: Any,
        key: str,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> Any:
        """Read a scalar from top-level agent payload, then nested dashboard fallback."""
        value = dash.get(key) if isinstance(dash, dict) else None
        if isinstance(nested_dashboard, dict) and StockAnalysisPipeline._is_agent_field_missing(
            value,
            scalar=scalar,
            allow_dict=allow_dict,
            expect_text=expect_text,
        ):
            nested_value = nested_dashboard.get(key)
            if not StockAnalysisPipeline._is_agent_field_missing(
                nested_value,
                scalar=scalar,
                allow_dict=allow_dict,
                expect_text=expect_text,
            ):
                value = nested_value
        return value

    @staticmethod
    def _extract_advice_text_from_dict(raw_advice: dict) -> str:
        for field in ("has_position", "no_position"):
            if isinstance(raw_advice.get(field), str):
                text = raw_advice[field].strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        for value in raw_advice.values():
            if isinstance(value, str):
                text = value.strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        return ""

    @staticmethod
    def _is_agent_placeholder_text(text: str) -> bool:
        if not text:
            return True
        return text.lower() in {
            "n/a", "na", "none", "null", "unknown", "tbd",
            # Giá trị giữ chỗ tiếng Việt / Vietnamese placeholders
            "không rõ", "chưa rõ", "đang cập nhật", "thiếu dữ liệu", "không có",
        }

    @staticmethod
    def _is_agent_field_missing(
        value: Any,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> bool:
        if scalar and isinstance(value, dict):
            if not allow_dict or not value:
                return True
            return not StockAnalysisPipeline._extract_advice_text_from_dict(value)
        if value is None:
            return True
        if expect_text and scalar:
            if not isinstance(value, str):
                return True
        if isinstance(value, str):
            text = value.strip()
            return StockAnalysisPipeline._is_agent_placeholder_text(text)
        if isinstance(value, dict):
            if scalar:
                return not allow_dict
            return not value
        if scalar and isinstance(value, (list, tuple, set)):
            return True
        return False

    @staticmethod
    def _trend_score_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[int]:
        if trend_result is None:
            return None
        try:
            score = int(getattr(trend_result, "signal_score", 0))
        except (TypeError, ValueError):
            return None
        return score if score > 0 else None

    @staticmethod
    def _trend_label_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "vi",
    ) -> str:
        if trend_result is None:
            return ""
        trend_status = getattr(trend_result, "trend_status", None)
        value = getattr(trend_status, "value", None) or str(trend_status or "").strip()
        # TrendStatus giờ là tiếng Anh chuẩn → luôn bản địa hoá theo ngôn ngữ báo cáo.
        # TrendStatus values are canonical English, so always localize.
        return localize_trend_prediction(value, report_language)

    @staticmethod
    def _trend_signal_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "vi",
    ) -> str:
        if trend_result is None:
            return ""
        buy_signal = getattr(trend_result, "buy_signal", None)
        value = getattr(buy_signal, "value", None) or str(buy_signal or "").strip()
        return localize_operation_advice(value, report_language)

    @staticmethod
    def _trend_decision_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[str]:
        if trend_result is None:
            return None
        signal_name = getattr(getattr(trend_result, "buy_signal", None), "name", "").lower()
        return {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }.get(signal_name)


    @staticmethod
    def _mark_trend_fallback_source(result: AnalysisResult) -> None:
        if "trend:fallback" in (result.data_sources or ""):
            return
        result.data_sources = (
            f"{result.data_sources},trend:fallback"
            if result.data_sources
            else "trend:fallback"
        )

    @staticmethod
    def _summary_fallback_from_result(result: AnalysisResult, report_language: str) -> str:
        trend = (result.trend_prediction or "").strip()
        advice = (result.operation_advice or "").strip()
        if trend and advice:
            if report_language == "en":
                return f"Trend view: {trend}; action advice: {advice}."
            return f"Nhận định xu hướng: {trend}; khuyến nghị hành động: {advice}."
        return ""

    def _backfill_agent_dashboard_fields(
        self,
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        if not isinstance(result.dashboard, dict):
            result.dashboard = {}
        dashboard = result.dashboard

        for key in (
            "sentiment_score",
            "trend_prediction",
            "operation_advice",
            "decision_type",
            "confidence_level",
            "analysis_summary",
        ):
            current = dashboard.get(key)
            if key == "sentiment_score":
                if self._is_agent_field_missing(current, scalar=True):
                    dashboard[key] = getattr(result, key)
            elif self._is_agent_field_missing(current, scalar=True, expect_text=True):
                dashboard[key] = getattr(result, key)

        core = dashboard.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
            dashboard["core_conclusion"] = core
        if self._is_agent_field_missing(core.get("one_sentence"), scalar=True):
            core["one_sentence"] = result.analysis_summary or self._summary_fallback_from_result(
                result,
                report_language,
            ) or "Analysis pending"

        intelligence = dashboard.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
            dashboard["intelligence"] = intelligence
        risk_alerts = intelligence.get("risk_alerts")
        if (
            "risk_alerts" not in intelligence
            or self._is_agent_field_missing(risk_alerts)
            or not isinstance(risk_alerts, list)
        ):
            risk_factors = getattr(trend_result, "risk_factors", None) or []
            intelligence["risk_alerts"] = list(risk_factors)

        if result.decision_type in ("buy", "hold"):
            battle = dashboard.get("battle_plan")
            if not isinstance(battle, dict):
                battle = {}
                dashboard["battle_plan"] = battle
            sniper_points = battle.get("sniper_points")
            if not isinstance(sniper_points, dict):
                sniper_points = {}
                battle["sniper_points"] = sniper_points
            if self._is_agent_field_missing(sniper_points.get("stop_loss"), scalar=True):
                sniper_points["stop_loss"] = self._stop_loss_fallback_from_trend(
                    trend_result,
                    report_language,
                )

    @staticmethod
    def _stop_loss_fallback_from_trend(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> Any:
        levels = getattr(trend_result, "support_levels", None) if trend_result else None
        if levels:
            return levels[0]
        return get_placeholder_text(report_language)

    @staticmethod
    def _apply_trend_fallback(
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        if trend_result is None:
            result.sentiment_score = 50
            result.operation_advice = localize_operation_advice("watch", report_language)
            return

        score = getattr(trend_result, "signal_score", None)
        try:
            numeric_score = int(score)
        except (TypeError, ValueError):
            numeric_score = 50
        result.sentiment_score = numeric_score if numeric_score > 0 else 50

        trend_label = StockAnalysisPipeline._trend_label_fallback(trend_result, report_language)
        if trend_label:
            result.trend_prediction = trend_label

        buy_signal = getattr(trend_result, "buy_signal", None)
        signal_label = StockAnalysisPipeline._trend_signal_fallback(
            trend_result,
            report_language,
        )
        if signal_label:
            result.operation_advice = signal_label
        else:
            result.operation_advice = localize_operation_advice("watch", report_language)

        from src.agent.protocols import normalize_decision_signal

        signal_name = getattr(buy_signal, "name", "").lower()
        signal_to_decision = {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }
        result.decision_type = signal_to_decision.get(signal_name, result.decision_type or "hold")
        result.decision_type = normalize_decision_signal(result.decision_type)
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"

    @staticmethod
    def _is_placeholder_stock_name(name: str, code: str) -> bool:
        """Return True when the stock name is missing or placeholder-like."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized == code:
            return True
        if normalized.startswith(PLACEHOLDER_NAME_PREFIXES):
            return True
        if "Unknown" in normalized:
            return True
        return False

    @staticmethod
    def _safe_int(value: Any, default: int = 50) -> int:
        """Chuyển giá trị sang số nguyên một cách an toàn."""
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            import re
            match = re.search(r'-?\d+', value)
            if match:
                return int(match.group())
        return default
    
    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        """
        Mô tả tỷ lệ khối lượng

        Tỷ lệ khối lượng = khối lượng hiện tại / khối lượng trung bình 5 phiên gần nhất
        """
        if volume_ratio < 0.5:
            return "Cạn kiệt mạnh"
        elif volume_ratio < 0.8:
            return "Thu hẹp rõ"
        elif volume_ratio < 1.2:
            return "Bình thường"
        elif volume_ratio < 2.0:
            return "Tăng nhẹ"
        elif volume_ratio < 3.0:
            return "Tăng rõ"
        else:
            return "Đột biến"

    @staticmethod
    def _compute_ma_status(close: float, ma5: float, ma10: float, ma20: float) -> str:
        """
        Compute MA alignment status from price and MA values.
        Logic mirrors storage._analyze_ma_status (Issue #234).
        """
        close = close or 0
        ma5 = ma5 or 0
        ma10 = ma10 or 0
        ma20 = ma20 or 0
        if close > ma5 > ma10 > ma20 > 0:
            return "MA xếp tăng 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "MA xếp giảm 📉"
        elif close > ma5 and ma5 > ma10:
            return "Ngắn hạn tích cực 🔼"
        elif close < ma5 and ma5 < ma10:
            return "Ngắn hạn suy yếu 🔽"
        else:
            return "Đi ngang tích lũy ↔️"

    def _augment_historical_with_realtime(
        self,
        df: pd.DataFrame,
        realtime_quote: Any,
        code: str,
        market: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Dùng giá realtime trong ngày để bù vào OHLCV lịch sử, phục vụ tính MA trong phiên.
        Issue #234: chỉ báo kỹ thuật dùng giá realtime chứ không lấy lại giá đóng cửa hôm qua.
        """
        if df is None or df.empty or 'close' not in df.columns:
            return df
        if realtime_quote is None:
            return df
        price = getattr(realtime_quote, 'price', None)
        if price is None or not (isinstance(price, (int, float)) and price > 0):
            return df

        # Ngày không giao dịch thì có thể bỏ qua bước bù realtime; gặp bất thường thì giữ
        # nguyên fail-open.
        enable_realtime_tech = getattr(
            self.config, 'enable_realtime_technical_indicators', True
        )
        if not enable_realtime_tech:
            return df
        if market is None:
            market = get_market_for_stock(code)
        market_today = get_market_now(market).date()
        if market and not is_market_open(market, market_today):
            return df

        last_val = df['date'].max()
        last_date = (
            last_val.date() if hasattr(last_val, 'date') else
            (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
        )
        yesterday_close = float(df.iloc[-1]['close']) if len(df) > 0 else price
        open_p = getattr(realtime_quote, 'open_price', None) or getattr(
            realtime_quote, 'pre_close', None
        ) or yesterday_close
        high_p = getattr(realtime_quote, 'high', None) or price
        low_p = getattr(realtime_quote, 'low', None) or price
        vol = getattr(realtime_quote, 'volume', None) or 0
        amt = getattr(realtime_quote, 'amount', None)
        pct = getattr(realtime_quote, 'change_pct', None)

        if last_date >= market_today:
            # Cập nhật dòng cuối bằng giá đóng cửa realtime; sao chép trước để không sửa df mà
            # phía gọi truyền vào.
            df = df.copy()
            idx = df.index[-1]
            df.loc[idx, 'close'] = price
            if open_p is not None:
                df.loc[idx, 'open'] = open_p
            if high_p is not None:
                df.loc[idx, 'high'] = high_p
            if low_p is not None:
                df.loc[idx, 'low'] = low_p
            if vol:
                df.loc[idx, 'volume'] = vol
            if amt is not None:
                df.loc[idx, 'amount'] = amt
            if pct is not None:
                df.loc[idx, 'pct_chg'] = pct
        else:
            # Nối thêm một dòng nến realtime ảo của ngày hôm nay.
            new_row = {
                'code': code,
                'date': market_today,
                'open': open_p,
                'high': high_p,
                'low': low_p,
                'close': price,
                'volume': vol,
                'amount': amt if amt is not None else 0,
                'pct_chg': pct if pct is not None else 0,
            }
            new_df = pd.DataFrame([new_row])
            df = pd.concat([df, new_df], ignore_index=True)
        return df

    def _build_context_snapshot(
        self,
        enhanced_context: Dict[str, Any],
        news_content: Optional[str],
        realtime_quote: Any,
        news_result_count: Optional[int] = None,
        analysis_context_pack_overview: Optional[Dict[str, Any]] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Dựng ảnh chụp ngữ cảnh phân tích
        """
        snapshot = {
            "enhanced_context": self._without_runtime_prompt_context(enhanced_context),
            "news_content": news_content,
            "realtime_quote_raw": self._safe_to_dict(realtime_quote),
        }
        if news_content is not None:
            snapshot["news_retrieval_content"] = news_content
        if news_result_count is not None:
            snapshot["news_result_count"] = news_result_count
        if analysis_context_pack_overview is not None:
            snapshot["analysis_context_pack_overview"] = analysis_context_pack_overview
        if market_phase_summary is not None:
            snapshot[MARKET_PHASE_SUMMARY_KEY] = market_phase_summary
        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            snapshot["diagnostics"] = diagnostic_snapshot
        if self.analysis_skills is not None:
            snapshot["skills"] = list(self.analysis_skills)
        return snapshot

    def _persist_skill_opinion_samples_after_history_save(
        self,
        *,
        runtime_facts: Any,
        analysis_history_id: int,
        stock_code: str,
        analysis_context_pack_overview: Optional[Dict[str, Any]],
    ) -> None:
        """Best-effort persistence for valid individual skill opinions."""
        opinions = getattr(runtime_facts, "skill_opinions", ()) if runtime_facts else ()
        if not opinions:
            return

        quality_level = None
        if isinstance(analysis_context_pack_overview, dict):
            quality = analysis_context_pack_overview.get("data_quality")
            if isinstance(quality, dict):
                quality_level = quality.get("level")

        try:
            from src.services.skill_opinion_sample_service import SkillOpinionSampleService

            SkillOpinionSampleService(db_manager=self.db).persist(
                analysis_history_id=analysis_history_id,
                stock_code=stock_code,
                opinions=opinions,
                data_quality_level=quality_level,
            )
        except Exception as exc:
            logger.warning(
                "Skill opinion sample persistence skipped after history save: "
                "stock_code=%s error_type=%s",
                stock_code,
                type(exc).__name__,
            )

    def _extract_decision_signal_after_history_save(
        self,
        *,
        result: AnalysisResult,
        query_id: str,
        source_report_id: int,
        report_type: str,
        context_snapshot: Dict[str, Any],
        portfolio_context: Optional[Dict[str, Any]] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> None:
        """Best-effort DecisionSignal extraction after analysis history is saved."""

        assert (
            isinstance(source_report_id, int)
            and not isinstance(source_report_id, bool)
            and source_report_id > 0
        )

        try:
            diagnostic_context = get_current_diagnostic_context()
            trace_id = (
                getattr(diagnostic_context, "trace_id", None)
                or getattr(self, "trace_id", None)
                or query_id
            )
            market_override = None
            signal_result = extract_and_persist_from_analysis_result(
                result,
                context_snapshot=context_snapshot,
                source_report_id=source_report_id,
                trace_id=str(trace_id),
                query_source=getattr(self, "query_source", None) or "system",
                report_type=report_type,
                portfolio_context=portfolio_context,
                profile_source="auto_default",
                market_override=market_override,
            )
            if isinstance(signal_result, dict):
                summary = summarize_decision_signal(signal_result.get("item"))
                if summary:
                    setattr(result, "decision_signal_summary", summary)
        except Exception as exc:
            logger.warning(
                "Decision signal extraction skipped after history save: query_id=%s stock_code=%s error=%s",
                query_id,
                getattr(result, "code", None),
                exc,
                exc_info=True,
            )

    @staticmethod
    def _build_notification_run_snapshot(
        *,
        channel: str,
        status: str,
        success: bool,
        attempts: int = 1,
        error_message: Optional[Any] = None,
    ) -> Dict[str, Any]:
        payload = {
            "channel": channel,
            "status": status,
            "success": success,
            "attempts": attempts,
            "created_at": datetime.now().isoformat(),
        }
        sanitized_error = sanitize_diagnostic_text(error_message)
        if sanitized_error:
            payload["error_message_sanitized"] = sanitized_error
        return payload

    def _refresh_saved_diagnostic_snapshot(
        self,
        *,
        result: Optional[AnalysisResult] = None,
        results: Optional[List[AnalysisResult]] = None,
        fallback_code: Optional[str] = None,
        notification_run: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Patch persisted history diagnostics with notification outcomes."""
        if not getattr(self, "save_context_snapshot", True):
            return

        db = getattr(self, "db", None)
        updater = getattr(db, "update_analysis_history_diagnostics", None)
        if not callable(updater):
            return

        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            query_id = (
                diagnostic_snapshot.get("query_id")
                or getattr(result, "query_id", None)
                or getattr(self, "query_id", None)
            )
            code = (
                getattr(result, "code", None)
                or fallback_code
                or diagnostic_snapshot.get("stock_code")
            )
            if not query_id:
                return
            try:
                updater(query_id=query_id, code=code, diagnostics=diagnostic_snapshot)
            except Exception as exc:
                logger.warning("Ghi lại snapshot chẩn đoán lượt chạy thất bại (fail-open): %s", exc)
            return

        if notification_run is None:
            return

        target_results = list(results or ([] if result is None else [result]))
        for item in target_results:
            query_id = getattr(item, "query_id", None) or getattr(self, "query_id", None)
            if not query_id:
                continue
            code = getattr(item, "code", None) or fallback_code
            try:
                updater(
                    query_id=query_id,
                    code=code,
                    notification_runs=[notification_run],
                )
            except Exception as exc:
                logger.warning("Ghi lại snapshot chẩn đoán thông báo thất bại (fail-open): %s", exc)

    def _load_persisted_intelligence_context(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        limit: int = 6,
    ) -> Optional[str]:
        """Load locally persisted intelligence as fail-open evidence context."""
        try:
            service = IntelligenceService(config=self.config)
            service.refresh_auto_sources()
            days = max(1, int(self.config.get_effective_news_window_days() or 1))
            collected: list[Dict[str, Any]] = []
            seen_urls: set[str] = set()
            symbol_filters = [
                {"scope_type": "symbol", "scope_value": scope_value, "market": market}
                for scope_value in _symbol_scope_lookup_values(code, market)
            ]
            for filters in symbol_filters + [{"scope_type": "market", "market": market}]:
                payload = service.list_items(published_days=days, page=1, page_size=limit, **filters)
                for item in payload.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    collected.append(item)
                    if len(collected) >= limit:
                        break
                if len(collected) >= limit:
                    break
            if not collected:
                return None
            # Khối này đi thẳng vào prompt của LLM, nên nhãn phải theo ngôn ngữ báo cáo
            # ('en' dùng tiếng Anh, còn lại tiếng Việt).
            # This block goes straight into the LLM prompt, so labels follow the report language.
            is_en = normalize_report_language(getattr(self.config, "report_language", None)) == "en"
            heading = (
                f"## Local news evidence pool ({stock_name}/{code})" if is_en
                else f"## Kho bằng chứng tin tức cục bộ ({stock_name}/{code})"
            )
            untitled = "Untitled news" if is_en else "Tin không có tiêu đề"
            summary_label = "Summary" if is_en else "Tóm tắt"
            source_label = "Source" if is_en else "Nguồn"
            lines = [heading]
            for idx, item in enumerate(collected[:limit], 1):
                title = str(item.get("title") or untitled).strip()
                summary = str(item.get("summary") or "").strip()
                source = str(item.get("source") or item.get("source_name") or "local-intel").strip()
                published = str(item.get("published_at") or "").strip()
                url = str(item.get("url") or "").strip()
                meta = " / ".join(part for part in (source, published) if part)
                if meta:
                    lines.append(f"{idx}. {title} ({meta})")
                else:
                    lines.append(f"{idx}. {title}")
                if summary:
                    lines.append(f"   {summary_label}: {summary[:220]}")
                if url and not url.startswith("no-url:intel:"):
                    lines.append(f"   {source_label}: {url}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("Đọc bằng chứng tin tức cục bộ thất bại (fail-open): %s", exc)
            return None

    def _build_legacy_analysis_artifacts(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        phase: Optional[Dict[str, Any]],
        context: Dict[str, Any],
        enhanced_context: Dict[str, Any],
        realtime_quote: Any,
        trend_result: Optional[TrendAnalysisResult],
        fundamental_context: Optional[Dict[str, Any]],
        news_context: Optional[str],
        news_result_count: Optional[int],
        query_id: str,
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> PipelineAnalysisArtifacts:
        return PipelineAnalysisArtifacts(
            code=code,
            stock_name=stock_name,
            market=market,
            phase=phase,
            base_context=context,
            enhanced_context=enhanced_context,
            realtime_quote=realtime_quote,
            trend_result=trend_result,
            fundamental_context=fundamental_context,
            news_context=news_context,
            news_result_count=news_result_count,
            metadata=_analysis_metadata(
                query_id=query_id,
                trigger_source=self.query_source,
            ),
            portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
        )

    def _build_agent_analysis_artifacts(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        phase: Optional[Dict[str, Any]],
        initial_context: Dict[str, Any],
        fundamental_context: Optional[Dict[str, Any]],
        query_id: str,
        base_context: Optional[Dict[str, Any]] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> PipelineAnalysisArtifacts:
        context_candidate = base_context
        if not isinstance(context_candidate, dict):
            context_candidate = initial_context.get("analysis_context")
        if isinstance(context_candidate, dict) and context_candidate:
            daily_context = dict(context_candidate)
            daily_context.setdefault("code", code)
            if stock_name:
                daily_context.setdefault("stock_name", stock_name)
        else:
            daily_context = {
                "code": code,
                "stock_name": stock_name,
                "data_missing": True,
                "today": {},
                "yesterday": {},
            }

        return PipelineAnalysisArtifacts(
            code=code,
            stock_name=stock_name,
            market=market,
            phase=phase,
            base_context=daily_context,
            enhanced_context={},
            realtime_quote=initial_context.get("realtime_quote"),
            trend_result=initial_context.get("trend_result"),
            fundamental_context=fundamental_context,
            news_context=initial_context.get("news_context"),
            news_result_count=None,
            metadata=_analysis_metadata(
                query_id=query_id,
                trigger_source=self.query_source,
            ),
            portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
        )

    def _build_analysis_context_pack_outputs(
        self,
        artifacts: PipelineAnalysisArtifacts,
        *,
        report_language: str,
        code: str,
        query_id: str,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        try:
            pack = AnalysisContextBuilder.build(artifacts)
            summary = format_analysis_context_pack_prompt_section(
                pack,
                report_language=report_language,
            )
            overview = render_analysis_context_pack_overview(
                pack,
                report_language=report_language,
            )
            return summary, overview
        except Exception as exc:
            logger.warning(
                "AnalysisContextPack output generation failed for %s query_id=%s: %s",
                code,
                query_id,
                exc,
            )
            return "", None

    @staticmethod
    def _without_runtime_prompt_context(context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a shallow copy without runtime-only prompt context.

        Market phase and AnalysisContextPack summaries are prompt inputs only.
        P4 stores only the separately rendered public overview at snapshot top level.
        """
        sanitized = dict(context)
        sanitized.pop("market_phase_context", None)
        sanitized.pop("portfolio_context", None)
        sanitized.pop("analysis_context_pack", None)
        sanitized.pop("analysis_context_pack_summary", None)
        sanitized.pop("daily_market_context_summary", None)
        enhanced_context = sanitized.get("enhanced_context")
        if isinstance(enhanced_context, dict):
            enhanced_context = dict(enhanced_context)
            enhanced_context.pop("daily_market_context_summary", None)
            sanitized["enhanced_context"] = enhanced_context
        return sanitized

    _without_market_phase_context = _without_runtime_prompt_context

    @staticmethod
    def _resolve_resume_target_date(
        code: str, current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> date:
        """
        Resolve the trading date used by checkpoint/resume checks.
        """
        market = get_market_for_stock(normalize_stock_code(code))
        return get_effective_trading_date(market, current_time=current_time)

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        """
        Chuyển sang dict một cách an toàn
        """
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            try:
                return value.to_dict()
            except Exception:
                return None
        if hasattr(value, "__dict__"):
            try:
                return dict(value.__dict__)
            except Exception:
                return None
        return None

    def _resolve_query_source(self, query_source: Optional[str] = None) -> str:
        """
        Phân giải nguồn gốc của request.

        Thứ tự ưu tiên (cao xuống thấp):
        1. query_source truyền vào tường minh: phía gọi chỉ định rõ thì ưu tiên dùng,
           tiện cho việc ghi đè kết quả suy luận hoặc tương thích với trường hợp sau này
           source_message đến từ nơi khác ngoài bot
        2. Có source_message thì suy ra là "bot": quy ước hiện tại là ngữ cảnh hội thoại bot
        3. Có query_id thì suy ra là "web": request do Web kích hoạt luôn kèm query_id
        4. Mặc định "system": tác vụ định kỳ hoặc CLI, không có các ngữ cảnh trên

        Args:
            query_source: nguồn do phía gọi chỉ định tường minh, ví dụ
                "bot" / "web" / "cli" / "system"

        Returns:
            Chuỗi định danh nguồn đã chuẩn hoá, ví dụ "bot" / "web" / "cli" / "system"
        """
        if query_source:
            return query_source
        if getattr(self, "source_message", None):
            return "bot"
        if getattr(self, "query_id", None):
            return "web"
        return "system"

    def _build_query_context(self, query_id: Optional[str] = None) -> Dict[str, str]:
        """
        Sinh thông tin liên kết truy vấn của người dùng
        """
        effective_query_id = query_id or self.query_id or ""

        context: Dict[str, str] = {
            "query_id": effective_query_id,
            "query_source": self.query_source or "",
        }

        if self.source_message:
            context.update({
                "requester_platform": self.source_message.platform or "",
                "requester_user_id": self.source_message.user_id or "",
                "requester_user_name": self.source_message.user_name or "",
                "requester_chat_id": self.source_message.chat_id or "",
                "requester_message_id": self.source_message.message_id or "",
                "requester_query": self.source_message.content or "",
            })

        return context
    
    def process_single_stock(
        self,
        code: str,
        skip_analysis: bool = False,
        single_stock_notify: bool = False,
        report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: Optional[str] = None,
        current_time: Optional[datetime] = None,
        analysis_target: Optional[AnalysisTarget] = None,
    ) -> Optional[AnalysisResult]:
        """
        Quy trình đầy đủ để xử lý một mã cổ phiếu

        Bao gồm:
        1. Lấy dữ liệu
        2. Lưu dữ liệu
        3. AI phân tích
        4. Đẩy thông báo từng mã (tuỳ chọn, #55)

        Phương thức này được thread pool gọi nên phải xử lý ngoại lệ cẩn thận

        Args:
            analysis_query_id: id liên kết chuỗi truy vấn
            code: mã cổ phiếu
            skip_analysis: có bỏ qua bước AI phân tích hay không
            single_stock_notify: có bật chế độ đẩy từng mã hay không (phân tích xong mã
                nào là đẩy ngay mã đó)
            report_type: enum loại báo cáo (đọc từ cấu hình, Issue #119)
            current_time: mốc thời gian tham chiếu đã đóng băng cho lượt chạy này, dùng
                để thống nhất việc xác định phiên mục tiêu khi tiếp tục dở dang
            analysis_target: mục tiêu phân tích có cấu trúc (mục tiêu chỉ số dùng để suy
                ra market=cn và ma trận năng lực)

        Returns:
            AnalysisResult hoặc None
        """
        logger.info(f"========== Bắt đầu xử lý {code} ==========")

        from src.services.history_loader import set_frozen_target_date, reset_frozen_target_date
        frozen_td = self._resolve_resume_target_date(
            code, current_time=current_time, analysis_target=analysis_target
        )
        token = set_frozen_target_date(frozen_td)
        effective_query_id = analysis_query_id or getattr(self, "query_id", None) or uuid.uuid4().hex
        effective_trace_id = getattr(self, "trace_id", None) or effective_query_id
        diag_token = None
        if get_current_diagnostic_context() is None:
            diag_token = activate_run_diagnostic_context(
                trace_id=effective_trace_id,
                query_id=effective_query_id,
                stock_code=code,
                trigger_source=getattr(self, "query_source", None),
            )
        try:
            self._emit_progress(12, self._progress_text("prepare", code=code))
            # Bước 1: lấy và lưu dữ liệu
            success, error = self.fetch_and_save_stock_data(
                code, current_time=current_time, analysis_target=analysis_target
            )
            
            if not success:
                logger.warning(f"[{code}] Lấy dữ liệu thất bại: {error}")
                # Kể cả khi lấy dữ liệu thất bại vẫn thử phân tích bằng dữ liệu sẵn có
            else:
                # Ghi nhận để chế độ dry-run thống kê đúng: có nguồn (như Yahoo cho
                # cổ phiếu Việt Nam) cố tình không ghi vào DB, nên không thể chỉ dựa
                # vào has_today_data() để kết luận là lấy dữ liệu thất bại.
                fetched = getattr(self, "_fetched_codes_this_run", None)
                if fetched is not None:
                    fetched.add(code)
                self._emit_progress(16, self._progress_text("market_ready", code=code))
            
            # Bước 2: AI phân tích
            if skip_analysis:
                logger.info(f"[{code}] Bỏ qua phân tích AI (chế độ dry-run)")
                return None
            
            analyze_kwargs = {"query_id": effective_query_id}
            if current_time is not None:
                analyze_kwargs["current_time"] = current_time
            if analysis_target is not None:
                analyze_kwargs["analysis_target"] = analysis_target
            result = self.analyze_stock(code, report_type, **analyze_kwargs)
            
            if result and result.success:
                logger.info(
                    f"[{code}] Phân tích hoàn tất: {result.operation_advice}, "
                    f"điểm {result.sentiment_score}"
                )
                
                # Chế độ đẩy từng mã (#55): phân tích xong mã nào là đẩy ngay mã đó
                if single_stock_notify:
                    self._send_single_stock_notification(
                        result,
                        report_type=report_type,
                        fallback_code=code,
                    )
            elif result:
                logger.warning(
                    f"[{code}] Phân tích không thành công: {result.error_message or 'lỗi không xác định'}"
                )
            
            return result
            
        except Exception as e:
            # Bắt mọi ngoại lệ để một mã lỗi không ảnh hưởng toàn bộ
            logger.exception(f"[{code}] Xảy ra lỗi không xác định khi xử lý: {e}")
            return None
        finally:
            reset_run_diagnostic_context(diag_token)
            reset_frozen_target_date(token)
    
    def run(
        self,
        stock_codes: Optional[List[str]] = None,
        dry_run: bool = False,
        send_notification: bool = True,
        merge_notification: bool = False,
        current_time: Optional[datetime] = None,
        analysis_targets: Optional[List[AnalysisTarget]] = None,
    ) -> List[AnalysisResult]:
        """
        Chạy toàn bộ quy trình phân tích

        Quy trình:
        1. Lấy danh sách cổ phiếu cần phân tích
        2. Xử lý đồng thời bằng thread pool
        3. Thu thập kết quả phân tích
        4. Gửi thông báo

        Args:
            stock_codes: danh sách mã cổ phiếu (tuỳ chọn, mặc định dùng danh mục theo dõi
                trong cấu hình)
            dry_run: có chỉ lấy dữ liệu mà không phân tích hay không
            send_notification: có gửi thông báo đẩy hay không
            merge_notification: có gộp thông báo hay không (bỏ qua lần đẩy này, tầng main
                sẽ gộp phần từng mã + toàn thị trường rồi gửi một lần, Issue #190)
            current_time: mốc thời gian tham chiếu đã đóng băng cho lượt chạy này; để
                trống thì sinh ngay trong run
            analysis_targets: danh sách mục tiêu phân tích có cấu trúc, khớp 1-1 với
                stock_codes (tuỳ chọn; mục tiêu chỉ số dùng để suy ra market=cn và ma
                trận năng lực)

        Returns:
            Danh sách kết quả phân tích
        """
        start_time = time.time()
        
        # Dùng danh sách cổ phiếu trong cấu hình
        if stock_codes is None:
            self.config.refresh_stock_list()
            stock_codes = self.config.stock_list
        
        if not stock_codes:
            logger.error("Chưa cấu hình danh sách theo dõi, hãy đặt STOCK_LIST trong file .env")
            return []
        
        # Lọc bỏ mã thuộc thị trường chưa bật. Khác với bước lọc unsupported bên dưới, bước
        # này có hiệu lực dù có hay không có target cấu trúc: ngữ nghĩa của SUPPORTED_MARKETS
        # là "thị trường đã tắt thì không gửi đi một request nào". Tầng manager cũng có chặn,
        # nhưng lúc đó đã vào thread pool rồi và sẽ bị ghi nhận là một lần thất bại - vừa ồn
        # vừa phí lịch chạy.
        stock_codes, analysis_targets = self._filter_codes_by_enabled_markets(
            stock_codes, analysis_targets
        )
        if not stock_codes:
            logger.warning("Danh sách theo dõi không có mã nào thuộc thị trường đã bật, lượt này không có tác vụ")
            return []

        # Lọc target unsupported và khử trùng lặp theo canonical identity, cả hai đều diễn ra
        # trước khi gọi prefetch/provider; các lối vào cũ không có target cấu trúc vẫn giữ
        # nguyên hành vi.
        if analysis_targets is not None:
            if len(analysis_targets) != len(stock_codes):
                raise ValueError("analysis_targets must align with stock_codes")
            supported_codes = []
            supported_targets = []
            seen_identities = set()
            for code, target in zip(stock_codes, analysis_targets):
                if target is not None and target.asset_type == ParseStatus.UNSUPPORTED:
                    logger.warning(
                        "Bỏ qua mục tiêu unsupported %r (%s), không gửi request mạng",
                        code,
                        target.unsupported_reason or "unsupported",
                    )
                    continue
                identity = code
                if identity in seen_identities:
                    logger.info("Bỏ qua mục tiêu phân tích trùng lặp %s (canonical_id=%s)", code, identity)
                    continue
                seen_identities.add(identity)
                supported_codes.append(code)
                supported_targets.append(target)
            stock_codes = supported_codes
            analysis_targets = supported_targets
            if not stock_codes:
                logger.warning("Mọi mục tiêu trong lô đều unsupported, không có tác vụ")
                return []

        logger.info(f"===== Bắt đầu phân tích {len(stock_codes)} mã cổ phiếu =====")
        logger.info(f"Danh sách mã: {', '.join(stock_codes)}")
        logger.info(f"Số luồng song song: {self.max_workers}, chế độ: {'chỉ lấy dữ liệu' if dry_run else 'phân tích đầy đủ'}")

        # Đóng băng một mốc thời gian tham chiếu chung cho lượt chạy này, tránh việc cùng một
        # lô cổ phiếu lại dùng các phiên mục tiêu khác nhau khi vắt qua ranh giới đóng cửa
        # của nhiều thị trường.
        resume_reference_time = current_time or datetime.now(timezone.utc)
        
        # === Prefetch giá realtime hàng loạt (tối ưu: tránh mỗi mã lại kích hoạt một lần
        # kéo toàn bộ) ===
        # Chỉ prefetch khi số mã >= 5; ít mã thì truy vấn từng mã hiệu quả hơn
        if len(stock_codes) >= 5:
            daily_prefetch_count = self.fetcher_manager.prefetch_daily_klines(stock_codes, days=30)
            if daily_prefetch_count > 0:
                logger.info(
                    "[prefetch] component=daily_kline_prefetch action=complete "
                    "provider=TickFlowFetcher cached=%d stock_count=%d",
                    daily_prefetch_count,
                    len(stock_codes),
                )

            prefetch_count = self.fetcher_manager.prefetch_realtime_quotes(stock_codes)
            if prefetch_count > 0:
                logger.info(f"Đã bật kiến trúc prefetch hàng loạt: lấy dữ liệu toàn thị trường một lần, {len(stock_codes)} mã dùng chung cache")

        # Issue #455: prefetch tên cổ phiếu, tránh hiển thị "cổ phiếu xxxxx" khi phân tích
        # đồng thời
        # dry_run chỉ kéo dữ liệu, không cần prefetch tên, để khỏi tốn thêm lưu lượng mạng
        if not dry_run:
            self.fetcher_manager.prefetch_stock_names(stock_codes, use_bulk=False)

        # Chế độ đẩy từng mã (#55): đọc từ cấu hình
        single_stock_notify = getattr(self.config, 'single_stock_notify', False)
        # Issue #119: đọc loại báo cáo từ cấu hình
        report_type_str = getattr(self.config, 'report_type', 'simple').lower()
        if report_type_str == 'brief':
            report_type = ReportType.BRIEF
        elif report_type_str == 'full':
            report_type = ReportType.FULL
        else:
            report_type = ReportType.SIMPLE
        # Issue #128: đọc khoảng nghỉ giữa các lần phân tích từ cấu hình
        analysis_delay = getattr(self.config, 'analysis_delay', 0)

        if single_stock_notify:
            logger.info(
                "Đã bật chế độ gửi từng mã: phân tích vẫn chạy song song, thông báo được gửi tuần tự ở phía thu kết quả (loại báo cáo: %s)",
                report_type_str,
            )
        
        results: List[AnalysisResult] = []
        # set.add của CPython là thao tác nguyên tử nên dùng trực tiếp trong thread pool.
        self._fetched_codes_this_run: Set[str] = set()
        
        # Xử lý đồng thời bằng thread pool
        # Lưu ý: max_workers để thấp (mặc định 3) nhằm tránh kích hoạt cơ chế chống bot
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Gửi tác vụ
            future_to_code = {}
            for idx, code in enumerate(stock_codes):
                target = (
                    analysis_targets[idx]
                    if analysis_targets is not None and idx < len(analysis_targets)
                    else None
                )
                submit_kwargs = {
                    "skip_analysis": dry_run,
                    "single_stock_notify": False,
                    "report_type": report_type,
                    "analysis_query_id": uuid.uuid4().hex,
                    "current_time": resume_reference_time,
                }
                if target is not None:
                    submit_kwargs["analysis_target"] = target
                future = executor.submit(
                    self.process_single_stock,
                    code,
                    **submit_kwargs,
                )
                future_to_code[future] = code
            
            # Thu thập kết quả
            for idx, future in enumerate(as_completed(future_to_code)):
                code = future_to_code[future]
                try:
                    result = future.result()
                    if result and result.success:
                        results.append(result)
                        if single_stock_notify and send_notification and not dry_run:
                            self._send_single_stock_notification(
                                result,
                                report_type=report_type,
                                fallback_code=code,
                            )
                    elif result and not result.success:
                        logger.warning(
                            f"[{code}] Kết quả phân tích bị đánh dấu thất bại, không tính vào tổng hợp: "
                            f"{result.error_message or 'nguyên nhân không xác định'}"
                        )

                    # Issue #128: khoảng nghỉ - chèn độ trễ giữa phân tích từng mã và phân tích toàn thị trường
                    if idx < len(stock_codes) - 1 and analysis_delay > 0:
                        # Lưu ý: lệnh sleep này nằm trong "vòng lặp thu thập future ở luồng chính",
                        # nên không ngăn được các tác vụ trong thread pool cùng lúc phát request mạng.
                        # Do đó tác dụng giảm đỉnh request đồng thời là hạn chế; đỉnh thật sự chủ yếu do
                        # max_workers quyết định.
                        # Hành vi này được giữ nguyên (yêu cầu không đổi logic).
                        logger.debug(f"Chờ {analysis_delay} giây rồi tiếp tục mã tiếp theo...")
                        time.sleep(analysis_delay)

                except Exception as e:
                    logger.error(f"[{code}] Tác vụ chạy thất bại: {e}")
        
        # Thống kê
        elapsed_time = time.time() - start_time
        
        # Ở chế độ dry-run, lấy được dữ liệu là coi như thành công
        if dry_run:
            # Kiểm tra những mã nào đã có sẵn dữ liệu của phiên gần nhất có thể tái dùng
            success_count = 0
            for idx, code in enumerate(stock_codes):
                target = (
                    analysis_targets[idx]
                    if analysis_targets is not None and idx < len(analysis_targets)
                    else None
                )
                if code in self._fetched_codes_this_run or self.db.has_today_data(
                    code,
                    self._resolve_resume_target_date(
                        code,
                        current_time=resume_reference_time,
                        analysis_target=target,
                    ),
                ):
                    success_count += 1
            fail_count = len(stock_codes) - success_count
        else:
            success_count = len(results)
            fail_count = len(stock_codes) - success_count
        
        logger.info("===== Phân tích hoàn tất =====")
        logger.info(f"Thành công: {success_count}, thất bại: {fail_count}, thời gian: {elapsed_time:.2f} giây")
        
        # Lưu báo cáo ra file cục bộ (lưu bất kể có đẩy thông báo hay không)
        if results and not dry_run:
            self._save_local_report(results, report_type)

        # Gửi thông báo (ở chế độ đẩy từng mã thì bỏ qua đẩy bản tổng hợp để tránh trùng)
        if results and send_notification and not dry_run:
            if single_stock_notify:
                # Chế độ đẩy từng mã: chỉ lưu báo cáo tổng hợp, không đẩy lại lần nữa
                logger.info("Chế độ gửi từng mã: bỏ qua gửi tổng hợp, chỉ lưu báo cáo cục bộ")
                self._send_notifications(results, report_type, skip_push=True)
            elif merge_notification:
                # Chế độ gộp (Issue #190): chỉ lưu, không đẩy; tầng main sẽ gộp phần từng mã + toàn
                # thị trường rồi gửi một lần
                logger.info("Chế độ gửi gộp: bỏ qua lần gửi này, sẽ gửi chung sau khi xong từng mã + tổng kết thị trường")
                self._send_notifications(results, report_type, skip_push=True)
            else:
                self._send_notifications(results, report_type)
        
        return results

    def _send_single_stock_notification(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: Optional[str] = None,
    ) -> None:
        """Gửi thông báo cho một mã, dùng chung cho lối vào từng mã trực tiếp và lối đẩy
        tuần tự theo lô."""
        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        notify_lock = getattr(self, "_single_stock_notify_lock", None)
        if notify_lock is None:
            with _SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD:
                notify_lock = getattr(self, "_single_stock_notify_lock", None)
                if notify_lock is None:
                    notify_lock = threading.Lock()
                    setattr(self, "_single_stock_notify_lock", notify_lock)

        with notify_lock:
            try:
                if report_type == ReportType.FULL:
                    report_content = self.notifier.generate_dashboard_report([result])
                    logger.info(f"[{stock_code}] Dùng định dạng báo cáo đầy đủ")
                elif report_type == ReportType.BRIEF:
                    report_content = self.notifier.generate_brief_report([result])
                    logger.info(f"[{stock_code}] Dùng định dạng báo cáo tóm tắt")
                else:
                    report_content = self.notifier.generate_single_stock_report(result)
                    logger.info(f"[{stock_code}] Dùng định dạng báo cáo rút gọn")

                save_report = getattr(self.notifier, "save_report_to_file", None)
                if callable(save_report):
                    try:
                        date_str = datetime.now().strftime('%Y%m%d')
                        filename = f"report_{date_str}_{stock_code}.md"
                        filepath = save_report(report_content, filename=filename)
                        logger.info(f"[{stock_code}] Đã lưu báo cáo từng mã cục bộ: {filepath}")
                    except Exception as exc:
                        logger.warning(f"[{stock_code}] Lưu báo cáo từng mã thất bại: {exc}")

                if not self.notifier.is_available():
                    notification_run = self._build_notification_run_snapshot(
                        channel="report",
                        status="not_configured",
                        success=False,
                        attempts=0,
                    )
                    record_notification_run(
                        channel="report",
                        status="not_configured",
                        success=False,
                        attempts=0,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        result=result,
                        fallback_code=fallback_code,
                        notification_run=notification_run,
                    )
                    return

                send_kwargs: Dict[str, Any] = {
                    "email_stock_codes": [stock_code],
                    "route_type": "report",
                    "severity": "info",
                    "dedup_key": f"report:single:{stock_code}:{report_type.value}",
                    "cooldown_key": f"report:single:{stock_code}:{report_type.value}",
                }
                if _supports_explicit_keyword(self.notifier.send, "structured_payload"):
                    send_kwargs["structured_payload"] = _share_image_payload(result)
                sent = self.notifier.send(report_content, **send_kwargs)
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="success" if sent else "failed",
                    success=sent,
                )
                record_notification_run(
                    channel="report",
                    status="success" if sent else "failed",
                    success=sent,
                )
                self._refresh_saved_diagnostic_snapshot(
                    result=result,
                    fallback_code=fallback_code,
                    notification_run=notification_run,
                )
                if sent:
                    logger.info(f"[{stock_code}] Gửi thông báo từng mã thành công")
                else:
                    logger.warning(f"[{stock_code}] Gửi thông báo từng mã thất bại")
            except Exception as e:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="failed",
                    success=False,
                    error_message=e,
                )
                record_notification_run(
                    channel="report",
                    status="failed",
                    success=False,
                    error_message=e,
                )
                self._refresh_saved_diagnostic_snapshot(
                    result=result,
                    fallback_code=fallback_code,
                    notification_run=notification_run,
                )
                logger.error(f"[{stock_code}] Gửi thông báo từng mã gặp lỗi: {e}")

    def _save_local_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
    ) -> Optional[str]:
        """Lưu báo cáo phân tích ra file cục bộ (tách rời khỏi việc đẩy thông báo)"""
        self._last_local_report_path = None
        self._last_local_report_error = None
        report: Optional[str] = None
        try:
            report = self._generate_aggregate_report(results, report_type)
        except Exception as e:
            self._last_local_report_error = str(e)
            logger.error("Tạo nội dung báo cáo cục bộ thất bại: %s", e)
            return None

        try:
            filepath = self.notifier.save_report_to_file(report)
            if filepath:
                filepath = str(filepath)
                self._last_local_report_path = filepath
                logger.info(f"Đã lưu báo cáo ngày bảng quyết định: {filepath}")
                return filepath
            self._last_local_report_error = "notifier returned empty report path"
            logger.error("Lưu báo cáo cục bộ thất bại: dịch vụ thông báo không trả về đường dẫn báo cáo")
        except Exception as e:
            self._last_local_report_error = str(e)
            logger.error(f"Lưu báo cáo cục bộ thất bại: {e}")

        logger.warning("Thử quay về đường dẫn hệ thống file cục bộ để lưu báo cáo tổng hợp")
        fallback_path = self._fallback_save_report_to_file(report)
        if fallback_path:
            self._last_local_report_path = fallback_path
            self._last_local_report_error = None
            logger.warning("Lưu báo cáo cục bộ theo đường dự phòng thành công: %s", fallback_path)
            return fallback_path
        if self._last_local_report_error is None:
            self._last_local_report_error = "fallback local report save failed"
            return None
        return None

    @staticmethod
    def _default_report_filename() -> str:
        """Build default local report filename (aligned with notifier defaults)."""
        date_str = datetime.now().strftime('%Y%m%d')
        return f"report_{date_str}.md"

    @staticmethod
    def _report_output_dir() -> Path:
        """Default report output directory (kept same as NotificationService behavior)."""
        return Path(__file__).resolve().parents[2] / 'reports'

    @classmethod
    def _fallback_save_report_to_file(
        cls,
        content: str,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """Persist report content via local filesystem as resilient fallback."""
        try:
            reports_dir = cls._report_output_dir()
            reports_dir.mkdir(parents=True, exist_ok=True)
            filename = filename or cls._default_report_filename()
            filepath = reports_dir / filename
            filepath.write_text(content, encoding='utf-8')
            logger.info("Đã ghi báo cáo ngày bảng quyết định theo đường dự phòng: %s", filepath)
            return str(filepath)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.error("Ghi báo cáo theo đường dự phòng thất bại: %s", exc)
            return None

    def _send_notifications(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
        skip_push: bool = False,
    ) -> None:
        """
        Gửi thông báo kết quả phân tích

        Sinh báo cáo theo định dạng bảng điều khiển quyết định

        Args:
            results: danh sách kết quả phân tích
            skip_push: có bỏ qua bước đẩy hay không (chỉ lưu cục bộ, dùng cho chế độ đẩy
                từng mã)
        """
        noise_decision = None
        noise_finalized = False
        try:
            logger.info("Đang tạo báo cáo ngày bảng quyết định...")
            report = self._generate_aggregate_report(results, report_type)
            
            # Bỏ qua bước đẩy (chế độ đẩy từng mã / chế độ gộp: báo cáo đã được _save_local_report lưu)
            if skip_push:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
                record_notification_run(
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
                self._refresh_saved_diagnostic_snapshot(
                    results=results,
                    notification_run=notification_run,
                )
                return
            
            # Đẩy thông báo
            if self.notifier.is_available():
                channels = self.notifier.get_available_channels()
                channels = self.notifier.get_channels_for_route("report", channels=channels)

                def _send_channel_safely(
                    channel_label: str,
                    send_func: Callable[[], bool],
                ) -> tuple[bool, Optional[Exception]]:
                    try:
                        return bool(send_func()), None
                    except Exception as e:
                        logger.exception(
                            "Kênh thông báo %s gửi lỗi, tiếp tục thử các kênh khác: %s",
                            channel_label,
                            e,
                        )
                        return False, e

                def _record_channel_result(
                    channel_label: str,
                    success: bool,
                    error_message: Optional[Exception] = None,
                    target_results: Optional[List[AnalysisResult]] = None,
                ) -> None:
                    notification_run = self._build_notification_run_snapshot(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                        error_message=error_message,
                    )
                    record_notification_run(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                        error_message=error_message,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        results=results if target_results is None else target_results,
                        notification_run=notification_run,
                    )

                send_context = self.notifier.send_to_context(report)
                if send_context:
                    _record_channel_result("__context__", True)

                should_broadcast_static = True
                should_broadcast_static_func = getattr(
                    self.notifier,
                    "should_broadcast_static_channels",
                    None,
                )
                if callable(should_broadcast_static_func):
                    should_broadcast_static = bool(should_broadcast_static_func())
                if not should_broadcast_static:
                    if not send_context:
                        _record_channel_result("__context__", False)
                    if send_context:
                        logger.info("Gửi bảng quyết định thành công")
                    else:
                        logger.warning("Gửi bảng quyết định thất bại")
                    logger.info("Chế độ trả lời theo ngữ cảnh tin nhắn tương tác: đã bỏ qua các kênh thông báo tĩnh")
                    return

                if channels and hasattr(self.notifier, "evaluate_noise_control"):
                    report_type_key = report_type.value if isinstance(report_type, ReportType) else str(report_type)
                    codes_key = ",".join(
                        sorted(str(getattr(result, "code", "") or "") for result in results)
                    )
                    noise_key = f"report:aggregate:{report_type_key}:{codes_key}"
                    noise_decision = self.notifier.evaluate_noise_control(
                        report,
                        route_type="report",
                        severity="info",
                        dedup_key=noise_key,
                        cooldown_key=noise_key,
                    )
                    if not noise_decision.should_send:
                        notification_run = self._build_notification_run_snapshot(
                            channel="report",
                            status="skipped",
                            success=False,
                            attempts=0,
                        )
                        record_notification_run(
                            channel="report",
                            status="skipped",
                            success=False,
                            attempts=0,
                        )
                        self._refresh_saved_diagnostic_snapshot(
                            results=results,
                            notification_run=notification_run,
                        )
                        logger.info(noise_decision.message)
                        return

                # Issue #455: chuyển Markdown thành ảnh (khớp logic của notification.send)
                from src.md2img import markdown_to_image

                channels_needing_image = {
                    ch for ch in channels
                    if ch.value in self.notifier._markdown_to_image_channels
                    and ch not in {NotificationChannel.NTFY, NotificationChannel.GOTIFY}
                }
                single_share_payload = (
                    _share_image_payload(results[0]) if len(results) == 1 else None
                )

                def _get_md2img_hint() -> str:
                    try:
                        engine = getattr(get_config(), "md2img_engine", "wkhtmltoimage")
                    except Exception:
                        engine = "wkhtmltoimage"
                    return (
                        "npm i -g markdown-to-file" if engine == "markdown-to-file"
                        else "wkhtmltopdf (apt install wkhtmltopdf / brew install wkhtmltopdf)"
                    )

                image_bytes = None
                if channels_needing_image:
                    image_kwargs: Dict[str, Any] = {
                        "max_chars": self.notifier._markdown_to_image_max_chars,
                    }
                    if single_share_payload is not None:
                        image_kwargs["structured_payload"] = single_share_payload
                    image_bytes = markdown_to_image(report, **image_kwargs)
                    if image_bytes:
                        logger.info(
                            "Markdown đã được chuyển thành ảnh, sẽ gửi ảnh tới %s",
                            [ch.value for ch in channels_needing_image],
                        )
                    else:
                        logger.warning(
                            "Chuyển Markdown thành ảnh thất bại, sẽ quay về gửi văn bản. Hãy kiểm tra cấu hình MARKDOWN_TO_IMAGE_CHANNELS và cài %s",
                            _get_md2img_hint(),
                        )

                # Gửi báo cáo đầy đủ tới từng kênh. / Send the full report to each channel.
                channels_success = False
                stock_email_groups = getattr(self.config, 'stock_email_groups', []) or []
                for channel in channels:
                    if channel == NotificationChannel.TELEGRAM:
                        def _send_telegram_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image:
                                return self.notifier._send_telegram_photo(image_bytes)
                            return self.notifier.send_to_telegram(report)

                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            _send_telegram_report,
                        )
                        channels_success = channel_success or channels_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.EMAIL:
                        if stock_email_groups:
                            code_to_emails: Dict[str, Optional[List[str]]] = {}
                            for r in results:
                                if r.code not in code_to_emails:
                                    canonical = normalize_stock_code(r.code)
                                    emails = []
                                    for stocks, emails_list in stock_email_groups:
                                        if canonical in stocks:
                                            emails.extend(emails_list)
                                    code_to_emails[r.code] = list(dict.fromkeys(emails)) if emails else None
                            emails_to_results: Dict[Optional[Tuple], List] = defaultdict(list)
                            for r in results:
                                recs = code_to_emails.get(r.code)
                                key = tuple(recs) if recs else None
                                emails_to_results[key].append(r)
                            for key, group_results in emails_to_results.items():
                                receivers = list(key) if key is not None else None

                                def _send_email_group(
                                    group_results=group_results,
                                    receivers=receivers,
                                ) -> bool:
                                    grp_report = self._generate_aggregate_report(group_results, report_type)
                                    grp_image_bytes = None
                                    if channel.value in self.notifier._markdown_to_image_channels:
                                        group_payload = (
                                            _share_image_payload(group_results[0])
                                            if len(group_results) == 1
                                            else None
                                        )
                                        group_image_kwargs: Dict[str, Any] = {
                                            "max_chars": self.notifier._markdown_to_image_max_chars,
                                        }
                                        if group_payload is not None:
                                            group_image_kwargs["structured_payload"] = group_payload
                                        grp_image_bytes = markdown_to_image(
                                            grp_report,
                                            **group_image_kwargs,
                                        )
                                    use_image = self.notifier._should_use_image_for_channel(
                                        channel, grp_image_bytes
                                    )
                                    if use_image:
                                        return self.notifier._send_email_with_inline_image(
                                            grp_image_bytes, receivers=receivers
                                        )
                                    return self.notifier.send_to_email(
                                        strip_hidden_markdown_metadata(grp_report).strip(),
                                        receivers=receivers,
                                    )

                                email_label = (
                                    f"{channel.value}:{','.join(receivers)}"
                                    if receivers else f"{channel.value}:default"
                                )
                                channel_success, channel_error = _send_channel_safely(
                                    email_label,
                                    _send_email_group,
                                )
                                channels_success = channel_success or channels_success
                                _record_channel_result(
                                    email_label,
                                    channel_success,
                                    channel_error,
                                    target_results=group_results,
                                )
                        else:
                            def _send_email_report() -> bool:
                                use_image = self.notifier._should_use_image_for_channel(
                                    channel, image_bytes
                                )
                                if use_image:
                                    return self.notifier._send_email_with_inline_image(image_bytes)
                                return self.notifier.send_to_email(
                                    strip_hidden_markdown_metadata(report).strip()
                                )

                            channel_success, channel_error = _send_channel_safely(
                                channel.value,
                                _send_email_report,
                            )
                            channels_success = channel_success or channels_success
                            _record_channel_result(
                                channel.value,
                                channel_success,
                                channel_error,
                            )
                    elif channel == NotificationChannel.CUSTOM:
                        def _send_custom_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image:
                                return self.notifier._send_custom_webhook_image(
                                    image_bytes, fallback_content=report
                                )
                            return self.notifier.send_to_custom(report)

                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            _send_custom_report,
                        )
                        channels_success = channel_success or channels_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.DISCORD:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_discord(report),
                        )
                        channels_success = channel_success or channels_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.PUSHOVER:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_pushover(report),
                        )
                        channels_success = channel_success or channels_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.NTFY:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_ntfy(report),
                        )
                        channels_success = channel_success or channels_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.GOTIFY:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_gotify(report),
                        )
                        channels_success = channel_success or channels_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.SLACK:
                        def _send_slack_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image and self.notifier._slack_bot_token and self.notifier._slack_channel_id:
                                return self.notifier._send_slack_image(
                                    image_bytes, fallback_content=report
                                )
                            return self.notifier.send_to_slack(report)

                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            _send_slack_report,
                        )
                        channels_success = channel_success or channels_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    else:
                        logger.warning(f"Kênh thông báo không xác định: {channel}")

                has_targeted_channels = bool(channels)
                success = channels_success or send_context
                if (
                    channels_success
                    and noise_decision is not None
                    and hasattr(self.notifier, "record_noise_control")
                ):
                    self.notifier.record_noise_control(noise_decision)
                    noise_finalized = True
                elif (
                    noise_decision is not None
                    and hasattr(self.notifier, "release_noise_control")
                ):
                    self.notifier.release_noise_control(noise_decision)
                    noise_finalized = True
                if success:
                    logger.info("Gửi bảng quyết định thành công")
                else:
                    logger.warning("Gửi bảng quyết định thất bại")
                if not has_targeted_channels and not send_context:
                    channel_label = ",".join(channel.value for channel in channels) or "report"
                    notification_run = self._build_notification_run_snapshot(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                    )
                    record_notification_run(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        results=results,
                        notification_run=notification_run,
                    )
            else:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
                record_notification_run(
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
                self._refresh_saved_diagnostic_snapshot(
                    results=results,
                    notification_run=notification_run,
                )
                logger.info("Chưa cấu hình kênh thông báo, bỏ qua gửi")
                
        except Exception as e:
            notification_run = self._build_notification_run_snapshot(
                channel="report",
                status="failed",
                success=False,
                error_message=e,
            )
            record_notification_run(
                channel="report",
                status="failed",
                success=False,
                error_message=e,
            )
            self._refresh_saved_diagnostic_snapshot(
                results=results,
                notification_run=notification_run,
            )
            if (
                noise_decision is not None
                and not noise_finalized
                and hasattr(self.notifier, "release_noise_control")
            ):
                self.notifier.release_noise_control(noise_decision)
            import traceback
            logger.error(f"Gửi thông báo thất bại: {e}\n{traceback.format_exc()}")

    def _generate_aggregate_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType,
    ) -> str:
        """Generate aggregate report with backward-compatible notifier fallback."""
        generator = getattr(self.notifier, "generate_aggregate_report", None)
        if callable(generator):
            return generator(results, report_type)
        if report_type == ReportType.BRIEF and hasattr(self.notifier, "generate_brief_report"):
            return self.notifier.generate_brief_report(results)
        return self.notifier.generate_dashboard_report(results)
