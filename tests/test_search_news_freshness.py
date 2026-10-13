# -*- coding: utf-8 -*-
"""
Unit tests for strict news freshness filtering and strategy window logic (Issue #697).
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import SearchResponse, SearchResult, SearchService
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    current_diagnostic_snapshot,
    reset_run_diagnostic_context,
)


def _result(
    title: str,
    published_date: str | None,
    *,
    snippet: str = "snippet",
    url: str | None = None,
    source: str = "example.com",
) -> SearchResult:
    return SearchResult(
        title=title,
        snippet=snippet,
        url=url or f"https://example.com/{title}",
        source=source,
        published_date=published_date,
    )


def _response(results) -> SearchResponse:
    return SearchResponse(
        query="test",
        results=results,
        provider="Mock",
        success=True,
    )


class SearchNewsFreshnessTestCase(unittest.TestCase):
    """Tests for strategy window and strict published_date filtering."""

    def _create_service_with_mock_provider(
        self,
        *,
        news_max_age_days: int = 3,
        news_strategy_profile: str = "short",
        response: SearchResponse | None = None,
    ):
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
            news_max_age_days=news_max_age_days,
            news_strategy_profile=news_strategy_profile,
        )
        mock_search = MagicMock(
            return_value=response
            or _response([_result("default", datetime.now().date().isoformat())])
        )
        service._providers[0].search = mock_search
        return service, mock_search

    def test_effective_window_uses_profile_and_news_max_age(self) -> None:
        """window = min(profile_days, NEWS_MAX_AGE_DAYS)."""
        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="medium",  # 7
        )
        service.search_stock_news("VNM", "Vinamilk", max_results=5)
        kwargs = mock_search.call_args[1]
        self.assertEqual(kwargs["days"], 3)

    def test_search_topic_news_reuses_provider_and_freshness_without_stock_identity_filter(self) -> None:
        today = datetime.now().date().isoformat()
        service, mock_search = self._create_service_with_mock_provider(
            response=_response([
                _result(
                    "影视传媒订单与政策催化",
                    today,
                    snippet="板块近期出现新订单。",
                )
            ]),
        )

        response = service.search_topic_news("影视传媒", max_results=2)

        self.assertTrue(response.success)
        self.assertEqual(response.results[0].title, "影视传媒订单与政策催化")
        self.assertIn('"影视传媒"', mock_search.call_args.args[0])
        self.assertEqual(mock_search.call_args.kwargs["days"], 3)

    def test_invalid_profile_falls_back_to_short(self) -> None:
        """Invalid profile should fallback to short (3 days)."""
        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=30,
            news_strategy_profile="invalid_profile",
        )
        service.search_stock_news("VNM", "Vinamilk", max_results=5)
        kwargs = mock_search.call_args[1]
        self.assertEqual(kwargs["days"], 3)

    def test_search_stock_news_strict_filters(self) -> None:
        """Drop old/unknown/future+2, keep future+1 and within-window dates."""
        today = datetime.now().date()
        fresh = today.isoformat()
        old = (today - timedelta(days=30)).isoformat()
        future_1 = (today + timedelta(days=1)).isoformat()
        future_2 = (today + timedelta(days=2)).isoformat()

        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=7,
            news_strategy_profile="medium",
            response=_response(
                [
                    _result("old", old),
                    _result("unknown", None),
                    _result("future_2", future_2),
                    _result("future_1", future_1),
                    _result("fresh", fresh),
                ]
            ),
        )

        resp = service.search_stock_news("VNM", "Vinamilk", max_results=5)
        titles = [r.title for r in resp.results]
        self.assertEqual(titles, ["future_1", "fresh"])
        for item in resp.results:
            self.assertRegex(item.published_date or "", r"^\d{4}-\d{2}-\d{2}$")

    def test_search_stock_news_overfetch_before_filter(self) -> None:
        """Provider request size should be increased before filtering."""
        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        service.search_stock_news("VNM", "Vinamilk", max_results=4)
        args, kwargs = mock_search.call_args
        requested = kwargs.get("max_results")
        if requested is None:
            requested = args[1]
        self.assertEqual(requested, 8)

    def test_search_stock_news_try_next_provider_when_filtered_empty(self) -> None:
        """If provider-A passes API call but all results are filtered, continue to provider-B."""
        today = datetime.now().date()
        old = (today - timedelta(days=90)).isoformat()
        fresh = today.isoformat()

        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
            news_max_age_days=3,
            news_strategy_profile="short",
        )

        p1 = SimpleNamespace(
            is_available=True,
            name="P1",
            search=MagicMock(return_value=_response([_result("too_old", old)])),
        )
        p2 = SimpleNamespace(
            is_available=True,
            name="P2",
            search=MagicMock(return_value=_response([_result("fresh", fresh)])),
        )
        service._providers = [p1, p2]

        resp = service.search_stock_news("VNM", "Vinamilk", max_results=3)
        self.assertEqual([r.title for r in resp.results], ["fresh"])
        p1.search.assert_called_once()
        p2.search.assert_called_once()

    def test_search_stock_news_records_provider_diagnostics_for_fallback(self) -> None:
        """News search provider attempts should appear in run-flow diagnostics."""
        today = datetime.now().date()
        old = (today - timedelta(days=90)).isoformat()
        fresh = today.isoformat()
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        tavily = SimpleNamespace(
            is_available=True,
            name="Tavily",
            search=MagicMock(return_value=_response([_result("too_old", old)])),
        )
        searxng = SimpleNamespace(
            is_available=True,
            name="SearXNG",
            search=MagicMock(return_value=_response([_result("Vinamilk VNM 最新公告", fresh)])),
        )
        service._providers = [tavily, searxng]

        token = activate_run_diagnostic_context(
            trace_id="trace-news",
            task_id="task-news",
            query_id="query-news",
            stock_code="VNM",
            trigger_source="api",
        )
        try:
            response = service.search_stock_news("VNM", "Vinamilk", max_results=3)
            diagnostics = current_diagnostic_snapshot()
        finally:
            reset_run_diagnostic_context(token)

        self.assertEqual([item.title for item in response.results], ["Vinamilk VNM 最新公告"])
        provider_runs = diagnostics["provider_runs"]
        self.assertEqual([run["data_type"] for run in provider_runs], ["news_search", "news_search"])
        self.assertEqual([run["provider"] for run in provider_runs], ["Tavily", "SearXNG"])
        self.assertFalse(provider_runs[0]["success"])
        self.assertTrue(provider_runs[1]["success"])
        self.assertEqual(provider_runs[1]["record_count"], 1)

    def test_search_stock_news_keeps_first_provider_with_direct_hit(self) -> None:
        """Provider đầu tiên có tin trực tiếp thì dừng, không gọi provider sau."""
        fresh = datetime.now().date().isoformat()
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
            news_max_age_days=3,
            news_strategy_profile="short",
        )

        p1 = SimpleNamespace(
            is_available=True,
            name="P1",
            search=MagicMock(return_value=_response([_result("Hòa Phát HPG lợi nhuận quý tăng mạnh", fresh)])),
        )
        p2 = SimpleNamespace(
            is_available=True,
            name="P2",
            search=MagicMock(return_value=_response([_result("Tin ngành thép", fresh)])),
        )
        service._providers = [p1, p2]

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=3)
        self.assertEqual([r.title for r in resp.results], ["Hòa Phát HPG lợi nhuận quý tăng mạnh"])
        p1.search.assert_called_once()
        p2.search.assert_not_called()

    def test_a_share_direct_company_news_beats_sector_provider_fallback(self) -> None:
        """A-share direct company hits should beat generic sector news from earlier providers."""
        fresh = datetime.now().date().isoformat()
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
            news_max_age_days=3,
            news_strategy_profile="short",
        )

        p1 = SimpleNamespace(
            is_available=True,
            name="P1",
            search=MagicMock(
                return_value=_response(
                    [
                        _result(
                            "白酒行业景气度回暖 多只龙头上涨",
                            fresh,
                            snippet="消费板块获得资金关注。",
                        )
                    ]
                )
            ),
        )
        p2 = SimpleNamespace(
            is_available=True,
            name="P2",
            search=MagicMock(
                return_value=_response(
                    [
                        _result("沪指震荡收涨，市场情绪回暖", fresh),
                        _result(
                            "Vinamilk VNM 发布回购公告",
                            fresh,
                            snippet="Vinamilk披露公司公告，董事会审议通过回购方案。",
                            source="hose",
                        ),
                    ]
                )
            ),
        )
        service._providers = [p1, p2]

        resp = service.search_stock_news("VNM", "Vinamilk", max_results=2)

        self.assertEqual(resp.results[0].title, "Vinamilk VNM 发布回购公告")
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")
        self.assertIn("mã cổ phiếu", "; ".join(resp.results[0].relevance_reasons or []))
        p1.search.assert_called_once()
        p2.search.assert_called_once()

    def test_search_stock_news_filters_low_quality_and_zero_relevance_fillers(self) -> None:
        """Download/listing pages and zero-relevance fillers should not enter stock news context."""
        fresh = datetime.now().date().isoformat()
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        provider = SimpleNamespace(
            is_available=True,
            name="MixedNoise",
            search=MagicMock(
                return_value=_response(
                    [
                        _result(
                            "Hòa Phát HPG 极速版安装包下载",
                            fresh,
                            snippet="当前版本 686.38MB，84%好评，适合下载安装到手机。",
                            url="https://download.example.invalid/apps/douyang",
                            source="download.example.invalid",
                        ),
                        _result(
                            "1000+ 宜昌小姐上门特殊服务",
                            fresh,
                            snippet="小姐预约 yue2345，同城约炮、保健按摩、推油套餐。",
                            url="https://spam.example.invalid/local/yue2345",
                            source="spam.example.invalid",
                        ),
                        _result(
                            "美国调整关税，社群讨论升温",
                            fresh,
                            snippet="社群用户分享生活话题，与目标股票没有直接关系。",
                            url="https://news.example.invalid/lifestyle/123",
                            source="news.example.invalid",
                        ),
                        _result(
                            "Hòa Phát HPG 早盘走强",
                            fresh,
                            snippet="Hòa Phát成交活跃，港股科技板块反弹。",
                            url="https://finance.example.invalid/HPG",
                            source="finance.example.invalid",
                        ),
                    ]
                )
            ),
        )
        service._providers = [provider]

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=3)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 早盘走强"])
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_download_like_news_without_size_or_url_hints_is_filtered(self) -> None:
        """Content-only Android/download signals should still trigger low-quality filtering."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 官方版客户端安卓版下载",
                        fresh,
                        snippet="点此获取最新版安卓版客户端，支持一键下载安装包。",
                        url="https://finance.example.invalid/tencent/stock/HPG",
                        source="finance.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        source="hose",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=2)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布回购公告"])

    def test_package_security_news_does_not_trigger_download_filter(self) -> None:
        """Bare package wording in product/security news should not look like a download page."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "金山办公 688111 WPS 安装包被曝漏洞",
                        fresh,
                        snippet="公司回应 WPS 安装包安全漏洞并发布修复计划。",
                        url="https://finance.example.invalid/news/688111-security",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("688111", "金山办公", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["金山办公 688111 WPS 安装包被曝漏洞"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_rich_media_client_phrase_in_ticker_news_is_not_filtered(self) -> None:
        """Client wording used in normal headline styles should not be treated as download spam."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "证券时报客户端讯，Vinamilk VNM 发布回购公告",
                        fresh,
                        snippet="贵茅披露股票回购公告。",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("VNM", "Vinamilk", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["证券时报客户端讯，Vinamilk VNM 发布回购公告"],
        )

    def test_outer_market_phrase_is_not_filtered_as_adult_spam(self) -> None:
        """`外围市场` market-context headlines should not be treated as adult spam."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "外围市场走弱拖累科技股",
                        fresh,
                        snippet="外围市场情绪走弱，带动科技股阶段性回撤。",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["外围市场走弱拖累科技股"],
        )

    def test_url_only_app_route_does_not_drop_direct_stock_news(self) -> None:
        """App-style news hosts or paths need content evidence before admission drops."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告，成交维持活跃。",
                        url="https://app.finance.example.invalid/apps/markets/HPG",
                        source="app.finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布回购公告"])
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_rating_phrase_does_not_trigger_app_download_filter(self) -> None:
        """Analyst star ratings in ticker news should not trip the app/download filter."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "FPT gets 5 stars from analysts after earnings",
                        fresh,
                        snippet="FPT shares rose after FPT Corporation revenue guidance improved.",
                        url="https://finance.example.invalid/fpt-analyst-rating",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("FPT", "FPT Corporation", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["FPT gets 5 stars from analysts after earnings"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_app_ticker_rating_phrase_does_not_trigger_app_download_filter(self) -> None:
        """Ticker APP and analyst ratings should not look like an app listing."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "APP stock jumps after advertising platform guidance",
                        fresh,
                        snippet=(
                            "APP shares rose after analysts gave the company "
                            "5 stars for revenue momentum."
                        ),
                        url="https://finance.example.invalid/app-stock-rating",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("APP", "AppLovin", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["APP stock jumps after advertising platform guidance"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_product_game_rating_does_not_trigger_app_download_filter(self) -> None:
        """Normal game/product ratings need download/install evidence before filtering."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 新游上线获玩家评分 9.0",
                        fresh,
                        snippet="腾讯游戏新品上线首周表现强劲，玩家评分 9.0。",
                        url="https://finance.example.invalid/app/news/HPG-game-rating",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["Hòa Phát HPG 新游上线获玩家评分 9.0"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_app_download_growth_metric_does_not_trigger_download_filter(self) -> None:
        """App download/install growth metrics are business news, not app-store pages."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "拼多多 PDD Temu 应用下载量增长",
                        fresh,
                        snippet="Temu 应用安装量同比提升，带动跨境业务收入改善。",
                        url="https://finance.example.invalid/app/news/pdd-temu-downloads",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("PDD", "PDD Holdings", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["拼多多 PDD Temu 应用下载量增长"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_neutral_app_download_scale_metric_does_not_trigger_download_filter(self) -> None:
        """Neutral app download scale metrics are operating news, not download pages."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "拼多多 PDD Temu 应用下载量达1亿",
                        fresh,
                        snippet="Temu 应用下载量达1亿，市场关注跨境业务获客效率。",
                        url="https://finance.example.invalid/app/news/pdd-temu-downloads",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("PDD", "PDD Holdings", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["拼多多 PDD Temu 应用下载量达1亿"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_app_download_and_install_metric_phrases_do_not_trigger_download_filter(self) -> None:
        """Application download/install metric phrases should remain business news."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "拼多多 PDD Temu 应用下载同比增长",
                        fresh,
                        snippet="Temu 应用安装同比提升，推动跨境业务增长。",
                        url="https://finance.example.invalid/app/news/pdd-temu-install-growth",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("PDD", "PDD Holdings", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["拼多多 PDD Temu 应用下载同比增长"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_neutral_english_app_install_metric_does_not_trigger_download_filter(self) -> None:
        """Neutral English app-install scale metrics should remain business news."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "PDD Temu mobile app install base reaches 100M",
                        fresh,
                        snippet="PDD shares rose as Temu mobile app install base reaches 100M.",
                        url="https://finance.example.invalid/app/news/pdd-temu-installs",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("PDD", "PDD Holdings", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["PDD Temu mobile app install base reaches 100M"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_app_download_decline_metrics_do_not_trigger_download_filter(self) -> None:
        """Negative app download/install metrics are also business news."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "拼多多 PDD Temu 应用下载量下降",
                        fresh,
                        snippet="Temu 应用安装量下滑，市场关注获客效率。",
                        url="https://finance.example.invalid/app/news/pdd-downloads-fall",
                        source="finance.example.invalid",
                    ),
                    _result(
                        "PDD app installs fell after campaign pullback",
                        fresh,
                        snippet="PDD shares moved as app installs fell in May.",
                        url="https://finance.example.invalid/apps/pdd-installs",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("PDD", "PDD Holdings", max_results=2)

        self.assertEqual(
            [item.title for item in resp.results],
            [
                "拼多多 PDD Temu 应用下载量下降",
                "PDD app installs fell after campaign pullback",
            ],
        )
        self.assertTrue(all(item.relevance_category == "direct_company_news" for item in resp.results))

    def test_url_backed_app_rating_listing_is_filtered(self) -> None:
        """App-store URL plus version/rating metrics should be treated as listing noise."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG app store rating",
                        fresh,
                        snippet="4.8 stars, 10M downloads, version 12.8 for mobile app users.",
                        url="https://apps.example.invalid/tencent/HPG",
                        source="apps.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        url="https://finance.example.invalid/news/HPG-buyback",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布回购公告"])

    def test_app_listing_metric_with_version_rating_still_filtered(self) -> None:
        """Business metric wording should not rescue obvious app listing pages."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 下载量突破1000万",
                        fresh,
                        snippet="应用版本 12.8，评分 4.9，安装包 256MB，下载量突破1000万。",
                        url="https://apps.example.invalid/tencent/HPG/download",
                        source="apps.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 发布业绩公告",
                        fresh,
                        snippet="Hòa Phát披露季度业绩，收入与利润保持增长。",
                        url="https://finance.example.invalid/news/HPG-earnings",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布业绩公告"])

    def test_finance_client_boilerplate_does_not_trigger_download_filter(self) -> None:
        """Finance media boilerplate such as 客户端讯 should not look like an app page."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Vinamilk VNM 发布回购公告",
                        fresh,
                        snippet="证券时报客户端讯，Vinamilk披露股份回购公告。",
                        url="https://finance.example.invalid/news/VNM-buyback",
                        source="证券时报",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("VNM", "Vinamilk", max_results=1)

        self.assertEqual([item.title for item in resp.results], ["Vinamilk VNM 发布回购公告"])
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_market_peripheral_phrase_does_not_trigger_adult_spam_filter(self) -> None:
        """Finance usage of 外围市场 should not be treated as adult-service spam."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 受外围市场走弱拖累",
                        fresh,
                        snippet="外围市场走弱拖累港股科技股，Hòa Phát成交活跃。",
                        url="https://finance.example.invalid/markets/HPG",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["Hòa Phát HPG 受外围市场走弱拖累"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_business_full_service_phrase_does_not_trigger_adult_spam_filter(self) -> None:
        """Business-safe 全套服务 wording should require adult-service context."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "华能国际 600011 推出全套服务解决方案",
                        fresh,
                        snippet="公司面向能源客户提供全套服务解决方案，提升运维效率。",
                        url="https://finance.example.invalid/news/600011-service-solution",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("600011", "华能国际", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["华能国际 600011 推出全套服务解决方案"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_mobile_app_wording_does_not_count_as_adult_contact_signal(self) -> None:
        """Mobile-app wording should not look like a phone/contact handle."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "美的集团 000333 mobile-app 按摩椅业务增长",
                        fresh,
                        snippet="公司 mobile app 渠道带动按摩椅和保健业务销售增长。",
                        url="https://finance.example.invalid/mobile-app/000333-health",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("000333", "美的集团", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["美的集团 000333 mobile-app 按摩椅业务增长"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_local_service_category_cluster_does_not_trigger_adult_spam_filter(self) -> None:
        """Benign local-service categories need adult-specific anchors before filtering."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "美团 03690 推出按摩足浴会所预约套餐服务",
                        fresh,
                        snippet="美团拓展本地生活服务，新增按摩足浴会所预约套餐。",
                        url="https://finance.example.invalid/news/03690-local-service",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("03690.HK", "美团", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["美团 03690 推出按摩足浴会所预约套餐服务"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_adult_term_platform_remediation_news_is_not_filtered(self) -> None:
        """Platform enforcement/remediation articles are risk news, not service ads."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "美团 03690 下架涉色情按摩会所商家",
                        fresh,
                        snippet="美团开展平台治理，清理涉色情低俗内容商家。",
                        url="https://finance.example.invalid/news/03690-risk-remediation",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("03690.HK", "美团", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["美团 03690 下架涉色情按摩会所商家"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_contact_like_product_token_does_not_trigger_adult_spam_filter(self) -> None:
        """Product/version identifiers such as QQ2024 need adult context before filtering."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG QQ2024 开放预约",
                        fresh,
                        snippet="QQ2024 产品升级开放预约，企业通信功能增强。",
                        url="https://finance.example.invalid/products/QQ2024",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["Hòa Phát HPG QQ2024 开放预约"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_adult_contact_id_with_full_width_separator_is_filtered(self) -> None:
        """Contact IDs such as QQ：123456 should count as adult-service spam signals."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 小姐上门 QQ：123456",
                        fresh,
                        snippet="联系获取详情。",
                        url="https://spam.example.invalid/local/qq123456",
                        source="spam.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        url="https://finance.example.invalid/news/HPG-buyback",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布回购公告"])

    def test_adult_alphanumeric_contact_handle_is_filtered(self) -> None:
        """Contact handles such as 微信：abc123 should count as adult-service spam signals."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 小姐上门 微信：abc123",
                        fresh,
                        snippet="联系获取详情。",
                        url="https://spam.example.invalid/local/wechat-abc123",
                        source="spam.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        url="https://finance.example.invalid/news/HPG-buyback",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布回购公告"])

    def test_adult_phone_contact_is_filtered(self) -> None:
        """Phone contact labels should count as contact signals with adult-service context."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 小姐上门 电话13800138000",
                        fresh,
                        snippet="联系获取详情。",
                        url="https://spam.example.invalid/local/phone-13800138000",
                        source="spam.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        url="https://finance.example.invalid/news/HPG-buyback",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布回购公告"])

    def test_healthcare_phone_contact_news_does_not_trigger_adult_spam_filter(self) -> None:
        """Normal phone contacts plus healthcare category wording are not adult-service spam."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "汤臣倍健 300146 保健品业务增长 联系电话02012345678",
                        fresh,
                        snippet="公司保健品业务增长，投资者联系电话02012345678。",
                        url="https://finance.example.invalid/news/300146-healthcare",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("300146", "汤臣倍健", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["汤臣倍健 300146 保健品业务增长 联系电话02012345678"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_content_moderation_pornography_phrase_does_not_trigger_adult_spam_filter(self) -> None:
        """Content-safety/regulatory news can mention 色情 without being adult-service spam."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 加强色情低俗内容治理",
                        fresh,
                        snippet="Hòa Phát升级内容安全体系，持续治理色情低俗内容风险。",
                        url="https://finance.example.invalid/news/HPG-content-safety",
                        source="finance.example.invalid",
                    )
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=1)

        self.assertEqual(
            [item.title for item in resp.results],
            ["Hòa Phát HPG 加强色情低俗内容治理"],
        )
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")

    def test_label_only_official_source_is_honored_without_url(self) -> None:
        """Exact official labels without URL should still receive official-source treatment."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    SearchResult(
                        title="董事会公告",
                        snippet="股份回购事项。",
                        url="",
                        source="hose",
                        published_date=fresh,
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        url="https://finance.example.invalid/news/HPG-buyback",
                        source="finance.example.invalid",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=2)

        self.assertEqual(
            [item.title for item in resp.results],
            ["Hòa Phát HPG 发布回购公告", "董事会公告"],
        )
        official_result = resp.results[1]
        self.assertGreater(official_result.relevance_score or 0, 0)
        self.assertIn("Nguồn gần với kênh công bố thông tin hoặc sở giao dịch", official_result.relevance_reasons)

    def test_spoofed_official_tokens_do_not_bypass_news_admission(self) -> None:
        """Official exemptions should require trusted parsed hosts or exact source labels."""
        fresh = datetime.now().date().isoformat()
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 极速版安装包下载",
                        fresh,
                        snippet="当前版本 686.38MB，84%好评，适合下载安装到手机。",
                        url="https://spam.example.invalid/ssc.gov.vn/apps/douyang",
                        source="spam.example.invalid",
                    ),
                    _result(
                        "1000+ 宜昌小姐上门特殊服务",
                        fresh,
                        snippet="小姐预约 yue2345，同城约炮、保健按摩、推油套餐。",
                        url="https://hsx.vn.evil.invalid/local/yue2345",
                        source="hsx.vn.evil.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 官方app下载链接",
                        fresh,
                        snippet="安卓客户端下载，支持极速版下载。",
                        url="https://hsx.vn.evil.invalid/guide/officialdownload",
                        source="hose",
                    ),
                    _result(
                        "Hòa Phát HPG SEC 官方app下载链接",
                        fresh,
                        snippet="安卓客户端下载，支持极速版下载。",
                        url="https://spam.example.invalid/apps/sec-download",
                        source="ssc.gov.vn",
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        url="https://www.hsx.vn/listedco/listconews/sehk/2026/0613/example.pdf",
                        source="hose",
                    ),
                ]
            ),
        )

        resp = service.search_stock_news("HPG", "Hòa Phát", max_results=3)

        self.assertEqual([item.title for item in resp.results], ["Hòa Phát HPG 发布回购公告"])

    def test_comprehensive_intel_filters_fillers_before_prompt_context(self) -> None:
        """Admission filtering should run before per-dimension result limiting."""
        fresh = datetime.now().date().isoformat()
        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
            response=_response(
                [
                    _result(
                        "Hòa Phát HPG 极速版安装包下载",
                        fresh,
                        snippet="当前版本 686.38MB，84%好评，适合下载安装到手机。",
                        url="https://cdn.example.invalid/apps/HPG/download",
                        source="cdn.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG Android 安装包评分",
                        fresh,
                        snippet="应用版本 12.8，评分 4.9，安装后可查看行情。",
                        url="https://finance.example.invalid/tencent/HPG-rating",
                        source="finance.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG iOS 旧版下载",
                        fresh,
                        snippet="历史版本安装包 256MB，用户好评率 96%。",
                        url="https://download.example.invalid/ios/HPG",
                        source="download.example.invalid",
                    ),
                    _result(
                        "Hòa Phát HPG 发布回购公告",
                        fresh,
                        snippet="Hòa Phát披露股份回购公告。",
                        source="hose",
                    ),
                ]
            ),
        )

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel(
                stock_code="HPG",
                stock_name="Hòa Phát",
                max_searches=1,
            )

        self.assertEqual(
            [item.title for item in intel["latest_news"].results],
            ["Hòa Phát HPG 发布回购公告"],
        )
        mock_search.assert_called_once()

    def test_suffixed_stock_codes_keep_canonical_identity_terms(self) -> None:
        """Mã Việt Nam dạng hậu tố/tiền tố vẫn sinh đủ biến thể khớp danh tính."""
        cases = (
            ("HPG", {"HPG", "HPG.VN", "HOSE:HPG"}),
            ("HPG.VN", {"HPG", "HPG.VN", "HOSE:HPG"}),
            ("vnHPG", {"HPG", "HPG.VN"}),
            ("FUEVN100", {"FUEVN100", "FUEVN100.VN"}),
            ("VNINDEX", {"VNINDEX", "VN-Index"}),
        )
        for stock_code, expected_terms in cases:
            with self.subTest(stock_code=stock_code):
                terms = set(SearchService._stock_code_identity_terms(stock_code))
                self.assertTrue(expected_terms.issubset(terms), terms)

    def test_suffixed_market_codes_score_canonical_code_hits_as_direct(self) -> None:
        """Canonical code hits from suffixed inputs should be direct company news."""
        fresh = datetime.now().date().isoformat()
        cases = (
            ("HPG.VN", "HPG công bố kết quả kinh doanh quý"),
            ("vnFPT", "HOSE:FPT announces quarterly results"),
            ("VNM", "VNM.VN chia cổ tức tiền mặt"),
        )
        for stock_code, title in cases:
            with self.subTest(stock_code=stock_code):
                scored = SearchService._score_news_relevance(
                    _result(
                        title,
                        fresh,
                        snippet="Doanh nghiệp công bố lợi nhuận và cổ tức.",
                    ),
                    stock_code=stock_code,
                    stock_name="Unmatched Name",
                )
                self.assertEqual(scored.relevance_category, "direct_company_news")
                self.assertIn("mã cổ phiếu", "; ".join(scored.relevance_reasons or []))

    def test_vn_ticker_matches_before_vn_suffix(self) -> None:
        """Ticker boundaries should allow the ``.VN`` suffix and sentence punctuation."""
        match = SearchService._contains_stock_code_identity_term
        self.assertTrue(match("HPG.VN tăng trần", "HPG"))
        self.assertTrue(match("hpg.vn tăng trần", "HPG"))
        self.assertTrue(match("https://cafef.vn/hpg-lai-lon.chn", "HPG"))
        self.assertTrue(match("Cổ phiếu HPG.", "HPG"))
        self.assertTrue(match("Cổ phiếu HPG, FPT tăng", "FPT"))
        self.assertFalse(match("HPG.COM ra mắt", "HPG"))
        self.assertFalse(match("HPGX tăng", "HPG"))

    def test_common_word_vn_ticker_does_not_match_title_case_words(self) -> None:
        """Ticker trùng từ tiếng Anh (GAS) không được khớp từ viết hoa chữ đầu."""
        self.assertFalse(
            SearchService._contains_stock_code_identity_term("Gas prices climb in Asia", "GAS")
        )
        self.assertTrue(
            SearchService._contains_stock_code_identity_term("GAS báo lãi quý III", "GAS")
        )

    def test_common_word_us_ticker_does_not_match_title_case_words(self) -> None:
        """Bare alphabetic tickers should not turn ordinary words into direct hits."""
        fresh = datetime.now().date().isoformat()
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        p1 = SimpleNamespace(
            is_available=True,
            name="GenericProvider",
            search=MagicMock(
                return_value=_response(
                    [
                        _result(
                            "All investors brace for inflation data",
                            fresh,
                            snippet="Market participants watch a broad macro update.",
                        )
                    ]
                )
            ),
        )
        p2 = SimpleNamespace(
            is_available=True,
            name="CompanyProvider",
            search=MagicMock(
                return_value=_response(
                    [
                        _result(
                            "ALL Allstate quarterly earnings beat expectations",
                            fresh,
                            snippet="Allstate revenue guidance improved after quarterly results.",
                        )
                    ]
                )
            ),
        )
        service._providers = [p1, p2]

        resp = service.search_stock_news("ALL", "Allstate", max_results=1)

        self.assertEqual(resp.results[0].title, "ALL Allstate quarterly earnings beat expectations")
        self.assertEqual(resp.results[0].relevance_category, "direct_company_news")
        p1.search.assert_called_once()
        p2.search.assert_called_once()

    def test_relevance_metadata_is_visible_in_news_context(self) -> None:
        result = SearchResult(
            title="Vinamilk VNM công bố nghị quyết",
            snippet="Doanh nghiệp công bố nghị quyết hội đồng quản trị.",
            url="https://example.com/news",
            source="hose",
            published_date=datetime.now().date().isoformat(),
            relevance_score=100,
            relevance_category="direct_company_news",
            relevance_reasons=["Tiêu đề khớp mã cổ phiếu VNM", "Tiêu đề khớp tên công ty Vinamilk"],
        )
        context = SearchResponse(query="Vinamilk", results=[result], provider="Unit").to_context()

        self.assertIn("Mức liên quan", context)
        self.assertIn("direct_company_news", context)
        self.assertIn("Tiêu đề khớp mã cổ phiếu VNM", context)

    def test_search_stock_news_brave_locale_matches_market_context(self) -> None:
        """Brave locale luôn hướng về Việt Nam (vi / VN)."""
        fresh_dt = datetime.now(timezone.utc).replace(microsecond=0)
        fresh_iso = fresh_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        for stock_code, stock_name, expected_lang, expected_country, title, description in (
            ("VNM", "Vinamilk", "vi", "VN", "Vinamilk VNM tin mới", "Tóm tắt"),
            ("VNINDEX", "VN-Index", "vi", "VN", "VN-Index tăng điểm", "Tóm tắt thị trường"),
        ):
            with self.subTest(stock_code=stock_code):
                fake_response = MagicMock()
                fake_response.status_code = 200
                fake_response.json.return_value = {
                    "web": {
                        "results": [
                            {
                                "title": title,
                                "description": description,
                                "url": "https://example.com/news",
                                "age": fresh_iso,
                            }
                        ]
                    }
                }

                with patch("src.search_service.requests.get", return_value=fake_response) as mock_get:
                    service = SearchService(
                        brave_keys=["dummy_key"],
                        searxng_public_instances_enabled=False,
                        news_max_age_days=3,
                        news_strategy_profile="short",
                    )
                    resp = service.search_stock_news(stock_code, stock_name, max_results=1)

                self.assertEqual(len(resp.results), 1)
                params = mock_get.call_args.kwargs["params"]
                self.assertEqual(params["search_lang"], expected_lang)
                self.assertEqual(params["country"], expected_country)

    def test_search_comprehensive_intel_splits_strict_and_non_strict_filters(self) -> None:
        """Latest news stays strict while market analysis keeps undated results."""
        today = datetime.now().date()
        old = (today - timedelta(days=20)).isoformat()
        fresh = (today - timedelta(days=1)).isoformat()
        analysis_dt = datetime.now(timezone.utc).replace(microsecond=0)
        analysis_text = analysis_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expected_analysis_date = analysis_dt.astimezone().date().isoformat()

        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="medium",  # min(7,3)=3
        )
        mock_search.side_effect = [
            _response([_result("old", old), _result("fresh", fresh)]),
            _response([_result("analysis_unknown", None), _result("analysis_dated", analysis_text)]),
        ]
        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel(
                stock_code="VNM",
                stock_name="Vinamilk",
                max_searches=2,
            )

        self.assertEqual(
            [call[1]["days"] for call in mock_search.call_args_list],
            [3, service.ANALYTICAL_INTEL_LOOKBACK_DAYS],
        )
        for call in mock_search.call_args_list:
            self.assertEqual(call[1]["max_results"], 6)  # target 3 -> overfetch 6

        self.assertEqual([item.title for item in intel["latest_news"].results], ["fresh"])
        self.assertEqual(
            [item.title for item in intel["market_analysis"].results],
            ["analysis_unknown", "analysis_dated"],
        )
        self.assertIsNone(intel["market_analysis"].results[0].published_date)
        self.assertEqual(intel["market_analysis"].results[1].published_date, expected_analysis_date)

    def test_search_comprehensive_intel_widens_analytical_provider_windows(self) -> None:
        """Market analysis and earnings should request a longer provider lookback."""
        fresh_dt = datetime.now(timezone.utc).replace(microsecond=0)
        fresh_text = fresh_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        mock_search.side_effect = [
            _response([_result("latest_news", fresh_text)]),
            _response([_result("market_analysis", None)]),
            _response([_result("risk_check", fresh_text)]),
            _response([_result("earnings", None)]),
        ]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel(
                stock_code="VNM",
                stock_name="Vinamilk",
                max_searches=4,
            )

        self.assertIn("earnings", intel)
        self.assertEqual(
            [call[1]["days"] for call in mock_search.call_args_list],
            [
                3,
                service.ANALYTICAL_INTEL_LOOKBACK_DAYS,
                3,
                service.ANALYTICAL_INTEL_LOOKBACK_DAYS,
            ],
        )

    def test_search_comprehensive_intel_analytical_keeps_unknown_dates_and_crops_by_window(self) -> None:
        """Analytical dimensions keep unknown-date results while clipping known results to 180 days."""
        today = datetime.now().date()
        very_old = (today - timedelta(days=220)).isoformat()
        in_window = (today - timedelta(days=170)).isoformat()
        fresh_dt = datetime.now(timezone.utc).replace(microsecond=0)
        fresh_text = fresh_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        mock_search.side_effect = [
            _response([_result("latest_news", fresh_text)]),
            _response([
                _result("market_analysis_too_old", very_old),
                _result("market_analysis_unknown", None),
                _result("market_analysis_in_window", in_window),
            ]),
            _response([_result("risk_check", fresh_text)]),
            _response([
                _result("earnings_too_old", very_old),
                _result("earnings_unknown", None),
                _result("earnings_in_window", in_window),
            ]),
        ]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel(
                stock_code="VNM",
                stock_name="Vinamilk",
                max_searches=4,
            )

        self.assertEqual(
            [item.title for item in intel["market_analysis"].results],
            ["market_analysis_unknown", "market_analysis_in_window"],
        )
        self.assertIsNone(intel["market_analysis"].results[0].published_date)
        self.assertEqual(intel["market_analysis"].results[1].published_date, in_window)
        self.assertEqual(
            [item.title for item in intel["earnings"].results],
            ["earnings_unknown", "earnings_in_window"],
        )
        self.assertIsNone(intel["earnings"].results[0].published_date)
        self.assertEqual(intel["earnings"].results[1].published_date, in_window)

    def test_search_comprehensive_intel_etf_risk_check_keeps_unknown_dates(self) -> None:
        """ETF risk_check should avoid strict freshness filtering."""
        fresh_dt = datetime.now(timezone.utc).replace(microsecond=0)
        fresh_text = fresh_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expected_fresh_date = fresh_dt.astimezone().date().isoformat()

        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        mock_search.side_effect = [
            _response([_result("latest_news", fresh_text)]),
            _response([_result("market_analysis_unknown", None)]),
            _response([_result("risk_unknown", None)]),
        ]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel(
                stock_code="FUEVN100",
                stock_name="Quỹ ETF DCVFMVN100",
                max_searches=3,
            )

        self.assertEqual(intel["latest_news"].results[0].published_date, expected_fresh_date)
        self.assertEqual([item.title for item in intel["market_analysis"].results], ["market_analysis_unknown"])
        self.assertIsNone(intel["market_analysis"].results[0].published_date)
        self.assertEqual([item.title for item in intel["risk_check"].results], ["risk_unknown"])
        self.assertIsNone(intel["risk_check"].results[0].published_date)

    def test_search_comprehensive_intel_non_etf_risk_check_stays_strict(self) -> None:
        """Non-ETF risk_check should keep strict freshness filtering."""
        fresh_dt = datetime.now(timezone.utc).replace(microsecond=0)
        fresh_text = fresh_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expected_fresh_date = fresh_dt.astimezone().date().isoformat()

        service, mock_search = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        mock_search.side_effect = [
            _response([_result("latest_news", fresh_text)]),
            _response([_result("market_analysis_unknown", None)]),
            _response([_result("risk_unknown", None)]),
        ]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel(
                stock_code="VNM",
                stock_name="Vinamilk",
                max_searches=3,
            )

        self.assertEqual(intel["latest_news"].results[0].published_date, expected_fresh_date)
        self.assertEqual([item.title for item in intel["market_analysis"].results], ["market_analysis_unknown"])
        self.assertIsNone(intel["market_analysis"].results[0].published_date)
        self.assertEqual(intel["risk_check"].results, [])

    def test_effective_window_helper_has_no_side_effect(self) -> None:
        """_effective_news_window_days should not mutate stored news_window_days."""
        service, _ = self._create_service_with_mock_provider(
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        service.news_window_days = 99
        resolved = service._effective_news_window_days()
        self.assertEqual(resolved, 3)
        self.assertEqual(service.news_window_days, 99)

    def test_unix_timestamp_normalizes_to_local_date(self) -> None:
        """Unix timestamp should be converted to local date before window filtering."""
        dt_utc = datetime(2026, 3, 15, 23, 30, tzinfo=timezone.utc)
        timestamp = str(int(dt_utc.timestamp()))
        expected_local_date = dt_utc.astimezone().date()
        parsed = SearchService._normalize_news_publish_date(timestamp)
        self.assertEqual(parsed, expected_local_date)

    def test_iso_utc_string_normalizes_to_local_date(self) -> None:
        """ISO datetime with timezone should be converted to local date."""
        dt_utc = datetime(2026, 3, 15, 23, 30, tzinfo=timezone.utc)
        iso_text = "2026-03-15T23:30:00Z"
        expected_local_date = dt_utc.astimezone().date()
        parsed = SearchService._normalize_news_publish_date(iso_text)
        self.assertEqual(parsed, expected_local_date)

    def test_rfc_utc_string_normalizes_to_local_date(self) -> None:
        """RFC datetime with timezone should be converted to local date."""
        dt_utc = datetime(2026, 3, 15, 23, 30, tzinfo=timezone.utc)
        rfc_text = "Sun, 15 Mar 2026 23:30:00 +0000"
        expected_local_date = dt_utc.astimezone().date()
        parsed = SearchService._normalize_news_publish_date(rfc_text)
        self.assertEqual(parsed, expected_local_date)

    # ---- Issue #2026: foreign-ticker Chinese display name -> English alias ----
    # massif-01 on PR #2047 asked for canonical boundary unification: when
    # STOCK_NAME_MAP maps a foreign ticker to a Chinese display name, the
    # search/scoring layers must resolve the canonical English alias from the
    # single source of truth (STOCK_ENGLISH_NAME_MAP in src/data/stock_mapping.py)
    # before contacting English news providers. These tests pin the contract.

    def test_search_stock_events_reuses_search_cache(self) -> None:
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
        )
        provider = SimpleNamespace(
            is_available=True,
            name="EventProvider",
            search=MagicMock(
                return_value=_response(
                    [_result("Vinamilk年度报告", datetime.now().date().isoformat())]
                )
            ),
        )
        service._providers = [provider]

        first = service.search_stock_events("VNM", "Vinamilk")
        second = service.search_stock_events("VNM", "Vinamilk")

        self.assertIs(second, first)
        provider.search.assert_called_once()


if __name__ == "__main__":
    unittest.main()
