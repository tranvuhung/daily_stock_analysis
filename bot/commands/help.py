# -*- coding: utf-8 -*-
"""
===================================
Help command
===================================

Show the list of available commands and their usage.
"""

from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


class HelpCommand(BotCommand):
    """
    Help command
    
    Show the list of all available commands and their usage.
    Can also show detailed help for a specific command.
    
    Usage:
        /help         - show all commands
        /help analyze - show detailed help for the analyze command
    """
    
    @property
    def name(self) -> str:
        return "help"
    
    @property
    def aliases(self) -> List[str]:
        return ["h", "trogiup", "?"]
    
    @property
    def description(self) -> str:
        return "Hiển thị hướng dẫn sử dụng"
    
    @property
    def usage(self) -> str:
        return "/help [tên lệnh]"
    
    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """Execute the help command"""
        # Lazy import to avoid circular dependencies
        from bot.dispatcher import get_dispatcher
        
        dispatcher = get_dispatcher()
        
        # If a command name is given, show its detailed help
        if args:
            cmd_name = args[0]
            command = dispatcher.get_command(cmd_name)
            
            if command is None:
                return BotResponse.error_response(f"Lệnh không tồn tại: {cmd_name}")
            
            # Build the detailed help
            help_text = self._format_command_help(command, dispatcher.command_prefix)
            return BotResponse.markdown_response(help_text)
        
        # Show the list of all commands
        commands = dispatcher.list_commands(include_hidden=False)
        prefix = dispatcher.command_prefix
        
        help_text = self._format_help_list(commands, prefix)
        return BotResponse.markdown_response(help_text)
    
    def _format_help_list(self, commands: List[BotCommand], prefix: str) -> str:
        """Format the command list"""
        lines = [
            "📚 **Trợ lý phân tích chứng khoán - Hướng dẫn lệnh**",
            "",
            "Các lệnh khả dụng:",
            "",
        ]
        
        for cmd in commands:
            # Command name and aliases
            aliases_str = ""
            if cmd.aliases:
                # Chỉ hiển thị alias ASCII (bỏ alias có dấu/ký tự đặc biệt)
                # Show only ASCII aliases
                en_aliases = [a for a in cmd.aliases if a.isascii()]
                if en_aliases:
                    aliases_str = f" ({', '.join(prefix + a for a in en_aliases[:2])})"
            
            lines.append(f"• {prefix}{cmd.name}{aliases_str} - {cmd.description}")
            lines.append("")

        lines.extend([
            "",
            "---",
            f"💡 Gõ {prefix}help <tên lệnh> để xem hướng dẫn chi tiết",
            "",
            "**Ví dụ:**",
            "",
            f"• {prefix}analyze HPG - Phân tích Hòa Phát",
            "",
            f"• {prefix}market - Điểm lại thị trường",
            "",
            f"• {prefix}batch - Phân tích hàng loạt danh mục theo dõi",
        ])
        
        return "\n".join(lines)
    
    def _format_command_help(self, command: BotCommand, prefix: str) -> str:
        """Format detailed help for a single command"""
        lines = [
            f"📖 **{prefix}{command.name}** - {command.description}",
            "",
            f"**Cách dùng:** `{command.usage}`",
            "",
        ]
        
        # Aliases
        if command.aliases:
            aliases = [f"`{prefix}{a}`" if a.isascii() else f"`{a}`" for a in command.aliases]
            lines.append(f"**Bí danh:** {', '.join(aliases)}")
            lines.append("")
        
        # Permissions
        if command.admin_only:
            lines.append("⚠️ **Yêu cầu quyền quản trị**")
            lines.append("")
        
        return "\n".join(lines)
