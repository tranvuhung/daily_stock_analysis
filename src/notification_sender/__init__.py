# -*- coding: utf-8 -*-
"""
===================================
Notification sender layer module
===================================

Provides various notification sending services
"""

from .custom_webhook_sender import CustomWebhookSender
from .discord_sender import DiscordSender
from .email_sender import EmailSender
from .gotify_sender import GotifySender, resolve_gotify_message_endpoint
from .ntfy_sender import NtfySender, resolve_ntfy_endpoint
from .pushover_sender import PushoverSender
from .slack_sender import SlackSender
from .telegram_sender import TelegramSender
