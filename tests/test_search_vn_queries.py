# -*- coding: utf-8 -*-
"""Tầng tìm kiếm tin tức cho thị trường Việt Nam.

Bối cảnh: ``src/search_service.py`` không hề được điều chỉnh trong đợt chuyển sang
thị trường Việt Nam. Nó chọn ngôn ngữ truy vấn bằng heuristic ký tự CJK cộng regex
ticker Mỹ ``_US_STOCK_RE = ^[A-Za-z]{1,5}(\\.[A-Za-z])?$`` — mà regex này khớp
**mọi** mã Việt Nam trần (HPG/FPT/VNM), nên mã Việt Nam bị xếp nhầm vào nhánh cổ
phiếu Mỹ; còn chỉ số Việt Nam (mã bị truyền rỗng) thì rơi xuống nhánh tiếng Trung.

Mock chỉ đặt ở biên provider; toàn bộ dựng truy vấn, chọn nhánh và định dạng báo
cáo vẫn chạy code thật.
"""

import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

if "newspaper" not in sys.modules:  # phụ thuộc tuỳ chọn, theo quy ước sẵn có của repo
    _mock_np = MagicMock()
    _mock_np.Article = MagicMock()
    _mock_np.Config = MagicMock()
    sys.modules["newspaper"] = _mock_np

from src.core import markets
from src.search_service import SearchResponse, SearchService


@contextmanager
def supported_markets(value):
    with patch.dict(os.environ, {"SUPPORTED_MARKETS": value}):
        markets.reset_cache()
        try:
            yield
        finally:
            markets.reset_cache()


def _service():
    return SearchService(
        tavily_keys=["dummy-key"],
        searxng_public_instances_enabled=False,
        news_max_age_days=3,
        news_strategy_profile="short",
    )


@contextmanager
def _captured_queries(service):
    """Bắt các truy vấn thực sự gửi tới provider."""

    queries = []
    provider = service._providers[0]

    def _fake_search(query, *args, **kwargs):
        queries.append(query)
        return SearchResponse(query=query, results=[], provider="fake", success=True)

    with patch.object(provider, "search", side_effect=_fake_search), patch.object(
        type(provider), "is_available", property(lambda self: True)
    ):
        yield queries


class VnSymbolDetectionTestCase(unittest.TestCase):
    def test_bare_vn_codes_and_indices_are_detected(self):
        with supported_markets("vn"):
            for code in ("HPG", "FPT", "VNM", "BSR", "VNINDEX", "VN30", "HNXIndex"):
                self.assertTrue(SearchService._is_vn_symbol(code), code)

    def test_explicit_vn_forms_are_detected(self):
        with supported_markets("vn,us"):
            # Dạng tường minh không phụ thuộc trạng thái cờ thị trường.
            self.assertTrue(SearchService._is_vn_symbol("HPG.VN"))
            self.assertTrue(SearchService._is_vn_symbol("vnHPG"))

    def test_other_markets_are_not_claimed(self):
        with supported_markets("vn"):
            for code in ("AAPL", "600519", "00700", "7203.T"):
                self.assertFalse(SearchService._is_vn_symbol(code), code)


