# -*- coding: utf-8 -*-
"""
===================================
Command dispatcher
===================================

Parses commands, matches handlers, and dispatches execution.
"""

import asyncio
import logging
import re
import threading
import time
import unicodedata
from collections import defaultdict
from typing import Dict, List, Optional, Type, Callable

from bot.models import BotMessage, BotResponse
from bot.commands.base import BotCommand

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Simple rate limiter

    Uses a sliding-window algorithm to limit each user's request rate.
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        """
        Args:
            max_requests: maximum requests within the window
            window_seconds: window length (seconds)
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, user_id: str) -> bool:
        """
        Check whether the user is allowed to make a request

        Args:
            user_id: user identifier

        Returns:
            Whether the request is allowed
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Purge expired records
        self._requests[user_id] = [
            t for t in self._requests[user_id]
            if t > window_start
        ]

        # Check whether the limit is exceeded
        if len(self._requests[user_id]) >= self.max_requests:
            return False

        # Record this request
        self._requests[user_id].append(now)
        return True

    def get_remaining(self, user_id: str) -> int:
        """Get the number of remaining allowed requests"""
        now = time.time()
        window_start = now - self.window_seconds

        # Purge expired records
        self._requests[user_id] = [
            t for t in self._requests[user_id]
            if t > window_start
        ]

        return max(0, self.max_requests - len(self._requests[user_id]))


