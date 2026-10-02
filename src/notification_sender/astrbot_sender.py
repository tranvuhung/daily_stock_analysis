# -*- coding: utf-8 -*-
"""
AstrBot notification sender service

Responsibilities:
1. Send AstrBot messages via the AstrBot API
"""
import logging
import json
import hmac
import hashlib
from typing import Optional

import requests

from src.config import Config
from src.formatters import markdown_to_html_document, strip_hidden_markdown_metadata


logger = logging.getLogger(__name__)


class AstrbotSender:
    
    def __init__(self, config: Config):
        """
        Initialize AstrBot config

        Args:
            config: Config object
        """
        self._astrbot_config = {
            'astrbot_url': getattr(config, 'astrbot_url', None),
            'astrbot_token': getattr(config, 'astrbot_token', None),
        }
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        
    def _is_astrbot_configured(self) -> bool:
        """Check whether the AstrBot config is complete (supports Bot or Webhook)"""
        # Available as long as a URL is configured
        url_ok = bool(self._astrbot_config['astrbot_url'])
        return url_ok

    def send_to_astrbot(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        Push a message to AstrBot (supported via adapter)

        Args:
            content: Message content in Markdown format

        Returns:
            Whether the send succeeded
        """
        if self._astrbot_config['astrbot_url']:
            return self._send_astrbot(content, timeout_seconds=timeout_seconds)

        logger.warning("AstrBot 配置不完整，跳过推送")
        return False


    def _send_astrbot(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        import time
        """
        使用 Bot API 发送消息到 AstrBot

        Args:
            content: Markdown 格式的消息内容

        Returns:
            是否发送成功
        """

        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        html_content = markdown_to_html_document(sanitized_content)

        try:
            payload = {
                'content': html_content
            }
            signature =  ""
            timestamp = str(int(time.time()))
            if self._astrbot_config['astrbot_token']:
                """计算请求签名"""
                payload_json = json.dumps(payload, sort_keys=True)
                sign_data = f"{timestamp}.{payload_json}".encode('utf-8')
                key = self._astrbot_config['astrbot_token']
                signature = hmac.new(
                    key.encode('utf-8'),
                    sign_data,
                    hashlib.sha256
                ).hexdigest()
            url = self._astrbot_config['astrbot_url']
            response = requests.post(
                url, json=payload, timeout=timeout_seconds or 10,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature": signature,
                    "X-Timestamp": timestamp
                },
                verify=self._webhook_verify_ssl
            )

            if response.status_code == 200:
                logger.info("AstrBot 消息发送成功")
                return True
            else:
                logger.error(f"AstrBot 发送失败: {response.status_code} {response.text}")
                return False
        except Exception as e:
            logger.error(f"AstrBot 发送异常: {e}")
            return False