class VnQueryLanguageTestCase(unittest.TestCase):
    def test_vn_stock_gets_vietnamese_queries(self):
        service = _service()
        with supported_markets("vn"), _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="HPG", stock_name="Hòa Phát", max_searches=5
            )

        self.assertTrue(queries)
        joined = " ".join(queries)
        self.assertIn("tin tức mới nhất", joined)
        self.assertIn("khuyến nghị giá mục tiêu", joined)
        # Không được lẫn truy vấn tiếng Trung hay tiếng Anh của nhánh khác.
        self.assertNotIn("最新", joined)
        self.assertNotIn("latest news events", joined)

    def test_vn_index_is_not_searched_with_chinese_terms(self):
        """Hồi quy: VNINDEX từng bị tìm bằng ``研报 目标价 评级``.

        Nguyên nhân: ``src/core/pipeline.py`` truyền ``stock_code=""`` cho mọi chỉ
        số (Story 1.5 V7, tránh đưa mã máy 6 chữ số của chỉ số A-share vào truy
        vấn), mã rỗng khiến nhánh nhận diện thất bại và rơi về bộ chiều tiếng Trung.
        """

        service = _service()
        with supported_markets("vn"), _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="VNINDEX", stock_name="VN-Index", max_searches=5
            )

        joined = " ".join(queries)
        for chinese_term in ("研报", "目标价", "评级", "上交所", "深交所", "cninfo"):
            self.assertNotIn(chinese_term, joined)
        self.assertIn("tin tức mới nhất", joined)

    def test_index_subject_is_not_duplicated(self):
        # VN30 có tên trùng mã; truy vấn không được thành "VN30 VN30 ...".
        service = _service()
        with supported_markets("vn"), _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="VN30", stock_name="VN30", max_searches=1
            )
        self.assertTrue(queries)
        self.assertNotIn("VN30 VN30", queries[0])

    def test_search_stock_news_uses_vietnamese(self):
        service = _service()
        with supported_markets("vn"), _captured_queries(service) as queries:
            service.search_stock_news("HPG", "Hòa Phát")
        self.assertTrue(queries)
        self.assertIn("tin tức mới nhất", queries[0])

    def test_us_and_a_share_queries_are_unchanged(self):
        """Chống hồi quy: nhánh VN đứng trước nên phải chứng minh không cướp mã khác."""

        service = _service()
        with supported_markets("vn,us,cn,hk"), _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="AAPL", stock_name="Apple", max_searches=1
            )
            us_query = queries[-1]
            service.search_comprehensive_intel(
                stock_code="600519", stock_name="贵州茅台", max_searches=1
            )
            cn_query = queries[-1]

        self.assertIn("latest news events", us_query)
        self.assertIn("最新", cn_query)


class VnLocaleTestCase(unittest.TestCase):
    def test_brave_locale_targets_vietnam_not_us(self):
        # ``_is_us_stock`` dùng chung ``_US_STOCK_RE`` nên HPG từng nhận country=US.
        with supported_markets("vn"):
            self.assertEqual(
                SearchService._brave_search_locale("HPG", prefer_chinese=False),
                {"search_lang": "vi", "country": "VN"},
            )

    def test_brave_locale_for_other_markets_is_unchanged(self):
        with supported_markets("vn,us"):
            self.assertEqual(
                SearchService._brave_search_locale("AAPL", prefer_chinese=False),
                {"search_lang": "en", "country": "US"},
            )
            self.assertEqual(
                SearchService._brave_search_locale("600519", prefer_chinese=True),
                {"search_lang": "zh-hans", "country": "CN"},
            )


class VnRelevanceTermsTestCase(unittest.TestCase):
    def test_vietnamese_event_terms_are_registered(self):
        # Thiếu nhóm này thì bài báo tiếng Việt không bao giờ được cộng điểm
        # "tin trực tiếp về doanh nghiệp" và luôn bị xếp xuống nhóm tin nền.
        for term in ("công bố", "cổ tức", "lợi nhuận", "báo cáo tài chính"):
            self.assertIn(term, SearchService._COMPANY_EVENT_TERMS, term)

    def test_vietnamese_exchanges_count_as_official_sources(self):
        for host in ("hsx.vn", "hnx.vn", "ssc.gov.vn"):
            self.assertIn(host, SearchService._OFFICIAL_SOURCE_HOSTS, host)


class VnIntelReportLanguageTestCase(unittest.TestCase):
    def test_report_labels_follow_report_language(self):
        service = _service()
        payload = {
            "latest_news": SearchResponse(
                query="q", results=[], provider="tavily", success=True
            )
        }
        with patch.object(SearchService, "_resolve_report_language", return_value="vi"):
            rendered = service.format_intel_report(payload, "Hòa Phát")

        self.assertIn("Kết quả tìm kiếm tin tức", rendered)
        self.assertIn("Tin mới nhất", rendered)
        self.assertNotIn("情报搜索结果", rendered)
        self.assertNotIn("未找到相关信息", rendered)

    def test_placeholder_still_returns_non_empty_text(self):
        """Giữ nguyên bẫy đã biết: zero-hit vẫn trả chuỗi không rỗng.

        ``src/services/empty_news.py`` dựa vào điều này: **không được** dùng
        news_context để suy ra "có bằng chứng tin tức hay không".
        """

        service = _service()
        payload = {
            "latest_news": SearchResponse(
                query="q", results=[], provider="tavily", success=True
            )
        }
        with patch.object(SearchService, "_resolve_report_language", return_value="vi"):
            rendered = service.format_intel_report(payload, "Hòa Phát")
        self.assertTrue(rendered.strip())


if __name__ == "__main__":
    unittest.main()