class CommandDispatcher:
    """
    Command dispatcher

    Responsibilities:
    1. Register and manage command handlers
    2. Parse commands and arguments from messages
    3. Dispatch commands to the matching handler
    4. Handle unknown commands and errors

    Usage example:
        dispatcher = CommandDispatcher()
        dispatcher.register(AnalyzeCommand())
        dispatcher.register(HelpCommand())

        response = dispatcher.dispatch(message)
    """

    def __init__(
        self,
        command_prefix: str = "/",
        rate_limit_requests: int = 10,
        rate_limit_window: int = 60,
        admin_users: Optional[List[str]] = None
    ):
        """
        Args:
            command_prefix: command prefix, defaults to "/"
            rate_limit_requests: rate limit: maximum requests within the window
            rate_limit_window: rate limit: window length (seconds)
            admin_users: list of admin user IDs
        """
        self.command_prefix = command_prefix
        self.admin_users = set(admin_users or [])

        self._commands: Dict[str, BotCommand] = {}
        self._aliases: Dict[str, str] = {}
        self._rate_limiter = RateLimiter(rate_limit_requests, rate_limit_window)

        # Callback: get the command list for the help command
        self._help_command_getter: Optional[Callable] = None

    def register(self, command: BotCommand) -> None:
        """
        Register a command

        Args:
            command: command instance
        """
        name = command.name.lower()

        if name in self._commands:
            logger.warning(f"[Dispatcher] Lệnh '{name}' đã tồn tại, sẽ bị ghi đè")

        self._commands[name] = command
        logger.debug(f"[Dispatcher] Đăng ký lệnh: {name}")

        # Register aliases
        for alias in command.aliases:
            alias_lower = alias.lower()
            if alias_lower in self._aliases:
                logger.warning(f"[Dispatcher] Bí danh '{alias_lower}' đã tồn tại, sẽ bị ghi đè")
            self._aliases[alias_lower] = name
            logger.debug(f"[Dispatcher] Đăng ký bí danh: {alias_lower} -> {name}")

    def register_class(self, command_class: Type[BotCommand]) -> None:
        """
        Register a command class (instantiated automatically)

        Args:
            command_class: command class
        """
        self.register(command_class())

    def unregister(self, name: str) -> bool:
        """
        Unregister a command

        Args:
            name: command name

        Returns:
            Whether it was unregistered successfully
        """
        name = name.lower()

        if name not in self._commands:
            return False

        command = self._commands.pop(name)

        # Remove aliases
        for alias in command.aliases:
            self._aliases.pop(alias.lower(), None)

        logger.debug(f"[Dispatcher] Huỷ đăng ký lệnh: {name}")
        return True

    def get_command(self, name: str) -> Optional[BotCommand]:
        """
        Get a command

        Supports lookup by command name and alias.

        Args:
            name: command name or alias

        Returns:
            Command instance, or None
        """
        name = name.lower()

        # Look up by command name first
        if name in self._commands:
            return self._commands[name]

        # Then by alias
        if name in self._aliases:
            return self._commands.get(self._aliases[name])

        return None

    def list_commands(self, include_hidden: bool = False) -> List[BotCommand]:
        """
        List all commands

        Args:
            include_hidden: whether to include hidden commands

        Returns:
            List of commands
        """
        commands = list(self._commands.values())

        if not include_hidden:
            commands = [c for c in commands if not c.hidden]

        return sorted(commands, key=lambda c: c.name)

    def is_admin(self, user_id: str) -> bool:
        """Check whether the user is an admin"""
        return user_id in self.admin_users

    def add_admin(self, user_id: str) -> None:
        """Add an admin"""
        self.admin_users.add(user_id)

    def remove_admin(self, user_id: str) -> None:
        """Remove an admin"""
        self.admin_users.discard(user_id)

    def dispatch(self, message: BotMessage) -> BotResponse:
        """Dispatch a message synchronously.

        Keeps existing synchronous callers compatible; the actual logic is delegated to `dispatch_async()`.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._dispatch_sync(message)

        result_holder: Dict[str, BotResponse] = {}
        error_holder: Dict[str, BaseException] = {}

        def _runner() -> None:
            try:
                result_holder["response"] = self._dispatch_sync(message)
            except BaseException as exc:  # pragma: no cover
                error_holder["error"] = exc

        worker = threading.Thread(target=_runner, daemon=True)
        worker.start()
        worker.join()

        if "error" in error_holder:
            raise error_holder["error"]

        return result_holder.get("response", BotResponse.error_response("Thực thi lệnh thất bại"))

    def _prepare_dispatch(self, message: BotMessage) -> tuple[Optional[str], List[str], Optional[BotCommand], Optional[BotResponse]]:
        """Run shared dispatch pre-checks for sync/async entrypoints."""
        if not self._rate_limiter.is_allowed(message.user_id):
            remaining_time = self._rate_limiter.window_seconds
            return None, [], None, BotResponse.error_response(
                f"Yêu cầu quá thường xuyên, vui lòng thử lại sau {remaining_time} giây"
            )

        cmd_name, args = message.get_command_and_args(self.command_prefix)
        if cmd_name is None:
            return None, args, None, None

        logger.info(f"[Dispatcher] Nhận lệnh: {cmd_name}, tham số: {args}, người dùng: {message.user_name}")

        command = self.get_command(cmd_name)
        if command is None:
            return cmd_name, args, None, BotResponse.error_response(
                f"Lệnh không tồn tại: {cmd_name}\n"
                f"Gửi `{self.command_prefix}help` để xem các lệnh khả dụng."
            )

        if command.admin_only and not self.is_admin(message.user_id):
            return cmd_name, args, None, BotResponse.error_response("Lệnh này yêu cầu quyền quản trị")

        error_msg = command.validate_args(args)
        if error_msg:
            return cmd_name, args, None, BotResponse.error_response(
                f"{error_msg}\nCách dùng: `{command.usage}`"
            )

        return cmd_name, args, command, None

    def _dispatch_sync(self, message: BotMessage) -> BotResponse:
        """Pure synchronous dispatch path for webhook/stream integrations."""
        cmd_name, args, command, early_response = self._prepare_dispatch(message)
        if early_response is not None:
            return early_response

        if cmd_name is None:
            nl_result = self._try_nl_routing_sync(message)
            if nl_result is not None:
                return nl_result
            if message.mentioned:
                return BotResponse.text_response(
                    "Xin chào! Tôi là trợ lý phân tích chứng khoán.\n"
                    f"Gửi `{self.command_prefix}help` để xem các lệnh khả dụng."
                )
            return BotResponse.text_response("")

        if command is None:
            return BotResponse.error_response("Thực thi lệnh thất bại")

        try:
            response = command.execute(message, args)
            logger.info(f"[Dispatcher] Lệnh {cmd_name} thực thi thành công")
            return response
        except Exception as e:
            logger.error(f"[Dispatcher] Lệnh {cmd_name} thực thi thất bại: {e}")
            logger.exception(e)
            return BotResponse.error_response(f"Thực thi lệnh thất bại: {str(e)[:100]}")

    async def dispatch_async(self, message: BotMessage) -> BotResponse:
        """
        Asynchronously dispatch a message to the matching command

        Args:
            message: message object

        Returns:
            Response object
        """
        cmd_name, args, command, early_response = self._prepare_dispatch(message)
        if early_response is not None:
            return early_response

        if cmd_name is None:
            # Not a command — try natural language routing before falling back
            nl_result = await self._try_nl_routing(message)
            if nl_result is not None:
                return nl_result
            # No NL match — check if @mentioned for a help hint
            if message.mentioned:
                return BotResponse.text_response(
                    "Xin chào! Tôi là trợ lý phân tích chứng khoán.\n"
                    f"Gửi `{self.command_prefix}help` để xem các lệnh khả dụng."
                )
            # Not a command message, ignore
            return BotResponse.text_response("")

        if command is None:
            return BotResponse.error_response("Thực thi lệnh thất bại")

        # 6. Execute the command
        try:
            response = await command.execute_async(message, args)
            logger.info(f"[Dispatcher] Lệnh {cmd_name} thực thi thành công")
            return response
        except Exception as e:
            logger.error(f"[Dispatcher] Lệnh {cmd_name} thực thi thất bại: {e}")
            logger.exception(e)
            return BotResponse.error_response(f"Thực thi lệnh thất bại: {str(e)[:100]}")

    def set_help_command_getter(self, getter: Callable) -> None:
        """
        Set the command-list getter for the help command

        Lets HelpCommand obtain the command list.

        Args:
            getter: callback that returns the command list
        """
        self._help_command_getter = getter

    # ------------------------------------------------------------------ #
    #  Natural language routing (LLM-based)                              #
    # ------------------------------------------------------------------ #

    # Lightweight intent-parsing prompt.  Asks the LLM to output a small
    # JSON object so we can route to the right command.
    _NL_PARSE_PROMPT = """\
