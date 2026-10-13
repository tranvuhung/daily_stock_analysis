# -*- coding: utf-8 -*-
"""
===================================
Bot webhook handler
===================================

Handles webhook callbacks from each platform and dispatches them to command handlers.
"""

import asyncio
import json
import logging
import threading
from typing import Dict, Optional, TYPE_CHECKING

from bot.models import WebhookResponse
from bot.dispatcher import get_dispatcher
from bot.platforms import ALL_PLATFORMS

if TYPE_CHECKING:
    from bot.platforms.base import BotPlatform  # noqa: F401

logger = logging.getLogger(__name__)

# Platform instance cache
_platform_instances: Dict[str, 'BotPlatform'] = {}


def get_platform(platform_name: str) -> Optional['BotPlatform']:
    """
    Get a platform adapter instance

    Uses a cache to avoid repeated creation.

    Args:
        platform_name: platform name

    Returns:
        Platform adapter instance, or None
    """
    if platform_name not in _platform_instances:
        platform_class = ALL_PLATFORMS.get(platform_name)
        if platform_class:
            _platform_instances[platform_name] = platform_class()
        else:
            logger.warning(f"[BotHandler] 未知平台: {platform_name}")
            return None

    return _platform_instances[platform_name]


def handle_webhook(
    platform_name: str,
    headers: Dict[str, str],
    body: bytes,
    query_params: Optional[Dict[str, list]] = None
) -> WebhookResponse:
    """
    Handle a webhook request

    This is the unified entry point for all platform webhooks.

    Args:
        platform_name: platform name (e.g. telegram, discord)
        headers: HTTP request headers
        body: raw request body bytes
        query_params: URL query parameters (used for verification on some platforms)

    Returns:
        WebhookResponse object
    """
    logger.info(f"[BotHandler] 收到 {platform_name} Webhook 请求")

    # Check whether the bot feature is enabled
    from src.config import get_config
    config = get_config()

    if not getattr(config, 'bot_enabled', True):
        logger.info("[BotHandler] 机器人功能未启用")
        return WebhookResponse.success()

    # Get the platform adapter
    platform = get_platform(platform_name)
    if not platform:
        return WebhookResponse.error(f"Unknown platform: {platform_name}", 400)

    # Parse JSON data
    try:
        data = json.loads(body.decode('utf-8')) if body else {}
    except json.JSONDecodeError as e:
        logger.error(f"[BotHandler] JSON 解析失败: {e}")
        return WebhookResponse.error("Invalid JSON", 400)

    logger.debug(f"[BotHandler] 请求数据: {json.dumps(data, ensure_ascii=False)[:500]}")

    # Handle the webhook
    message, immediate_response = platform.handle_webhook(headers, body, data)

    # If it is a verification/error response with no message to process, return directly
    if immediate_response and not message:
        logger.info("[BotHandler] 返回验证响应")
        return immediate_response

    # Deferred response (e.g. Discord type 5): return ACK immediately, process the command in the background
    if immediate_response and message:
        logger.info("[BotHandler] 返回延迟 ACK，后台处理命令")

        def _deferred_dispatch() -> None:
            try:
                dispatcher = get_dispatcher()
                response = dispatcher.dispatch(message)
                if response.text:
                    platform.send_followup(response, message)
            except Exception as exc:
                logger.error("[BotHandler] 延迟命令处理失败: %s", exc)

        threading.Thread(target=_deferred_dispatch, daemon=True).start()
        return immediate_response

    # If there is no message to process, return an empty response
    if not message:
        logger.debug("[BotHandler] 无需处理的消息")
        return WebhookResponse.success()

    logger.info(f"[BotHandler] 解析到消息: user={message.user_name}, content={message.content[:50]}")

    # Dispatch to the command handler
    dispatcher = get_dispatcher()
    response = dispatcher.dispatch(message)

    # Format the response
    if response.text:
        webhook_response = platform.format_response(response, message)
        return webhook_response

    return WebhookResponse.success()


async def handle_webhook_async(
    platform_name: str,
    headers: Dict[str, str],
    body: bytes,
    query_params: Optional[Dict[str, list]] = None
) -> WebhookResponse:
    """Async version of :func:`handle_webhook`.

    Preferred when called from an async context (e.g. FastAPI endpoint)
    to avoid blocking the event loop.
    """
    logger.info(f"[BotHandler] 收到 {platform_name} Webhook 请求 (async)")

    from src.config import get_config
    config = get_config()

    if not getattr(config, 'bot_enabled', True):
        logger.info("[BotHandler] 机器人功能未启用")
        return WebhookResponse.success()

    platform = get_platform(platform_name)
    if not platform:
        return WebhookResponse.error(f"Unknown platform: {platform_name}", 400)

    try:
        data = json.loads(body.decode('utf-8')) if body else {}
    except json.JSONDecodeError as e:
        logger.error(f"[BotHandler] JSON 解析失败: {e}")
        return WebhookResponse.error("Invalid JSON", 400)

    logger.debug(f"[BotHandler] 请求数据: {json.dumps(data, ensure_ascii=False)[:500]}")

    message, immediate_response = platform.handle_webhook(headers, body, data)

    if immediate_response and not message:
        logger.info("[BotHandler] 返回验证响应")
        return immediate_response

    if immediate_response and message:
        logger.info("[BotHandler] 返回延迟 ACK，后台处理命令 (async)")

        async def _deferred_dispatch() -> None:
            try:
                dispatcher = get_dispatcher()
                response = await dispatcher.dispatch_async(message)
                if response.text:
                    await asyncio.to_thread(platform.send_followup, response, message)
            except Exception as exc:
                logger.error("[BotHandler] 延迟命令处理失败: %s", exc)

        asyncio.ensure_future(_deferred_dispatch())
        return immediate_response

    if not message:
        logger.debug("[BotHandler] 无需处理的消息")
        return WebhookResponse.success()

    logger.info(f"[BotHandler] 解析到消息: user={message.user_name}, content={message.content[:50]}")

    dispatcher = get_dispatcher()
    response = await dispatcher.dispatch_async(message)

    if response.text:
        webhook_response = platform.format_response(response, message)
        return webhook_response

    return WebhookResponse.success()


def handle_telegram_webhook(headers: Dict[str, str], body: bytes) -> WebhookResponse:
    """Handle Telegram webhook"""
    return handle_webhook('telegram', headers, body)
