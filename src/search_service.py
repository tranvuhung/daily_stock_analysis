# -*- coding: utf-8 -*-
"""
===================================
Vietnam Watchlist Intelligent Analysis System - Search Service Module
===================================

Responsibilities:
1. Provide a unified news search interface
2. Support multiple search engines: Anspire, Tavily, Brave, SerpAPI, SearXNG
3. Multi-key load balancing and failover
4. Search result caching and formatting
"""

import logging
import multiprocessing
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional, Tuple
from itertools import cycle
from urllib.parse import parse_qsl, unquote, urlparse
import requests
from newspaper import Article, Config
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from src.config import (
    NEWS_STRATEGY_WINDOWS,
    normalize_news_strategy_profile,
    resolve_news_window_days,
)
from src.services.run_diagnostics import record_provider_run, record_provider_run_started

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT_PROCESS_START_METHOD = "spawn"
_SEARCH_TIMEOUT_PROCESS_JOIN_GRACE_SECONDS = 1.0
_SEARCH_TIMEOUT_WORKER_SLOTS = threading.BoundedSemaphore(4)


def _terminate_search_process(process: Any) -> None:
    """Stop and reap a search subprocess, escalating to kill when needed."""
    if not process.is_alive():
        return
    process.terminate()
    process.join(_SEARCH_TIMEOUT_PROCESS_JOIN_GRACE_SECONDS)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(_SEARCH_TIMEOUT_PROCESS_JOIN_GRACE_SECONDS)


def _search_topic_news_process_worker(
    conn: Any,
    constructor_kwargs: Dict[str, Any],
    topic: str,
    max_results: int,
    focus_keywords: Optional[List[str]],
) -> None:
    """Run the provider portion of a topic-news search in an isolated process."""
    try:
        service = SearchService(**constructor_kwargs)
        response = service.search_topic_news(
            topic,
            max_results=max_results,
            focus_keywords=focus_keywords,
        )
        conn.send((True, response))
    except BaseException as exc:
        try:
            conn.send((False, exc))
        except BaseException:
            try:
                conn.send((False, RuntimeError(f"{type(exc).__name__}: {exc}")))
            except BaseException:
                pass
    finally:
        conn.close()


def _call_topic_news_in_subprocess(
    *,
    constructor_kwargs: Dict[str, Any],
    topic: str,
    max_results: int,
    focus_keywords: Optional[List[str]],
    timeout_seconds: float,
    deadline: Optional[float] = None,
) -> "SearchResponse":
    """Execute a topic-news provider chain with a hard, process-level deadline."""
    wait_seconds = max(0.01, float(timeout_seconds))
    if not _SEARCH_TIMEOUT_WORKER_SLOTS.acquire(blocking=False):
        raise RuntimeError("Topic news search concurrency is full; retry later")

    process: Any = None
    process_started = False
    parent_conn: Any = None
    child_conn: Any = None
    try:
        try:
            multiprocessing.freeze_support()
            ctx = multiprocessing.get_context(_SEARCH_TIMEOUT_PROCESS_START_METHOD)
            parent_conn, child_conn = ctx.Pipe(duplex=False)
            process = ctx.Process(
                target=_search_topic_news_process_worker,
                args=(
                    child_conn,
                    constructor_kwargs,
                    topic,
                    max_results,
                    focus_keywords,
                ),
                name="search-topic-news",
                daemon=True,
            )
            process.start()
            process_started = True
            child_conn.close()
            child_conn = None

            poll_seconds = (
                wait_seconds
                if deadline is None
                else max(0.0, deadline - time.monotonic())
            )
            if poll_seconds <= 0 or not parent_conn.poll(poll_seconds):
                _terminate_search_process(process)
                raise TimeoutError(f"Topic news search exceeded {wait_seconds:g}s; the request process was terminated")
            try:
                ok, value = parent_conn.recv()
            except EOFError as exc:
                raise RuntimeError("Topic news search process returned no result") from exc
        finally:
            try:
                if child_conn is not None:
                    child_conn.close()
            finally:
                try:
                    if parent_conn is not None:
                        parent_conn.close()
                finally:
                    if process_started:
                        try:
                            process.join(_SEARCH_TIMEOUT_PROCESS_JOIN_GRACE_SECONDS)
                        finally:
                            _terminate_search_process(process)
    finally:
        # Capacity belongs to the accepted call, not to a successfully started
        # process. Release it even if Process.start() or cleanup itself fails.
        _SEARCH_TIMEOUT_WORKER_SLOTS.release()

    if ok:
        return value
    raise value


# Transient network errors (retryable)
_SEARCH_TRANSIENT_EXCEPTIONS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_SEARCH_TRANSIENT_EXCEPTIONS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _post_with_retry(url: str, *, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> requests.Response:
    """POST with retry on transient SSL/network errors."""
    return requests.post(url, headers=headers, json=json, timeout=timeout)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_SEARCH_TRANSIENT_EXCEPTIONS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _get_with_retry(
    url: str, *, headers: Dict[str, str], params: Dict[str, Any], timeout: int
) -> requests.Response:
    """GET with retry on transient SSL/network errors."""
    return requests.get(url, headers=headers, params=params, timeout=timeout)


def fetch_url_content(url: str, timeout: int = 5) -> str:
    """
    Fetch the main text content of a URL (using newspaper3k)
    """
    try:
        # Configure newspaper3k
        config = Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        config.request_timeout = timeout
        config.fetch_images = False  # Do not download images
        config.memoize_articles = False # No caching

        # Mặc định tiếng Việt (thị trường VN); newspaper vẫn xử lý được ngôn ngữ khác.
        # Default to Vietnamese (VN market); other languages still parse fine.
        article = Article(url, config=config, language='vi')
        article.download()
        article.parse()

        # Get the main text
        text = article.text.strip()

        # Simple post-processing: remove blank lines
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)

        return text[:1500]  # Limit returned length (slightly more than bs4, since newspaper parses more cleanly)
    except Exception as e:
        logger.debug(f"Fetch content failed for {url}: {e}")

    return ""


@dataclass
class SearchResult:
    """Search result data class"""
    title: str
    snippet: str  # Summary
    url: str
    source: str  # Source website
    published_date: Optional[str] = None
    relevance_score: Optional[int] = None
    relevance_category: Optional[str] = None
    relevance_reasons: Optional[List[str]] = None
    
    def to_text(self) -> str:
        """Convert to text format"""
        date_str = f" ({self.published_date})" if self.published_date else ""
        relevance_parts: List[str] = []
        if self.relevance_category:
            relevance_parts.append(self.relevance_category)
        if self.relevance_score is not None:
            relevance_parts.append(f"score={self.relevance_score}")
        if self.relevance_reasons:
            relevance_parts.append(f"căn cứ: {'; '.join(self.relevance_reasons[:3])}")
        relevance_str = f"\nMức liên quan: {'; '.join(relevance_parts)}" if relevance_parts else ""
        return f"【{self.source}】{self.title}{date_str}\n{self.snippet}{relevance_str}"


@dataclass 
class SearchResponse:
    """Search response"""
    query: str
    results: List[SearchResult]
    provider: str  # Search engine used
    success: bool = True
    error_message: Optional[str] = None
    search_time: float = 0.0  # Search duration (seconds)
    
    def to_context(self, max_results: int = 5) -> str:
        """Convert search results into context usable for AI analysis"""
        if not self.success or not self.results:
            return f"Tìm kiếm '{self.query}' không có kết quả liên quan."
        
        lines = [f"【Kết quả tìm kiếm: {self.query}】(nguồn: {self.provider})"]
        for i, result in enumerate(self.results[:max_results], 1):
            lines.append(f"\n{i}. {result.to_text()}")
        
        return "\n".join(lines)


class BaseSearchProvider(ABC):
    """Search engine base class"""
    
    def __init__(self, api_keys: List[str], name: str):
        """
        Initialize the search engine

        Args:
            api_keys: List of API keys (multiple keys for load balancing)
            name: Search engine name
        """
        self._api_keys = api_keys
        self._name = name
        self._key_cycle = cycle(api_keys) if api_keys else None
        self._key_usage: Dict[str, int] = {key: 0 for key in api_keys}
        self._key_errors: Dict[str, int] = {key: 0 for key in api_keys}
        self._state_lock = threading.RLock()
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def is_available(self) -> bool:
        """Check whether any API key is available"""
        return bool(self._api_keys)
    
    def _get_next_key(self) -> Optional[str]:
        """
        Get the next available API key (load balancing)

        Strategy: round-robin + skip keys with too many errors
        """
        with self._state_lock:
            if not self._key_cycle:
                return None
            
            # Try at most all keys
            for _ in range(len(self._api_keys)):
                key = next(self._key_cycle)
                # Skip keys with too many errors (more than 3)
                if self._key_errors.get(key, 0) < 3:
                    return key
            
            # All keys have problems; reset error counts and return the first one
            logger.warning(f"[{self._name}] mọi API Key đều đã ghi nhận lỗi, đặt lại bộ đếm lỗi")
            self._key_errors = {key: 0 for key in self._api_keys}
            return self._api_keys[0] if self._api_keys else None
    
    def _record_success(self, key: str) -> None:
        """Record a successful use"""
        with self._state_lock:
            self._key_usage[key] = self._key_usage.get(key, 0) + 1
            # Decrease the error count after success
            if key in self._key_errors and self._key_errors[key] > 0:
                self._key_errors[key] -= 1
    
    def _record_error(self, key: str) -> None:
        """Record an error"""
        with self._state_lock:
            self._key_errors[key] = self._key_errors.get(key, 0) + 1
            error_count = self._key_errors[key]
        logger.warning(f"[{self._name}] API Key {key[:8]}... số lỗi: {error_count}")
    
    @abstractmethod
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """Perform the search (implemented by subclasses)"""
        pass
    
    def _execute_search(
        self,
        query: str,
        *,
        max_results: int = 5,
        days: int = 7,
        api_key: Optional[str] = None,
        **search_kwargs: Any,
    ) -> SearchResponse:
        """Run the shared search flow with an optional preselected API key."""
        api_key = api_key or self._get_next_key()
        if not api_key:
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=f"{self._name} API key is not configured"
            )

        start_time = time.time()
        try:
            response = self._do_search(query, api_key, max_results, days=days, **search_kwargs)
            response.search_time = time.time() - start_time

            if response.success:
                self._record_success(api_key)
                logger.info(f"[{self._name}] tìm kiếm '{query}' thành công, trả về {len(response.results)} kết quả, mất {response.search_time:.2f}s")
            else:
                self._record_error(api_key)

            return response

        except Exception as e:
            self._record_error(api_key)
            elapsed = time.time() - start_time
            logger.error(f"[{self._name}] tìm kiếm '{query}' thất bại: {e}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=str(e),
                search_time=elapsed
            )

    def search(self, query: str, max_results: int = 5, days: int = 7) -> SearchResponse:
        """
        Perform a search

        Args:
            query: Search keywords
            max_results: Max number of results to return
            days: Time range of recent days to search (default 7 days)

        Returns:
            SearchResponse object
        """
        return self._execute_search(query, max_results=max_results, days=days)


class TavilySearchProvider(BaseSearchProvider):
    """
    Tavily search engine

    Features:
    - Search API optimized for AI/LLM
    - Free tier: 1000 requests per month
    - Returns structured search results

    Docs: https://docs.tavily.com/
    """
    
    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "Tavily")
    
    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        topic: Optional[str] = None,
    ) -> SearchResponse:
        """Perform a Tavily search"""
        try:
            from tavily import TavilyClient
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="tavily-python is not installed; run: pip install tavily-python"
            )
        
        try:
            client = TavilyClient(api_key=api_key)
            
            # Perform the search (optimization: advanced depth, limited to recent days)
            search_kwargs: Dict[str, Any] = {
                "query": query,
                "search_depth": "basic",  # To control credit usage, changed advanced to basic
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
                "days": days,  # Search content from the most recent days
            }
            if topic is not None:
                search_kwargs["topic"] = topic

            response = client.search(
                **search_kwargs,
            )
            
            # Log the raw response
            logger.info(f"[Tavily] tìm kiếm xong, query='{query}', trả về {len(response.get('results', []))} kết quả")
            logger.debug(f"[Tavily] phản hồi gốc: {response}")
            
            # Parse results
            results = []
            for item in response.get('results', []):
                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=item.get('content', '')[:500],  # Truncate to first 500 characters
                    url=item.get('url', ''),
                    source=self._extract_domain(item.get('url', '')),
                    published_date=item.get('published_date') or item.get('publishedDate'),
                ))
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            error_msg = str(e)
            # Check whether this is a quota issue
            if 'rate limit' in error_msg.lower() or 'quota' in error_msg.lower():
                error_msg = f"API quota exhausted: {error_msg}"
            
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )

    def search(
        self,
        query: str,
        max_results: int = 5,
        days: int = 7,
        topic: Optional[str] = None,
    ) -> SearchResponse:
        """Perform a Tavily search, with the caller choosing whether to enable the news topic."""
        if topic is None:
            return super().search(query, max_results=max_results, days=days)

        api_key = self._get_next_key()
        if not api_key:
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=f"{self._name} API key is not configured"
            )

        start_time = time.time()
        try:
            response = self._do_search(query, api_key, max_results, days=days, topic=topic)
            response.search_time = time.time() - start_time

            if response.success:
                self._record_success(api_key)
                logger.info(f"[{self._name}] tìm kiếm '{query}' thành công, trả về {len(response.results)} kết quả, mất {response.search_time:.2f}s")
            else:
                self._record_error(api_key)

            return response

        except Exception as e:
            self._record_error(api_key)
            elapsed = time.time() - start_time
            logger.error(f"[{self._name}] tìm kiếm '{query}' thất bại: {e}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=str(e),
                search_time=elapsed
            )
    
    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract the domain from the URL as the source"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            return domain or 'Nguồn không xác định'
        except Exception:
            return 'Nguồn không xác định'