You are a Vietnam stock analysis assistant router.  Given a user's
natural-language message (usually Vietnamese or English), determine whether it
contains a stock-analysis request.

Return a JSON object (and NOTHING else) with these fields:
- "intent": one of "analysis", "chat", "none"
  * "analysis" → the user wants stock analysis / diagnosis / comparison
  * "chat" → the user is asking a general question related to finance
  * "none" → the message is irrelevant or you are unsure
- "codes": a list of Vietnam stock codes mentioned (may be empty).
  Format: uppercase HOSE/HNX/UPCoM ticker ("HPG", "FPT", "VCB"), fund
  certificate ("FUEVN100") or index ("VNINDEX", "VN30").
  Do NOT invent a code from a company name; leave "codes" empty instead.
- "strategy": strategy/technique name if the user specified one, else null.
  e.g. "chan_theory", "MACD", "bull_trend", "wave_theory", etc.

Examples:
User: "Phân tích giúp tôi HPG và FPT"
{"intent":"analysis","codes":["HPG","FPT"],"strategy":null}

User: "Xem VCB theo lý thuyết sóng"
{"intent":"analysis","codes":["VCB"],"strategy":"wave_theory"}

User: "Thị trường hôm nay thế nào?"
{"intent":"chat","codes":[],"strategy":null}

User: "Ngày mai thời tiết ra sao?"
{"intent":"none","codes":[],"strategy":null}

User: "HPG"
{"intent":"analysis","codes":["HPG"],"strategy":null}

User: "Phân tích Hòa Phát"
{"intent":"analysis","codes":[],"strategy":null}

