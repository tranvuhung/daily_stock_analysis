# -*- coding: utf-8 -*-
"""Pytest compatibility hooks."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
import threading
from collections.abc import Awaitable, Callable
from contextvars import copy_context
from functools import wraps
from typing import Any, TypeVar
from warnings import warn

import anyio.to_thread
import fastapi.testclient
import httpx
import starlette.testclient
from anyio._backends import _asyncio

T = TypeVar("T")

_original_call_soon_threadsafe = asyncio.BaseEventLoop.call_soon_threadsafe


async def _shutdown_default_executor_inline(
    self: asyncio.BaseEventLoop,
    timeout: float | None = None,
) -> None:
    """Avoid lost wakeups while asyncio.run() tears down test-only executors."""
    del timeout
    executor = getattr(self, "_default_executor", None)
    if executor is None:
        return
    self._executor_shutdown_called = True
    self._default_executor = None
    executor.shutdown(wait=True)


def _call_soon_threadsafe_with_extra_wakeup(
    self: asyncio.BaseEventLoop,
    callback,
    *args,
    context=None,
):
    """Wake the selector again for sandboxed test runs where the first wake is lost."""
    handle = _original_call_soon_threadsafe(self, callback, *args, context=context)
    write_to_self = getattr(self, "_write_to_self", None)
    if callable(write_to_self):
        write_to_self()
        threading.Timer(0.001, write_to_self).start()
    return handle


asyncio.BaseEventLoop.call_soon_threadsafe = _call_soon_threadsafe_with_extra_wakeup
asyncio.BaseEventLoop.shutdown_default_executor = _shutdown_default_executor_inline


async def _run_sync_via_asyncio_to_thread(
    func: Callable[..., T],
    *args: Any,
    abandon_on_cancel: bool = False,
    cancellable: bool | None = None,
    limiter: Any = None,
) -> T:
    """Use asyncio's executor path when AnyIO worker queues miss wakeups."""
    del abandon_on_cancel, limiter
    if cancellable is not None:
        warn(
            "The `cancellable=` keyword argument to `anyio.to_thread.run_sync` is "
            "deprecated since AnyIO 4.1.0; use `abandon_on_cancel=` instead",
            DeprecationWarning,
            stacklevel=2,
        )
    future: concurrent.futures.Future[T] = concurrent.futures.Future()
    context = copy_context()

    def runner() -> None:
        try:
            future.set_result(context.run(func, *args))
        except BaseException as exc:
            future.set_exception(exc)

    threading.Thread(target=runner, name="pytest-anyio-worker", daemon=True).start()
    while not future.done():
        await asyncio.sleep(0.001)
    return future.result()


def _wait_for_cross_thread_result(loop: asyncio.AbstractEventLoop, future: concurrent.futures.Future[T]) -> T:
    write_to_self = getattr(loop, "_write_to_self", None)
    while not future.done():
        if callable(write_to_self):
            write_to_self()
        time.sleep(0.001)
    return future.result()


def _run_sync_from_thread_with_wakeup(
    cls,
    func: Callable[..., T],
    args: tuple[Any, ...],
    token: object,
) -> T:
    @wraps(func)
    def wrapper() -> None:
        try:
            _asyncio.set_current_async_library("asyncio")
            future.set_result(func(*args))
        except BaseException as exc:
            future.set_exception(exc)
            if not isinstance(exc, Exception):
                raise

    loop = token or _asyncio.threadlocals.current_token.native_token
    if loop.is_closed():
        raise _asyncio.RunFinishedError
    future: concurrent.futures.Future[T] = concurrent.futures.Future()
    loop.call_soon_threadsafe(wrapper)
    return _wait_for_cross_thread_result(loop, future)


def _run_async_from_thread_with_wakeup(
    cls,
    func: Callable[..., Awaitable[T]],
    args: tuple[Any, ...],
    token: object,
) -> T:
    loop = token or _asyncio.threadlocals.current_token.native_token
    if loop.is_closed():
        raise _asyncio.RunFinishedError
    context = copy_context()
    context.run(_asyncio.set_current_async_library, "asyncio")
    future = context.run(asyncio.run_coroutine_threadsafe, func(*args), loop=loop)
    return _wait_for_cross_thread_result(loop, future)


