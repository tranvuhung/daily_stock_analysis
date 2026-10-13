# -*- coding: utf-8 -*-
"""
===================================
Platform adapter module
===================================

Contains webhook handling and message parsing logic for each platform.

Ghi chú: các nền tảng DingTalk / Feishu (kể cả chế độ Stream) đã bị gỡ bỏ.
Note: DingTalk / Feishu platforms (including Stream mode) have been removed.
"""

from bot.platforms.base import BotPlatform


# Các nền tảng khả dụng (chế độ webhook) / All available platforms (webhook mode)
ALL_PLATFORMS = {}


__all__ = [
    'BotPlatform',
    'ALL_PLATFORMS',
]