class SerpAPISearchProvider(BaseSearchProvider):
    """
    SerpAPI search engine

    Features:
    - Supports multiple search engines such as Google, Bing, Baidu
    - Free tier: 100 requests per month
    - Returns real search results

    Docs: https://serpapi.com/github-daily-stock-analysis
    """

    _ORGANIC_CONTENT_FETCH_LIMIT = 1
    _ORGANIC_CONTENT_FETCH_RANK_LIMIT = 2
    _ORGANIC_CONTENT_FETCH_TIMEOUT = 2
    _ORGANIC_SNIPPET_SUFFICIENT_LENGTH = 140
    _ORGANIC_FETCHED_PREVIEW_LENGTH = 320
    _SKIPPED_CONTENT_FETCH_SUFFIXES = (
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".webp",
        ".zip",
        ".rar",
        ".7z",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".csv",
    )
    _SKIPPED_CONTENT_FETCH_QUERY_KEYS = {
        "attachment",
        "attachment_file",
        "doc",
        "document",
        "download",
        "download_file",
        "file",
        "file_name",
        "filename",
        "file_path",
        "filepath",
        "resource",
        "resource_file",
    }
    
    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "SerpAPI")
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """Perform a SerpAPI search"""
        try:
            from serpapi import GoogleSearch
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="google-search-results is not installed; run: pip install google-search-results"
            )
        
        try:
            # Determine the time range parameter tbs
            tbs = "qdr:w"  # Default: one week
            if days <= 1:
                tbs = "qdr:d"  # Past 24 hours
            elif days <= 7:
                tbs = "qdr:w"  # Past week
            elif days <= 30:
                tbs = "qdr:m"  # Past month
            else:
                tbs = "qdr:y"  # Past year

            # Use Google search (to get Knowledge Graph, Answer Box, etc.)
            params = {
                "engine": "google",
                "q": query,
                "api_key": api_key,
                "google_domain": "google.com.vn",  # Google Việt Nam / Google Vietnam domain
                "hl": "vi",  # Giao diện tiếng Việt / Vietnamese interface
                "gl": "vn",  # Ưu tiên khu vực Việt Nam / Vietnam region preference
                "tbs": tbs,     # Time range restriction
                "num": max_results # Number of results requested; note: the Google API does not always honor it strictly
            }
            
            search = GoogleSearch(params)
            response = search.get_dict()
            
            # Log the raw response
            logger.debug(f"[SerpAPI] phản hồi gốc keys: {response.keys()}")
            
            # Parse results
            results = []
            
            # 1. Parse Knowledge Graph
            kg = response.get('knowledge_graph', {})
            if kg:
                title = kg.get('title', 'Knowledge Graph')
                desc = kg.get('description', '')
                
                # Extract extra attributes
                details = []
                for key in ['type', 'founded', 'headquarters', 'employees', 'ceo']:
                    val = kg.get(key)
                    if val:
                        details.append(f"{key}: {val}")
                        
                snippet = f"{desc}\n" + " | ".join(details) if details else desc
                
                results.append(SearchResult(
                    title=f"[Knowledge Graph] {title}",
                    snippet=snippet,
                    url=kg.get('source', {}).get('link', ''),
                    source="Google Knowledge Graph"
                ))
                
            # 2. Parse Answer Box (featured answer / quote card)
            ab = response.get('answer_box', {})
            if ab:
                ab_title = ab.get('title', 'Câu trả lời nổi bật')
                ab_snippet = ""
                
                # Finance-type answer
                if ab.get('type') == 'finance_results':
                    stock = ab.get('stock', '')
                    price = ab.get('price', '')
                    currency = ab.get('currency', '')
                    movement = ab.get('price_movement', {})
                    mv_val = movement.get('percentage', 0)
                    mv_dir = movement.get('movement', '')
                    
                    ab_title = f"[Thẻ giá] {stock}"
                    ab_snippet = f"Giá: {price} {currency}\nBiến động: {mv_dir} {mv_val}%"
                    
                    # Extract table data
                    if 'table' in ab:
                        table_data = []
                        for row in ab['table']:
                            if 'name' in row and 'value' in row:
                                table_data.append(f"{row['name']}: {row['value']}")
                        if table_data:
                            ab_snippet += "\n" + "; ".join(table_data)
                            
                # Plain text answer
                elif 'snippet' in ab:
                    ab_snippet = ab.get('snippet', '')
                    list_items = ab.get('list', [])
                    if list_items:
                        ab_snippet += "\n" + "\n".join([f"- {item}" for item in list_items])
                
                elif 'answer' in ab:
                    ab_snippet = ab.get('answer', '')
                    
                if ab_snippet:
                    results.append(SearchResult(
                        title=f"[Câu trả lời nổi bật] {ab_title}",
                        snippet=ab_snippet,
                        url=ab.get('link', '') or ab.get('displayed_link', ''),
                        source="Google Answer Box"
                    ))

            # 3. Parse Related Questions
            rqs = response.get('related_questions', [])
            for rq in rqs[:3]: # Take the first 3
                question = rq.get('question', '')
                snippet = rq.get('snippet', '')
                link = rq.get('link', '')
                
                if question and snippet:
                     results.append(SearchResult(
                        title=f"[Câu hỏi liên quan] {question}",
                        snippet=snippet,
                        url=link,
                        source="Google Related Questions"
                     ))

            # 4. Parse Organic Results
            organic_results = response.get('organic_results', [])
            organic_content_fetch_attempts = 0

            for rank, item in enumerate(organic_results[:max_results]):
                link = item.get('link', '')
                rich_extensions = self._extract_rich_snippet_extensions(item)
                snippet = self._build_organic_snippet(item, rich_extensions=rich_extensions)

                if self._should_fetch_organic_content(
                    link=link,
                    snippet=snippet,
                    rank=rank,
                    fetched_count=organic_content_fetch_attempts,
                    has_structured_summary=bool(rich_extensions),
                ):
                    organic_content_fetch_attempts += 1
                    try:
                        fetched_content = fetch_url_content(
                            link,
                            timeout=self._ORGANIC_CONTENT_FETCH_TIMEOUT,
                        )
                        if fetched_content:
                            snippet = self._merge_organic_snippet_with_content(
                                snippet,
                                fetched_content,
                            )
                    except Exception as e:
                        logger.debug(f"[SerpAPI] Fetch content failed: {e}")

                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=snippet[:1000], # Limit total length
                    url=link,
                    source=item.get('source', self._extract_domain(link)),
                    published_date=item.get('date'),
                ))

            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            error_msg = str(e)
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
    
    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract the domain from the URL"""
        try:
            parsed = urlparse(url)
            return parsed.netloc.replace('www.', '') or 'Nguồn không xác định'
        except Exception:
            return 'Nguồn không xác định'

    @classmethod
    def _normalize_organic_text(cls, value: Any) -> str:
        """Normalize SerpAPI organic text fields."""
        text = "" if value is None else str(value)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _extract_rich_snippet_extensions(cls, item: Dict[str, Any]) -> List[str]:
        """Extract existing structured summaries from rich_snippet, preferring the raw API response."""
        rich_snippet = item.get("rich_snippet")
        if not isinstance(rich_snippet, dict):
            return []

        extensions: List[str] = []
        seen: set[str] = set()

        for section in ("top", "bottom"):
            section_data = rich_snippet.get(section)
            if not isinstance(section_data, dict):
                continue

            raw_extensions = section_data.get("extensions")
            if isinstance(raw_extensions, (list, tuple, set)):
                for raw_value in raw_extensions:
                    value = cls._normalize_organic_text(raw_value)
                    if not value or value in seen:
                        continue
                    seen.add(value)
                    extensions.append(value)

            for raw_value in cls._flatten_rich_snippet_values(
                section_data.get("detected_extensions")
            ):
                if raw_value in seen:
                    continue
                seen.add(raw_value)
                extensions.append(raw_value)

        return extensions

    @classmethod
    def _flatten_rich_snippet_values(
        cls,
        value: Any,
        *,
        label: Optional[str] = None,
        allow_unlabeled_scalar: bool = False,
    ) -> List[str]:
        """Flatten rich_snippet.detected_extensions into readable text."""
        if isinstance(value, dict):
            flattened: List[str] = []
            for key, nested_value in value.items():
                flattened.extend(
                    cls._flatten_rich_snippet_values(
                        nested_value,
                        label=cls._normalize_organic_text(str(key)).replace("_", " "),
                    )
                )
            return flattened

        if isinstance(value, (list, tuple, set)):
            flattened: List[str] = []
            for nested_value in value:
                flattened.extend(
                    cls._flatten_rich_snippet_values(
                        nested_value,
                        label=label,
                        allow_unlabeled_scalar=True,
                    )
                )
            return flattened

        text = cls._normalize_organic_text(value)
        if not text:
            return []

        if label:
            return [f"{label}: {text}"]

        if allow_unlabeled_scalar:
            return [text]

        return []

    @classmethod
    def _build_organic_snippet(
        cls,
        item: Dict[str, Any],
        *,
        rich_extensions: Optional[List[str]] = None,
    ) -> str:
        """Build the organic result summary, consuming information already returned by SerpAPI first."""
        snippet = cls._normalize_organic_text(item.get("snippet", ""))
        if rich_extensions is None:
            rich_extensions = cls._extract_rich_snippet_extensions(item)

        if rich_extensions:
            rich_text = " | ".join(rich_extensions)
            if rich_text and rich_text not in snippet:
                snippet = f"{snippet}\n{rich_text}".strip() if snippet else rich_text

        return snippet

    @classmethod
    def _matches_skipped_content_fetch_suffix(cls, value: Any) -> bool:
        """Determine whether a link fragment points to an attachment or another non-HTML resource."""
        normalized_value = cls._normalize_organic_text(value).lower()
        if not normalized_value:
            return False

        decoded_value = unquote(normalized_value)
        if decoded_value.endswith(cls._SKIPPED_CONTENT_FETCH_SUFFIXES):
            return True

        return urlparse(decoded_value).path.lower().endswith(
            cls._SKIPPED_CONTENT_FETCH_SUFFIXES
        )

    @classmethod
    def _matches_skipped_content_fetch_query_param(
        cls, key: Any, value: Any
    ) -> bool:
        """Skip body fetching only for a few explicit attachment parameters, to avoid hitting normal HTML pages."""
        normalized_key = cls._normalize_organic_text(key)
        if not normalized_key:
            return False

        snake_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized_key)
        canonical_key = re.sub(r"[^a-z0-9]+", "_", snake_key.lower()).strip("_")
        if canonical_key not in cls._SKIPPED_CONTENT_FETCH_QUERY_KEYS:
            return False

        return cls._matches_skipped_content_fetch_suffix(value)

    @classmethod
    def _should_fetch_organic_content(
        cls,
        *,
        link: Any,
        snippet: str,
        rank: int,
        fetched_count: int,
        has_structured_summary: bool,
    ) -> bool:
        """Fetch the body only for a very small number of top-ranked results with clearly insufficient summaries."""
        if fetched_count >= cls._ORGANIC_CONTENT_FETCH_LIMIT:
            return False

        if rank >= cls._ORGANIC_CONTENT_FETCH_RANK_LIMIT:
            return False

        if has_structured_summary:
            return False

        if len(snippet) >= cls._ORGANIC_SNIPPET_SUFFICIENT_LENGTH:
            return False

        if not isinstance(link, str):
            return False

        if not link or not link.startswith(("http://", "https://")):
            return False

        parsed_link = urlparse(link)
        if parsed_link.scheme not in {"http", "https"}:
            return False

        if cls._matches_skipped_content_fetch_suffix(parsed_link.path):
            return False

        for key, value in parse_qsl(parsed_link.query, keep_blank_values=True):
            if cls._matches_skipped_content_fetch_query_param(key, value):
                return False

        return True

    @classmethod
    def _merge_organic_snippet_with_content(cls, snippet: str, content: str) -> str:
        """Enrich the snippet with a short body preview, avoiding longer per-search latency and response size."""
        normalized = cls._normalize_organic_text(content)
        if not normalized:
            return snippet

        preview = normalized[:cls._ORGANIC_FETCHED_PREVIEW_LENGTH]
        if len(normalized) > cls._ORGANIC_FETCHED_PREVIEW_LENGTH:
            preview = f"{preview}..."

        if snippet:
            return f"{snippet}\n\n【Chi tiết trang】\n{preview}"

        return f"【Chi tiết trang】\n{preview}"


class AnspireSearchProvider(BaseSearchProvider):
    """
    Anspire Search engine

    Features:
    - Next-generation realtime intelligent search engine for the AI ecosystem
    - Precise results and fast responses
    - Suitable for stock news and market intelligence searches

    Docs: https://open.anspire.cn/document/docs/searchApi/
    """
    
    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "Anspire")
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7, region_mode: int = 2) -> SearchResponse:
        """Perform an Anspire search"""
        try:
            import requests
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="requests is not installed; run: pip install requests"
            )
        
        try:
            # API endpoint
            url = "https://plugin.anspire.cn/api/ntsearch/search"
            
            # Request headers
            headers = {
                'Authorization': f'Bearer {api_key}'
            }

            # Request parameters
            payload = {
                "query": query,
                "top_k": min(max_results,50), 
                "FromTime": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
                "ToTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "region_mode": region_mode
            }
            
            # Perform the search
            response = _get_with_retry(url, headers=headers, params=payload, timeout=10)
            
            # Check the HTTP status code
            if response.status_code != 200:
                # Try to parse the error message
                try:
                    if response.headers.get('content-type', '').startswith('application/json'):
                        error_data = response.json()
                        error_message = error_data.get('message', response.text)
                    else:
                        error_message = response.text
                except Exception:
                    error_message = response.text
                
                # Handle by error code
                if response.status_code == 403:
                    error_msg = f"Insufficient balance or permission: {error_message}"
                elif response.status_code == 401:
                    error_msg = f"Invalid API key: {error_message}"
                elif response.status_code == 400:
                    error_msg = f"Invalid request parameters: {error_message}"
                else:
                    error_msg = f"HTTP {response.status_code}: {error_message}"
                
                logger.warning(f"[Anspire] tìm kiếm thất bại: {error_msg}")
                
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=error_msg
                )
            
            # Parse the response
            try:
                data = response.json()
            except ValueError as e:
                error_msg = f"Failed to parse JSON response: {str(e)}"
                logger.error(f"[Anspire] {error_msg}")
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=error_msg
                )
            
            if 'code' in data and data.get('code') != 200:
                error_msg = data.get('msg') or f"API returned error code: {data.get('code')}"
                logger.warning(f"[Anspire] tìm kiếm thất bại: {error_msg}")
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=error_msg
                )
            
            if 'results' not in data:
                error_msg = "Response is missing the results field"
                logger.error(f"[Anspire] {error_msg}, phản hồi gốc: {data}")
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=error_msg
                )
            
            # Log the raw response
            logger.info(f"[Anspire] tìm kiếm xong, query='{query}'")
            logger.debug(f"[Anspire] phản hồi gốc: {data}")
            
            results = []
            value_list = data.get('results', [])
            
            for item in value_list[:max_results]:
                snippet = item.get('content')
                if snippet and isinstance(snippet, str) and len(snippet) > 500:
                    snippet = snippet[:500] + "..."
                
                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=snippet,
                    url=item.get('url', ''),
                    source=self._extract_domain(item.get('url', '')),
                    published_date=item.get('date', '')
                ))
            
            logger.info(f"[Anspire] phân tích thành công {len(results)} kết quả")
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except requests.exceptions.Timeout:
            error_msg = "Request timed out"
            logger.error(f"[Anspire] {error_msg}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
        except requests.exceptions.RequestException as e:
            error_msg = f"Network request failed: {str(e)}"
            logger.error(f"[Anspire] {error_msg}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
        except Exception as e:
            error_msg = f"Unknown error: {str(e)}"
            logger.error(f"[Anspire] {error_msg}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
    
    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract the domain from the URL as the source"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            return domain or 'Nguồn không xác định'
        except Exception:
            return 'Nguồn không xác định'


class BraveSearchProvider(BaseSearchProvider):
    """
    Brave Search engine

    Features:
    - Privacy-first independent search engine
    - Index of over 30 billion pages
    - Free tier available
    - Supports time range filtering

    Docs: https://brave.com/search/api/
    """

    API_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "Brave")

    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        search_lang: Optional[str] = None,
        country: Optional[str] = None,
    ) -> SearchResponse:
        """Perform a Brave search"""
        try:
            # Request headers
            headers = {
                'X-Subscription-Token': api_key,
                'Accept': 'application/json'
            }

            # Determine the time range (freshness parameter)
            if days <= 1:
                freshness = "pd"  # Past day (24 hours)
            elif days <= 7:
                freshness = "pw"  # Past week
            elif days <= 30:
                freshness = "pm"  # Past month
            else:
                freshness = "py"  # Past year

            # Request parameters
            params = {
                "q": query,
                "count": min(max_results, 20),  # Brave supports at most 20 items
                "freshness": freshness,
                "safesearch": "moderate"
            }
            if search_lang:
                params["search_lang"] = search_lang
            if country:
                params["country"] = country

            # Perform the search (GET request)
            response = requests.get(
                self.API_ENDPOINT,
                headers=headers,
                params=params,
                timeout=10
            )

            # Check the HTTP status code
            if response.status_code != 200:
                error_msg = self._parse_error(response)
                logger.warning(f"[Brave] tìm kiếm thất bại: {error_msg}")
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=error_msg
                )

            # Parse the response
            try:
                data = response.json()
            except ValueError as e:
                error_msg = f"Failed to parse JSON response: {str(e)}"
                logger.error(f"[Brave] {error_msg}")
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=error_msg
                )

            logger.info(f"[Brave] tìm kiếm xong, query='{query}'")
            logger.debug(f"[Brave] phản hồi gốc: {data}")

            # Parse search results
            results = []
            web_data = data.get('web', {})
            web_results = web_data.get('results', [])

            for item in web_results[:max_results]:
                # Parse the publish date (ISO 8601 format)
                published_date = None
                age = item.get('age') or item.get('page_age')
                if age:
                    try:
                        # Convert ISO format to a simple date string
                        dt = datetime.fromisoformat(age.replace('Z', '+00:00'))
                        published_date = dt.strftime('%Y-%m-%d')
                    except (ValueError, AttributeError):
                        published_date = age  # Use the raw value if parsing fails

                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=item.get('description', '')[:500],  # Truncate to 500 characters
                    url=item.get('url', ''),
                    source=self._extract_domain(item.get('url', '')),
                    published_date=published_date
                ))

            logger.info(f"[Brave] phân tích thành công {len(results)} kết quả")

            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True
            )

        except requests.exceptions.Timeout:
            error_msg = "Request timed out"
            logger.error(f"[Brave] {error_msg}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
        except requests.exceptions.RequestException as e:
            error_msg = f"Network request failed: {str(e)}"
            logger.error(f"[Brave] {error_msg}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
        except Exception as e:
            error_msg = f"Unknown error: {str(e)}"
            logger.error(f"[Brave] {error_msg}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )

    def _parse_error(self, response) -> str:
        """Parse the error response"""
        try:
            if response.headers.get('content-type', '').startswith('application/json'):
                error_data = response.json()
                # Error format returned by the Brave API
                if 'message' in error_data:
                    return error_data['message']
                if 'error' in error_data:
                    return error_data['error']
                return str(error_data)
            return response.text[:200]
        except Exception:
            return f"HTTP {response.status_code}: {response.text[:200]}"

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract the domain from the URL as the source"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            return domain or 'Nguồn không xác định'
        except Exception:
            return 'Nguồn không xác định'

    def search(
        self,
        query: str,
        max_results: int = 5,
        days: int = 7,
        search_lang: Optional[str] = None,
        country: Optional[str] = None,
    ) -> SearchResponse:
        """Perform a Brave search, with caller-supplied region and language preferences."""
        if search_lang is None and country is None:
            return super().search(query, max_results=max_results, days=days)

        return self._execute_search(
            query,
            max_results=max_results,
            days=days,
            search_lang=search_lang,
            country=country,
        )


class SearXNGSearchProvider(BaseSearchProvider):
    """
    SearXNG search engine (self-hosted, no quota).

    Self-hosted instances are used when explicitly configured.
    Otherwise, the provider can lazily discover public instances from
    searx.space and rotate across them with per-request failover.
    """

    PUBLIC_INSTANCES_URL = "https://searx.space/data/instances.json"
    PUBLIC_INSTANCES_CACHE_TTL_SECONDS = 3600
    PUBLIC_INSTANCES_STALE_REFRESH_BACKOFF_SECONDS = 60
    PUBLIC_INSTANCES_POOL_LIMIT = 20
    PUBLIC_INSTANCES_MAX_ATTEMPTS = 3
    PUBLIC_INSTANCES_TIMEOUT_SECONDS = 5
    SELF_HOSTED_TIMEOUT_SECONDS = 10

    _public_instances_cache: Optional[Tuple[float, List[str]]] = None
    _public_instances_stale_retry_after: float = 0.0
    _public_instances_lock = threading.Lock()

    def __init__(
        self,
        base_urls: Optional[List[str]] = None,
        *,
        use_public_instances: bool = False,
        self_hosted_timeout_seconds: Optional[int] = None,
    ):
        normalized_base_urls = [url.rstrip("/") for url in (base_urls or []) if url.strip()]
        super().__init__(normalized_base_urls, "SearXNG")
        self._base_urls = normalized_base_urls
        if self_hosted_timeout_seconds and int(self_hosted_timeout_seconds) > 0:
            self._self_hosted_timeout_seconds = int(self_hosted_timeout_seconds)
        else:
            self._self_hosted_timeout_seconds = self.SELF_HOSTED_TIMEOUT_SECONDS
        self._use_public_instances = bool(use_public_instances and not self._base_urls)
        self._cursor = 0
        self._cursor_lock = threading.Lock()

    @property
    def is_available(self) -> bool:
        return bool(self._base_urls) or self._use_public_instances

    @classmethod
    def reset_public_instance_cache(cls) -> None:
        """Reset the shared searx.space cache (used by tests)."""
        with cls._public_instances_lock:
            cls._public_instances_cache = None
            cls._public_instances_stale_retry_after = 0.0

    @staticmethod
    def _parse_http_error(response) -> str:
        """Parse HTTP error details for easier diagnostics."""
        try:
            raw_content_type = response.headers.get("content-type", "")
            content_type = raw_content_type if isinstance(raw_content_type, str) else ""
            if "json" in content_type:
                error_data = response.json()
                if isinstance(error_data, dict):
                    message = error_data.get("error") or error_data.get("message")
                    if message:
                        return str(message)
                return str(error_data)
            raw_text = getattr(response, "text", "")
            body = raw_text.strip() if isinstance(raw_text, str) else ""
            return body[:200] if body else f"HTTP {response.status_code}"
        except Exception:
            raw_text = getattr(response, "text", "")
            body = raw_text if isinstance(raw_text, str) else ""
            return f"HTTP {response.status_code}: {body[:200]}"

    @staticmethod
    def _time_range(days: int) -> str:
        if days <= 1:
            return "day"
        if days <= 7:
            return "week"
        if days <= 30:
            return "month"
        return "year"

    @classmethod
    def _search_latency_seconds(cls, instance_data: Dict[str, Any]) -> float:
        timing = (instance_data.get("timing") or {}).get("search") or {}
        all_timing = timing.get("all")
        if isinstance(all_timing, dict):
            for key in ("mean", "median"):
                value = all_timing.get(key)
                if isinstance(value, (int, float)):
                    return float(value)
        return float("inf")

    @classmethod
    def _extract_public_instances(cls, payload: Any) -> List[str]:
        if not isinstance(payload, dict):
            return []

        instances = payload.get("instances")
        if not isinstance(instances, dict):
            return []

        ranked: List[Tuple[float, float, str]] = []
        for raw_url, item in instances.items():
            if not isinstance(raw_url, str) or not isinstance(item, dict):
                continue
            if item.get("network_type") != "normal":
                continue
            http_status = (item.get("http") or {}).get("status_code")
            if http_status != 200:
                continue
            timing = (item.get("timing") or {}).get("search") or {}
            uptime = timing.get("success_percentage")
            if not isinstance(uptime, (int, float)) or float(uptime) <= 0:
                continue

            ranked.append(
                (
                    float(uptime),
                    cls._search_latency_seconds(item),
                    raw_url.rstrip("/"),
                )
            )

        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [url for _, _, url in ranked[: cls.PUBLIC_INSTANCES_POOL_LIMIT]]

    @classmethod
    def _get_public_instances(cls) -> List[str]:
        now = time.time()
        with cls._public_instances_lock:
            stale_urls: List[str] = []
            if cls._public_instances_cache is None and cls._public_instances_stale_retry_after > now:
                logger.debug(
                    "[SearXNG] đang lùi (backoff) làm mới danh sách instance công khai lúc khởi động, còn %.0fs",
                    cls._public_instances_stale_retry_after - now,
                )
                return []
            if cls._public_instances_cache is not None:
                cached_at, cached_urls = cls._public_instances_cache
                if now - cached_at < cls.PUBLIC_INSTANCES_CACHE_TTL_SECONDS:
                    return list(cached_urls)
                stale_urls = list(cached_urls)
                if cls._public_instances_stale_retry_after > now:
                    logger.debug(
                        "[SearXNG] đang lùi (backoff) làm mới instance công khai, tiếp tục dùng cache hết hạn, còn %.0fs",
                        cls._public_instances_stale_retry_after - now,
                    )
                    return stale_urls

            try:
                response = requests.get(
                    cls.PUBLIC_INSTANCES_URL,
                    timeout=cls.PUBLIC_INSTANCES_TIMEOUT_SECONDS,
                )
                if response.status_code != 200:
                    logger.warning(
                        "[SearXNG] lấy danh sách instance công khai thất bại: HTTP %s",
                        response.status_code,
                    )
                else:
                    urls = cls._extract_public_instances(response.json())
                    if urls:
                        cls._public_instances_cache = (now, list(urls))
                        cls._public_instances_stale_retry_after = 0.0
                        logger.info("[SearXNG] đã làm mới pool instance công khai, tổng %s instance ứng viên", len(urls))
                        return list(urls)
                    logger.warning("[SearXNG] searx.space không trả về instance công khai khả dụng, giữ cache hiện có")
            except Exception as exc:
                logger.warning("[SearXNG] lấy danh sách instance công khai thất bại: %s", exc)

            if stale_urls:
                cls._public_instances_stale_retry_after = (
                    now + cls.PUBLIC_INSTANCES_STALE_REFRESH_BACKOFF_SECONDS
                )
                logger.warning(
                    "[SearXNG] làm mới instance công khai thất bại, tiếp tục dùng cache hết hạn, tổng %s instance ứng viên; "
                    "không làm mới lại trong %.0fs",
                    len(stale_urls),
                    cls.PUBLIC_INSTANCES_STALE_REFRESH_BACKOFF_SECONDS,
                )
                return stale_urls
            cls._public_instances_stale_retry_after = (
                now + cls.PUBLIC_INSTANCES_STALE_REFRESH_BACKOFF_SECONDS
            )
            logger.warning(
                "[SearXNG] làm mới instance công khai lúc khởi động thất bại, không làm mới lại trong %.0fs",
                cls.PUBLIC_INSTANCES_STALE_REFRESH_BACKOFF_SECONDS,
            )
            return []

    def _rotate_candidates(self, pool: List[str], *, max_attempts: int) -> List[str]:
        if not pool or max_attempts <= 0:
            return []
        with self._cursor_lock:
            start = self._cursor % len(pool)
            self._cursor = (self._cursor + 1) % len(pool)
        ordered = pool[start:] + pool[:start]
        return ordered[:max_attempts]

    def _do_search(  # type: ignore[override]
        self,
        query: str,
        base_url: str,
        max_results: int,
        days: int = 7,
        *,
        timeout: int,
        retry_enabled: bool,
    ) -> SearchResponse:
        """Execute one SearXNG search against a specific instance."""
        try:
            base = base_url.rstrip("/")
            search_url = base if base.endswith("/search") else base + "/search"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            params = {
                "q": query,
                "format": "json",
                "time_range": self._time_range(days),
                "pageno": 1,
            }

            request_get = _get_with_retry if retry_enabled else requests.get
            response = request_get(search_url, headers=headers, params=params, timeout=timeout)

            if response.status_code != 200:
                error_msg = self._parse_http_error(response)
                if response.status_code == 403:
                    error_msg = (
                        f"{error_msg}; the SearXNG instance may not have JSON output enabled "
                        "(check settings.yml), or the instance/proxy rejected the request"
                    )
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=error_msg,
                )

            try:
                data = response.json()
            except Exception:
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message="Failed to parse JSON response",
                )

            if not isinstance(data, dict):
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message="Invalid response format",
                )

            raw = data.get("results", [])
            if not isinstance(raw, list):
                raw = []

            results = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                url_val = item.get("url")
                if not url_val:
                    continue
                raw_published_date = item.get("publishedDate")

                snippet = (item.get("content") or item.get("description") or "")[:500]
                published_date = None
                if raw_published_date:
                    try:
                        dt = datetime.fromisoformat(raw_published_date.replace("Z", "+00:00"))
                        published_date = dt.strftime("%Y-%m-%d")
                    except (ValueError, AttributeError):
                        published_date = raw_published_date

                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        snippet=snippet,
                        url=url_val,
                        source=self._extract_domain(url_val),
                        published_date=published_date,
                    )
                )
                if len(results) >= max_results:
                    break

            return SearchResponse(query=query, results=results, provider=self.name, success=True)

        except requests.exceptions.Timeout:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="Request timed out",
            )
        except requests.exceptions.RequestException as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=f"Network request failed: {e}",
            )
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=f"Unknown error: {e}",
            )

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL as source label."""
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            domain = parsed.netloc.replace("www.", "")
            return domain or "Nguồn không xác định"
        except Exception:
            return "Nguồn không xác định"

    def search(self, query: str, max_results: int = 5, days: int = 7) -> SearchResponse:
        """Execute SearXNG search with instance rotation and per-request failover."""
        start_time = time.time()
        if self._base_urls:
            candidates = self._rotate_candidates(
                self._base_urls,
                max_attempts=len(self._base_urls),
            )
            retry_enabled = True
            timeout = self._self_hosted_timeout_seconds
            empty_error = "SearXNG chưa cấu hình instance khả dụng"
        elif self._use_public_instances:
            public_instances = self._get_public_instances()
            candidates = self._rotate_candidates(
                public_instances,
                max_attempts=min(len(public_instances), self.PUBLIC_INSTANCES_MAX_ATTEMPTS),
            )
            retry_enabled = False
            timeout = self.PUBLIC_INSTANCES_TIMEOUT_SECONDS
            empty_error = "Không lấy được instance SearXNG công khai khả dụng"
        else:
            candidates = []
            retry_enabled = False
            timeout = self.PUBLIC_INSTANCES_TIMEOUT_SECONDS
            empty_error = "SearXNG chưa cấu hình instance khả dụng"

        if not candidates:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=empty_error,
                search_time=time.time() - start_time,
            )

        errors: List[str] = []
        for base_url in candidates:
            response = self._do_search(
                query,
                base_url,
                max_results,
                days=days,
                timeout=timeout,
                retry_enabled=retry_enabled,
            )
            response.search_time = time.time() - start_time
            if response.success:
                logger.info(
                    "[%s] tìm kiếm '%s' thành công, instance=%s, trả về %s kết quả, mất %.2fs",
                    self.name,
                    query,
                    base_url,
                    len(response.results),
                    response.search_time,
                )
                return response

            errors.append(f"{base_url}: {response.error_message or 'unknown error'}")
            logger.warning("[%s] instance %s tìm kiếm thất bại: %s", self.name, base_url, response.error_message)

        elapsed = time.time() - start_time
        return SearchResponse(
            query=query,
            results=[],
            provider=self.name,
            success=False,
            error_message="; ".join(errors[:3]) if errors else empty_error,
            search_time=elapsed,
        )