anyio.to_thread.run_sync = _run_sync_via_asyncio_to_thread
_asyncio.AsyncIOBackend.run_sync_from_thread = classmethod(_run_sync_from_thread_with_wakeup)
_asyncio.AsyncIOBackend.run_async_from_thread = classmethod(_run_async_from_thread_with_wakeup)


class _ThreadlessTestClient:
    """Small TestClient replacement that avoids AnyIO's cross-thread portal."""

    def __init__(
        self,
        app,
        base_url: str = "http://testserver",
        raise_server_exceptions: bool = True,
        follow_redirects: bool = True,
        **_: Any,
    ) -> None:
        self.app = app
        self.base_url = base_url
        self.raise_server_exceptions = raise_server_exceptions
        self.follow_redirects = follow_redirects
        self.cookies = httpx.Cookies()
        self._lifespan_ctx = None
        self._lifespan_enter_count = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_client: httpx.AsyncClient | None = None

    def _get_lifespan_context(self):
        return getattr(getattr(self.app, "router", None), "lifespan_context", None)

    def _build_async_client(self, follow_redirects: bool) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=self.app,
            raise_app_exceptions=self.raise_server_exceptions,
        )
        return httpx.AsyncClient(
            transport=transport,
            base_url=self.base_url,
            follow_redirects=follow_redirects,
            cookies=self.cookies,
        )

    def __enter__(self):
        if self._lifespan_enter_count == 0:
            self._loop = asyncio.new_event_loop()
            lifespan_context = self._get_lifespan_context()
            if callable(lifespan_context):
                self._lifespan_ctx = lifespan_context(self.app)
                self._loop.run_until_complete(self._lifespan_ctx.__aenter__())
            self._async_client = self._build_async_client(self.follow_redirects)
        self._lifespan_enter_count += 1
        return self

    def __exit__(self, *args: Any) -> None:
        if self._lifespan_enter_count == 0:
            return None

        self._lifespan_enter_count -= 1
        if self._lifespan_enter_count == 0 and self._loop is not None:
            try:
                async def _close() -> None:
                    if self._async_client is not None:
                        await self._async_client.aclose()
                    if self._lifespan_ctx is not None:
                        await self._lifespan_ctx.__aexit__(*args)

                self._loop.run_until_complete(_close())
            finally:
                self._lifespan_ctx = None
                self._async_client = None
                self._loop.close()
                self._loop = None
        return None

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        follow_redirects = kwargs.pop("follow_redirects", self.follow_redirects)
        kwargs.pop("allow_redirects", None)

        if self._lifespan_enter_count > 0 and self._loop is not None and self._async_client is not None:
            response = self._loop.run_until_complete(
                self._async_client.request(method, url, follow_redirects=follow_redirects, **kwargs)
            )
            self.cookies = httpx.Cookies(self._async_client.cookies)
            return response

        async def _send() -> httpx.Response:
            async with self._build_async_client(follow_redirects) as client:
                response = await client.request(method, url, **kwargs)
                self.cookies = httpx.Cookies(client.cookies)
                return response

        return asyncio.run(_send())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("HEAD", url, **kwargs)


fastapi.testclient.TestClient = _ThreadlessTestClient
starlette.testclient.TestClient = _ThreadlessTestClient


# ---------------------------------------------------------------------------
# Market allowlist (SUPPORTED_MARKETS)
# ---------------------------------------------------------------------------
# 运行时默认只启用越南市场，但既有测试套件覆盖 A股/港股/美股/日股/韩股/台股，
# 约 75 个文件直接断言这些市场的行为。这里把测试进程的 allowlist 打开成全量，
# 使"默认只跑越南"这个部署决策不会把历史回归测试一起关掉。
#
# 必须在**模块导入期**设置，而不是放进 fixture：部分测试模块（例如
# ``test_analysis_api_contract.py``）在自己被导入时就 ``dict(os.environ)``
# 快照了环境变量，并在每个用例后把 ``os.environ`` 还原成那份快照。session
# 级 fixture 在收集之后才执行，设置的值会被这类还原逻辑抹掉。conftest 在所有
# 测试模块之前导入，所以在这里设置能被那些快照捕获。
#
# 只在未显式设置时生效，针对 allowlist 本身的测试仍可用 ``patch.dict`` 覆盖
# （记得同时调用 ``src.core.markets.reset_cache()``）。
import os as _os  # noqa: E402

_os.environ.setdefault("SUPPORTED_MARKETS", "all")
