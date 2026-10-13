# -*- coding: utf-8 -*-
"""
===================================
A-share watchlist intelligent analysis system - main scheduler
===================================

Responsibilities:
1. Coordinate modules to complete the stock analysis flow
2. Low-concurrency thread pool scheduling
3. Global exception handling so a single stock failure does not affect the whole run
4. Provide the command-line entry point

Usage:
    python main.py              # Normal run
    python main.py --debug      # Debug mode
    python main.py --dry-run    # Fetch data only, no analysis

Trading philosophy (built into the analysis):
- Strict entry: do not chase highs; do not buy when deviation rate > 5%
- Trend trading: only trade MA5>MA10>MA20 bullish alignment
- Efficiency first: focus on stocks with good chip concentration
- Preferred entry: low-volume pullback to MA5/MA10 support
"""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

# Proxy config - controlled by USE_PROXY env var, off by default
# Proxy config is skipped automatically in GitHub Actions
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    # Local dev environment: enable proxy (PROXY_HOST and PROXY_PORT configurable in .env)
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

_packaged_import_probe = os.getenv("DSA_PACKAGED_IMPORT_PROBE")
if _packaged_import_probe:
    import importlib
    import sys

    try:
        importlib.import_module(_packaged_import_probe)
    except Exception as exc:
        print(
            f"ERROR: packaged import failed for {_packaged_import_probe}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"OK: packaged import succeeded for {_packaged_import_probe}")
    sys.exit(0)

import argparse
import logging
import sys
import time
import uuid
from datetime import date, datetime, timezone

from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging
from src.services.stock_list_parser import (
    AnalysisTarget,
    ParseStatus,
    parse_analysis_target,
    split_stock_list,
)
from src.services.stock_code_utils import resolve_index_stock_code_for_analysis


logger = logging.getLogger(__name__)
_RUNTIME_ENV_FILE_KEYS = set()
_PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*"})
_LAST_ANALYSIS_FAILURE_REASON: Optional[str] = None


def _get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parent / ".env"


def _is_public_bind_host(host: str) -> bool:
    return (host or "").strip().lower() in _PUBLIC_BIND_HOSTS


def _warn_if_public_webui_without_auth(host: str) -> None:
    if not _is_public_bind_host(host):
        return

    from src.auth import is_auth_enabled

    if is_auth_enabled():
        return
    logger.warning(
        "WEBUI_HOST=%s binds the Web UI to a public interface while "
        "ADMIN_AUTH_ENABLED=false. Keep this service behind a trusted network "
        "boundary or enable admin authentication before exposing it.",
        host,
    )


def _resolve_web_service_bind(args: argparse.Namespace, config: Config) -> Tuple[str, int]:
    """Resolve the effective Web/API bind address from CLI first, then config."""
    host = args.host if args.host is not None else (config.webui_host or "127.0.0.1")
    port = args.port if args.port is not None else config.webui_port
    return host, port


def _read_active_env_values() -> Optional[Dict[str, str]]:
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("Đọc file cấu hình %s thất bại, tiếp tục dùng biến môi trường hiện tại: %s", env_path, exc)
        return None

    return {
        str(key): "" if value is None else str(value)
        for key, value in values.items()
        if key is not None
    }


_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {
    key for key in _ACTIVE_ENV_FILE_VALUES
    if key not in _INITIAL_PROCESS_ENV
}

# setup_env() already ran at import time above.
_env_bootstrapped = True


def _bootstrap_environment() -> None:
    """Load .env and apply optional local proxy settings.

    Guarded to be idempotent so it can safely be called from lazy-import
    paths used by API / bot consumers.
    """
    global _env_bootstrapped
    if _env_bootstrapped:
        return

    from src.config import setup_env

    setup_env()

    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    _env_bootstrapped = True


def _setup_bootstrap_logging(debug: bool = False) -> None:
    """Initialize stderr-only logging before config is loaded.

    File handlers are deferred until ``config.log_dir`` is known (via the
    subsequent ``setup_logging()`` call) so that healthy runs never create
    log files in a hard-coded directory.
    """
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)


def _setup_runtime_logging(log_dir: str, debug: bool = False) -> bool:
    """Switch to configured logging, falling back to console on file I/O errors."""
    try:
        setup_logging(log_prefix="stock_analysis", debug=debug, log_dir=log_dir)
        return True
    except OSError as exc:
        logger.warning(
            "Khởi tạo log ra file thất bại, đã hạ cấp sang log ra console; thư mục log %r hiện không ghi được hoặc không tạo được: %s. "
            "Entrypoint của Docker image chính thức sẽ tự sửa quyền thư mục mount mặc định; nếu vẫn lỗi, "
            "hãy kiểm tra có dùng --user, mount chỉ đọc, rootless Docker hoặc NFS... làm hạn chế ghi hay không.",
            log_dir,
            exc,
        )
        return False


def _get_stock_analysis_pipeline():
    """Lazily import StockAnalysisPipeline for external consumers.

    Also ensures env/proxy bootstrap has run so that API / bot consumers
    that never call ``main()`` still get ``USE_PROXY`` applied.
    """
    _bootstrap_environment()
    from src.core.pipeline import StockAnalysisPipeline as _Pipeline

    return _Pipeline


class _LazyPipelineDescriptor:
    """Descriptor that resolves StockAnalysisPipeline on first attribute access."""

    _resolved = None

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if self._resolved is None:
            self._resolved = _get_stock_analysis_pipeline()
        return self._resolved


class _ModuleExports:
    StockAnalysisPipeline = _LazyPipelineDescriptor()


_exports = _ModuleExports()


