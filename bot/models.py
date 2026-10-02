# -*- coding: utf-8 -*-
"""
===================================
Bot message models
===================================

Defines unified message and response models that hide platform differences.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List


class ChatType(str, Enum):
    """Chat type"""
    GROUP = "group"      # Group chat
    PRIVATE = "private"  # Private chat
    UNKNOWN = "unknown"  # Unknown


class Platform(str, Enum):
    """Platform type"""
    FEISHU = "feishu"        # Feishu
    DINGTALK = "dingtalk"    # DingTalk
    WECOM = "wecom"          # WeCom (WeChat Work)
    TELEGRAM = "telegram"    # Telegram
    UNKNOWN = "unknown"      # Unknown


@dataclass
class BotMessage:
    """
    Unified bot message model
    
    Normalizes each platform's message format into this model for the command handlers.
    
    Attributes:
        platform: platform identifier
        message_id: message ID (platform's original ID)
        user_id: sender ID
        user_name: sender name
        chat_id: chat ID (group chat ID or private chat ID)
        chat_type: chat type
        content: message text (with the @bot part removed)
        raw_content: original message content
        mentioned: whether the bot was @-mentioned
        mentions: list of @-mentioned users
        timestamp: message timestamp
        raw_data: raw request data (platform-specific, for debugging)
    """
    platform: str
    message_id: str
    user_id: str
    user_name: str
    chat_id: str
    chat_type: ChatType
    content: str
    raw_content: str = ""
    mentioned: bool = False
    mentions: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    def get_command_and_args(self, prefix: str = "/") -> tuple:
        """
        Parse the command and arguments
        
        Args:
            prefix: command prefix, defaults to "/"
            
        Returns:
            A (command, args) tuple, e.g. ("analyze", ["600519"])
            Returns (None, []) if the message is not a command
        """
        text = self.content.strip()
        
        # Check whether it starts with the command prefix
        if not text.startswith(prefix):
            # Try to match a Chinese command (no prefix)
            chinese_commands = {
                '分析': 'analyze',
                '大盘': 'market',
                '批量': 'batch',
                '帮助': 'help',
                '状态': 'status',
            }
            for cn_cmd, en_cmd in chinese_commands.items():
                if text.startswith(cn_cmd):
                    args = text[len(cn_cmd):].strip().split()
                    return en_cmd, args
            return None, []
        
        # Strip the prefix
        text = text[len(prefix):]
        
        # Split the command and arguments
        parts = text.split()
        if not parts:
            return None, []
        
        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []
        
        return command, args
    
    def is_command(self, prefix: str = "/") -> bool:
        """Check whether the message is a command"""
        cmd, _ = self.get_command_and_args(prefix)
        return cmd is not None


@dataclass
class BotResponse:
    """
    Unified bot response model
    
    Returned by command handlers and converted to the platform-specific format by platform adapters.
    
    Attributes:
        text: reply text
        markdown: whether the text is Markdown
        at_user: whether to @ the sender
        reply_to_message: whether to reply to the original message
        extra: extra data (platform-specific)
    """
    text: str
    markdown: bool = False
    at_user: bool = True
    reply_to_message: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def text_response(cls, text: str, at_user: bool = True) -> 'BotResponse':
        """Create a plain-text response"""
        return cls(text=text, markdown=False, at_user=at_user)
    
    @classmethod
    def markdown_response(cls, text: str, at_user: bool = True) -> 'BotResponse':
        """Create a Markdown response"""
        return cls(text=text, markdown=True, at_user=at_user)
    
    @classmethod
    def error_response(cls, message: str) -> 'BotResponse':
        """Create an error response"""
        return cls(text=f"❌ 错误：{message}", markdown=False, at_user=True)


@dataclass
class WebhookResponse:
    """
    Webhook response model
    
    Returned by platform adapters; contains the HTTP response content.
    
    Attributes:
        status_code: HTTP status code
        body: response body (dict, will be JSON-serialized)
        headers: extra response headers
    """
    status_code: int = 200
    body: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    
    @classmethod
    def success(cls, body: Optional[Dict] = None) -> 'WebhookResponse':
        """Create a success response"""
        return cls(status_code=200, body=body or {})
    
    @classmethod
    def challenge(cls, challenge: str) -> 'WebhookResponse':
        """Create a verification response (for platform URL verification)"""
        return cls(status_code=200, body={"challenge": challenge})
    
    @classmethod
    def error(cls, message: str, status_code: int = 400) -> 'WebhookResponse':
        """Create an error response"""
        return cls(status_code=status_code, body={"error": message})
