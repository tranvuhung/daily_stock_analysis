# -*- coding: utf-8 -*-
"""Tầng tìm kiếm tin tức cho thị trường Việt Nam.

Hệ thống chỉ còn thị trường Việt Nam: mọi truy vấn tin tức phải là tiếng Việt,
gợi ý locale hướng về Việt Nam, và nhận diện chỉ số / chứng chỉ quỹ dựa trên
``data_provider.base`` (nguồn sự thật duy nhất).

Mock chỉ đặt ở biên provider; toàn bộ dựng truy vấn, chọn nhánh và định dạng báo
cáo vẫn chạy code thật.
"""

import sys
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

if "newspaper" not in sys.modules:  # phụ thuộc tuỳ chọn, theo quy ước sẵn có của repo
    _mock_np = MagicMock()
    _mock_np.Article = MagicMock()
    _mock_np.Config = MagicMock()
    sys.modules["newspaper"] = _mock_np

from src.search_service import SearchResponse, SearchService


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
        for code in ("HPG", "FPT", "VNM", "BSR", "VNINDEX", "VN30", "HNXIndex"):
            self.assertTrue(SearchService._is_vn_symbol(code), code)

    def test_explicit_vn_forms_are_detected(self):
        self.assertTrue(SearchService._is_vn_symbol("HPG.VN"))
        self.assertTrue(SearchService._is_vn_symbol("vnHPG"))

    def test_non_vn_shapes_are_not_claimed(self):
        # Hình dạng mã không thuộc Việt Nam không được nhận là mã VN.
        for code in ("AAPL", "600519", "00700", "7203.T"):
            self.assertFalse(SearchService._is_vn_symbol(code), code)


class VnIndexOrEtfTestCase(unittest.TestCase):
    def test_vn_indices_are_index_or_etf(self):
        for code in ("VNINDEX", "VN30", "HNXIndex", "HNXUpcomIndex"):
            self.assertTrue(SearchService.is_index_or_etf(code, code), code)

    def test_vn_fund_certificates_are_index_or_etf(self):
        for code in ("FUEVN100", "E1VFVN30", "FUEVN100.VN"):
            self.assertTrue(SearchService.is_index_or_etf(code, ""), code)

    def test_vn_stocks_are_not_index_or_etf(self):
        for code in ("HPG", "FPT", "VNM", "HPG.VN", ""):
            self.assertFalse(SearchService.is_index_or_etf(code, "Hòa Phát"), code)


class VnQueryLanguageTestCase(unittest.TestCase):
    def test_vn_stock_gets_vietnamese_queries(self):
        service = _service()
        with _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="HPG", stock_name="Hòa Phát", max_searches=5
            )

        self.assertTrue(queries)
        joined = " ".join(queries)
        self.assertIn("tin tức mới nhất", joined)
        self.assertIn("khuyến nghị giá mục tiêu", joined)
        self.assertNotIn("最新", joined)
        self.assertNotIn("latest news events", joined)

    def test_vn_index_uses_index_dimensions(self):
        service = _service()
        with _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="VNINDEX", stock_name="VN-Index", max_searches=5
            )

        joined = " ".join(queries)
        self.assertIn("tin tức mới nhất", joined)
        self.assertIn("nhận định thị trường xu hướng", joined)
        self.assertNotIn("khuyến nghị giá mục tiêu", joined)

    def test_empty_code_index_subject_still_vietnamese(self):
        # pipeline có thể truyền stock_code="" cho chỉ số; truy vấn vẫn phải tiếng Việt.
        service = _service()
        with _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="", stock_name="VN-Index", max_searches=1
            )
        self.assertEqual(queries, ["VN-Index tin tức mới nhất"])

    def test_index_subject_is_not_duplicated(self):
        # VN30 có tên trùng mã; truy vấn không được thành "VN30 VN30 ...".
        service = _service()
        with _captured_queries(service) as queries:
            service.search_comprehensive_intel(
                stock_code="VN30", stock_name="VN30", max_searches=1
            )
        self.assertTrue(queries)
        self.assertNotIn("VN30 VN30", queries[0])

    def test_search_stock_news_uses_vietnamese(self):
        service = _service()
        with _captured_queries(service) as queries:
            service.search_stock_news("HPG", "Hòa Phát")
        self.assertEqual(queries[0], "Hòa Phát HPG cổ phiếu tin tức mới nhất")

    def test_search_stock_news_without_code(self):
        service = _service()
        with _captured_queries(service) as queries:
            service.search_stock_news("", "VN-Index")
        self.assertEqual(queries[0], "VN-Index tin tức mới nhất")

    def test_stock_events_default_to_vietnamese_event_types(self):
        service = _service()
        with _captured_queries(service) as queries:
            service.search_stock_events("HPG", "Hòa Phát")
        self.assertTrue(queries)
        self.assertIn("kết quả kinh doanh", queries[0])
        self.assertTrue(queries[0].startswith("Hòa Phát ("))

    def test_topic_news_default_query_is_vietnamese(self):
        service = _service()
        with _captured_queries(service) as queries:
            service.search_topic_news("thép")
        self.assertEqual(queries, ['"thép" chứng khoán Việt Nam tin mới nhất'])


class VnLocaleTestCase(unittest.TestCase):
    def test_brave_locale_targets_vietnam(self):
        for code in ("HPG", "VNINDEX", ""):
            self.assertEqual(
                SearchService._brave_search_locale(code),
                {"search_lang": "vi", "country": "VN"},
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

    def test_report_labels_in_english(self):
        service = _service()
        payload = {
            "latest_news": SearchResponse(
                query="q", results=[], provider="tavily", success=True
            )
        }
        with patch.object(SearchService, "_resolve_report_language", return_value="en"):
            rendered = service.format_intel_report(payload, "Hoa Phat")

        self.assertIn("News search results: Hoa Phat", rendered)
        self.assertIn("Latest news", rendered)
        self.assertIn("No relevant information found", rendered)

    def test_legacy_zh_language_falls_back_to_vietnamese(self):
        fake_config = MagicMock(report_language="zh")
        with patch("src.config.get_config", return_value=fake_config):
            self.assertEqual(SearchService._resolve_report_language(), "vi")
        fake_config = MagicMock(report_language="en")
        with patch("src.config.get_config", return_value=fake_config):
            self.assertEqual(SearchService._resolve_report_language(), "en")

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
