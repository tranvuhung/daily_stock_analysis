# -*- coding: utf-8 -*-
"""Tests for notification route channel parsing."""

from src.notification_routing import ROUTABLE_NOTIFICATION_CHANNELS, split_notification_route_channels


def test_ntfy_and_gotify_are_routable_notification_channels() -> None:
    valid, invalid = split_notification_route_channels(["telegram", "ntfy", "gotify", "not-a-channel"])

    assert "ntfy" in ROUTABLE_NOTIFICATION_CHANNELS
    assert "gotify" in ROUTABLE_NOTIFICATION_CHANNELS
    assert valid == ["telegram", "ntfy", "gotify"]
    assert invalid == ["not-a-channel"]


def test_removed_chinese_channels_are_not_routable() -> None:
    # Kênh WeChat/DingTalk/Feishu/PushPlus/ServerChan3/AstrBot đã bị gỡ -> coi là token không hợp lệ
    # Removed channels are treated as invalid route tokens.
    removed = ["wechat", "dingtalk", "feishu", "pushplus", "serverchan3", "astrbot"]
    valid, invalid = split_notification_route_channels(removed)

    assert valid == []
    assert invalid == removed
