# -*- coding: utf-8 -*-
"""
Unit tests for src.notification_sender module.

Tests sender classes in isolation (config, request shape, error handling).
Does not duplicate test_notification.py which tests NotificationService.send() flow.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import unittest
from email.header import decode_header, make_header
from email.utils import parseaddr
from types import SimpleNamespace
from unittest import mock
from typing import Optional

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import Config
from src.notification_sender import (
    CustomWebhookSender,
    DiscordSender,
    EmailSender,
    GotifySender,
    NtfySender,
    PushoverSender,
    SlackSender,
    TelegramSender,
)


def _config(**overrides):
    """Minimal Config for sender tests."""
    return Config(stock_list=[], **overrides)


def _response(status_code: int, json_body: Optional[dict] = None):
    resp = mock.MagicMock()
    resp.status_code = status_code
    if status_code == 200:
        resp.text = "ok"
    else:
        resp.text = "error"
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


class TestDiscordSender(unittest.TestCase):
    """Unit tests for DiscordSender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("hello")
        self.assertFalse(result)

    def test_is_discord_configured_webhook_only(self):
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)
        self.assertTrue(sender._is_discord_configured())

    def test_is_discord_configured_bot_only(self):
        cfg = _config(discord_bot_token="T", discord_main_channel_id="123")
        sender = DiscordSender(cfg)
        self.assertTrue(sender._is_discord_configured())

    def test_is_discord_configured_neither(self):
        cfg = _config()
        sender = DiscordSender(cfg)
        self.assertFalse(sender._is_discord_configured())

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_success_builds_correct_payload(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("content")
        self.assertTrue(result)
        mock_post.assert_called_once()
        call_kw = mock_post.call_args[1]
        self.assertEqual(call_kw["json"]["content"], "content")
        self.assertIn("username", call_kw["json"])

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_strips_hidden_market_region_metadata_from_text_payload(self, mock_post):
        mock_post.return_value = _response(204)
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)

        result = sender.send_to_discord("[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        call_kw = mock_post.call_args[1]
        self.assertNotIn("[dsa-market-region]", call_kw["json"]["content"])
        self.assertIn("# 🎯 Market Review", call_kw["json"]["content"])
        self.assertIn("Body", call_kw["json"]["content"])

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_preserves_standard_markdown_reference_definitions(self, mock_post):
        mock_post.return_value = _response(204)
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)

        content = "[详情][ref]\n\n[ref]: https://example.com/report"
        result = sender.send_to_discord(content)

        self.assertTrue(result)
        call_kw = mock_post.call_args[1]
        self.assertEqual(call_kw["json"]["content"], content)

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_http_error_returns_false(self, mock_post):
        mock_post.return_value = _response(400)
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("content")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_bot_success_uses_channel_url(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(discord_bot_token="TOKEN", discord_main_channel_id="CH123")
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("content")
        self.assertTrue(result)
        self.assertIn("discord.com/api/v10/channels/CH123/messages", mock_post.call_args[0][0])
        call_kw = mock_post.call_args[1]
        self.assertEqual(call_kw["headers"]["Authorization"], "Bot TOKEN")

    @mock.patch("src.notification_sender.discord_sender.time.sleep", return_value=None)
    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_long_content_sends_all_chunks(self, mock_post, mock_sleep):
        mock_post.return_value = _response(204)
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1", discord_max_words=2000)
        sender = DiscordSender(cfg)

        result = sender.send_to_discord("A" * 6000)

        self.assertTrue(result)
        self.assertGreater(mock_post.call_count, 1)
        self.assertEqual(mock_sleep.call_count, mock_post.call_count - 1)
        payload_lengths = [len(call.kwargs["json"]["content"]) for call in mock_post.call_args_list]
        self.assertTrue(all(length <= 2000 for length in payload_lengths))

    @mock.patch("src.notification_sender.discord_sender.time.sleep", return_value=None)
    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_long_content_retries_429_and_continues(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            _response(204),
            _response(429, {"retry_after": 0}),
            _response(204),
            _response(204),
            _response(204),
        ]
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1", discord_max_words=2000)
        sender = DiscordSender(cfg)

        result = sender.send_to_discord("A" * 6000)

        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 5)
        mock_sleep.assert_any_call(0.0)

    @mock.patch("src.notification_sender.discord_sender.time.sleep", return_value=None)
    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_long_content_does_not_short_circuit_after_failed_chunk(self, mock_post, _mock_sleep):
        mock_post.side_effect = [
            _response(204),
            _response(400),
            _response(204),
            _response(204),
        ]
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1", discord_max_words=2000)
        sender = DiscordSender(cfg)

        result = sender.send_to_discord("A" * 6000)

        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 4)

    @mock.patch("src.notification_sender.discord_sender.time.sleep", return_value=None)
    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_long_content_continues_after_request_exception(self, mock_post, _mock_sleep):
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1", discord_max_words=2000)
        sender = DiscordSender(cfg)
        content = "A" * 6000
        chunk_count = len(sender._split_discord_content(content))
        request_error = requests.exceptions.ChunkedEncodingError("broken response")
        mock_post.side_effect = (
            [_response(204)]
            + [request_error] * 3
            + [_response(204)] * (chunk_count - 2)
        )

        result = sender.send_to_discord(content)

        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, chunk_count + 2)

    @mock.patch("src.notification_sender.discord_sender.time.sleep", return_value=None)
    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_bot_clamps_configured_limit_to_discord_content_limit(self, mock_post, _mock_sleep):
        mock_post.return_value = _response(200)
        cfg = _config(
            discord_bot_token="TOKEN",
            discord_main_channel_id="CH123",
            discord_max_words=5000,
        )
        sender = DiscordSender(cfg)

        result = sender.send_to_discord("A" * 4500)

        self.assertTrue(result)
        self.assertGreater(mock_post.call_count, 1)
        payload_lengths = [len(call.kwargs["json"]["content"]) for call in mock_post.call_args_list]
        self.assertTrue(all(length <= 2000 for length in payload_lengths))