User: "analyze FPT and VCB using trend strategy"
{"intent":"analysis","codes":["FPT","VCB"],"strategy":"bull_trend"}
"""

    # Bộ lọc rẻ trước khi gọi LLM: chỉ gọi khi câu có khả năng liên quan cổ phiếu.
    # Cheap pre-filter: only invoke the LLM when the message plausibly contains
    # stock-related content.  This regex checks for:
    #   - Uppercase Vietnam tickers / fund / index codes (HPG, FUEVN100, VN30)
    #   - Common finance/analysis keywords (Vietnamese and English)
    _NL_PREFILTER = re.compile(
        r'(?<![A-Za-z0-9])[A-Z][A-Z0-9]{2,9}(?![A-Za-z0-9])'  # Mã VN viết hoa / uppercase VN code
        r'|(?i:phân tích|nghiên cứu|đánh giá|nhận định|\bxem\b|\bsoi\b'
        r'|thế nào|ra sao|xu hướng|diễn biến|có nên|nên mua|nên bán|khuyến nghị'
        r'|giá mục tiêu|hỗ trợ|kháng cự|cắt lỗ|điểm mua|điểm bán'
        r'|kỹ thuật|cơ bản|cổ phiếu|chứng khoán|thị trường)'
        r'|(?i:analyz|stock|buy|sell|trend|backtest|strateg)',
    )

    # Mẫu loại bỏ từ đệm để tách tên công ty (vd: "phân tích giúp tôi Hòa Phát" -> "Hòa Phát").
    # Filler-word cleanup patterns used to isolate a company name.
    _NL_NAME_CLEANUP_PATTERNS = (
        r'[，,。.!！?？:：;；`\'"“”‘’（）()\[\]{}<>]+',
        r'(?i:\b(?:please|analy[sz]e|analysis|research|check|look\s+at|stock|ticker|trend|price)\b)',
        r'(?i:\b(?:giúp|giùm|hộ|cho|tôi|mình|em|làm ơn|hãy|vui lòng|muốn|dùng|theo|về)\b)',
        r'(?i:\b(?:phân tích|xem|soi|nghiên cứu|đánh giá|nhận định|kiểm tra|nói về)\b)',
        r'(?i:\b(?:gần đây|hiện tại|hôm nay|mã|cổ phiếu|này|đó)\b)',
        r'(?i:\b(?:xu hướng|diễn biến|tình hình|thế nào|ra sao|có nên mua|có nên|nên mua không|không|kỹ thuật|cơ bản|chiến lược)\b)',
        r'\s+',
    )

    @classmethod
    def _passes_nl_prefilter(cls, text: str) -> bool:
        """Return whether the message is worth the LLM intent-routing cost."""
        if cls._NL_PREFILTER.search(text):
            return True

        stripped = (text or "").strip()
        if " " in stripped or len(stripped) > 10:
            return False

        from src.agent.orchestrator import _extract_stock_code

        return bool(_extract_stock_code(stripped))

    async def _try_nl_routing(self, message: BotMessage) -> Optional[BotResponse]:
        """Route a non-command message to the appropriate command via LLM intent parsing.

        Two-layer approach to balance cost and accuracy:
        1. **Cheap regex pre-filter**: skip messages that clearly have no stock
           or finance content (avoids LLM cost for irrelevant messages).
        2. **LLM intent parsing**: extract intent, stock codes, and strategy
           from the user text with full multilingual support.

        Only activates when:
        - ``AGENT_NL_ROUTING=true`` in config, **and**
        - the message is in a private chat, **or** the bot was @mentioned.

        Returns ``BotResponse`` if a route was found, ``None`` otherwise.
        """
        from src.config import get_config
        config = get_config()

        if not getattr(config, 'agent_nl_routing', False):
            return None

        # Only handle private chat or @mentioned messages to avoid hijacking
        is_private = message.chat_type.value == "private"
        if not is_private and not message.mentioned:
            return None

        # Keep Bot-side Agent entrypoints behind explicit opt-in so NL routing
        # cannot bypass AGENT_MODE=false.
        if not getattr(config, 'agent_mode', False):
            return None

        text = message.content.strip()
        if not text or len(text) > 500:
            return None

        # Layer 1: cheap pre-filter — skip obviously irrelevant messages
        if not self._passes_nl_prefilter(text):
            return None

        # Layer 2: LLM intent parsing — extract codes, intent, strategy
        parsed = await self._parse_intent_via_llm(text, config)
        if parsed is None:
            return None

        intent = parsed.get("intent", "none")
        codes = parsed.get("codes") or []
        strategy = parsed.get("strategy")

        if intent == "none":
            return None

        if intent == "analysis" and not codes:
            resolved_code = self._resolve_stock_code_from_text(text)
            if resolved_code:
                codes = [resolved_code]

        # "chat" intent → route to /chat with original text
        if intent == "chat":
            chat_cmd = self.get_command("chat")
            if chat_cmd:
                logger.info("[Dispatcher] NL routing → /chat: %s", text[:60])
                return await chat_cmd.execute_async(message, [text])
            return None

        # "analysis" intent → route to /ask
        if intent == "analysis" and codes:
            ask_cmd = self.get_command("ask")
            if not ask_cmd:
                return None

            # Build args: "code1,code2 [strategy]"
            code_str = ",".join(codes[:5])  # cap at 5
            args = [code_str]
            if strategy:
                args.append(strategy)

            logger.info(
                "[Dispatcher] NL routing → /ask %s (strategy=%s, text=%s)",
                code_str, strategy, text[:60],
            )
            return await ask_cmd.execute_async(message, args)

        return None

    def _try_nl_routing_sync(self, message: BotMessage) -> Optional[BotResponse]:
        """Synchronous companion to `_try_nl_routing` for legacy call sites."""
        from src.config import get_config

        config = get_config()
        if not getattr(config, 'agent_nl_routing', False):
            return None

        is_private = message.chat_type.value == "private"
        if not is_private and not message.mentioned:
            return None

        if not getattr(config, 'agent_mode', False):
            return None

        text = message.content.strip()
        if not text or len(text) > 500:
            return None

        if not self._passes_nl_prefilter(text):
            return None

        parsed = self._parse_intent_via_llm_sync(text, config)
        if parsed is None:
            return None

        intent = parsed.get("intent", "none")
        codes = parsed.get("codes") or []
        strategy = parsed.get("strategy")

        if intent == "none":
            return None

        if intent == "analysis" and not codes:
            resolved_code = self._resolve_stock_code_from_text(text)
            if resolved_code:
                codes = [resolved_code]

        if intent == "chat":
            chat_cmd = self.get_command("chat")
            if chat_cmd:
                logger.info("[Dispatcher] NL routing → /chat: %s", text[:60])
                return chat_cmd.execute(message, [text])
            return None

        if intent == "analysis" and codes:
            ask_cmd = self.get_command("ask")
            if not ask_cmd:
                return None

            code_str = ",".join(codes[:5])
            args = [code_str]
            if strategy:
                args.append(strategy)

            logger.info(
                "[Dispatcher] NL routing → /ask %s (strategy=%s, text=%s)",
                code_str, strategy, text[:60],
            )
            return ask_cmd.execute(message, args)

        return None

    @staticmethod
    async def _parse_intent_via_llm(text: str, config) -> Optional[dict]:
        """Call LLM to parse user intent.  Returns parsed dict or None on failure."""
        try:
            from src.agent.llm_adapter import LLMToolAdapter

            messages = [
                {"role": "system", "content": CommandDispatcher._NL_PARSE_PROMPT},
                {"role": "user", "content": text},
            ]
            adapter = LLMToolAdapter(config)
            resp = await asyncio.to_thread(
                adapter.call_text,
                messages,
                temperature=0,
                max_tokens=200,
                timeout=10,
            )
            return CommandDispatcher._parse_intent_payload(resp.content or "")
        except Exception as exc:
            logger.debug("[Dispatcher] NL parse LLM call failed: %s", exc)
            return None

    @staticmethod
    def _parse_intent_via_llm_sync(text: str, config) -> Optional[dict]:
        """Synchronous variant for webhook/stream integrations."""
        try:
            from src.agent.llm_adapter import LLMToolAdapter

            messages = [
                {"role": "system", "content": CommandDispatcher._NL_PARSE_PROMPT},
                {"role": "user", "content": text},
            ]
            adapter = LLMToolAdapter(config)
            resp = adapter.call_text(
                messages,
                temperature=0,
                max_tokens=200,
                timeout=10,
            )
            return CommandDispatcher._parse_intent_payload(resp.content or "")
        except Exception as exc:
            logger.debug("[Dispatcher] NL parse LLM call failed: %s", exc)
            return None

    @staticmethod
    def _parse_intent_payload(raw: str) -> Optional[dict]:
        """Parse the JSON payload returned by the intent-routing LLM call."""
        import json as _json

        cleaned = (raw or "").strip()
        if not cleaned:
            return None

        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)

        try:
            result = _json.loads(cleaned)
        except _json.JSONDecodeError:
            logger.debug("[Dispatcher] NL parse: invalid JSON from LLM: %s", cleaned[:200])
            return None

        if isinstance(result, dict) and "intent" in result:
            return result

        logger.debug("[Dispatcher] NL parse: unexpected structure: %s", cleaned[:200])
        return None

    @classmethod
    def _resolve_stock_code_from_text(cls, text: str) -> Optional[str]:
        """Phân giải tên/mã cổ phiếu Việt Nam từ câu tự nhiên (best-effort, offline).

        Thứ tự: mã VN viết hoa xuất hiện trong câu -> cả câu / câu đã bỏ từ đệm
        qua :func:`resolve_name_to_code` (vd: "phân tích Hòa Phát" -> HPG).
        Từ viết thường 3 ký tự (vd: "cho") không được coi là mã để tránh nhận nhầm.

        Best-effort Vietnam stock name/code resolution for NL-routed analysis requests.
        """
        from src.agent.stock_scope import extract_stock_codes
        from src.services.name_to_code_resolver import resolve_name_to_code
        from src.services.stock_code_utils import is_code_like

        stripped = unicodedata.normalize("NFC", text or "").strip()
        if not stripped:
            return None

        # 1) Mã viết hoa trong câu (HPG, FUEVN100, VN30) / explicit uppercase codes.
        explicit_codes = extract_stock_codes(stripped)
        if explicit_codes:
            return explicit_codes[0]

        # 2) Tên công ty: cả câu, rồi câu đã loại từ đệm / company names.
        candidates: List[str] = [stripped]
        cleaned = stripped
        for pattern in cls._NL_NAME_CLEANUP_PATTERNS:
            cleaned = re.sub(pattern, " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

        for candidate in candidates:
            # Từ viết thường giống mã (vd: "cho") không phải ý định chỉ định mã.
            # A lowercase code-like word is not an explicit code request.
            if is_code_like(candidate) and candidate != candidate.upper():
                continue
            resolved = resolve_name_to_code(candidate)
            if resolved:
                return resolved

        return None


# Global dispatcher instance
_dispatcher: Optional[CommandDispatcher] = None


def get_dispatcher() -> CommandDispatcher:
    """
    Get the global dispatcher instance

    Singleton: initialized on first call, with all commands registered automatically.
    """
    global _dispatcher

    if _dispatcher is None:
        from src.config import get_config

        config = get_config()

        # Create the dispatcher
        _dispatcher = CommandDispatcher(
            command_prefix=getattr(config, 'bot_command_prefix', '/'),
            rate_limit_requests=getattr(config, 'bot_rate_limit_requests', 10),
            rate_limit_window=getattr(config, 'bot_rate_limit_window', 60),
            admin_users=getattr(config, 'bot_admin_users', []),
        )

        # Register all commands automatically
        from bot.commands import ALL_COMMANDS
        for command_class in ALL_COMMANDS:
            _dispatcher.register_class(command_class)

        logger.info(f"[Dispatcher] Khởi tạo xong, đã đăng ký {len(_dispatcher._commands)} lệnh")

    return _dispatcher


def reset_dispatcher() -> None:
    """Reset the global dispatcher (mainly for tests)"""
    global _dispatcher
    _dispatcher = None