def __getattr__(name: str):
    if name == "StockAnalysisPipeline":
        return _exports.StockAnalysisPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reload_env_file_values_preserving_overrides() -> None:
    """Refresh `.env`-managed env vars without clobbering process env overrides."""
    global _RUNTIME_ENV_FILE_KEYS

    latest_values = _read_active_env_values()
    if latest_values is None:
        return

    managed_keys = {
        key for key in latest_values
        if key not in _INITIAL_PROCESS_ENV
    }

    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)

    for key in managed_keys:
        os.environ[key] = latest_values[key]

    _RUNTIME_ENV_FILE_KEYS = managed_keys


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Hệ thống phân tích thông minh cổ phiếu Việt Nam',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Ví dụ:
  python main.py                    # Chạy bình thường
  python main.py --debug            # Chế độ debug
  python main.py --dry-run          # Chỉ lấy dữ liệu, không chạy phân tích AI
  python main.py --stocks HPG,FPT,VCB  # Phân tích các mã chỉ định
  python main.py --stocks VNINDEX   # Phân tích chỉ số đã đăng ký
  python main.py --no-notify        # Không gửi thông báo
  python main.py --check-notify     # Kiểm tra cấu hình thông báo, không gửi
  python main.py --single-notify    # Bật chế độ gửi từng mã (phân tích xong mã nào gửi ngay mã đó)
  python main.py --schedule         # Bật chế độ chạy định kỳ
  python main.py --market-review    # Chỉ chạy tổng kết thị trường
        '''
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Bật chế độ debug, ghi log chi tiết'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Chỉ lấy dữ liệu, không chạy phân tích AI'
    )

    parser.add_argument(
        '--stocks',
        type=str,
        help='Mã cổ phiếu cần phân tích, phân tách bằng dấu phẩy (ghi đè file cấu hình), ví dụ HPG,FPT,VCB; hỗ trợ chỉ số đã đăng ký như VNINDEX'
    )

    parser.add_argument(
        '--no-notify',
        action='store_true',
        help='Không gửi thông báo'
    )

    parser.add_argument(
        '--check-notify',
        action='store_true',
        help='Chỉ kiểm tra (chỉ đọc) cấu hình kênh thông báo, không gửi thông báo'
    )

    parser.add_argument(
        '--single-notify',
        action='store_true',
        help='Bật chế độ gửi từng mã: phân tích xong mã nào gửi ngay mã đó thay vì gửi tổng hợp'
    )

    parser.add_argument(
        '--workers',
        type=int,
        default=None,
        help='Số luồng chạy song song (mặc định dùng giá trị cấu hình)'
    )

    parser.add_argument(
        '--schedule',
        action='store_true',
        help='Bật chế độ chạy định kỳ, chạy hằng ngày theo giờ đã đặt'
    )

    parser.add_argument(
        '--no-run-immediately',
        action='store_true',
        help='Không chạy ngay một lần khi khởi động chế độ định kỳ'
    )

    parser.add_argument(
        '--market-review',
        action='store_true',
        help='Chỉ chạy tổng kết thị trường'
    )

    parser.add_argument(
        '--no-market-review',
        action='store_true',
        help='Bỏ qua tổng kết thị trường'
    )

    parser.add_argument(
        '--force-run',
        action='store_true',
        help='Bỏ qua kiểm tra ngày giao dịch, buộc chạy phân tích toàn bộ (Issue #373)'
    )

    parser.add_argument(
        '--webui',
        action='store_true',
        help='Khởi động giao diện quản trị Web'
    )

    parser.add_argument(
        '--webui-only',
        action='store_true',
        help='Chỉ khởi động dịch vụ Web, không tự chạy phân tích'
    )

    parser.add_argument(
        '--serve',
        action='store_true',
        help='Khởi động dịch vụ backend FastAPI (đồng thời chạy tác vụ phân tích)'
    )

    parser.add_argument(
        '--serve-only',
        action='store_true',
        help='Chỉ khởi động dịch vụ backend FastAPI, không tự chạy phân tích'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help='Cổng dịch vụ FastAPI (mặc định dùng WEBUI_PORT, nếu chưa cấu hình là 8000)'
    )

    parser.add_argument(
        '--host',
        type=str,
        default=None,
        help='Địa chỉ lắng nghe của dịch vụ FastAPI (mặc định dùng WEBUI_HOST, nếu chưa cấu hình là 127.0.0.1)'
    )

    parser.add_argument(
        '--no-context-snapshot',
        action='store_true',
        help='Không lưu snapshot ngữ cảnh phân tích'
    )

    # === Backtest ===
    parser.add_argument(
        '--backtest',
        action='store_true',
        help='Chạy backtest (đánh giá các kết quả phân tích lịch sử)'
    )

    parser.add_argument(
        '--backtest-code',
        type=str,
        default=None,
        help='Chỉ backtest mã cổ phiếu chỉ định'
    )

    parser.add_argument(
        '--backtest-days',
        type=int,
        default=None,
        help='Cửa sổ đánh giá backtest (số ngày giao dịch, mặc định dùng cấu hình)'
    )

    parser.add_argument(
        '--backtest-force',
        action='store_true',
        help='Buộc backtest (tính lại kể cả khi đã có kết quả backtest)'
    )

    return parser.parse_args()


def _compute_trading_day_filter(
    config: Config,
    args: argparse.Namespace,
    stock_codes: List[str],
) -> Tuple[List[str], Optional[str], bool]:
    """
    Compute filtered stock list and effective market review region (Issue #373).

    Returns:
        (filtered_codes, effective_region, should_skip_all)
        - effective_region None = use config default (check disabled)
        - effective_region '' = all relevant markets closed, skip market review
        - should_skip_all: skip entire run when no stocks and no market review to run
    """
    force_run = getattr(args, 'force_run', False)
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import (
        get_market_for_stock,
        get_open_markets_today,
        compute_effective_region,
    )

    open_markets = get_open_markets_today()
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)

    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(
            getattr(config, 'market_review_region', 'vn') or 'vn', open_markets
        )
    else:
        effective_region = None

    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)


def _classify_stock_list_tokens(
    tokens: List[str],
    *,
    entry_label: str,
) -> Optional[Tuple[List[str], List[AnalysisTarget]]]:
    """Classify stock-list tokens into aligned ``(stock_codes, analysis_targets)``.

    Shared by the one-shot ``--stocks`` entry and the GitHub Actions
    ``STOCK_LIST`` entry: explicit index tokens resolve through
    ``parse_analysis_target`` and keep their index canonical, while every
    other token keeps the legacy ``resolve_index_stock_code_for_analysis``
    path. ``unsupported`` tokens (e.g. an unregistered ``.CSI``) reject the
    whole run up-front with an explicit error — provider calls are never
    reached for any token in the batch. Returns ``None`` after logging the
    rejection, so callers exit without running analysis.
    """
    targets = [parse_analysis_target(t) for t in tokens]
    unsupported = [
        (token, target.unsupported_reason or "unsupported target")
        for token, target in zip(tokens, targets)
        if target.asset_type == ParseStatus.UNSUPPORTED
    ]
    if unsupported:
        token, reason = unsupported[0]
        logger.error(
            "%s chứa mục tiêu không được hỗ trợ %r: %s; lượt này sẽ không chạy phân tích nào.",
            entry_label,
            token,
            reason,
        )
        return None
    stock_codes = [resolve_index_stock_code_for_analysis(raw) for raw in tokens]
    return stock_codes, targets


def _run_market_review_with_shared_lock(
    config: Config,
    run_market_review_func: Callable[..., Any],
    **kwargs: Any,
) -> Any:
    from src.core.market_review_lock import (
        release_market_review_lock,
        try_acquire_market_review_lock,
    )

    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        logger.warning("Tổng kết thị trường đang chạy, bỏ qua lần tổng kết thị trường này")
        return None

    try:
        params = dict(kwargs)
        params.setdefault("config", config)
        return run_market_review_func(**params)
    finally:
        release_market_review_lock(lock_token)


def _is_multi_market_region(region: str) -> bool:
    normalized = str(region or "").strip().lower()
    if not normalized:
        return False
    if normalized == "both":
        return True
    parts = {item.strip() for item in normalized.split(",") if item.strip()}
    return len(parts) > 1


def _prime_daily_market_context(
    config: Config,
    pipeline: Any,
    *,
    region: str,
    no_market_review: bool,
    allow_generate: bool,
    force_refresh: bool = False,
    target_date: Optional[date] = None,
    return_full_report: bool = False,
    require_current_query_match: bool = False,
) -> Union[str, Tuple[str, str]]:
    """Load/reuse the run's market context, avoiding unbounded background generation."""
    if no_market_review or not region:
        return ("", "") if return_full_report else ""

    from src.services.daily_market_context import DailyMarketContextService

    if not _is_multi_market_region(region):
        service = getattr(pipeline, "_daily_market_context_service", None)
        if service is None:
            service = DailyMarketContextService(db_manager=pipeline.db)
            pipeline._daily_market_context_service = service
    else:
        service = DailyMarketContextService(db_manager=pipeline.db)

    get_context_kwargs = {
        "region": region,
        "config": config,
        "notifier": pipeline.notifier,
        "analyzer": pipeline.analyzer,
        "search_service": pipeline.search_service,
        "force_refresh": force_refresh,
        "allow_generate": allow_generate,
        "persist_market_review_history": False,
        "target_date": target_date,
        "require_query_id_match": require_current_query_match,
    }
    current_query_id = getattr(pipeline, "query_id", None)
    if isinstance(current_query_id, str) and current_query_id.strip():
        get_context_kwargs["current_query_id"] = current_query_id

    context = service.get_context(**get_context_kwargs)
    if context is None:
        return ("", "") if return_full_report else ""

    # Runtime context generation is preload-only and must not replace the full
    # market review run, except the query-scoped fallback after that run fails.
    if context.source != "analysis_history" and not (
        require_current_query_match and context.source == "market_review_runtime"
    ):
        return ("", "") if return_full_report else ""

    summary = str(getattr(context, "summary", ""))
    full_report = str(getattr(context, "full_report", "") or "")
    if return_full_report:
        return summary, full_report
    return summary


def _can_reuse_market_context_for_review(summary: str, region: str) -> bool:
    if not summary:
        return False
    normalized = str(region or "").strip().lower()
    if normalized == "both":
        return False
    parts = {item.strip() for item in normalized.split(",") if item.strip()}
    return len(parts) <= 1


def _resolve_daily_market_context_target_date(
    region: str,
    current_time: datetime,
) -> date:
    """Ngày nến hiệu lực cho bối cảnh thị trường trong ngày (chỉ thị trường Việt Nam).

    Mọi giá trị ``region`` (kể cả vùng cũ cn/jp/kr) đều theo lịch giao dịch ``vn``;
    trước đây vùng rỗng/không rõ rơi về ``cn`` — thị trường đã bị loại khỏi registry
    nên không còn múi giờ và ngày hiệu lực bị tính theo giờ hệ thống.

    Effective daily-bar date for the daily market context (Vietnam only).
    """
    from src.core.trading_calendar import get_effective_trading_date

    return get_effective_trading_date("vn", current_time=current_time)


def _market_review_report_text(review_result: Any) -> str:
    if review_result is None:
        return ""
    report = getattr(review_result, "report", None)
    if isinstance(report, str):
        return report
    return review_result if isinstance(review_result, str) else ""


def _save_reused_market_review_report(
    notifier: Any,
    market_report: str,
    *,
    config: Config,
    trigger_source: str,
    region: str,
) -> None:
    body = str(market_report or "").strip()
    if not body:
        return
    # Cả vi và en đều dùng tiêu đề tiếng Anh cố định khi lưu file.
    # Both vi and en use a fixed English title for the saved file.
    title = "# 🎯 Market Review"
    if not any(body.startswith(item) for item in ("# 🎯 Điểm lại thị trường", "# 🎯 Market Review")):
        body = f"{title}\n\n{body}"
    try:
        date_str = datetime.now().strftime('%Y%m%d')
        report_filename = f"market_review_{date_str}.md"
        filepath = notifier.save_report_to_file(body, report_filename)
        logger.info(
            "[MarketReview] component=market_review action=save_reused_report "
            "trigger_source=%s region=%s path=%s",
            trigger_source,
            region,
            filepath,
        )
    except Exception as exc:
        logger.warning("Lưu báo cáo tổng kết thị trường từ ngữ cảnh thị trường dùng lại thất bại: %s", exc)


def _run_auto_backtest(config: Config) -> None:
    """Run the independently configured auto-backtest without failing analysis."""

    try:
        if not getattr(config, 'backtest_enabled', False):
            return

        from src.services.backtest_service import BacktestService

        logger.info("Bắt đầu backtest tự động...")
        service = BacktestService()
        stats = service.run_backtest(
            force=False,
            eval_window_days=getattr(config, 'backtest_eval_window_days', 10),
            min_age_days=getattr(config, 'backtest_min_age_days', 14),
            limit=200,
        )
        logger.info(
            f"Backtest tự động hoàn tất: processed={stats.get('processed')} "
            f"saved={stats.get('saved')} completed={stats.get('completed')} "
            f"insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
        )
    except Exception as exc:
        logger.warning(f"Backtest tự động thất bại (đã bỏ qua): {exc}")


def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None,
    *,
    raise_errors: bool = False,
    analysis_targets: Optional[List[AnalysisTarget]] = None,
) -> bool:
    """
    Run the full analysis flow (individual stocks + market review)

    This is the main function invoked by scheduled tasks.
    ``analysis_targets`` is aligned with ``stock_codes`` and carries structured analysis targets
    (index targets are used to derive market=cn and the capability matrix).
    """
    market_review_requested = (
        getattr(config, 'market_review_enabled', False)
        and not getattr(args, 'no_market_review', False)
    )

    # Import pipeline modules outside the broad try/except so that import-time
    # failures propagate to the caller instead of being silently swallowed.
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

    global _LAST_ANALYSIS_FAILURE_REASON
    _LAST_ANALYSIS_FAILURE_REASON = None

    def _return_with_auto_backtest(result: bool) -> bool:
        _run_auto_backtest(config)
        return result

    try:

        # Issue #529: Hot-reload STOCK_LIST from .env on each scheduled run
        if stock_codes is None:
            config.refresh_stock_list()

        using_config_stock_list = stock_codes is None
        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        # Fail fast on an empty persisted watchlist before trading-day filtering.
        # Otherwise should_skip=True would mask the configuration error as success.
        if (
            not getattr(args, "dry_run", False)
            and using_config_stock_list
            and not effective_codes
            and not market_review_requested
        ):
            _LAST_ANALYSIS_FAILURE_REASON = "empty_stock_list"
            logger.error(
                "Lượt phân tích này không tạo báo cáo: STOCK_LIST rỗng và chưa bật tổng kết thị trường."
            )
            return _return_with_auto_backtest(False)

        # Issue #373: Trading day filter (per-stock, per-market)
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes
        )
        if should_skip:
            logger.info(
                "Hôm nay mọi thị trường liên quan đều không phải ngày giao dịch, bỏ qua. "
                "Có thể dùng --force-run để buộc chạy."
            )
            return True
        if set(filtered_codes) != set(effective_codes):
            skipped = set(effective_codes) - set(filtered_codes)
            logger.info("Đã bỏ qua các mã có thị trường nghỉ hôm nay: %s", skipped)
        if analysis_targets is not None:
            if len(analysis_targets) != len(effective_codes):
                raise ValueError("analysis_targets must align with stock_codes")
            remaining_pairs = list(zip(effective_codes, analysis_targets))
            filtered_targets = []
            for filtered_code in filtered_codes:
                for index, (code, target) in enumerate(remaining_pairs):
                    if code == filtered_code:
                        filtered_targets.append(target)
                        remaining_pairs.pop(index)
                        break
            analysis_targets = filtered_targets
        stock_codes = filtered_codes

        # The --single-notify CLI argument overrides config (#55)
        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        # Issue #190: merge individual-stock and market-review notifications
        merge_notification = (
            getattr(config, 'merge_email_notification', False)
            and config.market_review_enabled
            and not getattr(args, 'no_market_review', False)
            and not config.single_stock_notify
        )

        # Create the scheduler
        save_context_snapshot = None
        if getattr(args, 'no_context_snapshot', False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        market_review_region = (
            effective_region
            if effective_region is not None
            else (getattr(config, 'market_review_region', 'cn') or 'cn')
        )
        should_run_market_review = (
            config.market_review_enabled
            and not args.no_market_review
            and (market_review_region or '') != ''
        )
        if (
            not getattr(args, "dry_run", False)
            and not stock_codes
            and using_config_stock_list
            and not should_run_market_review
        ):
            _LAST_ANALYSIS_FAILURE_REASON = "empty_stock_list"
            logger.error(
                "Lượt phân tích này không tạo báo cáo: STOCK_LIST rỗng và chưa bật tổng kết thị trường."
            )
            return _return_with_auto_backtest(False)
        should_use_daily_market_context = (
            should_run_market_review
            and getattr(config, 'daily_market_context_enabled', True)
        )
        analysis_reference_time = datetime.now(timezone.utc)
        daily_market_context_target_date = None
        if should_use_daily_market_context:
            daily_market_context_target_date = _resolve_daily_market_context_target_date(
                market_review_region,
                analysis_reference_time,
            )
        market_report = ""
        market_context_summary = ""
        market_context_full_report = ""
        market_context_generated_during_stock = False
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=args.workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=save_context_snapshot,
            daily_market_context_enabled=should_use_daily_market_context,
            daily_market_context_allow_generate=should_use_daily_market_context,
        )
        if should_use_daily_market_context:
            # Prompt-side context can reuse historical summaries, while full-merge
            # content must avoid silently reusing unrelated historical reports.
            _prime_daily_market_context(
                config,
                pipeline=pipeline,
                region=market_review_region,
                no_market_review=args.no_market_review,
                allow_generate=False,
                target_date=daily_market_context_target_date,
                return_full_report=False,
            )
            (
                market_context_summary,
                market_context_full_report,
            ) = _prime_daily_market_context(
                config,
                pipeline=pipeline,
                region=market_review_region,
                no_market_review=args.no_market_review,
                allow_generate=False,
                target_date=daily_market_context_target_date,
                return_full_report=True,
                require_current_query_match=True,
            )

        # 1. Run individual stock analysis
        results = pipeline.run(
            stock_codes=stock_codes,
            dry_run=args.dry_run,
            send_notification=not args.no_notify,
            merge_notification=merge_notification,
            current_time=analysis_reference_time,
            analysis_targets=analysis_targets,
        )

        if should_use_daily_market_context and not market_context_summary:
            (
                market_context_summary,
                market_context_full_report,
            ) = _prime_daily_market_context(
                config,
                pipeline=pipeline,
                region=market_review_region,
                no_market_review=args.no_market_review,
                allow_generate=False,
                target_date=daily_market_context_target_date,
                return_full_report=True,
                require_current_query_match=True,
            )
            market_context_generated_during_stock = bool(market_context_summary)

        # Issue #128: analysis delay - add a pause between stock analysis and market analysis
        analysis_delay = getattr(config, 'analysis_delay', 0)

        # 2. Run market review (if enabled and not stocks-only mode)
        if should_run_market_review:
            schedule_mode = bool(
                getattr(args, 'schedule', False)
                or getattr(config, 'schedule_enabled', False)
            )
            review_trigger_source = "schedule" if schedule_mode else "cli"
            can_reuse_market_context = (
                _can_reuse_market_context_for_review(
                    market_context_summary,
                    market_review_region,
                )
                if should_use_daily_market_context
                else False
            )

            can_skip_market_review = (
                (merge_notification or market_context_generated_during_stock)
                and can_reuse_market_context
                and bool(market_context_full_report or market_context_summary)
            )
            if can_skip_market_review:
                market_report = market_context_full_report or market_context_summary
                logger.info(
                    "Ngữ cảnh tổng kết có thể dùng lại, bỏ qua tổng kết thị trường trùng lặp và dùng lại nội dung ngữ cảnh."
                )
                _save_reused_market_review_report(
                    pipeline.notifier,
                    market_report,
                    config=config,
                    trigger_source=review_trigger_source,
                    region=market_review_region,
                )
                if (
                    market_context_generated_during_stock
                    and not merge_notification
                    and not args.no_notify
                    and pipeline.notifier.is_available()
                ):
                    if pipeline.notifier.send(
                        f"# 📈 Điểm lại thị trường\n\n{market_report}",
                        email_send_to_all=True,
                        route_type="report",
                    ):
                        logger.info("Gửi tổng kết thị trường từ ngữ cảnh thị trường của lượt này thành công")
                    else:
                        logger.warning("Gửi tổng kết thị trường từ ngữ cảnh thị trường của lượt này thất bại")

            review_result = None
            if not can_skip_market_review:
                if analysis_delay > 0:
                    logger.info(f"Chờ {analysis_delay} giây rồi chạy tổng kết thị trường (tránh giới hạn tần suất API)...")
                    time.sleep(analysis_delay)

                review_result = _run_market_review_with_shared_lock(
                    config,
                    run_market_review,
                    notifier=pipeline.notifier,
                    analyzer=pipeline.analyzer,
                    search_service=pipeline.search_service,
                    send_notification=not args.no_notify,
                    merge_notification=merge_notification,
                    override_region=market_review_region,
                    query_id=query_id,
                    trigger_source=review_trigger_source,
                )
                # If the review still has not succeeded, retry reading history/cache once more (guards against concurrent-run races).
                if not review_result and should_use_daily_market_context:
                    (
                        market_context_summary,
                        market_context_full_report,
                    ) = _prime_daily_market_context(
                        config,
                        pipeline=pipeline,
                        region=market_review_region,
                        no_market_review=args.no_market_review,
                        allow_generate=False,
                        target_date=daily_market_context_target_date,
                        return_full_report=True,
                        require_current_query_match=True,
                    )
                    can_reuse_market_context = _can_reuse_market_context_for_review(
                        market_context_summary,
                        market_review_region,
                    )
                elif not review_result:
                    can_reuse_market_context = False

            # If there is a result, assign it to market_report for later Feishu doc generation
            if review_result:
                market_report = _market_review_report_text(review_result)
            elif can_reuse_market_context:
                market_report = market_context_full_report or market_context_summary

        expected_stock_report = (
            not getattr(args, "dry_run", False)
            and bool(stock_codes)
        )
        deferred_failure_result = None
        if expected_stock_report and results and not getattr(
            pipeline, "_last_local_report_path", None
        ):
            _LAST_ANALYSIS_FAILURE_REASON = "report_save_failed"
            save_error = getattr(pipeline, "_last_local_report_error", None) or "unknown error"
            logger.error(
                "Lượt phân tích đã có kết quả từng mã, nhưng lưu báo cáo tổng hợp thất bại, không tạo file báo cáo cục bộ: %s",
                save_error,
            )
            deferred_failure_result = False
        expected_market_report = (
            not getattr(args, "dry_run", False)
            and should_run_market_review
        )
        if (expected_stock_report or expected_market_report) and not results and not market_report:
            _LAST_ANALYSIS_FAILURE_REASON = "no_report"
            logger.error(
                "Lượt phân tích không tạo báo cáo nào: cả phân tích từng mã lẫn tổng kết thị trường dự kiến đều không ra kết quả."
            )
            return _return_with_auto_backtest(False)

        # Issue #190: merged notification (stocks + market review)
        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 Điểm lại thị trường\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results,
                    getattr(config, 'report_type', 'simple'),
                )
                parts.append(f"# 🚀 Bảng quyết định cổ phiếu\n\n{dashboard_content}")
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True, route_type="report"):
                        logger.info("Đã gửi gộp (từng mã + tổng kết thị trường)")
                    else:
                        logger.warning("Gửi gộp thất bại")

        # Print summary
        if results:
            logger.info("\n===== Tóm tắt kết quả phân tích =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(
                    f"{emoji} {r.name}({r.code}): {r.operation_advice} | "
                    f"Điểm {r.sentiment_score} | {r.trend_prediction}"
                )

        logger.info("\nTác vụ hoàn tất")

        return _return_with_auto_backtest(
            deferred_failure_result
            if deferred_failure_result is not None
            else True
        )

    except Exception as e:
        if _LAST_ANALYSIS_FAILURE_REASON is None:
            _LAST_ANALYSIS_FAILURE_REASON = "runtime_error"
        logger.exception(f"Luồng phân tích chạy thất bại: {e}")
        if raise_errors:
            raise
        return False


def run_scheduled_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None,
) -> bool:
    """Run scheduled analysis with failures propagated to the scheduler."""
    return run_full_analysis(config, args, stock_codes, raise_errors=True)


def _run_analysis_with_runtime_scheduler_lock(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None,
    analysis_targets: Optional[List[AnalysisTarget]] = None,
) -> bool:
    from src.services.runtime_scheduler import run_with_global_analysis_lock

    task_result: Dict[str, bool] = {"ok": True}

    def _locked_task_runner(
        locked_config: Config,
        locked_args: argparse.Namespace,
        locked_stock_codes: Optional[List[str]] = None,
    ) -> bool:
        if analysis_targets is None:
            result = run_full_analysis(locked_config, locked_args, locked_stock_codes)
        else:
            result = run_full_analysis(
                locked_config,
                locked_args,
                locked_stock_codes,
                analysis_targets=analysis_targets,
            )
        task_result["ok"] = bool(result)
        return task_result["ok"]

    # Keep startup/triggered analysis in sync with API runtime scheduler and
    # run-now entrypoint. Blocking is expected here because startup paths should
    # wait for an in-flight job before returning a response.
    lock_acquired = run_with_global_analysis_lock(
        task_runner=_locked_task_runner,
        config=config,
        args=args,
        stock_codes=stock_codes,
        blocking=True,
    )
    return bool(lock_acquired and task_result["ok"])


def start_api_server(host: str, port: int, config: Config) -> None:
    """
    Start the FastAPI service in a background thread

    Args:
        host: listen address
        port: listen port
        config: config object
    """
    import socket
    import threading
    import uvicorn

    probe = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"FastAPI port is not available: {host}:{port}") from exc
    finally:
        probe.close()

    level_name = (config.log_level or "INFO").lower()
    use_config_signal_handlers = True
    uvicorn_kwargs = {
        "host": host,
        "port": port,
        "log_level": level_name,
        "log_config": None,
    }
    # Import the ASGI app object in the calling thread instead of handing uvicorn
    # the "api.app:app" import string. With the string, uvicorn imports the app
    # lazily inside the server thread, and that import (litellm + the full app
    # tree, ~10s+ on constrained hosts) runs inside the startup probe window
    # below, tripping the 3.0s timeout and causing a restart loop on slower
    # machines. Importing first keeps the heavy work out of the probe window;
    # genuine import failures still surface immediately to the caller.
    from api.app import app as fastapi_app

    try:
        uvicorn_config = uvicorn.Config(
            fastapi_app,
            install_signal_handlers=False,
            **uvicorn_kwargs,
        )
    except TypeError:
        # Older uvicorn versions do not accept install_signal_handlers in
        # Config; fall back and only disable signal handling via Server attribute
        # when it's a boolean flag.
        use_config_signal_handlers = False
        uvicorn_config = uvicorn.Config(
            fastapi_app,
            **uvicorn_kwargs,
        )
    uvicorn_server = uvicorn.Server(config=uvicorn_config)
    if not use_config_signal_handlers:
        install_signal_handlers = getattr(uvicorn_server, "install_signal_handlers", None)
        if isinstance(install_signal_handlers, bool):
            uvicorn_server.install_signal_handlers = False

    startup_error: list[BaseException] = []

    def run_server():
        try:
            uvicorn_server.run()
        except Exception as exc:  # noqa: BLE001 - surface startup issues to caller promptly
            startup_error.append(exc)

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    timeout_seconds = 3.0
    wait_deadline = time.time() + timeout_seconds
    while time.time() < wait_deadline:
        if startup_error:
            raise RuntimeError(
                f"FastAPI server failed to start: {host}:{port}; {startup_error[0]}"
            )
        if uvicorn_server.started:
            logger.info(f"Dịch vụ FastAPI đã khởi động: http://{host}:{port}")
            return
        if not thread.is_alive():
            break
        time.sleep(0.05)

    if startup_error:
        raise RuntimeError(f"FastAPI server failed to start: {host}:{port}; {startup_error[0]}")
    if uvicorn_server.started:
        logger.info(f"Dịch vụ FastAPI đã khởi động: http://{host}:{port}")
        return
    if not thread.is_alive():
        raise RuntimeError(f"Máy chủ FastAPI thoát ngay sau khi khởi động: {host}:{port}")

    raise RuntimeError(f"Dịch vụ FastAPI không khởi động xong trong {timeout_seconds:.1f}s: {host}:{port}")


def _is_truthy_env(var_name: str, default: str = "true") -> bool:
    """Parse common truthy / falsy environment values."""
    value = os.getenv(var_name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _resolve_scheduled_stock_codes(stock_codes: Optional[List[str]]) -> Optional[List[str]]:
    """Scheduled runs should always read the latest persisted watchlist."""
    if stock_codes is not None:
        logger.warning(
            "Phát hiện tham số --stocks ở chế độ định kỳ; các lần chạy theo lịch sẽ bỏ qua danh sách mã lúc khởi động và đọc lại STOCK_LIST mới nhất trước mỗi lần chạy."
        )
    return None


def _skips_stock_entry(args: argparse.Namespace, config: Config) -> bool:
    """Whether the active mode consumes a stock list at all.

    Modes that do not analyze the watchlist must not parse/classify
    ``--stocks``/``STOCK_LIST`` tokens nor refresh the stock-index registry
    before the mode dispatch, otherwise an unsupported index token would
    wrongly block a run that never consumes it. Covered modes:
    ``--backtest``, ``--market-review``, ``--serve-only``/``--webui-only``
    and ``--schedule``/``config.schedule_enabled``.
    ``--serve`` (not serve-only) and plain one-shot runs still consume the
    stock list and stay outside the guard. The webui-only flag is read
    directly here because the webui -> serve mapping happens only later in
    ``main()``.
    """
    return bool(
        getattr(args, "backtest", False)
        or getattr(args, "market_review", False)
        or getattr(args, "serve_only", False)
        or getattr(args, "webui_only", False)
        or getattr(args, "schedule", False)
        or getattr(config, "schedule_enabled", False)
    )


def _reload_runtime_config() -> Config:
    """Reload config from the latest persisted `.env` values for scheduled runs."""
    _reload_env_file_values_preserving_overrides()
    Config.reset_instance()
    new_config = get_config()

    # Drop the module-level ToolRegistry so the next
    # ``build_agent_executor`` / ``build_agent_chat_executor`` call rebuilds
    # it against the freshly-loaded Config and picks up new
    # ``AGENT_*_TOOL_TIMEOUT_S`` overrides (Issue #1890). Wrap in try/except
    # so a future runtime reset helper cannot crash scheduled-job bootstrap.
    try:
        from src.agent.factory import reset_tool_registry

        reset_tool_registry()
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Failed to reset tool registry during config reload: %s", exc)

    return new_config


def _build_schedule_time_provider(default_schedule_time: str):
    """Read the latest schedule time directly from the active config file.

    Fallback order:
    1. Process-level env override (set before launch) → honour it.
    2. Persisted config file value (written by WebUI) → use it.
    3. Documented system default ``"18:00"`` → always fall back here so
       that clearing SCHEDULE_TIME in WebUI correctly resets the schedule.
    """
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SCHEDULE_TIME = "18:00"
    manager = ConfigManager()

    def _provider() -> str:
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("SCHEDULE_TIME", default_schedule_time)

        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip()
        if schedule_time:
            return schedule_time
        return _SYSTEM_DEFAULT_SCHEDULE_TIME

    return _provider


def _build_schedule_times_provider(default_schedule_time: str):
    """Read the latest SCHEDULE_TIMES with SCHEDULE_TIME fallback."""
    from src.core.config_manager import ConfigManager
    from src.scheduler import normalize_schedule_times

    _SYSTEM_DEFAULT_SCHEDULE_TIME = "18:00"
    manager = ConfigManager()

    def _provider():
        if "SCHEDULE_TIMES" in _INITIAL_PROCESS_ENV:
            return normalize_schedule_times(
                os.getenv("SCHEDULE_TIMES", ""),
                fallback_time=os.getenv("SCHEDULE_TIME", default_schedule_time),
            )
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return normalize_schedule_times(
                os.getenv("SCHEDULE_TIMES", ""),
                fallback_time=os.getenv("SCHEDULE_TIME", default_schedule_time),
            )

        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip() or _SYSTEM_DEFAULT_SCHEDULE_TIME
        return normalize_schedule_times(
            config_map.get("SCHEDULE_TIMES", ""),
            fallback_time=schedule_time,
        )

    return _provider


def main() -> int:
    """
    Main entry function

    Returns:
        Exit code (0 means success)
    """
    # Parse command-line arguments
    args = parse_arguments()

    # Initialize bootstrap logging before loading config so early failures are also written to disk
    try:
        _setup_bootstrap_logging(debug=args.debug)
    except Exception as exc:
        logging.basicConfig(
            level=logging.DEBUG if getattr(args, "debug", False) else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
        )
        logger.warning("Khởi tạo log bootstrap thất bại, đã quay về stderr: %s", exc)

    # Load config (after bootstrap logging, so exceptions get logged)
    try:
        config = get_config()
    except Exception as exc:
        logger.exception("Tải cấu hình thất bại: %s", exc)
        return 1

    # Configure logging (console and file)
    try:
        _setup_runtime_logging(config.log_dir, debug=args.debug)
    except Exception as exc:
        logger.exception("Chuyển sang thư mục log theo cấu hình thất bại: %s", exc)
        return 1

    logger.info("=" * 60)
    logger.info("Hệ thống phân tích thông minh cổ phiếu Việt Nam khởi động")
    logger.info(f"Thời gian chạy: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # Validate config
    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    if getattr(args, "check_notify", False):
        from src.services.notification_diagnostics import (
            format_notification_diagnostics,
            run_notification_diagnostics,
        )

        result = run_notification_diagnostics(config)
        print(format_notification_diagnostics(result))
        return 0 if result.ok else 1

    # Parse the stock list (normalized to uppercase, Issue #355)
    # Story 1.5: the one-off --stocks entry uses parse_analysis_target to build a structured
    # AnalysisTarget list; index targets (sh/sz/csi prefixes and .CSI aliases) keep their identity
    # semantics at the entry; unsupported targets (e.g. unregistered .CSI) are rejected up front and never reach a provider.
    # Modes that do not consume the stock list (backtest/market review/serve-only/schedule) skip
    # classification and index refresh entirely (_skips_stock_entry), so unrelated bad tokens cannot block the mode.
    _skip_stock_entry = _skips_stock_entry(args, config)
    stock_codes = None
    analysis_targets = None
    if args.stocks and not _skip_stock_entry:
        tokens = [c for c in split_stock_list(args.stocks) if (c or "").strip()]
        resolved = _classify_stock_list_tokens(tokens, entry_label="--stocks")
        if resolved is None:
            return 1
        stock_codes, analysis_targets = resolved
        logger.info(f"Dùng danh sách mã chỉ định từ dòng lệnh: {stock_codes}")
    elif (
        os.getenv("GITHUB_ACTIONS") == "true"
        and not _skip_stock_entry
    ):
        # The daily workflow (GITHUB_ACTIONS=true) runs `python main.py` with no args (full or
        # stocks-only mode): classify STOCK_LIST tokens into structured targets equivalent to the one-off --stocks
        # entry; explicit index tokens take the index path, stock tokens keep the existing path.
        # Modes that do not consume the stock list are skipped entirely by _skips_stock_entry (no refresh, no classification);
        # the local default path does not build targets at this entry.
        raw_value = config.stock_list or []
        raw_tokens = (
            split_stock_list(raw_value)
            if isinstance(raw_value, str)
            else [str(c).strip() for c in raw_value if str(c or "").strip()]
        )
        raw_tokens = [c for c in raw_tokens if (c or "").strip()]
        if raw_tokens:
            resolved = _classify_stock_list_tokens(
                raw_tokens, entry_label="GitHub Actions STOCK_LIST"
            )
            if resolved is None:
                return 1
            stock_codes, analysis_targets = resolved
            logger.info(f"STOCK_LIST mặc định của GitHub Actions: {stock_codes}")

    # === Handle --webui / --webui-only, mapped to --serve / --serve-only ===
    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True

    # Backward compatibility with the legacy WEBUI_ENABLED env var
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True

    # === Start the Web service (if enabled) ===
    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"

    if start_serve:
        args.host, args.port = _resolve_web_service_bind(args, config)
        _warn_if_public_webui_without_auth(args.host)

    if start_serve:
        from src.services.runtime_scheduler import (
            CLI_SCHEDULER_OWNER_ENV,
            RUNTIME_SCHEDULER_ARGS_ENV,
            RUNTIME_SCHEDULER_FORCE_ENABLED_ENV,
            RUNTIME_SCHEDULER_RUN_IMMEDIATELY_ENV,
            RUNTIME_SCHEDULER_SUPPRESS_START_ENV,
        )

        # The API runtime scheduler owns schedules once the Web/API service starts.
        # This keeps Web settings, status, and run-now actions attached to the real
        # scheduler instead of a separate CLI loop.
        os.environ.pop(CLI_SCHEDULER_OWNER_ENV, None)
        os.environ.pop(RUNTIME_SCHEDULER_SUPPRESS_START_ENV, None)
        runtime_schedule_requested = args.schedule or config.schedule_enabled
        if args.schedule:
            os.environ[RUNTIME_SCHEDULER_FORCE_ENABLED_ENV] = "true"
        else:
            os.environ.pop(RUNTIME_SCHEDULER_FORCE_ENABLED_ENV, None)
        if runtime_schedule_requested:
            # ``--serve-only`` must restore persisted schedules, but it must not
            # turn service/Desktop startup into an immediate analysis run.
            runtime_run_immediately = (
                False if args.serve_only else config.schedule_run_immediately
            )
            if getattr(args, 'no_run_immediately', False):
                runtime_run_immediately = False
            os.environ[RUNTIME_SCHEDULER_RUN_IMMEDIATELY_ENV] = (
                "true" if runtime_run_immediately else "false"
            )
        else:
            os.environ.pop(RUNTIME_SCHEDULER_RUN_IMMEDIATELY_ENV, None)
        runtime_scheduler_args = {
            "no_notify": bool(getattr(args, "no_notify", False)),
            "no_market_review": bool(getattr(args, "no_market_review", False)),
            "dry_run": bool(getattr(args, "dry_run", False)),
            "force_run": bool(getattr(args, "force_run", False)),
            "single_notify": bool(getattr(args, "single_notify", False)),
            "no_context_snapshot": bool(getattr(args, "no_context_snapshot", False)),
            "workers": getattr(args, "workers", None),
        }
        os.environ[RUNTIME_SCHEDULER_ARGS_ENV] = json.dumps(runtime_scheduler_args)
        if not prepare_webui_frontend_assets():
            logger.warning("Tài nguyên tĩnh frontend chưa sẵn sàng, vẫn tiếp tục khởi động dịch vụ FastAPI (trang Web có thể không dùng được)")
        try:
            start_api_server(host=args.host, port=args.port, config=config)
        except Exception as e:
            logger.error(f"Khởi động dịch vụ FastAPI thất bại: {e}")
            if args.serve_only:
                return 1
            start_serve = False

    # === Web-service-only mode: do not run analysis automatically ===
    if args.serve_only:
        logger.info("Chế độ: chỉ dịch vụ Web")
        logger.info(f"Dịch vụ Web đang chạy: http://{args.host}:{args.port}")
        logger.info("Kích hoạt phân tích qua endpoint /api/v1/analysis/analyze")
        logger.info(f"Tài liệu API: http://{args.host}:{args.port}/docs")
        logger.info("Nhấn Ctrl+C để thoát...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nNgười dùng ngắt, chương trình thoát")
        return 0

    try:
        # Mode 0: backtest
        if getattr(args, 'backtest', False):
            logger.info("Chế độ: backtest")
            from src.services.backtest_service import BacktestService

            service = BacktestService()
            stats = service.run_backtest(
                code=getattr(args, 'backtest_code', None),
                force=getattr(args, 'backtest_force', False),
                eval_window_days=getattr(args, 'backtest_days', None),
            )
            logger.info(
                f"Backtest hoàn tất: processed={stats.get('processed')} saved={stats.get('saved')} "
                f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
            )
            return 0

        # Mode 1: market review only
        if args.market_review:
            from src.core.market_review import run_market_review
            from src.core.market_review_runtime import build_market_review_runtime

            # Issue #373: Trading day check for market-review-only mode.
            # Do NOT use _compute_trading_day_filter here: that helper checks
            # config.market_review_enabled, which would wrongly block an
            # explicit --market-review invocation when the flag is disabled.
            effective_region = None
            if not getattr(args, 'force_run', False) and getattr(config, 'trading_day_check_enabled', True):
                from src.core.trading_calendar import get_open_markets_today, compute_effective_region as _compute_region
                open_markets = get_open_markets_today()
                effective_region = _compute_region(
                    getattr(config, 'market_review_region', 'vn') or 'vn', open_markets
                )
                if effective_region == '':
                    logger.info("Hôm nay các thị trường của tổng kết thị trường đều không phải ngày giao dịch, bỏ qua. Có thể dùng --force-run để buộc chạy.")
                    return 0

            logger.info("Chế độ: chỉ tổng kết thị trường")
            notifier, analyzer, search_service = build_market_review_runtime(config)

            market_review_result = _run_market_review_with_shared_lock(
                config,
                run_market_review,
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=not args.no_notify,
                override_region=effective_region,
                trigger_source="cli",
            )
            return 0 if market_review_result else 1

        # Mode 2: scheduled task mode
        if args.schedule or config.schedule_enabled:
            if start_serve:
                # Mode 2 + Web service: this path comes from combining --schedule/--webui/--serve with
                # config.schedule_enabled (all already guarded); the API runtime
                # scheduler takes over; --serve-only already returned in the serve_only branch above.
                logger.info("Chế độ: Web/API runtime scheduler")
                logger.info(f"Dịch vụ Web đang chạy: http://{args.host}:{args.port}")
                logger.info("Web/API runtime scheduler đã tiếp quản tác vụ định kỳ, lưu cài đặt sẽ áp dụng cho tiến trình hiện tại")
                logger.info("Nhấn Ctrl+C để thoát...")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    logger.info("\nNgười dùng ngắt, chương trình thoát")
                return 0

            logger.info("Chế độ: chạy định kỳ")
            logger.info(f"Giờ chạy hằng ngày: {config.schedule_time}")

            # Determine whether to run immediately:
            # Command line arg --no-run-immediately overrides config if present.
            # Otherwise use config (defaults to True).
            should_run_immediately = config.schedule_run_immediately
            if getattr(args, 'no_run_immediately', False):
                should_run_immediately = False

            logger.info(f"Chạy ngay khi khởi động: {should_run_immediately}")

            from src.scheduler import run_with_schedule
            scheduled_stock_codes = _resolve_scheduled_stock_codes(args.stocks or None)
            schedule_time_provider = _build_schedule_time_provider(config.schedule_time)
            schedule_times_provider = _build_schedule_times_provider(config.schedule_time)

            def scheduled_task():
                runtime_config = _reload_runtime_config()
                result = run_full_analysis(runtime_config, args, scheduled_stock_codes)
                if result is False:
                    reason = _LAST_ANALYSIS_FAILURE_REASON or "unknown"
                    raise RuntimeError(
                        f"scheduled analysis reported failure: {reason}"
                    )

            background_tasks = []
            if getattr(config, 'agent_event_monitor_enabled', False):
                from src.services.alert_worker import AlertWorker

                interval_minutes = max(1, getattr(config, 'agent_event_monitor_interval_minutes', 5))
                alert_worker = AlertWorker(config_provider=_reload_runtime_config)

                def event_monitor_task():
                    stats = alert_worker.run_once()
                    triggered_count = stats.get("triggered", 0)
                    if triggered_count:
                        logger.info("[EventMonitor] Lượt này kích hoạt %d cảnh báo", triggered_count)

                background_tasks.append({
                    "task": event_monitor_task,
                    "interval_seconds": interval_minutes * 60,
                    "run_immediately": True,
                    "name": "agent_event_monitor",
                })

                # Cảnh báo realtime theo tick (MQTT TVSI) nếu được bật; idempotent với FastAPI lifespan.
                from src.services.realtime_alert_runtime import start_realtime_alerts

                start_realtime_alerts(config_provider=_reload_runtime_config)

            schedule_kwargs = {
                "task": scheduled_task,
                "schedule_time": config.schedule_time,
                "run_immediately": should_run_immediately,
                "background_tasks": background_tasks,
                "schedule_time_provider": schedule_time_provider,
            }
            if hasattr(config, "schedule_times"):
                schedule_kwargs["schedule_times"] = config.schedule_times
                schedule_kwargs["schedule_times_provider"] = schedule_times_provider
            run_with_schedule(**schedule_kwargs)
            return 0

        # Mode 3: normal single run
        if config.run_immediately:
            analysis_ok = _run_analysis_with_runtime_scheduler_lock(
                config, args, stock_codes, analysis_targets
            )
            if analysis_ok is False:
                if start_serve:
                    logger.error("Chạy phân tích lúc khởi động thất bại, dịch vụ Web/API vẫn tiếp tục chạy.")
                elif _LAST_ANALYSIS_FAILURE_REASON in {
                    "no_report",
                    "empty_stock_list",
                    "report_save_failed",
                }:
                    return 1
        else:
            logger.info("Đã cấu hình không chạy phân tích ngay (RUN_IMMEDIATELY=false)")

        logger.info("\nChương trình chạy xong")

        # If the service is enabled and not in scheduled mode, keep the process running
        keep_running = start_serve and not (args.schedule or config.schedule_enabled)
        if keep_running:
            logger.info("Dịch vụ API đang chạy (nhấn Ctrl+C để thoát)...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        return 0

    except KeyboardInterrupt:
        logger.info("\nNgười dùng ngắt, chương trình thoát")
        return 130

    except Exception as e:
        logger.exception(f"Chương trình chạy thất bại: {e}")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