class TestEmailSender(unittest.TestCase):
    """Unit tests for EmailSender (config and receiver logic; send path covered via service)."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = EmailSender(cfg)
        result = sender.send_to_email("body")
        self.assertFalse(result)

    def test_get_receivers_for_stocks_no_groups_returns_default(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com", "c@qq.com"],
        )
        sender = EmailSender(cfg)
        self.assertEqual(
            sender.get_receivers_for_stocks(["000001"]),
            ["b@qq.com", "c@qq.com"],
        )

    def test_get_receivers_for_stocks_with_matching_group(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["default@qq.com"],
            stock_email_groups=[(["000001", "600519"], ["group1@qq.com"])],
        )
        sender = EmailSender(cfg)
        self.assertEqual(
            sender.get_receivers_for_stocks(["000001"]),
            ["group1@qq.com"],
        )

    def test_get_receivers_for_stocks_no_match_falls_back_to_default(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["default@qq.com"],
            stock_email_groups=[(["000001"], ["group@qq.com"])],
        )
        sender = EmailSender(cfg)
        self.assertEqual(
            sender.get_receivers_for_stocks(["999999"]),
            ["default@qq.com"],
        )

    def test_get_all_email_receivers_returns_union(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["default@qq.com"],
            stock_email_groups=[
                (["000001"], ["g1@qq.com"]),
                (["600519"], ["g2@qq.com"]),
            ],
        )
        sender = EmailSender(cfg)
        receivers = sender.get_all_email_receivers()
        self.assertIn("g1@qq.com", receivers)
        self.assertIn("g2@qq.com", receivers)
        self.assertIn("default@qq.com", receivers)

    @mock.patch("smtplib.SMTP_SSL")
    def test_send_to_email_encodes_non_ascii_sender_name(self, mock_smtp_ssl):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
            email_sender_name="daily_stock_analysis股票分析助手",
        )
        sender = EmailSender(cfg)

        result = sender.send_to_email("body", subject="测试主题")

        self.assertTrue(result)
        server = mock_smtp_ssl.return_value
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        realname, addr = parseaddr(msg["From"])
        self.assertEqual(addr, "a@qq.com")
        self.assertEqual(
            str(make_header(decode_header(realname))),
            "daily_stock_analysis股票分析助手",
        )
        server.quit.assert_called_once()

    @mock.patch("smtplib.SMTP_SSL")
    def test_send_to_email_strips_hidden_market_metadata(self, mock_smtp_ssl):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
        )
        sender = EmailSender(cfg)

        result = sender.send_to_email("[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        msg = mock_smtp_ssl.return_value.send_message.call_args[0][0]
        payloads = msg.get_payload()
        self.assertNotIn("[dsa-market-region]", payloads[0].get_payload(decode=True).decode("utf-8"))
        self.assertNotIn("[dsa-market-region]", payloads[1].get_payload(decode=True).decode("utf-8"))

    @mock.patch("smtplib.SMTP_SSL")
    def test_send_image_email_encodes_non_ascii_sender_name(self, mock_smtp_ssl):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
            email_sender_name="daily_stock_analysis股票分析助手",
        )
        sender = EmailSender(cfg)

        result = sender._send_email_with_inline_image(b"PNG_BYTES", receivers=["b@qq.com"])

        self.assertTrue(result)
        server = mock_smtp_ssl.return_value
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        realname, addr = parseaddr(msg["From"])
        self.assertEqual(addr, "a@qq.com")
        self.assertEqual(
            str(make_header(decode_header(realname))),
            "daily_stock_analysis股票分析助手",
        )
        server.quit.assert_called_once()


class TestNtfySender(unittest.TestCase):
    """Unit tests for NtfySender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = NtfySender(cfg)

        result = sender.send_to_ntfy("hello")

        self.assertFalse(result)

    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_send_success_uses_json_publish_with_topic_endpoint(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(
            ntfy_url="https://ntfy.sh/dsa-topic",
            ntfy_token="secret-token",
            webhook_verify_ssl=False,
        )
        sender = NtfySender(cfg)

        result = sender.send_to_ntfy("正文 **Markdown**", title="中文标题", timeout_seconds=5)

        self.assertTrue(result)
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], "https://ntfy.sh")
        call_kw = mock_post.call_args.kwargs
        self.assertEqual(
            call_kw["json"],
            {
                "topic": "dsa-topic",
                "title": "中文标题",
                "message": "正文 **Markdown**",
                "markdown": True,
            },
        )
        self.assertEqual(call_kw["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(call_kw["timeout"], 5)
        self.assertFalse(call_kw["verify"])

    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_send_strips_hidden_market_region_metadata_from_message(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(ntfy_url="https://ntfy.sh/dsa-topic")
        sender = NtfySender(cfg)

        result = sender.send_to_ntfy("[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["message"],
            "# 🎯 Market Review\n\nBody",
        )

    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_send_supports_self_hosted_path_prefix(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(ntfy_url="https://example.com/ntfy/dsa-topic")
        sender = NtfySender(cfg)

        result = sender.send_to_ntfy("body", title="title")

        self.assertTrue(result)
        self.assertEqual(mock_post.call_args.args[0], "https://example.com/ntfy")
        self.assertEqual(mock_post.call_args.kwargs["json"]["topic"], "dsa-topic")

    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_send_returns_false_when_url_has_no_topic(self, mock_post):
        cfg = _config(ntfy_url="https://ntfy.sh")
        sender = NtfySender(cfg)

        result = sender.send_to_ntfy("body")

        self.assertFalse(result)
        mock_post.assert_not_called()

    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_send_returns_false_when_url_scheme_is_not_http(self, mock_post):
        cfg = _config(ntfy_url="ftp://ntfy.example/dsa-topic")
        sender = NtfySender(cfg)

        result = sender.send_to_ntfy("body")

        self.assertFalse(result)
        mock_post.assert_not_called()

    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_send_http_error_returns_false(self, mock_post):
        mock_post.return_value = _response(500)
        cfg = _config(ntfy_url="https://ntfy.sh/dsa-topic")
        sender = NtfySender(cfg)

        result = sender.send_to_ntfy("body")

        self.assertFalse(result)

    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_send_timeout_does_not_log_token_value(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("secret-token")
        cfg = _config(ntfy_url="https://ntfy.sh/dsa-topic", ntfy_token="secret-token")
        sender = NtfySender(cfg)

        with self.assertLogs("src.notification_sender.ntfy_sender", level="ERROR") as captured:
            result = sender.send_to_ntfy("body")

        self.assertFalse(result)
        self.assertNotIn("secret-token", "\n".join(captured.output))


class TestGotifySender(unittest.TestCase):
    """Unit tests for GotifySender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = GotifySender(cfg)

        result = sender.send_to_gotify("hello")

        self.assertFalse(result)

    def test_send_returns_false_when_token_is_blank(self):
        cfg = _config(gotify_url="https://gotify.example", gotify_token="   ")
        sender = GotifySender(cfg)

        result = sender.send_to_gotify("hello")

        self.assertFalse(result)

    @mock.patch("src.notification_sender.gotify_sender.requests.post")
    def test_send_success_uses_json_payload_and_header_auth(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(
            gotify_url="https://gotify.example",
            gotify_token="secret-token",
            webhook_verify_ssl=False,
        )
        sender = GotifySender(cfg)

        result = sender.send_to_gotify("正文 **Markdown**", title="中文标题", timeout_seconds=5)

        self.assertTrue(result)
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], "https://gotify.example/message")
        call_kw = mock_post.call_args.kwargs
        self.assertEqual(
            call_kw["json"],
            {
                "title": "中文标题",
                "message": "正文 **Markdown**",
                "extras": {
                    "client::display": {
                        "contentType": "text/markdown",
                    },
                },
            },
        )
        self.assertEqual(call_kw["headers"]["X-Gotify-Key"], "secret-token")
        self.assertNotIn("secret-token", mock_post.call_args.args[0])
        self.assertEqual(call_kw["timeout"], 5)
        self.assertFalse(call_kw["verify"])

    @mock.patch("src.notification_sender.gotify_sender.requests.post")
    def test_send_strips_hidden_market_region_metadata_from_message(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(gotify_url="https://gotify.example", gotify_token="secret-token")
        sender = GotifySender(cfg)

        result = sender.send_to_gotify("[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["message"],
            "# 🎯 Market Review\n\nBody",
        )

    @mock.patch("src.notification_sender.gotify_sender.requests.post")
    def test_send_supports_reverse_proxy_path_prefix(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(gotify_url="https://example.com/gotify", gotify_token="secret-token")
        sender = GotifySender(cfg)

        result = sender.send_to_gotify("body", title="title")

        self.assertTrue(result)
        self.assertEqual(mock_post.call_args.args[0], "https://example.com/gotify/message")

    @mock.patch("src.notification_sender.gotify_sender.requests.post")
    def test_send_returns_false_when_url_already_includes_message_endpoint(self, mock_post):
        cfg = _config(gotify_url="https://gotify.example/message", gotify_token="secret-token")
        sender = GotifySender(cfg)

        result = sender.send_to_gotify("body")

        self.assertFalse(result)
        mock_post.assert_not_called()

    @mock.patch("src.notification_sender.gotify_sender.requests.post")
    def test_send_timeout_does_not_log_token_value(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("secret-token")
        cfg = _config(gotify_url="https://gotify.example", gotify_token="secret-token")
        sender = GotifySender(cfg)

        with self.assertLogs("src.notification_sender.gotify_sender", level="ERROR") as captured:
            result = sender.send_to_gotify("body")

        self.assertFalse(result)
        self.assertNotIn("secret-token", "\n".join(captured.output))


class TestCustomWebhookSender(unittest.TestCase):
    """Unit tests for CustomWebhookSender."""

    def test_send_returns_false_when_no_urls(self):
        cfg = _config()
        sender = CustomWebhookSender(cfg)
        result = sender.send_to_custom("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_send_success_payload_has_text_and_content(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(custom_webhook_urls=["https://example.com/webhook"])
        sender = CustomWebhookSender(cfg)
        result = sender.send_to_custom("hello")
        self.assertTrue(result)
        body = mock_post.call_args[1]["data"].decode("utf-8")
        self.assertIn("hello", body)

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_send_strips_hidden_market_region_metadata_from_payload(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(custom_webhook_urls=["https://example.com/webhook"])
        sender = CustomWebhookSender(cfg)

        result = sender.send_to_custom("[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        body = mock_post.call_args.kwargs["data"].decode("utf-8")
        self.assertNotIn("[dsa-market-region]", body)
        self.assertIn("Market Review", body)
        self.assertIn("Body", body)

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_send_returns_true_when_one_custom_webhook_succeeds(self, mock_post):
        mock_post.side_effect = [_response(500), _response(200)]
        cfg = _config(
            custom_webhook_urls=[
                "https://example.com/fail",
                "https://example.com/ok",
            ]
        )
        sender = CustomWebhookSender(cfg)

        result = sender.send_to_custom("hello")

        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_test_custom_webhooks_returns_ordered_attempts(self, mock_post):
        mock_post.side_effect = [_response(500), _response(200)]
        cfg = _config(
            custom_webhook_urls=[
                "https://example.com/fail?access_token=secret",
                "https://example.com/ok",
            ]
        )
        sender = CustomWebhookSender(cfg)

        attempts = sender.test_custom_webhooks("hello", timeout_seconds=7)

        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0]["success"])
        self.assertTrue(attempts[1]["success"])
        self.assertEqual(attempts[0]["http_status"], 500)
        self.assertEqual(mock_post.call_args_list[0].kwargs["timeout"], 7)

    def test_bark_payload_shape_is_stable(self):
        sender = CustomWebhookSender(_config())

        payload = sender._build_custom_webhook_payload("https://api.day.app/key", "hello")

        self.assertEqual(
            payload,
            {
                "title": "股票分析报告",
                "body": "hello",
                "group": "stock",
            },
        )

    def test_bark_payload_truncates_long_content(self):
        sender = CustomWebhookSender(_config())

        payload = sender._build_custom_webhook_payload("https://api.day.app/key", "x" * 5000)

        self.assertEqual(len(payload["body"]), 4000)
        self.assertEqual(payload["body"], "x" * 4000)

    def test_custom_body_template_overrides_bark_auto_payload(self):
        cfg = _config(
            custom_webhook_body_template=(
                '{"title":$title_json,"body":$content_json,"sound":"bell"}'
            ),
        )
        sender = CustomWebhookSender(cfg)

        payload = sender._build_custom_webhook_payload("https://api.day.app/key", "hello")

        self.assertEqual(
            payload,
            {
                "title": "股票分析报告",
                "body": "hello",
                "sound": "bell",
            },
        )
        self.assertNotIn("group", payload)

    def test_custom_body_template_json_placeholders_escape_content(self):
        cfg = _config(
            custom_webhook_body_template=(
                '{"title":$title_json,"content":$content_json}'
            ),
        )
        sender = CustomWebhookSender(cfg)

        payload = sender._build_custom_webhook_payload(
            "https://example.com/webhook",
            'line 1\nline "2"',
        )

        self.assertEqual(
            payload,
            {
                "title": "股票分析报告",
                "content": 'line 1\nline "2"',
            },
        )

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_send_uses_custom_body_template(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(
            custom_webhook_urls=["https://example.com/webhook"],
            custom_webhook_body_template='{"msg_type":"text","content":$content_json}',
        )
        sender = CustomWebhookSender(cfg)

        result = sender.send_to_custom('hello "world"')

        self.assertTrue(result)
        body = mock_post.call_args[1]["data"].decode("utf-8")
        self.assertEqual(
            json.loads(body),
            {"msg_type": "text", "content": 'hello "world"'},
        )

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_custom_send_uses_custom_body_template_for_any_url(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(
            custom_webhook_urls=["https://example.com/robot/send?access_token=token"],
            custom_webhook_body_template='{"msgtype":"text","text":{"content":$content_json}}',
        )
        sender = CustomWebhookSender(cfg)

        result = sender.send_to_custom("hello webhook")

        self.assertTrue(result)
        mock_post.assert_called_once()
        body = mock_post.call_args[1]["data"].decode("utf-8")
        self.assertEqual(
            json.loads(body),
            {"msgtype": "text", "text": {"content": "hello webhook"}},
        )

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_invalid_custom_body_template_falls_back(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(
            custom_webhook_urls=["https://example.com/webhook"],
            custom_webhook_body_template='{"content": $content',
        )
        sender = CustomWebhookSender(cfg)

        result = sender.send_to_custom("hello")

        self.assertTrue(result)
        body = mock_post.call_args[1]["data"].decode("utf-8")
        self.assertIn("hello", body)

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_non_object_custom_body_template_falls_back(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(
            custom_webhook_urls=["https://example.com/webhook"],
            custom_webhook_body_template='["not", "object"]',
        )
        sender = CustomWebhookSender(cfg)

        result = sender.send_to_custom("hello")

        self.assertTrue(result)
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(body["content"], "hello")
        self.assertEqual(body["message"], "hello")


class TestPushoverSender(unittest.TestCase):
    """Unit tests for PushoverSender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = PushoverSender(cfg)
        result = sender.send_to_pushover("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.pushover_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"status": 1})
        cfg = _config(pushover_user_key="U", pushover_api_token="T")
        sender = PushoverSender(cfg)
        result = sender.send_to_pushover("hello")
        self.assertTrue(result)
        call_data = mock_post.call_args[1]["data"]
        self.assertEqual(call_data["user"], "U")
        self.assertEqual(call_data["token"], "T")

    @mock.patch("src.notification_sender.pushover_sender.requests.post")
    def test_send_strips_hidden_market_region_metadata_from_plain_text(self, mock_post):
        mock_post.return_value = _response(200, {"status": 1})
        cfg = _config(pushover_user_key="U", pushover_api_token="T")
        sender = PushoverSender(cfg)

        result = sender.send_to_pushover("[dsa-market-region]: # (us)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        call_data = mock_post.call_args[1]["data"]
        self.assertNotIn("[dsa-market-region]", call_data["message"])
        self.assertIn("🎯 Market Review", call_data["message"])
        self.assertIn("Body", call_data["message"])

    @mock.patch("time.sleep")
    @mock.patch("src.notification_sender.pushover_sender.requests.post")
    def test_send_chunked_uses_test_timeout(self, mock_post, _mock_sleep):
        mock_post.return_value = _response(200, {"status": 1})
        cfg = _config(pushover_user_key="U", pushover_api_token="T")
        sender = PushoverSender(cfg)

        result = sender.send_to_pushover("\n\n".join(["A" * 800, "B" * 800, "C" * 800]), timeout_seconds=9)

        self.assertTrue(result)
        self.assertGreaterEqual(mock_post.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 9 for call in mock_post.call_args_list))


class TestSlackSender(unittest.TestCase):
    """Unit tests for SlackSender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertFalse(result)

    def test_is_slack_configured_webhook_only(self):
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        self.assertTrue(sender._is_slack_configured())

    def test_is_slack_configured_bot_only(self):
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        self.assertTrue(sender._is_slack_configured())

    def test_is_slack_configured_neither(self):
        cfg = _config()
        sender = SlackSender(cfg)
        self.assertFalse(sender._is_slack_configured())

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_webhook_success(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertTrue(result)
        mock_post.assert_called_once()

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_webhook_http_error_returns_false(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 400
        resp.text = "invalid_payload"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_bot_success(self, mock_post):
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertTrue(result)
        self.assertIn("chat.postMessage", mock_post.call_args[0][0])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_bot_error_returns_false(self, mock_post):
        mock_post.return_value = _response(200, {"ok": False, "error": "channel_not_found"})
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertFalse(result)

    def test_build_blocks_splits_long_content(self):
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        content = "A" * 6500  # > 3000 * 2, should produce 3 blocks
        blocks = sender._build_blocks(content)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0]["type"], "section")
        self.assertEqual(blocks[0]["text"]["type"], "mrkdwn")

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_preserves_legacy_text_payload(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)

        result = sender.send_to_slack("## 日报\n\n[详情](https://example.com/report)")

        self.assertTrue(result)
        payload = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        self.assertIn("## 日报", payload["text"])
        self.assertIn("[详情](https://example.com/report)", payload["text"])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_strips_hidden_market_region_metadata_from_text_payload(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)

        result = sender.send_to_slack("[dsa-market-region]: # (us)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        payload = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        self.assertNotIn("[dsa-market-region]", payload["text"])
        self.assertIn("# 🎯 Market Review", payload["text"])
        self.assertIn("Body", payload["text"])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_preserves_standard_markdown_reference_definitions(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)

        content = "[详情][ref]\n\n[ref]: https://example.com/report"
        result = sender.send_to_slack(content)

        self.assertTrue(result)
        payload = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        self.assertIn("[详情][ref]", payload["text"])
        self.assertIn("[ref]: https://example.com/report", payload["text"])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_text_prefers_bot_when_both_configured(self, mock_post):
        """When both webhook and bot are configured, text must go via bot
        so it lands in the same channel as images."""
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(
            slack_webhook_url="https://hooks.slack.com/services/T/B/xxx",
            slack_bot_token="xoxb-test",
            slack_channel_id="C123",
        )
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertTrue(result)
        self.assertIn("chat.postMessage", mock_post.call_args[0][0])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_image_bot_success(self, mock_post):
        # Mock three sequential calls: getUploadURLExternal, PUT upload, completeUploadExternal
        mock_post.side_effect = [
            _response(200, {"ok": True, "upload_url": "https://files.slack.com/upload/v1/test", "file_id": "F123"}),
            _response(200, {}),
            _response(200, {"ok": True}),
        ]
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        result = sender._send_slack_image(b"PNG_BYTES")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 3)
        self.assertIn("getUploadURLExternal", mock_post.call_args_list[0][0][0])
        # Step 2: upload must send raw bytes (not multipart) to match declared length
        upload_call_kwargs = mock_post.call_args_list[1][1]
        self.assertEqual(upload_call_kwargs.get("data"), b"PNG_BYTES")
        self.assertNotIn("files", upload_call_kwargs)
        self.assertIn("completeUploadExternal", mock_post.call_args_list[2][0][0])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_image_fallback_to_text_when_no_bot(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        result = sender._send_slack_image(b"PNG_BYTES", fallback_content="fallback text")
        self.assertTrue(result)


class TestTelegramSender(unittest.TestCase):
    """Unit tests for TelegramSender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("hello")
        self.assertTrue(result)
        self.assertIn("sendMessage", mock_post.call_args[0][0])

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_retries_plain_text_when_markdown_http_400(self, mock_post):
        markdown_error = _response(400)
        markdown_error.text = (
            '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'
        )
        plain_text_success = _response(200, {"ok": True})
        mock_post.side_effect = [markdown_error, plain_text_success]

        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("*ST宝实")

        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)
        first_payload = mock_post.call_args_list[0][1]["json"]
        second_payload = mock_post.call_args_list[1][1]["json"]
        self.assertEqual(first_payload["parse_mode"], "Markdown")
        self.assertNotIn("parse_mode", second_payload)
        self.assertEqual(second_payload["text"], "*ST宝实")

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_plain_text_fallback_keeps_original_text_after_legacy_markdown_error(self, mock_post):
        markdown_error = _response(400)
        markdown_error.text = (
            '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'
        )
        plain_text_success = _response(200, {"ok": True})
        mock_post.side_effect = [markdown_error, plain_text_success]

        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        content = "关注 **AAPL** (未闭合)"
        result = sender.send_to_telegram(content)

        self.assertTrue(result)
        first_payload = mock_post.call_args_list[0][1]["json"]
        second_payload = mock_post.call_args_list[1][1]["json"]
        self.assertEqual(first_payload["text"], "关注 *AAPL* \\(未闭合\\)")
        self.assertEqual(second_payload["text"], content)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_uses_legacy_telegram_report_formatter(self, mock_post):
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        content = (
            "# 日报\n\n"
            "## 📊 分析结果摘要\n\n"
            "| 股票 | 信号 |\n"
            "| --- | --- |\n"
            "| 600519 | 强势 |\n\n"
            "[详情](https://example.com/report)"
        )

        result = sender.send_to_telegram(content)

        self.assertTrue(result)
        payload = mock_post.call_args.kwargs["json"]
        rendered = payload["text"]
        self.assertIn("日报", rendered)
        self.assertIn("📊 分析结果摘要", rendered)
        self.assertIn("| 股票 | 信号 |", rendered)
        self.assertIn("[详情](https://example.com/report)", rendered)
        self.assertNotIn("# 日报", rendered)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_strips_hidden_market_region_metadata_from_text_payload(self, mock_post):
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)

        result = sender.send_to_telegram("[dsa-market-region]: # (us)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("[dsa-market-region]", payload["text"])
        self.assertIn("🎯 Market Review", payload["text"])
        self.assertIn("Body", payload["text"])

    @mock.patch.object(TelegramSender, "_send_telegram_message", return_value=True)
    def test_send_telegram_chunked_splits_large_reason_without_delimiter(self, mock_send_telegram_message):
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        long_reason = "原因过长需分段测试：" + ("A" * 5000)
        content = f"**AI 决策信号**\n- 理由: {long_reason}\n"

        result = sender._send_telegram_chunked(
            "http://api.telegram.org",
            "CHAT",
            sender._convert_to_telegram_markdown(content),
            max_length=4096,
            timeout_seconds=3,
        )

        self.assertTrue(result)
        self.assertGreaterEqual(len(mock_send_telegram_message.call_args_list), 2)
        for call in mock_send_telegram_message.call_args_list:
            sent_text = call.args[2]
            self.assertLessEqual(len(sent_text), 4096)

    @mock.patch.object(TelegramSender, "_send_telegram_message", return_value=True)
    def test_send_to_telegram_chunked_path_with_long_reason_without_delimiter(self, mock_send_telegram_message):
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        long_reason = "原因过长需分段测试：" + ("B" * 5000)
        content = f"**AI 决策信号**\n- 理由: {long_reason}\n"

        result = sender.send_to_telegram(content)

        self.assertTrue(result)
        self.assertGreaterEqual(len(mock_send_telegram_message.call_args_list), 2)
        for call in mock_send_telegram_message.call_args_list:
            sent_text = call.args[2]
            self.assertLessEqual(len(sent_text), 4096)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_chunks_by_final_markdown_payload_length(self, mock_post):
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        content = "(" * 4090

        result = sender.send_to_telegram(content)

        self.assertTrue(result)
        self.assertGreaterEqual(mock_post.call_count, 2)
        payload_texts = [call.kwargs["json"]["text"] for call in mock_post.call_args_list]
        self.assertTrue(all(len(text) <= 4096 for text in payload_texts))
        self.assertEqual("".join(payload_texts), sender._convert_to_telegram_markdown(content))

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_plain_text_fallback_handles_non_json_200(self, mock_post):
        markdown_error = _response(400)
        markdown_error.text = (
            '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'
        )
        plain_text_non_json = _response(200)
        plain_text_non_json.text = "upstream proxy error"
        plain_text_non_json.json.side_effect = ValueError("invalid json")
        mock_post.side_effect = [markdown_error, plain_text_non_json]

        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("*ST宝实")

        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
