# -*- coding: utf-8 -*-
"""
===================================
Command base class
===================================

Defines the abstract base class for command handlers; every command must inherit from it.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional

from bot.models import BotMessage, BotResponse


class BotCommand(ABC):
    """
    Abstract base class for command handlers

    Every command must inherit from this class and implement the abstract methods.

    Example:
        class MyCommand(BotCommand):
            @property
            def name(self) -> str:
                return "mycommand"

            @property
            def aliases(self) -> List[str]:
                return ["mc", "lenhcuatoi"]

            @property
            def description(self) -> str:
                return "This is my command"

            @property
            def usage(self) -> str:
                return "/mycommand [args]"

            def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
                return BotResponse.text_response("Command executed successfully")
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Command name (without prefix)

        e.g. "analyze"; triggered when the user types "/analyze"
        """
        pass

    @property
    @abstractmethod
    def aliases(self) -> List[str]:
        """
        List of command aliases

        e.g. ["a", "phantich"]; typing "/a" or "phantich" also triggers it
        """
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Command description (used in help text)"""
        pass

    @property
    @abstractmethod
    def usage(self) -> str:
        """
        Usage text (used in help text)

        e.g. "/analyze <stock_code>"
        """
        pass

    @property
    def hidden(self) -> bool:
        """
        Whether to hide from the help list

        Defaults to False; set True to hide it from the /help list
        """
        return False

    @property
    def admin_only(self) -> bool:
        """
        Whether only admins may use it

        Defaults to False; set True to require admin permission
        """
        return False

    @abstractmethod
    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """
        Execute the command

        Args:
            message: Original message object
            args: Command argument list (already split)

        Returns:
            BotResponse object
        """
        pass

    async def execute_async(self, message: BotMessage, args: List[str]) -> BotResponse:
        """Execute the command asynchronously.

        By default, offloads the synchronous `execute()` to a thread pool to avoid blocking the event loop in the async dispatch path.
        """
        return await asyncio.to_thread(self.execute, message, args)

    def validate_args(self, args: List[str]) -> Optional[str]:
        """
        Validate arguments

        Subclasses may override this method to validate arguments.

        Args:
            args: Command argument list

        Returns:
            None if the arguments are valid, otherwise an error message
        """
        return None

    def get_help_text(self) -> str:
        """Get the help text"""
        return f"**{self.name}** - {self.description}\nCách dùng: `{self.usage}`"
