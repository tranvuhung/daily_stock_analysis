# -*- coding: utf-8 -*-
"""
===================================
DingTalk Stream mode adapter
===================================

Connects the bot via the official DingTalk Stream SDK; no public IP or webhook configuration needed.

Advantages:
- No public IP or domain required
- No webhook URL configuration required
- Receives messages over a long-lived WebSocket connection
- Simpler integration

Dependencies:
pip install dingtalk-stream

DingTalk Stream SDK:
https://github.com/open-dingtalk/dingtalk-stream-sdk-python
"""

import logging
import inspect
import threading
from datetime import datetime
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

# Try to import the DingTalk Stream SDK
try:
    import dingtalk_stream
    from dingtalk_stream import AckMessage

    DINGTALK_STREAM_AVAILABLE = True
except ImportError:
    DINGTALK_STREAM_AVAILABLE = False
    logger.warning("[DingTalk Stream] dingtalk-stream SDK 未安装，Stream 模式不可用")
    logger.warning("[DingTalk Stream] 请运行: pip install dingtalk-stream")

from bot.models import BotMessage, BotResponse, ChatType


class DingtalkStreamHandler:
    """
    DingTalk Stream mode message handler

    Converts Stream SDK callbacks into the unified BotMessage format,
    and calls the command dispatcher to handle them.
    """

    def __init__(self, on_message: Callable[[BotMessage], Any]):
        """
        Args:
            on_message: message callback; receives a BotMessage and returns a BotResponse
        """
        self._on_message = on_message
        self._logger = logger

    @staticmethod
    def _truncate_log_content(text: str, max_len: int = 200) -> str:
        cleaned = text.replace("\n", " ").strip()
        if len(cleaned) > max_len:
            return f"{cleaned[:max_len]}..."
        return cleaned

    def _log_incoming_message(self, message: BotMessage) -> None:
        content = message.raw_content or message.content or ""
        summary = self._truncate_log_content(content)
        self._logger.info(
            "[DingTalk Stream] Incoming message: msg_id=%s user_id=%s chat_id=%s chat_type=%s content=%s",
            message.message_id,
            message.user_id,
            message.chat_id,
            getattr(message.chat_type, "value", message.chat_type),
            summary,
        )

    if DINGTALK_STREAM_AVAILABLE:
        class _ChatbotHandler(dingtalk_stream.ChatbotHandler):
            """Internal message handler"""

            def __init__(self, parent: 'DingtalkStreamHandler'):
                super().__init__()
                self._parent = parent
                self.logger = logger

            async def process(self, callback: dingtalk_stream.CallbackMessage):
                """Handle an incoming message"""
                try:
                    # Parse the message
                    incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)

                    # Convert to the unified format
                    bot_message = self._parent._parse_stream_message(incoming, callback.data)

                    if bot_message:
                        self._parent._log_incoming_message(bot_message)
                        # Call the message callback
                        response = self._parent._on_message(bot_message)
                        if inspect.isawaitable(response):
                            response = await response

                        # Send the reply
                        if response and response.text:
                            # Build the @user prefix (in group chats the text must include @username)
                            if response.at_user and incoming.sender_nick:
                                if response.markdown:
                                    self.reply_markdown(
                                        title="股票分析助手",
                                        text=f"@{incoming.sender_nick} " + response.text,
                                        incoming_message=incoming
                                    )
                                else:
                                    self.reply_text(response.text, incoming)

                    return AckMessage.STATUS_OK, 'OK'

                except Exception as e:
                    self.logger.error(f"[DingTalk Stream] 处理消息失败: {e}")
                    self.logger.exception(e)
                    return AckMessage.STATUS_SYSTEM_EXCEPTION, str(e)

        def create_handler(self) -> '_ChatbotHandler':
            """Create the handler instance required by the SDK"""
            return self._ChatbotHandler(self)

    def _parse_stream_message(self, incoming: Any, raw_data: dict) -> Optional[BotMessage]:
        """
        Parse a Stream message into the unified format

        Args:
            incoming: ChatbotMessage object
            raw_data: raw callback data
        """
        try:
            raw_data = dict(raw_data or {})

            # Get message content
            raw_content = incoming.text.content if incoming.text else ''

            # Extract the command (strip @bot)
            content = self._extract_command(raw_content)

            # Conversation type
            conversation_type = getattr(incoming, 'conversation_type', None)
            if conversation_type == '1':
                chat_type = ChatType.PRIVATE
            elif conversation_type == '2':
                chat_type = ChatType.GROUP
            else:
                chat_type = ChatType.UNKNOWN

            # Whether the bot was @-mentioned (messages received in Stream mode are generally @bot)
            mentioned = True

            # Extract sessionWebhook for async push
            session_webhook = (
                    getattr(incoming, 'session_webhook', None)
                    or raw_data.get('sessionWebhook')
                    or raw_data.get('session_webhook')
            )
            if session_webhook:
                raw_data['_session_webhook'] = session_webhook

            return BotMessage(
                platform='dingtalk',
                message_id=getattr(incoming, 'msg_id', '') or '',
                user_id=getattr(incoming, 'sender_id', '') or '',
                user_name=getattr(incoming, 'sender_nick', '') or '',
                chat_id=getattr(incoming, 'conversation_id', '') or '',
                chat_type=chat_type,
                content=content,
                raw_content=raw_content,
                mentioned=mentioned,
                mentions=[],
                timestamp=datetime.now(),
                raw_data=raw_data,
            )

        except Exception as e:
            logger.error(f"[DingTalk Stream] 解析消息失败: {e}")
            return None

    def _extract_command(self, text: str) -> str:
        """Extract command content (strip @bot)"""
        import re
        text = re.sub(r'^@[\S]+\s*', '', text.strip())
        return text.strip()


