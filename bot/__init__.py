# -*- coding: utf-8 -*-
"""
===================================
Bot command trigger system
===================================

Triggers stock analysis and other features via @bot mentions or commands.
Supports platforms such as Telegram and Discord.

Module layout:
- models.py: unified message/response models
- dispatcher.py: command dispatcher
- commands/: command handlers
- platforms/: platform adapters
- handler.py: Webhook handler

Usage:
1. Configure environment variables (each platform's token, etc.)
2. Start the WebUI service
3. Configure the Webhook URL on each platform:
   - Telegram: http://your-server/bot/telegram

Supported commands:
- /analyze <stock_code>  - analyze the given stock
- /market             - market review
- /batch              - batch-analyze the watchlist
- /help               - show help
- /status             - system status
"""

from bot.models import BotMessage, BotResponse, ChatType, WebhookResponse
from bot.dispatcher import CommandDispatcher, get_dispatcher

__all__ = [
    'BotMessage',
    'BotResponse',
    'ChatType',
    'WebhookResponse',
    'CommandDispatcher',
    'get_dispatcher',
]
