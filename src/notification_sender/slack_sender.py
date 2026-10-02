# -*- coding: utf-8 -*-
"""
Slack notification sender

Responsibilities:
1. Send Slack messages via the Slack Bot API or an Incoming Webhook
   (when both are configured, the Bot API is preferred so text and images land in the same channel)
"""
import logging
import json
from typing import Optional

import requests

from src.config import Config
from src.formatters import chunk_content_by_max_bytes, strip_hidden_markdown_metadata

logger = logging.getLogger(__name__)

# The text field of a single section block in Slack Block Kit is limited to 3000 characters
_BLOCK_TEXT_LIMIT = 3000
# The text field of Slack chat.postMessage / Webhook is limited to about 40000 characters; use 39000 to be safe
_TEXT_LIMIT = 39000


class SlackSender:

    def __init__(self, config: Config):
        """
        Initialise the Slack configuration

        Args:
            config: configuration object
        """
        self._slack_webhook_url = getattr(config, 'slack_webhook_url', None)
        self._slack_bot_token = getattr(config, 'slack_bot_token', None)
        self._slack_channel_id = getattr(config, 'slack_channel_id', None)
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)

    @property
    def _use_bot(self) -> bool:
        """Prefer the Bot API when the Bot config is complete, so text and images use the same transport."""
        return bool(self._slack_bot_token and self._slack_channel_id)

    def _is_slack_configured(self) -> bool:
        """Check whether the Slack configuration is complete (Webhook or Bot API)"""
        return self._use_bot or bool(self._slack_webhook_url)

    def send_to_slack(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        Push a message to Slack (Webhook and Bot API supported)

        Transport priority matches _send_slack_image(): Bot > Webhook,
        so text via Webhook and images via Bot never end up in different channels.

        Args:
            content: message content in Markdown

        Returns:
            Whether the send succeeded
        """
        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        if not sanitized_content:
            logger.warning("Slack 消息内容为空，跳过推送")
            return False

        # Chunk by bytes so a single message never exceeds the limit
        try:
            chunks = chunk_content_by_max_bytes(sanitized_content, _TEXT_LIMIT, add_page_marker=True)
        except Exception as e:
            logger.error(f"分割 Slack 消息失败: {e}, 尝试整段发送。")
            chunks = [sanitized_content]

        # Prefer the Bot API (consistent with _send_slack_image)
        if self._use_bot:
            return all(self._send_slack_bot(chunk, timeout_seconds=timeout_seconds) for chunk in chunks)

        # Fall back to the Webhook
        if self._slack_webhook_url:
            return all(self._send_slack_webhook(chunk, timeout_seconds=timeout_seconds) for chunk in chunks)

        logger.warning("Slack 配置不完整，跳过推送")
        return False

    def _build_blocks(self, content: str) -> list:
        """
        Build the content into Slack Block Kit format

        If the content exceeds the single section block limit, it is split into multiple blocks automatically.
        """
        blocks = []
        # Split by the block text limit
        pos = 0
        while pos < len(content):
            segment = content[pos:pos + _BLOCK_TEXT_LIMIT]
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": segment
                }
            })
            pos += _BLOCK_TEXT_LIMIT
        return blocks

    def _send_slack_webhook(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        Send a message to Slack via an Incoming Webhook

        Args:
            content: message content

        Returns:
            Whether the send succeeded
        """
        try:
            payload = {
                "text": content,
                "blocks": self._build_blocks(content),
            }
            response = requests.post(
                self._slack_webhook_url,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json; charset=utf-8'},
                timeout=timeout_seconds or 15,
                verify=self._webhook_verify_ssl,
            )
            if response.status_code == 200 and response.text == "ok":
                logger.info("Slack Webhook 消息发送成功")
                return True
            logger.error(f"Slack Webhook 发送失败: HTTP {response.status_code} {response.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Slack Webhook 发送异常: {e}")
            return False

    def _send_slack_bot(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        Send a message to Slack via the Bot API (chat.postMessage)

        Args:
            content: message content

        Returns:
            Whether the send succeeded
        """
        try:
            headers = {
                'Authorization': f'Bearer {self._slack_bot_token}',
                'Content-Type': 'application/json; charset=utf-8',
            }
            payload = {
                "channel": self._slack_channel_id,
                "text": content,
                "blocks": self._build_blocks(content),
            }
            response = requests.post(
                'https://slack.com/api/chat.postMessage',
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers=headers,
                timeout=timeout_seconds or 15,
            )
            result = response.json()
            if result.get("ok"):
                logger.info("Slack Bot 消息发送成功")
                return True
            logger.error(f"Slack Bot 发送失败: {result.get('error', 'unknown')}")
            return False
        except Exception as e:
            logger.error(f"Slack Bot 发送异常: {e}")
            return False

    def _send_slack_image(self, image_bytes: bytes, fallback_content: str = "") -> bool:
        """
        Send an image to Slack

        Bot mode uses files.getUploadURLExternal + files.completeUploadExternal
        (Slack's new file upload API); Webhook mode falls back to text.

        Args:
            image_bytes: PNG image bytes
            fallback_content: fallback text used when sending the image fails

        Returns:
            Whether the send succeeded
        """
        # Bot mode: use the new file upload API
        if self._use_bot:
            headers = {'Authorization': f'Bearer {self._slack_bot_token}'}
            try:
                # Step 1: get the upload URL
                resp1 = requests.post(
                    'https://slack.com/api/files.getUploadURLExternal',
                    headers=headers,
                    data={
                        'filename': 'report.png',
                        'length': len(image_bytes),
                    },
                    timeout=30,
                )
                result1 = resp1.json()
                if not result1.get("ok"):
                    logger.error("Slack 获取上传 URL 失败: %s", result1.get('error', 'unknown'))
                    raise RuntimeError(result1.get('error', 'unknown'))

                upload_url = result1['upload_url']
                file_id = result1['file_id']

                # Step 2: upload the file content (raw body, multipart is not allowed)
                resp2 = requests.post(
                    upload_url,
                    data=image_bytes,
                    headers={'Content-Type': 'application/octet-stream'},
                    timeout=30,
                )
                if resp2.status_code != 200:
                    logger.error("Slack 文件上传失败: HTTP %s", resp2.status_code)
                    raise RuntimeError(f"HTTP {resp2.status_code}")

                # Step 3: complete the upload and share it to the channel
                resp3 = requests.post(
                    'https://slack.com/api/files.completeUploadExternal',
                    headers={**headers, 'Content-Type': 'application/json'},
                    json={
                        'files': [{'id': file_id, 'title': '股票分析报告'}],
                        'channel_id': self._slack_channel_id,
                    },
                    timeout=30,
                )
                result3 = resp3.json()
                if result3.get("ok"):
                    logger.info("Slack Bot 图片发送成功")
                    return True
                logger.error("Slack 完成上传失败: %s", result3.get('error', 'unknown'))
            except Exception as e:
                logger.error("Slack Bot 图片发送异常: %s", e)

        # Webhook mode or Bot upload failed: fall back to text
        if fallback_content:
            logger.info("Slack 图片不支持或失败，回退为文本发送")
            return self.send_to_slack(fallback_content)

        logger.warning("Slack 图片发送失败，且无回退内容")
        return False