class SearchService:
    """
    Search service

    Features:
    1. Manage multiple search engines
    2. Automatic failover
    3. Result aggregation and formatting
    4. Enhanced search when data sources fail (stock price, trend, etc.)
    5. Vietnam-oriented queries (Vietnamese keywords, VN locale hints)
    """

    # Mẫu từ khoá tìm kiếm tăng cường khi nguồn dữ liệu lỗi (cổ phiếu Việt Nam).
    # Enhanced search keyword templates used when data sources fail (Vietnam stocks).
    ENHANCED_SEARCH_KEYWORDS = [
        "{name} {code} giá cổ phiếu hôm nay",
        "{name} {code} diễn biến giá mới nhất",
        "{name} cổ phiếu phân tích kỹ thuật",
        "{name} {code} biểu đồ giá",
        "{name} {code} khối lượng giao dịch",
    ]
    NEWS_OVERSAMPLE_FACTOR = 2
    NEWS_OVERSAMPLE_MAX = 10
    FUTURE_TOLERANCE_DAYS = 1
    ANALYTICAL_INTEL_LOOKBACK_DAYS = 180
    ANALYTICAL_INTEL_DIMENSIONS = {"market_analysis", "earnings"}
    # Ticker dạng chữ hoa/số (HPG, FPT, VN30, FUEVN100) dùng để so khớp có biên từ.
    # Uppercase ticker-like terms (HPG, FPT, VN30, FUEVN100) matched with word boundaries.
    _TICKER_TERM_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
    _DIRECT_NEWS_CATEGORY = "direct_company_news"
    _SECTOR_NEWS_CATEGORY = "sector_related_news"
    _MACRO_NEWS_CATEGORY = "macro_market_news"
    _NEWS_CATEGORY_PRIORITY = {
        _DIRECT_NEWS_CATEGORY: 0,
        _SECTOR_NEWS_CATEGORY: 1,
        _MACRO_NEWS_CATEGORY: 2,
    }
    _AMBIGUOUS_EN_COMPANY_NAMES = {"apple", "meta", "square", "target", "gap"}
    _AMBIGUOUS_EN_CONFIRMING_EVENT_TERMS = (
        "earnings", "revenue", "profit", "guidance", "filing", "buyback",
        "dividend", "lawsuit", "merger", "acquisition",
    )
    _COMPANY_EVENT_TERMS = (
        "earnings", "revenue", "profit", "guidance", "filing",
        "shares", "stock", "buyback", "dividend", "lawsuit", "merger",
        "acquisition", "results", "quarterly", "annual", "announces", "launches",
        # Tiếng Việt: thiếu nhóm này thì bài báo Việt không bao giờ được cộng điểm
        # "tin trực tiếp về doanh nghiệp", luôn bị xếp xuống nhóm tin nền.
        "công bố", "nghị quyết", "cổ tức", "phát hành", "chào bán", "mua lại",
        "thoái vốn", "kiện", "xử phạt", "vi phạm", "lợi nhuận", "doanh thu",
        "báo cáo tài chính", "đại hội cổ đông", "hội đồng quản trị", "trúng thầu",
        "hợp tác", "niêm yết", "giao dịch nội bộ",
    )
    _SECTOR_NEWS_TERMS = (
        "sector", "industry", "peers", "competitors", "supply chain", "market share",
        "ngành", "nhóm ngành", "chuỗi cung ứng", "thị phần", "đối thủ",
    )
    _MACRO_NEWS_TERMS = (
        "market", "index", "fed", "inflation", "interest rate", "central bank",
        "nasdaq", "s&p 500", "dow jones",
        "vn-index", "vnindex", "vn30", "hnx", "upcom", "thị trường", "chỉ số",
        "chứng khoán", "vĩ mô", "lãi suất", "lạm phát", "ngân hàng nhà nước",
        "tỷ giá",
    )
    # Việt Nam: hai sàn (HOSE/HNX) và Uỷ ban Chứng khoán Nhà nước.
    # Vietnam: the two exchanges (HOSE/HNX) and the State Securities Commission.
    _OFFICIAL_SOURCE_HOSTS = (
        "hsx.vn", "hnx.vn", "ssc.gov.vn",
    )
    _OFFICIAL_SOURCE_LABELS = (
        "hose", "hnx", "sở giao dịch chứng khoán", "uỷ ban chứng khoán nhà nước",
        "ủy ban chứng khoán nhà nước",
    )
    _LOW_QUALITY_DOWNLOAD_ACTION_TERMS = (
        "下载", "安装", "下载安装", "下载安装到手机", "下载链接",
        "免费下载", "客户端下载", "应用下载", "官方app下载",
        "安装包", "apk", "download", "install", "installer",
    )
    _LOW_QUALITY_DOWNLOAD_INTENT_TERMS = (
        "安装包", "客户端下载", "应用下载", "下载安装", "下载安装到手机",
        "下载链接", "免费下载", "旧版下载", "极速版下载", "官方app下载",
    )
    _LOW_QUALITY_APP_CONTEXT_TERMS = (
        "好评", "评分", "版本", "大小", "适用年龄", "开发者", "应用",
        "ratings", "reviews", "stars", "version", "developer", "package",
    )
    _LOW_QUALITY_APP_METADATA_TERMS = (
        "版本", "大小", "适用年龄", "开发者", "应用", "应用商店",
        "安卓版", "苹果版", "官方版", "最新版", "version", "developer",
        "package", "mobile app",
    )
    _LOW_QUALITY_APP_PAGE_DETAIL_TERMS = (
        "客户端", "安卓版", "苹果版", "官方版", "最新版", "应用商店",
        "下载安装到手机", "一键下载", "旧版下载", "极速版下载",
    )
    _LOW_QUALITY_FILE_SIZE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb)\b", re.IGNORECASE)
    _LOW_QUALITY_RATING_RE = re.compile(
        r"(?:\d{1,3}\s*%\s*好评|好评率|用户评分|"
        r"(?:用户)?评分\s*[:：]?\s*(?:10|[0-9])(?:\.\d{1,2})?|"
        r"\b\d(?:\.\d)?\s*(?:stars?|ratings?|reviews?)\b)",
        re.IGNORECASE,
    )
    _LOW_QUALITY_URL_RE = re.compile(
        r"(?:^|[/_.=-])(?:download|downloads|apk|ipa|exe|dmg|installer|"
        r"software|soft|game|games|app|apps|package)(?:$|[/_.?&=-])",
        re.IGNORECASE,
    )
    _BUSINESS_APP_METRIC_RE = re.compile(
        r"(?:(?:下载量|安装量|装机量|应用下载|应用安装|app下载|app安装).{0,12}"
        r"(?:增长|同比|环比|上升|增加|提升|突破|达到|达|超过|超|累计|接近|保持|创新高|下降|下滑|减少|回落|放缓|持平|承压|低迷)|"
        r"(?:增长|同比|环比|上升|增加|提升|突破|达到|达|超过|超|累计|接近|保持|创新高|下降|下滑|减少|回落|放缓|持平|承压|低迷)"
        r".{0,12}(?:下载量|安装量|装机量|应用下载|应用安装|app下载|app安装)|"
        r"\b(?:downloads?|installs?)\b.{0,16}"
        r"\b(?:grew|growth|rose|increase|increased|surged|reached|reach|reaches|"
        r"hit|hits|topped|totaled|totalled|exceeded|exceeds|surpassed|surpasses|"
        r"fell|fall|declined|decline|decreased|dropped|drop|slowed|flat|weakened)\b|"
        r"\b(?:grew|growth|rose|increase|increased|surged|reached|reach|reaches|"
        r"hit|hits|topped|totaled|totalled|exceeded|exceeds|surpassed|surpasses|"
        r"fell|fall|declined|decline|decreased|dropped|drop|slowed|flat|weakened)\b"
        r".{0,16}\b(?:downloads?|installs?)\b)",
        re.IGNORECASE,
    )
    _ADULT_SERVICE_SPAM_STRONG_TERMS = (
        "上门特殊服务", "同城约", "约炮", "援交", "楼凤", "外围女",
        "外围服务", "包夜", "大保健", "莞式", "推油",
        "成人服务", "adult service", "escort service",
        "sex service", "call girl",
    )
    _ADULT_SERVICE_SPAM_AMBIGUOUS_TERMS = (
        "全套服务", "色情",
    )
    _ADULT_SERVICE_SPAM_CONTEXT_TERMS = (
        "小姐", "上门", "预约", "同城", "按摩", "保健", "足浴", "桑拿",
        "会所", "技师", "全套", "套餐", "vip",
    )
    _ADULT_SERVICE_SPAM_CONTACT_RE = re.compile(
        r"(?:^|[^a-z0-9])(?:yue|vx|wx|qq|wechat|weixin|微信号?|微[信讯]|"
        r"电话|手机|联系电话|tel|phone)"
        r"[-_:\s：]*[a-z0-9][a-z0-9_-]{2,}(?:[^a-z0-9]|$)",
        re.IGNORECASE,
    )
    _ADULT_SERVICE_SPAM_CONTACT_CONTEXT_TERMS = (
        "小姐", "上门", "同城", "预约",
        "全套", "包夜", "大保健", "推油",
        "约炮", "援交", "成人", "色情",
    )
    _ADULT_SERVICE_REMEDIATION_TERMS = (
        "治理", "整治", "下架", "处罚", "监管", "打击", "清理",
        "封禁", "整改", "内容安全", "低俗内容", "平台风险",
    )
    _ADULT_SERVICE_SOLICITATION_TERMS = (
        "上门", "同城", "预约", "套餐", "包夜", "大保健",
        "推油", "联系", "咨询", "加微信", "加qq", "vip",
    )

    def __init__(
        self,
        tavily_keys: Optional[List[str]] = None,
        anspire_keys: Optional[List[str]] = None,
        brave_keys: Optional[List[str]] = None,
        serpapi_keys: Optional[List[str]] = None,
        searxng_base_urls: Optional[List[str]] = None,
        searxng_public_instances_enabled: bool = False,
        searxng_timeout_seconds: Optional[int] = None,
        news_max_age_days: int = 3,
        news_strategy_profile: str = "short",
    ):
        """
        Initialize the search service

        Args:
            tavily_keys: List of Tavily API keys
            anspire_keys: List of Anspire Search API keys
            brave_keys: List of Brave Search API keys
            serpapi_keys: List of SerpAPI keys
            searxng_base_urls: List of SearXNG instance URLs (self-hosted, quota-free fallback)
            searxng_public_instances_enabled: Whether to use public SearXNG instances automatically when no self-hosted instance is configured
            news_max_age_days: Max news age (days)
            news_strategy_profile: News window strategy profile (ultra_short/short/medium/long)
        """
        self._constructor_kwargs: Dict[str, Any] = {
            "tavily_keys": list(tavily_keys or []),
            "anspire_keys": list(anspire_keys or []),
            "brave_keys": list(brave_keys or []),
            "serpapi_keys": list(serpapi_keys or []),
            "searxng_base_urls": list(searxng_base_urls or []),
            "searxng_public_instances_enabled": bool(searxng_public_instances_enabled),
            "searxng_timeout_seconds": searxng_timeout_seconds,
            "news_max_age_days": int(news_max_age_days),
            "news_strategy_profile": news_strategy_profile,
        }
        self._providers: List[BaseSearchProvider] = []
        self.news_max_age_days = max(1, news_max_age_days)
        raw_profile = (news_strategy_profile or "short").strip().lower()
        self.news_strategy_profile = normalize_news_strategy_profile(news_strategy_profile)
        if raw_profile != self.news_strategy_profile:
            logger.warning(
                "NEWS_STRATEGY_PROFILE '%s' không hợp lệ, đã lùi về 'short'",
                news_strategy_profile,
            )
        self.news_window_days = resolve_news_window_days(
            news_max_age_days=self.news_max_age_days,
            news_strategy_profile=self.news_strategy_profile,
        )
        self.news_profile_days = NEWS_STRATEGY_WINDOWS.get(
            self.news_strategy_profile,
            NEWS_STRATEGY_WINDOWS["short"],
        )

        # Khởi tạo các công cụ tìm kiếm theo thứ tự ưu tiên.
        # Initialize search engines (ordered by priority)
        # 1. Tavily (larger free quota, 1000 per month)
        if tavily_keys:
            self._providers.append(TavilySearchProvider(tavily_keys))
            logger.info("Đã cấu hình tìm kiếm Tavily, tổng %s API Key", len(tavily_keys))

        # 2. Brave Search (privacy-first, global coverage)
        if brave_keys:
            self._providers.append(BraveSearchProvider(brave_keys))
            logger.info("Đã cấu hình tìm kiếm Brave, tổng %s API Key", len(brave_keys))

        # 3. SerpAPI as a fallback (100 per month)
        if serpapi_keys:
            self._providers.append(SerpAPISearchProvider(serpapi_keys))
            logger.info("Đã cấu hình tìm kiếm SerpAPI, tổng %s API Key", len(serpapi_keys))

        # 4. SearXNG (self-hosted instances first; public instances can be auto-discovered when none are configured)
        searxng_provider = SearXNGSearchProvider(
            searxng_base_urls,
            use_public_instances=bool(searxng_public_instances_enabled and not searxng_base_urls),
            self_hosted_timeout_seconds=searxng_timeout_seconds,
        )
        if searxng_provider.is_available:
            self._providers.append(searxng_provider)
            if searxng_base_urls:
                logger.info("Đã cấu hình tìm kiếm SearXNG, tổng %s instance tự host", len(searxng_base_urls))
            else:
                logger.info("Đã bật chế độ tự khám phá instance SearXNG công khai")

        # 5. Anspire Search (optimized realtime intelligent search) — luôn đứng đầu khi được cấu hình.
        # Anspire is inserted first whenever configured.
        if anspire_keys:
            self._providers.insert(0, AnspireSearchProvider(anspire_keys))
            logger.info("Đã cấu hình tìm kiếm Anspire Search, tổng %s API Key", len(anspire_keys))

        if not self._providers:
            logger.warning("Chưa cấu hình công cụ tìm kiếm nào, tính năng tìm tin tức sẽ không khả dụng")

        # In-memory search result cache: {cache_key: (timestamp, SearchResponse)}
        self._cache: Dict[str, Tuple[float, 'SearchResponse']] = {}
        self._cache_lock = threading.RLock()
        self._cache_inflight: Dict[str, threading.Event] = {}
        # Default cache TTL in seconds (10 minutes)
        self._cache_ttl: int = 600
        logger.info(
            "Đã bật chiến lược độ mới tin tức: profile=%s, profile_days=%s, NEWS_MAX_AGE_DAYS=%s, effective_window=%s",
            self.news_strategy_profile,
            self.news_profile_days,
            self.news_max_age_days,
            self.news_window_days,
        )

    @staticmethod
    def _vn_code_helpers() -> Optional[Dict[str, Any]]:
        """Nạp trễ các hàm nhận diện mã Việt Nam từ ``data_provider.base``.

        Import trễ theo đúng quy ước sẵn có trong repo, tránh kéo cả
        ``data_provider.base`` vào thời điểm import module này.
        Lazily import the Vietnam code helpers to keep module import light.
        """

        try:
            from data_provider import base as vn_base
        except Exception:  # pragma: no cover - chỉ xảy ra khi lắp ghép lỗi
            return None
        return {
            "is_vn_market": vn_base._is_vn_market,
            "is_vn_index_code": vn_base.is_vn_index_code,
            "is_vn_fund_code": vn_base.is_vn_fund_code,
            "normalize_stock_code": vn_base.normalize_stock_code,
        }

    @classmethod
    def _is_vn_symbol(cls, stock_code: str, stock_name: str = "") -> bool:
        """Mã này có thuộc thị trường Việt Nam (cổ phiếu / chỉ số / quỹ) hay không.

        Tái dùng ``data_provider.base`` làm nguồn sự thật thay vì thêm regex riêng.
        Reuses ``data_provider.base`` as the single source of truth.
        """

        helpers = cls._vn_code_helpers()
        if helpers is None:
            return False
        for candidate in (stock_code, stock_name):
            text = (candidate or "").strip()
            if not text:
                continue
            if helpers["is_vn_index_code"](text) or helpers["is_vn_market"](text):
                return True
        return False

    @classmethod
    def _brave_search_locale(cls, stock_code: str = "") -> Dict[str, str]:
        """Gợi ý locale cho Brave: hệ thống chỉ phục vụ thị trường Việt Nam.

        Brave locale hints: the system only serves the Vietnam market, so every
        query (stock, index or topic) is biased towards Vietnamese results.
        """
        return {"search_lang": "vi", "country": "VN"}

    @classmethod
    def is_index_or_etf(cls, stock_code: str, stock_name: str) -> bool:
        """
        Xác định mã là chỉ số thị trường Việt Nam hay chứng chỉ quỹ ETF / quỹ Việt Nam.
        Judge if symbol is a Vietnam market index or a Vietnam ETF / fund certificate.
        For such symbols, analysis focuses on index movement only, not issuer company risks.
        """
        raw = (stock_code or '').strip()
        if not raw:
            return False
        helpers = cls._vn_code_helpers()
        if helpers is None:
            return False
        # Chỉ số Việt Nam (VNINDEX / VN30 / HNXIndex / HNXUpcomIndex ...): mã chính là
        # tên gọi nên đi thẳng qua bảng đăng ký thay vì suy đoán theo hình dạng.
        # Vietnam indices are looked up in the registry directly.
        if helpers["is_vn_index_code"](raw):
            return True
        # Chứng chỉ quỹ ETF / quỹ đóng Việt Nam (FUEVN100, E1VFVN30, ...), chấp nhận
        # cả dạng hậu tố ``.VN`` / tiền tố ``vn``.
        # Vietnam fund certificates, including ``.VN`` suffix / ``vn`` prefix forms.
        code = helpers["normalize_stock_code"](raw)
        return bool(helpers["is_vn_fund_code"](code))

    @property
    def is_available(self) -> bool:
        """Check whether any search engine is available"""
        return any(p.is_available for p in self._providers)

    def _cache_key(self, query: str, max_results: int, days: int) -> str:
        """Build a cache key from query parameters."""
        return f"{query}|{max_results}|{days}"

    def _get_cached_locked(self, key: str) -> Optional['SearchResponse']:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, response = entry
        if time.time() - ts > self._cache_ttl:
            self._cache.pop(key, None)
            return None
        logger.debug(f"Search cache hit: {key[:60]}...")
        return response

    def _get_cached(self, key: str) -> Optional['SearchResponse']:
        """Return cached SearchResponse if still valid, else None."""
        with self._cache_lock:
            return self._get_cached_locked(key)

    def _get_cached_or_reserve(
        self,
        key: str,
    ) -> Tuple[Optional['SearchResponse'], bool, Optional[threading.Event]]:
        with self._cache_lock:
            cached = self._get_cached_locked(key)
            if cached is not None:
                return cached, False, None

            event = self._cache_inflight.get(key)
            if event is None:
                event = threading.Event()
                self._cache_inflight[key] = event
                return None, True, event
            return None, False, event

    def _release_cache_fill(self, key: str, event: threading.Event) -> None:
        with self._cache_lock:
            current = self._cache_inflight.get(key)
            if current is event:
                self._cache_inflight.pop(key, None)
                event.set()

    def _wait_for_cached(
        self,
        key: str,
        event: threading.Event,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Optional['SearchResponse']:
        wait_seconds = (
            max(1.0, min(float(self._cache_ttl), 30.0))
            if timeout_seconds is None
            else max(0.0, float(timeout_seconds))
        )
        event.wait(timeout=wait_seconds)
        return self._get_cached(key)

    def _get_cached_or_wait_for_reservation(
        self,
        key: str,
        *,
        deadline: Optional[float] = None,
    ) -> Tuple[Optional['SearchResponse'], bool, Optional[threading.Event], bool]:
        """Return a cache hit or exclusive ownership of one cache fill.

        Waiters never proceed to provider work without becoming the owner. If
        an owner finishes without a cacheable response, all waiters compete for
        the next reservation and the losers keep waiting on that new owner.
        """
        waited = False
        while True:
            cached, cache_owner, cache_event = self._get_cached_or_reserve(key)
            if cached is not None or cache_owner:
                return cached, cache_owner, cache_event, waited
            if cache_event is None:  # Defensive: the reservation API promises an event here.
                raise RuntimeError("Search cache request coalescing is in an invalid state")
            waited = True
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Topic news search waited past the call deadline")
            if remaining is None:
                cached = self._wait_for_cached(key, cache_event)
            else:
                cached = self._wait_for_cached(key, cache_event, timeout_seconds=remaining)
            if cached is not None:
                return cached, False, None, waited
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Topic news search waited past the call deadline")

    def _put_cache(self, key: str, response: 'SearchResponse') -> None:
        """Store a successful SearchResponse in cache."""
        with self._cache_lock:
            # Hard cap: evict oldest entries when cache exceeds limit
            _MAX_CACHE_SIZE = 500
            if len(self._cache) >= _MAX_CACHE_SIZE:
                now = time.time()
                # First pass: remove expired entries
                expired = [k for k, (ts, _) in self._cache.items() if now - ts > self._cache_ttl]
                for k in expired:
                    self._cache.pop(k, None)
                # Second pass: if still over limit, evict oldest entries (FIFO)
                if len(self._cache) >= _MAX_CACHE_SIZE:
                    excess = len(self._cache) - _MAX_CACHE_SIZE + 1
                    oldest = sorted(self._cache.keys(), key=lambda k: self._cache[k][0])[:excess]
                    for k in oldest:
                        self._cache.pop(k, None)
            self._cache[key] = (time.time(), response)

    def _effective_news_window_days(self) -> int:
        """Resolve effective news window from strategy profile and global max-age."""
        return resolve_news_window_days(
            news_max_age_days=self.news_max_age_days,
            news_strategy_profile=self.news_strategy_profile,
        )

    @classmethod
    def _provider_request_size(cls, max_results: int) -> int:
        """Apply light overfetch before time filtering to avoid sparse outputs."""
        target = max(1, int(max_results))
        return max(target, min(target * cls.NEWS_OVERSAMPLE_FACTOR, cls.NEWS_OVERSAMPLE_MAX))

    @staticmethod
    def _append_unique(values: List[str], value: Optional[str]) -> None:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)

    @classmethod
    def _stock_code_identity_terms(cls, stock_code: str) -> List[str]:
        """Các biến thể mã chứng khoán Việt Nam được tính là trùng khớp danh tính mạnh.

        Return Vietnam code/ticker variants that should count as strong identity
        hits: bare ticker (``HPG``), ``.VN`` suffix form and exchange-prefixed
        forms (``HOSE:HPG`` / ``HNX:HPG`` / ``UPCOM:HPG``).
        """
        raw = (stock_code or "").strip()
        if not raw:
            return []

        terms: List[str] = []
        helpers = cls._vn_code_helpers()
        code = raw.upper()
        if helpers is not None:
            code = (helpers["normalize_stock_code"](raw) or raw).strip().upper()

        cls._append_unique(terms, code)
        if helpers is not None and helpers["is_vn_index_code"](code):
            # Chỉ số: báo chí thường viết "VN-Index" thay cho mã VNINDEX.
            # Indices: the press usually writes "VN-Index" instead of VNINDEX.
            if code == "VNINDEX":
                cls._append_unique(terms, "VN-Index")
            return terms

        cls._append_unique(terms, f"{code}.VN")
        for exchange in ("HOSE", "HNX", "UPCOM"):
            cls._append_unique(terms, f"{exchange}:{code}")
        return terms

    @classmethod
    def _company_identity_terms(cls, stock_name: str) -> List[str]:
        """Return conservative company-name variants for relevance matching."""
        raw = (stock_name or "").strip()
        if not raw:
            return []

        terms: List[str] = []
        cls._append_unique(terms, raw)

        without_market_suffix = re.sub(r"[-－（(].*$", "", raw).strip()
        cls._append_unique(terms, without_market_suffix)

        # Bỏ tiền tố loại hình doanh nghiệp Việt Nam ("Công ty Cổ phần", "Tập đoàn", ...).
        # Strip Vietnamese legal-form prefixes so "Tập đoàn Hòa Phát" also matches "Hòa Phát".
        cleaned = re.sub(
            r"^(công ty cổ phần|công ty cp|ctcp|tổng công ty|tập đoàn|ngân hàng tmcp)\s+",
            "",
            without_market_suffix,
            flags=re.IGNORECASE,
        ).strip()
        cleaned = re.sub(
            r"\b(incorporated|inc|corporation|corp|company|co|plc|ltd|limited|holdings?|jsc)\.?$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        if len(cleaned) >= 3:
            cls._append_unique(terms, cleaned)

        return terms

    @classmethod
    def _contains_identity_term(cls, text: str, term: str) -> bool:
        if not text or not term:
            return False

        lower_text = text.lower()
        lower_term = term.lower()
        if lower_term.startswith("$"):
            return lower_term in lower_text

        pattern = r"(?<![A-Za-z0-9])" + re.escape(lower_term) + r"(?![A-Za-z0-9])"
        return bool(re.search(pattern, lower_text))

    @classmethod
    def _contains_stock_code_identity_term(cls, text: str, term: str) -> bool:
        if not text or not term:
            return False

        if cls._TICKER_TERM_RE.match(term):
            # Ticker trần: chỉ khớp dạng viết hoa toàn bộ hoặc thường toàn bộ (URL),
            # cho phép hậu tố ``.VN``; tránh khớp nhầm từ thường viết hoa chữ đầu.
            # Bare ticker: match all-upper or all-lower (URL) forms, allow ``.VN``.
            ticker_pattern = f"(?:{re.escape(term)}|{re.escape(term.lower())})"
            pattern = (
                r"(?<![A-Za-z0-9$:.])"
                + ticker_pattern
                + r"(?=$|[^A-Za-z0-9.]|\.(?:VN|vn)\b|\.(?![A-Za-z0-9]))"
            )
            return bool(re.search(pattern, text))

        return cls._contains_identity_term(text, term)

    @classmethod
    def _contains_any_news_term(cls, text: str, terms: Tuple[str, ...]) -> bool:
        lower = (text or "").lower()
        return any(term.lower() in lower for term in terms)

    @classmethod
    def _contains_any_low_quality_news_term(cls, text: str, terms: Tuple[str, ...]) -> bool:
        lower = (text or "").lower()
        if not lower:
            return False

        for term in terms:
            normalized_term = term.lower()
            if not normalized_term:
                continue
            if normalized_term.isascii() and re.search(r"[a-z0-9]", normalized_term):
                pattern = r"(?<![A-Za-z0-9])" + re.escape(normalized_term) + r"(?![A-Za-z0-9])"
                if re.search(pattern, lower):
                    return True
                continue
            if normalized_term in lower:
                return True
        return False

    @staticmethod
    def _candidate_hostname(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if not raw or re.search(r"\s", raw):
            return ""

        parse_value = (
            raw
            if re.match(r"^[a-z][a-z0-9+.-]*://", raw) or raw.startswith("//")
            else f"//{raw}"
        )
        return (urlparse(parse_value).hostname or "").rstrip(".")

    @staticmethod
    def _source_resembles_hostname(value: Any) -> bool:
        raw = str(value or "").strip().lower()
        if not raw or re.search(r"\s", raw):
            return False
        if re.match(r"^[a-z][a-z0-9+.-]*://", raw) or raw.startswith("//"):
            return True
        return bool(re.search(r"\.[a-z0-9-]{2,}(?::\d+)?/?$", raw))

    @classmethod
    def _is_trusted_official_news_source(cls, item: SearchResult) -> bool:
        """Only trust official exemptions from trusted hosts; fallback to labels only when URL host is absent."""
        url_host = cls._candidate_hostname(item.url)
        source_label = str(item.source or "").strip().lower()
        source_host = (
            cls._candidate_hostname(item.source)
            if cls._source_resembles_hostname(item.source)
            else ""
        )

        if url_host:
            # When a URL is present, use the URL host, to avoid official allowlisting via a spoofed source label/host.
            return any(
                url_host == official_host or url_host.endswith(f".{official_host}")
                for official_host in cls._OFFICIAL_SOURCE_HOSTS
            )

        if source_host:
            return any(
                source_host == official_host or source_host.endswith(f".{official_host}")
                for official_host in cls._OFFICIAL_SOURCE_HOSTS
            )

        return source_label in cls._OFFICIAL_SOURCE_LABELS

    @classmethod
    def _has_low_quality_news_page_signal(cls, item: SearchResult) -> bool:
        """Detect app/download/listing pages without relying on a domain blocklist."""
        content_text = " ".join(filter(None, [item.title, item.snippet])).lower()
        parsed_url = urlparse(item.url or "")
        url_surface = unquote(
            " ".join(filter(None, [parsed_url.netloc, parsed_url.path, parsed_url.query]))
        ).lower()

        has_app_context = cls._contains_any_low_quality_news_term(
            content_text,
            cls._LOW_QUALITY_APP_CONTEXT_TERMS,
        )
        has_app_metadata = cls._contains_any_low_quality_news_term(
            content_text,
            cls._LOW_QUALITY_APP_METADATA_TERMS,
        )
        has_download_action = cls._contains_any_low_quality_news_term(
            content_text,
            cls._LOW_QUALITY_DOWNLOAD_ACTION_TERMS,
        )
        has_download_intent = cls._contains_any_low_quality_news_term(
            content_text,
            cls._LOW_QUALITY_DOWNLOAD_INTENT_TERMS,
        )
        has_app_page_detail = cls._contains_any_low_quality_news_term(
            content_text,
            cls._LOW_QUALITY_APP_PAGE_DETAIL_TERMS,
        )
        has_file_size = bool(cls._LOW_QUALITY_FILE_SIZE_RE.search(content_text))
        has_rating = bool(cls._LOW_QUALITY_RATING_RE.search(content_text))
        has_url_signal = bool(cls._LOW_QUALITY_URL_RE.search(url_surface))
        has_business_app_metric = bool(cls._BUSINESS_APP_METRIC_RE.search(content_text))
        has_app_listing_detail = (
            has_file_size
            or has_rating
            or cls._contains_any_low_quality_news_term(
                content_text,
                (
                    "版本", "适用年龄", "开发者", "应用商店", "安卓版",
                    "苹果版", "官方版", "最新版", "version", "developer",
                    "package",
                ),
            )
        )
        has_strong_app_page_evidence = (
            has_app_listing_detail
            and (
                has_url_signal
                or has_download_intent
                or (has_download_action and has_app_metadata)
            )
        )
        has_business_app_metric_only = (
            has_business_app_metric
            and not has_strong_app_page_evidence
        )
        has_app_listing_context = (
            not has_business_app_metric_only
            and has_app_context
            and has_app_metadata
            and (has_download_action or has_download_intent)
            and (has_file_size or has_rating)
        )
        has_content_download_page = (
            not has_business_app_metric_only
            and (
                (has_download_intent and (has_app_page_detail or has_file_size or has_rating))
                or (has_download_action and (has_app_metadata or has_file_size))
            )
        )
        has_url_backed_download_page = (
            not has_business_app_metric_only
            and has_url_signal
            and (
                has_file_size
                or has_download_intent
                or (has_download_action and has_app_metadata)
                or (has_app_metadata and has_rating)
            )
        )

        return (
            has_content_download_page
            or has_app_listing_context
            or has_url_backed_download_page
        )

    @classmethod
    def _has_adult_service_spam_news_page_signal(cls, item: SearchResult) -> bool:
        """Detect adult-service spam by content signals instead of domain names."""
        combined_text = " ".join(
            filter(None, [item.title, item.snippet, item.source, item.url])
        ).lower()

        if cls._contains_any_news_term(
            combined_text,
            cls._ADULT_SERVICE_SPAM_STRONG_TERMS,
        ):
            return True
        has_contact_signal = bool(cls._ADULT_SERVICE_SPAM_CONTACT_RE.search(combined_text))
        has_remediation_context = cls._contains_any_news_term(
            combined_text,
            cls._ADULT_SERVICE_REMEDIATION_TERMS,
        )
        if has_remediation_context and not has_contact_signal:
            return False

        if (
            "外围" in combined_text
            and cls._contains_any_news_term(
                combined_text,
                ("上门", "同城", "约炮", "援交", "包夜", "大保健", "推油", "小姐", "技师"),
            )
        ):
            return True

        context_hits = sum(
            1
            for term in cls._ADULT_SERVICE_SPAM_CONTEXT_TERMS
            if term.lower() in combined_text
        )
        has_service_anchor = cls._contains_any_news_term(
            combined_text,
            ("小姐", "按摩", "足浴", "桑拿", "会所", "技师"),
        )
        has_adult_specific_anchor = cls._contains_any_news_term(
            combined_text,
            (
                "小姐", "约炮", "援交", "楼凤", "外围", "包夜",
                "大保健", "莞式", "推油", "成人", "色情",
            ),
        )
        if has_contact_signal:
            return has_adult_specific_anchor and cls._contains_any_news_term(
                combined_text,
                cls._ADULT_SERVICE_SPAM_CONTACT_CONTEXT_TERMS,
            )
        has_solicitation_signal = cls._contains_any_news_term(
            combined_text,
            cls._ADULT_SERVICE_SOLICITATION_TERMS,
        )
        has_ambiguous_adult_phrase = cls._contains_any_news_term(
            combined_text,
            cls._ADULT_SERVICE_SPAM_AMBIGUOUS_TERMS,
        )
        if has_ambiguous_adult_phrase:
            return has_service_anchor and has_solicitation_signal

        return (
            has_adult_specific_anchor
            and has_service_anchor
            and has_solicitation_signal
            and context_hits >= 3
        )

    @classmethod
    def _score_news_relevance(
        cls,
        item: SearchResult,
        *,
        stock_code: str,
        stock_name: str,
    ) -> SearchResult:
        """Attach conservative, explainable relevance metadata to one news item."""
        title = item.title or ""
        snippet = item.snippet or ""
        url = item.url or ""
        source = item.source or ""
        full_text = " ".join([title, snippet, url, source])

        score = 0
        direct_signal = 0
        reasons: List[str] = []
        has_stock_code_signal = False
        has_unambiguous_company_signal = False
        has_ambiguous_company_signal = False

        def add_reason(reason: str) -> None:
            if reason not in reasons and len(reasons) < 5:
                reasons.append(reason)

        for term in cls._stock_code_identity_terms(stock_code):
            if cls._contains_stock_code_identity_term(title, term):
                score += 55
                direct_signal += 55
                has_stock_code_signal = True
                add_reason(f"Tiêu đề khớp mã cổ phiếu {term}")
                break
        else:
            for term in cls._stock_code_identity_terms(stock_code):
                if cls._contains_stock_code_identity_term(snippet, term):
                    score += 34
                    direct_signal += 34
                    has_stock_code_signal = True
                    add_reason(f"Tóm tắt khớp mã cổ phiếu {term}")
                    break
            else:
                for term in cls._stock_code_identity_terms(stock_code):
                    if cls._contains_stock_code_identity_term(url, term):
                        score += 18
                        direct_signal += 18
                        has_stock_code_signal = True
                        add_reason(f"Liên kết khớp mã cổ phiếu {term}")
                        break

        for term in cls._company_identity_terms(stock_name):
            ambiguous_en = term.lower() in cls._AMBIGUOUS_EN_COMPANY_NAMES
            title_score = 26 if ambiguous_en else 45
            snippet_score = 16 if ambiguous_en else 28
            if cls._contains_identity_term(title, term):
                score += title_score
                direct_signal += title_score
                if ambiguous_en:
                    has_ambiguous_company_signal = True
                else:
                    has_unambiguous_company_signal = True
                add_reason(f"Tiêu đề khớp tên công ty {term}")
                break
            if cls._contains_identity_term(snippet, term):
                score += snippet_score
                direct_signal += snippet_score
                if ambiguous_en:
                    has_ambiguous_company_signal = True
                else:
                    has_unambiguous_company_signal = True
                add_reason(f"Tóm tắt khớp tên công ty {term}")
                break

        has_company_event = cls._contains_any_news_term(full_text, cls._COMPANY_EVENT_TERMS)
        if has_company_event and direct_signal > 0:
            score += 12
            ambiguous_name_only = (
                has_ambiguous_company_signal
                and not has_stock_code_signal
                and not has_unambiguous_company_signal
            )
            has_confirming_event = cls._contains_any_news_term(
                full_text,
                cls._AMBIGUOUS_EN_CONFIRMING_EVENT_TERMS,
            )
            if not ambiguous_name_only or has_confirming_event:
                direct_signal += 12
            add_reason("Khớp từ khoá sự kiện doanh nghiệp (công bố/báo cáo tài chính/giao dịch)")

        if cls._is_trusted_official_news_source(item):
            score += 8
            add_reason("Nguồn gần với kênh công bố thông tin hoặc sở giao dịch")

        has_sector_signal = cls._contains_any_news_term(full_text, cls._SECTOR_NEWS_TERMS)
        has_macro_signal = cls._contains_any_news_term(full_text, cls._MACRO_NEWS_TERMS)

        if direct_signal >= 38:
            category = cls._DIRECT_NEWS_CATEGORY
        elif has_macro_signal and not direct_signal:
            category = cls._MACRO_NEWS_CATEGORY
            score = max(0, score - 12)
            add_reason("Không khớp danh tính doanh nghiệp, xếp vào tin vĩ mô/thị trường")
        else:
            category = cls._SECTOR_NEWS_CATEGORY
            if has_sector_signal:
                score += 6
                add_reason("Chỉ khớp bối cảnh ngành")
            else:
                add_reason("Không khớp mã cổ phiếu hoặc tên công ty, hạ xuống tin nền")

        score = max(0, min(100, score))
        return SearchResult(
            title=item.title,
            snippet=item.snippet,
            url=item.url,
            source=item.source,
            published_date=item.published_date,
            relevance_score=score,
            relevance_category=category,
            relevance_reasons=reasons,
        )

    @classmethod
    def _rank_news_response(
        cls,
        response: SearchResponse,
        *,
        stock_code: str,
        stock_name: str,
        max_results: int,
        log_scope: str,
    ) -> SearchResponse:
        """Score and sort news so direct company items are not crowded out."""
        if not response.success or not response.results:
            return response

        scored_results = [
            cls._score_news_relevance(item, stock_code=stock_code, stock_name=stock_name)
            for item in response.results
        ]

        indexed_results = list(enumerate(scored_results))

        def sort_key(entry: Tuple[int, SearchResult]) -> Tuple[int, int, int]:
            index, result = entry
            category = result.relevance_category or cls._SECTOR_NEWS_CATEGORY
            category_rank = cls._NEWS_CATEGORY_PRIORITY.get(category, 9)
            score = result.relevance_score or 0
            return (category_rank, -score, index)

        ranked_results = [result for _, result in sorted(indexed_results, key=sort_key)]
        limited_results = ranked_results[:max_results]
        category_counts = {
            cls._DIRECT_NEWS_CATEGORY: 0,
            cls._SECTOR_NEWS_CATEGORY: 0,
            cls._MACRO_NEWS_CATEGORY: 0,
        }
        for result in limited_results:
            if result.relevance_category in category_counts:
                category_counts[result.relevance_category] += 1
        if limited_results:
            top = limited_results[0]
            logger.info(
                "[Mức liên quan tin tức] %s: direct=%s, sector=%s, macro=%s, top_score=%s, top_category=%s, reasons=%s",
                log_scope,
                category_counts[cls._DIRECT_NEWS_CATEGORY],
                category_counts[cls._SECTOR_NEWS_CATEGORY],
                category_counts[cls._MACRO_NEWS_CATEGORY],
                top.relevance_score,
                top.relevance_category,
                "; ".join(top.relevance_reasons or []),
            )

        return SearchResponse(
            query=response.query,
            results=limited_results,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        )

    @classmethod
    def _filter_ranked_news_for_context(
        cls,
        response: SearchResponse,
        *,
        log_scope: str,
    ) -> SearchResponse:
        """Drop obvious non-news pages and zero-relevance fillers from ranked results."""
        if not response.success or not response.results:
            return response

        candidates: List[SearchResult] = []
        dropped_low_quality = 0
        dropped_adult_spam = 0
        dropped_zero_relevance = 0

        for item in response.results:
            is_official_source = cls._is_trusted_official_news_source(item)
            if (
                not is_official_source
                and cls._has_low_quality_news_page_signal(item)
            ):
                dropped_low_quality += 1
                continue
            if (
                not is_official_source
                and cls._has_adult_service_spam_news_page_signal(item)
            ):
                dropped_adult_spam += 1
                continue
            candidates.append(item)

        meaningful_candidates = [
            item
            for item in candidates
            if item.relevance_category == cls._DIRECT_NEWS_CATEGORY
            or (item.relevance_score or 0) > 0
        ]
        if meaningful_candidates:
            dropped_zero_relevance = len(candidates) - len(meaningful_candidates)
            filtered_results = meaningful_candidates
        else:
            filtered_results = candidates

        if dropped_low_quality or dropped_adult_spam or dropped_zero_relevance:
            logger.info(
                "[Lọc chất lượng tin] %s: provider=%s, total=%s, kept=%s, "
                "drop_low_quality=%s, drop_adult_spam=%s, drop_zero_relevance=%s",
                log_scope,
                response.provider,
                len(response.results),
                len(filtered_results),
                dropped_low_quality,
                dropped_adult_spam,
                dropped_zero_relevance,
            )

        return SearchResponse(
            query=response.query,
            results=filtered_results,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        )

    @classmethod
    def _news_relevance_stats(
        cls,
        response: SearchResponse,
    ) -> Dict[str, int]:
        results = response.results if response and response.results else []
        return {
            "direct_count": sum(
                1 for item in results if item.relevance_category == cls._DIRECT_NEWS_CATEGORY
            ),
            "max_score": max((item.relevance_score or 0 for item in results), default=0),
            "result_count": len(results),
        }

    @classmethod
    def _is_better_ranked_news_response(
        cls,
        candidate: SearchResponse,
        *,
        candidate_stats: Dict[str, int],
        best_response: Optional[SearchResponse],
        best_stats: Optional[Dict[str, int]],
    ) -> bool:
        if best_response is None or best_stats is None:
            return True
        if candidate_stats["direct_count"] != best_stats["direct_count"]:
            return candidate_stats["direct_count"] > best_stats["direct_count"]
        if candidate_stats["max_score"] != best_stats["max_score"]:
            return candidate_stats["max_score"] > best_stats["max_score"]
        return candidate_stats["result_count"] > best_stats["result_count"]

    @staticmethod
    def _parse_relative_news_date(text: str, now: datetime) -> Optional[date]:
        """Phân tích chuỗi thời gian tương đối tiếng Việt / tiếng Anh.

        Parse common Vietnamese/English relative-time strings
        (``3 giờ trước`` / ``hôm qua`` / ``2 hours ago``).
        """
        raw = (text or "").strip()
        if not raw:
            return None

        lower = raw.lower()
        if lower in {"hôm nay", "vừa xong", "today", "just now", "now"}:
            return now.date()
        if lower in {"hôm qua", "yesterday"}:
            return (now - timedelta(days=1)).date()
        if lower == "hôm kia":
            return (now - timedelta(days=2)).date()

        vi = re.match(r"^\s*(\d+)\s*(phút|giờ|tiếng|ngày|tuần|tháng|năm)\s*trước\s*$", lower)
        if vi:
            amount = int(vi.group(1))
            unit = vi.group(2)
            if unit == "phút":
                return (now - timedelta(minutes=amount)).date()
            if unit in {"giờ", "tiếng"}:
                return (now - timedelta(hours=amount)).date()
            if unit == "ngày":
                return (now - timedelta(days=amount)).date()
            if unit == "tuần":
                return (now - timedelta(weeks=amount)).date()
            if unit == "tháng":
                return (now - timedelta(days=amount * 30)).date()
            if unit == "năm":
                return (now - timedelta(days=amount * 365)).date()

        en = re.match(
            r"^\s*(\d+)\s*(minute|minutes|min|mins|hour|hours|day|days|week|weeks|month|months|year|years)\s*ago\s*$",
            lower,
        )
        if en:
            amount = int(en.group(1))
            unit = en.group(2)
            if unit in {"minute", "minutes", "min", "mins"}:
                return (now - timedelta(minutes=amount)).date()
            if unit in {"hour", "hours"}:
                return (now - timedelta(hours=amount)).date()
            if unit in {"day", "days"}:
                return (now - timedelta(days=amount)).date()
            if unit in {"week", "weeks"}:
                return (now - timedelta(weeks=amount)).date()
            if unit in {"month", "months"}:
                return (now - timedelta(days=amount * 30)).date()
            if unit in {"year", "years"}:
                return (now - timedelta(days=amount * 365)).date()

        return None

    @classmethod
    def _normalize_news_publish_date(cls, value: Any) -> Optional[date]:
        """Normalize provider date value into a date object."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                local_tz = datetime.now().astimezone().tzinfo or timezone.utc
                return value.astimezone(local_tz).date()
            return value.date()
        if isinstance(value, date):
            return value

        text = str(value).strip()
        if not text:
            return None
        now = datetime.now()
        local_tz = now.astimezone().tzinfo or timezone.utc

        relative_date = cls._parse_relative_news_date(text, now)
        if relative_date:
            return relative_date

        # Unix timestamp fallback
        if text.isdigit() and len(text) in (10, 13):
            try:
                ts = int(text[:10]) if len(text) == 13 else int(text)
                # Provider timestamps are typically UTC epoch seconds.
                # Normalize to local date to keep window checks aligned with local "today".
                return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(local_tz).date()
            except (OSError, OverflowError, ValueError):
                pass

        iso_candidate = text.replace("Z", "+00:00")
        try:
            parsed_iso = datetime.fromisoformat(iso_candidate)
            if parsed_iso.tzinfo is not None:
                return parsed_iso.astimezone(local_tz).date()
            return parsed_iso.date()
        except ValueError:
            pass

        normalized = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE)

        try:
            parsed_rfc = parsedate_to_datetime(normalized)
            if parsed_rfc:
                if parsed_rfc.tzinfo is not None:
                    return parsed_rfc.astimezone(local_tz).date()
                return parsed_rfc.date()
        except (TypeError, ValueError):
            pass

        ymd_match = re.search(r"(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})", text)
        if ymd_match:
            try:
                return date(int(ymd_match.group(1)), int(ymd_match.group(2)), int(ymd_match.group(3)))
            except ValueError:
                pass

        # Báo chí Việt Nam dùng ngày/tháng/năm (12/10/2026, 12-10-2026 08:30).
        # Vietnamese press uses day/month/year order.
        dmy_match = re.search(r"(?<!\d)(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})(?!\d)", text)
        if dmy_match:
            try:
                return date(int(dmy_match.group(3)), int(dmy_match.group(2)), int(dmy_match.group(1)))
            except ValueError:
                pass

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d",
            "%Y%m%d",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d %b %Y",
            "%d %B %Y",
            "%a, %d %b %Y %H:%M:%S %z",
        ):
            try:
                parsed_dt = datetime.strptime(normalized, fmt)
                if parsed_dt.tzinfo is not None:
                    return parsed_dt.astimezone(local_tz).date()
                return parsed_dt.date()
            except ValueError:
                continue

        return None

    def _filter_news_response(
        self,
        response: SearchResponse,
        *,
        search_days: int,
        max_results: int,
        log_scope: str,
        keep_unknown: bool = False,
    ) -> SearchResponse:
        """Hard-filter results by published_date recency and normalize date strings."""
        if not response.success or not response.results:
            return response

        today = datetime.now().date()
        earliest = today - timedelta(days=max(0, int(search_days) - 1))
        latest = today + timedelta(days=self.FUTURE_TOLERANCE_DAYS)

        filtered: List[SearchResult] = []
        dropped_unknown = 0
        dropped_old = 0
        dropped_future = 0

        for item in response.results:
            published = self._normalize_news_publish_date(item.published_date)
            if published is None:
                if keep_unknown:
                    filtered.append(
                        SearchResult(
                            title=item.title,
                            snippet=item.snippet,
                            url=item.url,
                            source=item.source,
                            published_date=item.published_date,
                            relevance_score=item.relevance_score,
                            relevance_category=item.relevance_category,
                            relevance_reasons=item.relevance_reasons,
                        )
                    )
                    if len(filtered) >= max_results:
                        break
                    continue
                dropped_unknown += 1
                continue
            if published < earliest:
                dropped_old += 1
                continue
            if published > latest:
                dropped_future += 1
                continue

            filtered.append(
                SearchResult(
                    title=item.title,
                    snippet=item.snippet,
                    url=item.url,
                    source=item.source,
                    published_date=published.isoformat(),
                    relevance_score=item.relevance_score,
                    relevance_category=item.relevance_category,
                    relevance_reasons=item.relevance_reasons,
                )
            )
            if len(filtered) >= max_results:
                break

        if dropped_unknown or dropped_old or dropped_future:
            logger.info(
                "[Lọc độ mới tin tức] %s: provider=%s, total=%s, kept=%s, drop_unknown=%s, drop_old=%s, drop_future=%s, window=[%s,%s]",
                log_scope,
                response.provider,
                len(response.results),
                len(filtered),
                dropped_unknown,
                dropped_old,
                dropped_future,
                earliest.isoformat(),
                latest.isoformat(),
            )

        return SearchResponse(
            query=response.query,
            results=filtered,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        )

    def _normalize_and_limit_response(
        self,
        response: SearchResponse,
        *,
        max_results: int,
    ) -> SearchResponse:
        """Normalize parseable dates without enforcing freshness filtering."""
        if not response.success or not response.results:
            return response

        normalized_results: List[SearchResult] = []
        for item in response.results[:max_results]:
            normalized_date = self._normalize_news_publish_date(item.published_date)
            normalized_results.append(
                SearchResult(
                    title=item.title,
                    snippet=item.snippet,
                    url=item.url,
                    source=item.source,
                    published_date=(
                        normalized_date.isoformat() if normalized_date is not None else item.published_date
                    ),
                    relevance_score=item.relevance_score,
                    relevance_category=item.relevance_category,
                    relevance_reasons=item.relevance_reasons,
                )
            )

        return SearchResponse(
            query=response.query,
            results=normalized_results,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        )

    @staticmethod
    def _limit_search_response(
        response: SearchResponse,
        *,
        max_results: int,
    ) -> SearchResponse:
        """Trim response results without changing the rest of the metadata."""
        if not response.success or not response.results:
            return response

        limited_results = response.results[:max_results]
        if len(limited_results) == len(response.results):
            return response

        return SearchResponse(
            query=response.query,
            results=limited_results,
            provider=response.provider,
            success=response.success,
            error_message=response.error_message,
            search_time=response.search_time,
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((time.monotonic() - started_at) * 1000))

    @staticmethod
    def _record_news_search_run(
        *,
        provider: str,
        operation: str,
        success: bool,
        latency_ms: Optional[int] = None,
        record_count: Optional[int] = None,
        cache_hit: Optional[bool] = None,
        error_type: Optional[str] = None,
        error_message: Optional[Any] = None,
    ) -> None:
        record_provider_run(
            data_type="news_search",
            provider=provider,
            operation=operation,
            success=success,
            latency_ms=latency_ms,
            error_type=error_type,
            error_message=error_message,
            cache_hit=cache_hit,
            record_count=record_count,
        )

    @staticmethod
    def _default_topic_query(topic_text: str) -> str:
        """Truy vấn mặc định cho tin chủ đề/ngành khi không có từ khoá trọng tâm.

        Default topic/sector news query (Vietnam market) when no focus keywords are given.
        """
        return f'"{topic_text}" chứng khoán Việt Nam tin mới nhất'

    def search_topic_news(
        self,
        topic: str,
        max_results: int = 5,
        focus_keywords: Optional[List[str]] = None,
    ) -> SearchResponse:
        """Search recent topic/sector news without applying single-stock identity filters."""
        topic_text = (topic or "").strip()
        if not topic_text or not self.is_available:
            return SearchResponse(
                query=topic_text,
                results=[],
                provider="None",
                success=False,
                error_message="Search is not configured or the topic is empty",
            )

        search_days = self._effective_news_window_days()
        provider_max_results = self._provider_request_size(max_results)
        query_terms = [str(item).strip() for item in (focus_keywords or []) if str(item).strip()]
        query = " ".join(query_terms) if query_terms else self._default_topic_query(topic_text)
        cache_key = self._cache_key(f"topic_news:{topic_text}:{query}", max_results, search_days)
        cached, cache_owner, cache_event, _waited = self._get_cached_or_wait_for_reservation(cache_key)
        if cached is not None:
            return cached

        had_provider_success = False
        try:
            for provider in self._providers:
                if not provider.is_available:
                    continue
                search_kwargs: Dict[str, Any] = {}
                if isinstance(provider, TavilySearchProvider):
                    search_kwargs["topic"] = "news"
                elif isinstance(provider, BraveSearchProvider):
                    search_kwargs.update(self._brave_search_locale(""))

                started_at = time.monotonic()
                try:
                    record_provider_run_started(
                        data_type="news_search",
                        provider=provider.name,
                        operation="search_topic_news",
                    )
                    response = provider.search(query, provider_max_results, days=search_days, **search_kwargs)
                except Exception as exc:
                    self._record_news_search_run(
                        provider=provider.name,
                        operation="search_topic_news",
                        success=False,
                        latency_ms=self._elapsed_ms(started_at),
                        error_type=type(exc).__name__,
                        error_message=exc,
                    )
                    logger.warning("%s tìm tin chủ đề thất bại: %s, thử công cụ tiếp theo", provider.name, exc)
                    continue

                had_provider_success = had_provider_success or bool(response.success)
                filtered = self._filter_news_response(
                    response,
                    search_days=search_days,
                    max_results=provider_max_results,
                    log_scope=f"{topic_text}:{provider.name}:topic_news",
                )
                if filtered.success and filtered.results:
                    limited = self._limit_search_response(filtered, max_results=max_results)
                    self._record_news_search_run(
                        provider=provider.name,
                        operation="search_topic_news",
                        success=True,
                        latency_ms=self._elapsed_ms(started_at),
                        record_count=len(limited.results or []),
                    )
                    self._put_cache(cache_key, limited)
                    return limited

                self._record_news_search_run(
                    provider=provider.name,
                    operation="search_topic_news",
                    success=False,
                    latency_ms=self._elapsed_ms(started_at),
                    record_count=0,
                    error_type="NoUsableNews",
                    error_message=response.error_message or "No usable topic news after filtering",
                )

            return SearchResponse(
                query=query,
                results=[],
                provider="Filtered" if had_provider_success else "None",
                success=had_provider_success,
                error_message=None if had_provider_success else "All search engines are unavailable or failed",
            )
        finally:
            if cache_owner and cache_event is not None:
                self._release_cache_fill(cache_key, cache_event)

    def search_topic_news_bounded(
        self,
        topic: str,
        max_results: int = 5,
        focus_keywords: Optional[List[str]] = None,
        *,
        timeout_seconds: float = 12.0,
    ) -> SearchResponse:
        """Search topic news within one cache-wait and provider deadline."""
        topic_text = (topic or "").strip()
        if not topic_text or not self.is_available:
            return SearchResponse(
                query=topic_text,
                results=[],
                provider="None",
                success=False,
                error_message="Search is not configured or the topic is empty",
            )

        wait_seconds = float(timeout_seconds)
        if wait_seconds <= 0:
            raise ValueError("Topic news search timeout must be greater than 0 seconds")
        deadline = time.monotonic() + wait_seconds
        search_days = self._effective_news_window_days()
        query_terms = [str(item).strip() for item in (focus_keywords or []) if str(item).strip()]
        query = " ".join(query_terms) if query_terms else self._default_topic_query(topic_text)
        cache_key = self._cache_key(f"topic_news:{topic_text}:{query}", max_results, search_days)
        cached, cache_owner, cache_event, _waited = self._get_cached_or_wait_for_reservation(
            cache_key,
            deadline=deadline,
        )
        if cached is not None:
            return cached

        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Topic news search waited past the call deadline")
            response = _call_topic_news_in_subprocess(
                constructor_kwargs=self._constructor_kwargs,
                topic=topic_text,
                max_results=max_results,
                focus_keywords=focus_keywords,
                timeout_seconds=remaining,
                deadline=deadline,
            )
            if response.success and response.results:
                self._put_cache(cache_key, response)
            return response
        finally:
            if cache_owner and cache_event is not None:
                self._release_cache_fill(cache_key, cache_event)

    def search_stock_news(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
        focus_keywords: Optional[List[str]] = None
    ) -> SearchResponse:
        """
        Search for stock-related news

        Args:
            stock_code: Stock code
            stock_name: Stock name
            max_results: Max number of results to return
            focus_keywords: List of keywords to focus on

        Returns:
            SearchResponse object
        """
        # Strategy window first: ultra_short/short/medium/long = 1/3/7/30 days,
        # all capped by NEWS_MAX_AGE_DAYS.
        search_days = self._effective_news_window_days()
        provider_max_results = self._provider_request_size(max_results)

        # Xây truy vấn tiếng Việt cho thị trường Việt Nam (báo chí tài chính CafeF,
        # Vietstock, VnExpress...). Chỉ số thường có tên trùng mã (VN30/VN30) và một số
        # lối gọi chỉ truyền tên hiển thị (code rỗng) — tránh lặp từ trong truy vấn.
        # Build a Vietnamese query for the Vietnam market; avoid repeating the code
        # when it equals the display name or is empty.
        if focus_keywords:
            # If keywords are provided, use them directly as the query
            query = " ".join(focus_keywords)
        elif stock_code and stock_code.strip().upper() != (stock_name or "").strip().upper():
            query = f"{stock_name} {stock_code} cổ phiếu tin tức mới nhất".strip()
        else:
            query = f"{(stock_name or stock_code or '').strip()} tin tức mới nhất"

        subject_label = f"{stock_name}({stock_code})" if stock_code else stock_name
        logger.info(
            (
                "Tìm tin tức cổ phiếu: %s, query='%s', khoảng thời gian: %s ngày gần nhất "
                "(profile=%s, NEWS_MAX_AGE_DAYS=%s), số kết quả mục tiêu=%s, số kết quả yêu cầu provider=%s"
            ),
            subject_label,
            query,
            search_days,
            self.news_strategy_profile,
            self.news_max_age_days,
            max_results,
            provider_max_results,
        )

        cache_key = self._cache_key(
            f"{query}|target={stock_code}:{stock_name}",
            max_results,
            search_days,
        )
        cached, cache_owner, cache_event, waited = self._get_cached_or_wait_for_reservation(cache_key)
        if cached is not None:
            logger.info(
                "%s: %s(%s)",
                "Dùng kết quả cache do luồng song song điền" if waited else "Dùng kết quả tìm kiếm trong cache",
                stock_name,
                stock_code,
            )
            self._record_news_search_run(
                provider=cached.provider or "SearchCache",
                operation="search_stock_news_cache_wait" if waited else "search_stock_news_cache",
                success=bool(cached.success),
                latency_ms=0,
                record_count=len(cached.results or []),
                cache_hit=True,
                error_message=cached.error_message,
            )
            return cached

        try:
            # Try each search engine in turn (if empty after filtering, move on to the next engine)
            had_provider_success = False
            best_ranked_response: Optional[SearchResponse] = None
            best_ranked_stats: Optional[Dict[str, int]] = None
            for provider in self._providers:
                if not provider.is_available:
                    continue

                search_kwargs: Dict[str, Any] = {}
                if isinstance(provider, TavilySearchProvider):
                    search_kwargs["topic"] = "news"
                elif isinstance(provider, BraveSearchProvider):
                    search_kwargs.update(self._brave_search_locale(stock_code))

                started_at = time.monotonic()
                try:
                    record_provider_run_started(
                        data_type="news_search",
                        provider=provider.name,
                        operation="search_stock_news",
                    )
                    response = provider.search(query, provider_max_results, days=search_days, **search_kwargs)
                except Exception as exc:
                    self._record_news_search_run(
                        provider=provider.name,
                        operation="search_stock_news",
                        success=False,
                        latency_ms=self._elapsed_ms(started_at),
                        error_type=type(exc).__name__,
                        error_message=exc,
                    )
                    raise
                filtered_response = self._filter_news_response(
                    response,
                    search_days=search_days,
                    max_results=provider_max_results,
                    log_scope=f"{stock_code}:{provider.name}:stock_news",
                )
                had_provider_success = had_provider_success or bool(response.success)

                if filtered_response.success and filtered_response.results:
                    ranked_response = self._rank_news_response(
                        filtered_response,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        max_results=provider_max_results,
                        log_scope=f"{stock_code}:{provider.name}:stock_news",
                    )
                    admitted_response = self._filter_ranked_news_for_context(
                        ranked_response,
                        log_scope=f"{stock_code}:{provider.name}:stock_news",
                    )
                    limited_response = self._limit_search_response(
                        admitted_response,
                        max_results=max_results,
                    )
                    admitted_count = len(limited_response.results or [])
                    self._record_news_search_run(
                        provider=provider.name,
                        operation="search_stock_news",
                        success=bool(limited_response.success and limited_response.results),
                        latency_ms=self._elapsed_ms(started_at),
                        record_count=admitted_count,
                        error_type=None if admitted_count else "NoUsableNews",
                        error_message=None if admitted_count else (
                            response.error_message or "No usable news after filtering"
                        ),
                    )
                    if not admitted_count:
                        logger.info(
                            "%s tìm kiếm thành công nhưng sau bộ lọc chất lượng không còn tin hợp lệ, thử công cụ tiếp theo",
                            provider.name,
                        )
                        continue

                    stats = self._news_relevance_stats(limited_response)
                    if self._is_better_ranked_news_response(
                        limited_response,
                        candidate_stats=stats,
                        best_response=best_ranked_response,
                        best_stats=best_ranked_stats,
                    ):
                        best_ranked_response = limited_response
                        best_ranked_stats = stats

                    if stats["direct_count"] > 0:
                        logger.info(
                            "%s tìm kiếm thành công, nhận diện %s tin trực tiếp về cổ phiếu, ưu tiên trả về",
                            provider.name,
                            stats["direct_count"],
                        )
                        self._put_cache(cache_key, limited_response)
                        return limited_response

                    logger.info(
                        "%s tìm kiếm thành công nhưng không nhận diện được tin trực tiếp về cổ phiếu, thử công cụ tiếp theo",
                        provider.name,
                    )
                else:
                    filtered_count = len(filtered_response.results or []) if filtered_response.success else 0
                    self._record_news_search_run(
                        provider=provider.name,
                        operation="search_stock_news",
                        success=bool(filtered_response.success and filtered_response.results),
                        latency_ms=self._elapsed_ms(started_at),
                        record_count=filtered_count,
                        error_type=None if filtered_count else "NoUsableNews",
                        error_message=None if filtered_count else (
                            response.error_message or "No usable news after filtering"
                        ),
                    )
                    if response.success and not filtered_response.results:
                        logger.info(
                            "%s tìm kiếm thành công nhưng sau khi lọc không còn tin hợp lệ, thử công cụ tiếp theo",
                            provider.name,
                        )
                    else:
                        logger.warning(
                            "%s tìm kiếm thất bại: %s, thử công cụ tiếp theo",
                            provider.name,
                            response.error_message,
                        )

            if best_ranked_response is not None:
                self._put_cache(cache_key, best_ranked_response)
                return best_ranked_response

            if had_provider_success:
                return SearchResponse(
                    query=query,
                    results=[],
                    provider="Filtered",
                    success=True,
                    error_message=None,
                )
            
            # All engines failed
            return SearchResponse(
                query=query,
                results=[],
                provider="None",
                success=False,
                error_message="All search engines are unavailable or failed"
            )
        finally:
            if cache_owner and cache_event is not None:
                self._release_cache_fill(cache_key, cache_event)
    
    def search_stock_events(
        self,
        stock_code: str,
        stock_name: str,
        event_types: Optional[List[str]] = None
    ) -> SearchResponse:
        """
        Search for stock-specific events (annual report previews, share reductions, etc.)

        Targets important events relevant to trading decisions

        Args:
            stock_code: Stock code
            stock_name: Stock name
            event_types: List of event types

        Returns:
            SearchResponse object
        """
        if event_types is None:
            # Sự kiện ảnh hưởng quyết định giao dịch cổ phiếu Việt Nam.
            # Default event types relevant to Vietnam stock trading decisions.
            event_types = ["kết quả kinh doanh", "giao dịch cổ đông nội bộ", "báo cáo tài chính quý"]

        # Build targeted queries
        event_query = " OR ".join(event_types)
        query = f"{stock_name} ({event_query})"
        cache_key = self._cache_key(
            f"stock_events:{query}|target={stock_code}:{stock_name}",
            5,
            0,
        )
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.info("Dùng kết quả tìm sự kiện trong cache: %s(%s)", stock_name, stock_code)
            return cached
        
        logger.info("Tìm sự kiện cổ phiếu: %s(%s) - %s", stock_name, stock_code, event_types)
        
        # Try each search engine in turn
        for provider in self._providers:
            if not provider.is_available:
                continue
            
            response = provider.search(query, max_results=5)
            
            if response.success:
                self._put_cache(cache_key, response)
                return response
        
        return SearchResponse(
            query=query,
            results=[],
            provider="None",
            success=False,
            error_message="Event search failed"
        )
    
    def search_comprehensive_intel(
        self,
        stock_code: str,
        stock_name: str,
        max_searches: int = 3
    ) -> Dict[str, SearchResponse]:
        """
        Multi-dimensional intelligence search (multiple engines and dimensions at once)

        Search dimensions:
        1. Latest news - recent news developments
        2. Risk screening - share reductions, penalties, negative news
        3. Earnings expectations - annual report previews, preliminary earnings

        Args:
            stock_code: Stock code
            stock_name: Stock name
            max_searches: Max number of searches

        Returns:
            {dimension name: SearchResponse} dict
        """
        results = {}
        search_count = 0

        is_index_etf = self.is_index_or_etf(stock_code, stock_name)

        # Truy vấn tiếng Việt để không bỏ lỡ báo chí tài chính Việt Nam
        # (CafeF, Vietstock, VnExpress). Chỉ số thường có tên trùng mã (VN30/VN30) —
        # tránh lặp từ trong truy vấn.
        # Vietnamese queries; avoid repeating the code when it equals the name.
        if stock_code and stock_code.strip().upper() != (stock_name or "").strip().upper():
            subject = f"{stock_name} {stock_code}".strip()
        else:
            subject = (stock_name or stock_code or "").strip()
        search_dimensions = [
            {
                'name': 'latest_news',
                'query': f"{subject} tin tức mới nhất",
                'desc': 'Tin mới nhất',
                'tavily_topic': 'news',
                'strict_freshness': True,
            },
            {
                'name': 'market_analysis',
                'query': (
                    f"{subject} nhận định thị trường xu hướng"
                    if is_index_etf
                    else f"{stock_name} khuyến nghị giá mục tiêu báo cáo phân tích"
                ),
                'desc': 'Phân tích tổ chức',
                'tavily_topic': None,
                'strict_freshness': False,
            },
            {
                'name': 'risk_check',
                'query': (
                    f"{subject} biến động rủi ro thị trường"
                    if is_index_etf
                    else f"{stock_name} rủi ro vi phạm xử phạt kiện tụng bán ra"
                ),
                'desc': 'Rà soát rủi ro',
                'tavily_topic': None if is_index_etf else 'news',
                'strict_freshness': not is_index_etf,
            },
            {
                'name': 'earnings',
                'query': (
                    f"{subject} thanh khoản vốn hóa diễn biến"
                    if is_index_etf
                    else f"{stock_name} kết quả kinh doanh doanh thu lợi nhuận"
                ),
                'desc': 'Kỳ vọng kết quả kinh doanh',
                'tavily_topic': None,
                'strict_freshness': False,
            },
            {
                'name': 'industry',
                'query': (
                    f"{subject} nhóm ngành dẫn dắt"
                    if is_index_etf
                    else f"{stock_name} ngành đối thủ thị phần triển vọng"
                ),
                'desc': 'Phân tích ngành',
                'tavily_topic': None,
                'strict_freshness': False,
            },
        ]

        search_days = self._effective_news_window_days()
        target_per_dimension = 3
        provider_max_results = self._provider_request_size(target_per_dimension)

        logger.info(
            (
                "Bắt đầu tìm kiếm thông tin đa chiều: %s(%s), khoảng thời gian: %s ngày gần nhất "
                "(profile=%s, NEWS_MAX_AGE_DAYS=%s), số kết quả mục tiêu=%s, số kết quả yêu cầu provider=%s"
            ),
            stock_name,
            stock_code,
            search_days,
            self.news_strategy_profile,
            self.news_max_age_days,
            target_per_dimension,
            provider_max_results,
        )
        
        # Rotate between search engines
        provider_index = 0
        
        for dim in search_dimensions:
            if search_count >= max_searches:
                break
            
            # Select a search engine (round-robin)
            available_providers = [p for p in self._providers if p.is_available]
            if not available_providers:
                break
            
            provider = available_providers[provider_index % len(available_providers)]
            provider_index += 1
            
            request_days = (
                self.ANALYTICAL_INTEL_LOOKBACK_DAYS
                if dim['name'] in self.ANALYTICAL_INTEL_DIMENSIONS
                else search_days
            )

            logger.info(
                "[Tìm kiếm thông tin] %s: dùng %s, cửa sổ yêu cầu: %s ngày gần nhất",
                dim['desc'],
                provider.name,
                request_days,
            )

            if isinstance(provider, TavilySearchProvider) and dim.get('tavily_topic'):
                response = provider.search(
                    dim['query'],
                    max_results=provider_max_results,
                    days=request_days,
                    topic=dim['tavily_topic'],
                )
            else:
                response = provider.search(
                    dim['query'],
                    max_results=provider_max_results,
                    days=request_days,
                )
            if dim['strict_freshness']:
                filtered_response = self._filter_news_response(
                    response,
                    search_days=search_days,
                    max_results=provider_max_results,
                    log_scope=f"{stock_code}:{provider.name}:{dim['name']}",
                )
            elif dim['name'] in self.ANALYTICAL_INTEL_DIMENSIONS:
                filtered_response = self._filter_news_response(
                    response,
                    search_days=self.ANALYTICAL_INTEL_LOOKBACK_DAYS,
                    max_results=provider_max_results,
                    keep_unknown=True,
                    log_scope=f"{stock_code}:{provider.name}:{dim['name']}",
                )
            else:
                filtered_response = self._normalize_and_limit_response(
                    response,
                    max_results=provider_max_results,
                )
            filtered_response = self._rank_news_response(
                filtered_response,
                stock_code=stock_code,
                stock_name=stock_name,
                max_results=provider_max_results,
                log_scope=f"{stock_code}:{provider.name}:{dim['name']}:rank",
            )
            filtered_response = self._filter_ranked_news_for_context(
                filtered_response,
                log_scope=f"{stock_code}:{provider.name}:{dim['name']}:admission",
            )
            filtered_response = self._limit_search_response(
                filtered_response,
                max_results=target_per_dimension,
            )
            results[dim['name']] = filtered_response
            search_count += 1
            
            if response.success:
                logger.info(
                    "[Tìm kiếm thông tin] %s: ban đầu=%s, sau lọc=%s",
                    dim['desc'],
                    len(response.results),
                    len(filtered_response.results),
                )
            else:
                logger.warning("[Tìm kiếm thông tin] %s: tìm kiếm thất bại - %s", dim['desc'], response.error_message)
            
            # Brief delay to avoid requesting too fast
            time.sleep(0.5)
        
        return results
    
    @staticmethod
    def _resolve_report_language() -> str:
        """Ngôn ngữ báo cáo đang hiệu lực, chuẩn hoá về ``vi`` hoặc ``en``.

        Hệ thống chỉ còn hai ngôn ngữ báo cáo: mọi giá trị khác ``en`` (kể cả
        ``zh`` cũ còn sót trong cấu hình) đều coi là ``vi``; lỗi đọc cấu hình cũng lùi về ``vi``.
        Effective report language normalized to ``vi``/``en``; anything other
        than ``en`` (including legacy ``zh``) is treated as ``vi``.
        """

        try:
            from src.config import get_config

            language = (get_config().report_language or "vi").strip().lower()
        except Exception:  # pragma: no cover - cấu hình chưa sẵn sàng
            return "vi"
        return "en" if language == "en" else "vi"

    def format_intel_report(self, intel_results: Dict[str, SearchResponse], stock_name: str) -> str:
        """
        Format intelligence search results into a report

        Args:
            intel_results: Multi-dimensional search results
            stock_name: Stock name

        Returns:
            Formatted intelligence report text
        """
        # Khối văn bản này được nạp thẳng vào prompt của LLM (src/core/pipeline.py),
        # nên nhãn phải theo ngôn ngữ báo cáo (vi/en).
        #
        # LƯU Ý: hàm này luôn trả về chuỗi KHÔNG rỗng kể cả khi không có kết quả.
        # Không được dùng giá trị trả về để suy ra "có bằng chứng tin tức hay không"
        # — xem src/services/empty_news.py, ngữ nghĩa ba trạng thái None/0/>0.
        language = self._resolve_report_language()
        is_vi = language != "en"

        lines = [
            f"【Kết quả tìm kiếm tin tức: {stock_name}】" if is_vi
            else f"【News search results: {stock_name}】"
        ]

        # Dimension display order
        display_order = ['latest_news', 'announcements', 'market_analysis', 'risk_check', 'earnings', 'industry']

        if is_vi:
            dim_labels = {
                'latest_news': '📰 Tin mới nhất',
                'announcements': '📋 Công bố thông tin',
                'market_analysis': '📈 Phân tích tổ chức',
                'risk_check': '⚠️ Rà soát rủi ro',
                'earnings': '📊 Kết quả kinh doanh',
                'industry': '🏭 Phân tích ngành',
            }
        else:
            dim_labels = {
                'latest_news': '📰 Latest news',
                'announcements': '📋 Company announcements',
                'market_analysis': '📈 Institutional analysis',
                'risk_check': '⚠️ Risk check',
                'earnings': '📊 Earnings outlook',
                'industry': '🏭 Industry analysis',
            }

        for dim_name in display_order:
            if dim_name not in intel_results:
                continue
                
            resp = intel_results[dim_name]
            
            # Get the dimension description
            dim_desc = dim_labels.get(dim_name, dim_name)
            
            source_label = "nguồn" if is_vi else "source"
            lines.append(f"\n{dim_desc} ({source_label}: {resp.provider}):")
            if resp.success and resp.results:
                # Show more items
                for i, r in enumerate(resp.results[:4], 1):
                    date_str = f" [{r.published_date}]" if r.published_date else ""
                    lines.append(f"  {i}. {r.title}{date_str}")
                    # If the summary is too short, it may lack information
                    snippet = r.snippet[:150] if len(r.snippet) > 20 else r.snippet
                    lines.append(f"     {snippet}...")
                    if r.relevance_category or r.relevance_reasons:
                        relevance_parts = []
                        if r.relevance_category:
                            relevance_parts.append(r.relevance_category)
                        if r.relevance_score is not None:
                            relevance_parts.append(f"score={r.relevance_score}")
                        if r.relevance_reasons:
                            reason_label = "căn cứ" if is_vi else "basis"
                            relevance_parts.append(
                                f"{reason_label}: {'; '.join(r.relevance_reasons[:3])}"
                            )
                        rel_label = "Mức liên quan" if is_vi else "Relevance"
                        lines.append(f"     {rel_label}: {'; '.join(relevance_parts)}")
            else:
                lines.append(
                    "  Không tìm thấy thông tin liên quan" if is_vi else "  No relevant information found"
                )
        
        return "\n".join(lines)
    
    def batch_search(
        self,
        stocks: List[Dict[str, str]],
        max_results_per_stock: int = 3,
        delay_between: float = 1.0
    ) -> Dict[str, SearchResponse]:
        """
        Batch search news for multiple stocks.
        
        Args:
            stocks: List of stocks
            max_results_per_stock: Max results per stock
            delay_between: Delay between searches (seconds)
            
        Returns:
            Dict of results
        """
        results = {}
        
        for i, stock in enumerate(stocks):
            if i > 0:
                time.sleep(delay_between)
            
            code = stock.get('code', '')
            name = stock.get('name', '')
            
            response = self.search_stock_news(code, name, max_results_per_stock)
            results[code] = response
        
        return results

    def search_stock_price_fallback(
        self,
        stock_code: str,
        stock_name: str,
        max_attempts: int = 3,
        max_results: int = 5
    ) -> SearchResponse:
        """
        Enhance search when data sources fail.
        
        When all data sources fail to get
        stock data, use search engines to find stock trends and price info as supplemental data for AI analysis.
        
        Strategy:
        1. Search using multiple keyword templates
        2. Try all available search engines for each keyword
        3. Aggregate and deduplicate results
        
        Args:
            stock_code: Stock Code
            stock_name: Stock Name
            max_attempts: Max search attempts (using different keywords)
            max_results: Max results to return
            
        Returns:
            SearchResponse object with aggregated results
        """

        if not self.is_available:
            return SearchResponse(
                query=f"{stock_name} diễn biến giá cổ phiếu",
                results=[],
                provider="None",
                success=False,
                error_message="Search is not configured"
            )
        
        logger.info("[Tìm kiếm tăng cường] Nguồn dữ liệu lỗi, bật tìm kiếm tăng cường: %s(%s)", stock_name, stock_code)
        
        all_results = []
        seen_urls = set()
        successful_providers = []
        
        # Search with multiple keyword templates
        keywords = self.ENHANCED_SEARCH_KEYWORDS
        for i, keyword_template in enumerate(keywords[:max_attempts]):
            query = keyword_template.format(name=stock_name, code=stock_code)
            
            logger.info("[Tìm kiếm tăng cường] Lần %s/%s: %s", i + 1, max_attempts, query)
            
            # Try each search engine in turn
            for provider in self._providers:
                if not provider.is_available:
                    continue
                
                try:
                    response = provider.search(query, max_results=3)
                    
                    if response.success and response.results:
                        # Deduplicate and add results
                        for result in response.results:
                            if result.url not in seen_urls:
                                seen_urls.add(result.url)
                                all_results.append(result)
                                
                        if provider.name not in successful_providers:
                            successful_providers.append(provider.name)
                        
                        logger.info("[Tìm kiếm tăng cường] %s trả về %s kết quả", provider.name, len(response.results))
                        break  # Move to the next keyword after success
                    else:
                        logger.debug("[Tìm kiếm tăng cường] %s không có kết quả hoặc thất bại", provider.name)
                        
                except Exception as e:
                    logger.warning("[Tìm kiếm tăng cường] %s lỗi khi tìm kiếm: %s", provider.name, e)
                    continue
            
            # Brief delay to avoid requesting too fast
            if i < max_attempts - 1:
                time.sleep(0.5)
        
        # Aggregate results
        if all_results:
            # Take the first max_results items
            final_results = all_results[:max_results]
            provider_str = ", ".join(successful_providers) if successful_providers else "None"
            
            logger.info("[Tìm kiếm tăng cường] Hoàn tất, lấy được %s kết quả (nguồn: %s)", len(final_results), provider_str)
            
            return SearchResponse(
                query=f"{stock_name}({stock_code}) diễn biến giá cổ phiếu",
                results=final_results,
                provider=provider_str,
                success=True,
            )
        else:
            logger.warning("[Tìm kiếm tăng cường] Không lượt tìm kiếm nào trả về kết quả")
            return SearchResponse(
                query=f"{stock_name}({stock_code}) diễn biến giá cổ phiếu",
                results=[],
                provider="None",
                success=False,
                error_message="Enhanced search found no relevant information"
            )

    def search_stock_with_enhanced_fallback(
        self,
        stock_code: str,
        stock_name: str,
        include_news: bool = True,
        include_price: bool = False,
        max_results: int = 5
    ) -> Dict[str, SearchResponse]:
        """
        Combined search interface (news and stock price information)

        When include_price=True, searches both news and stock price information.
        Mainly used as a fallback when data sources fail completely.

        Args:
            stock_code: Stock code
            stock_name: Stock name
            include_news: Whether to search news
            include_price: Whether to search stock price/trend information
            max_results: Max results per search category

        Returns:
            {'news': SearchResponse, 'price': SearchResponse} dict
        """
        results = {}
        
        if include_news:
            results['news'] = self.search_stock_news(
                stock_code, 
                stock_name, 
                max_results=max_results
            )
        
        if include_price:
            results['price'] = self.search_stock_price_fallback(
                stock_code,
                stock_name,
                max_attempts=3,
                max_results=max_results
            )
        
        return results

    def format_price_search_context(self, response: SearchResponse) -> str:
        """
        Format stock price search results as AI analysis context

        Args:
            response: Search response object

        Returns:
            Formatted text, ready for AI analysis
        """
        is_vi = self._resolve_report_language() != "en"
        if not response.success or not response.results:
            if is_vi:
                return "【Tìm kiếm diễn biến giá】Không tìm thấy thông tin liên quan, vui lòng dựa vào dữ liệu từ kênh khác."
            return "【Price trend search】No relevant information found; rely on data from other channels."

        if is_vi:
            lines = [
                f"【Kết quả tìm kiếm diễn biến giá】(nguồn: {response.provider})",
                "⚠️ Lưu ý: thông tin dưới đây lấy từ tìm kiếm trên mạng, chỉ mang tính tham khảo, có thể chậm hoặc không chính xác.",
                ""
            ]
        else:
            lines = [
                f"【Price trend search results】(source: {response.provider})",
                "⚠️ Note: the information below comes from web search, is for reference only and may be delayed or inaccurate.",
                ""
            ]

        for i, result in enumerate(response.results, 1):
            date_str = f" [{result.published_date}]" if result.published_date else ""
            lines.append(f"{i}. 【{result.source}】{result.title}{date_str}")
            lines.append(f"   {result.snippet[:200]}...")
            lines.append("")
        
        return "\n".join(lines)


# === Convenience functions ===
_search_service: Optional[SearchService] = None
_search_service_lock = threading.Lock()


def get_search_service() -> SearchService:
    """Get the search service singleton"""
    global _search_service
    
    if _search_service is None:
        with _search_service_lock:
            if _search_service is None:
                from src.config import get_config
                config = get_config()
                
                _search_service = SearchService(
                    tavily_keys=config.tavily_api_keys,
                    anspire_keys=config.anspire_api_keys,
                    brave_keys=config.brave_api_keys,
                    serpapi_keys=config.serpapi_keys,
                    searxng_base_urls=config.searxng_base_urls,
                    searxng_public_instances_enabled=config.searxng_public_instances_enabled,
                    searxng_timeout_seconds=getattr(config, "searxng_timeout_seconds", None),
                    news_max_age_days=config.news_max_age_days,
                    news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
                )
    
    return _search_service


def reset_search_service() -> None:
    """Reset the search service (for tests)"""
    global _search_service
    with _search_service_lock:
        _search_service = None


if __name__ == "__main__":
    # Test the search service
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s'
    )
    
    # Manual test (requires API keys to be configured)
    service = get_search_service()
    
    if service.is_available:
        print("=== Thử tìm tin tức cổ phiếu ===")
        response = service.search_stock_news("HPG", "Hòa Phát")
        print(f"Trạng thái: {'thành công' if response.success else 'thất bại'}")
        print(f"Công cụ tìm kiếm: {response.provider}")
        print(f"Số kết quả: {len(response.results)}")
        print(f"Thời gian: {response.search_time:.2f}s")
        print("\n" + response.to_context())
    else:
        print("Chưa cấu hình công cụ tìm kiếm, bỏ qua thử nghiệm")