class DingtalkStreamClient:
    """
    DingTalk Stream mode client

    Wraps the dingtalk-stream SDK and provides a simple start interface.

    Usage:
        client = DingtalkStreamClient()
        client.start()  # blocking

        # or run in the background
        client.start_background()
    """

    def __init__(
            self,
            client_id: Optional[str] = None,
            client_secret: Optional[str] = None
    ):
        """
        Args:
            client_id: app AppKey (read from config if not given)
            client_secret: app AppSecret (read from config if not given)
        """
        if not DINGTALK_STREAM_AVAILABLE:
            raise ImportError(
                "dingtalk-stream SDK 未安装。\n"
                "请运行: pip install dingtalk-stream"
            )

        from src.config import get_config
        config = get_config()

        self._client_id = client_id or getattr(config, 'dingtalk_app_key', None)
        self._client_secret = client_secret or getattr(config, 'dingtalk_app_secret', None)

        if not self._client_id or not self._client_secret:
            raise ValueError(
                "钉钉 Stream 模式需要配置 DINGTALK_APP_KEY 和 DINGTALK_APP_SECRET"
            )

        self._client: Optional[dingtalk_stream.DingTalkStreamClient] = None
        self._background_thread: Optional[threading.Thread] = None
        self._running = False

    def _create_message_handler(self) -> Callable[[BotMessage], Any]:
        """Create the message handler function"""

        async def handle_message(message: BotMessage) -> BotResponse:
            from bot.dispatcher import get_dispatcher
            dispatcher = get_dispatcher()
            return await dispatcher.dispatch_async(message)

        return handle_message

    def start(self) -> None:
        """
        Start the Stream client (blocking)

        This method blocks the current thread until the client stops.
        """
        logger.info("[DingTalk Stream] 正在启动...")

        # Create credentials
        credential = dingtalk_stream.Credential(
            self._client_id,
            self._client_secret
        )

        # Create the client
        self._client = dingtalk_stream.DingTalkStreamClient(credential)

        # Register the message handler
        handler = DingtalkStreamHandler(self._create_message_handler())
        self._client.register_callback_handler(
            dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
            handler.create_handler()
        )

        self._running = True
        logger.info("[DingTalk Stream] 客户端已启动，等待消息...")

        # Start (blocking)
        self._client.start_forever()

    def start_background(self) -> None:
        """
        Start the Stream client in a background thread (non-blocking)

        Suitable when running alongside other services (e.g. WebUI).
        """
        if self._background_thread and self._background_thread.is_alive():
            logger.warning("[DingTalk Stream] 客户端已在运行")
            return

        self._running = True
        self._background_thread = threading.Thread(
            target=self._run_in_background,
            daemon=True,
            name="DingtalkStreamClient"
        )
        self._background_thread.start()
        logger.info("[DingTalk Stream] 后台客户端已启动")

    def _run_in_background(self) -> None:
        """Run in the background (handles exceptions and reconnects)"""
        import time

        while self._running:
            try:
                self.start()
            except Exception as e:
                logger.error(f"[DingTalk Stream] 运行异常: {e}")
                if self._running:
                    logger.info("[DingTalk Stream] 5 秒后重连...")
                    time.sleep(5)

    def stop(self) -> None:
        """Stop the client"""
        self._running = False
        logger.info("[DingTalk Stream] 客户端已停止")

    @property
    def is_running(self) -> bool:
        """Whether the client is running"""
        return self._running


# Global client instance
_stream_client: Optional[DingtalkStreamClient] = None


def get_dingtalk_stream_client() -> Optional[DingtalkStreamClient]:
    """Get the global Stream client instance"""
    global _stream_client

    if _stream_client is None and DINGTALK_STREAM_AVAILABLE:
        try:
            _stream_client = DingtalkStreamClient()
        except (ImportError, ValueError) as e:
            logger.warning(f"[DingTalk Stream] 无法创建客户端: {e}")
            return None

    return _stream_client


def start_dingtalk_stream_background() -> bool:
    """
    Start the DingTalk Stream client in the background

    Returns:
        Whether it started successfully
    """
    client = get_dingtalk_stream_client()
    if client:
        client.start_background()
        return True
    return False
