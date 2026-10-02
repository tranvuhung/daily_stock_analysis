# -*- coding: utf-8 -*-
"""
ServerChan3 notification sender

Responsibilities:
1. Send ServerChan3 messages via the ServerChan3 API
"""
import logging
from typing import Optional
import requests
from datetime import datetime
import re

from src.config import Config
from src.formatters import strip_hidden_markdown_metadata


logger = logging.getLogger(__name__)


class Serverchan3Sender:
    
    def __init__(self, config: Config):
        """
        Initialise the ServerChan3 configuration

        Args:
            config: configuration object
        """
        self._serverchan3_sendkey = getattr(config, 'serverchan3_sendkey', None)
        
    def send_to_serverchan3(
        self,
        content: str,
        title: Optional[str] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """
        Push a message to ServerChan3

        ServerChan3 API format:
        POST https://sctapi.ftqq.com/{sendkey}.send
        or
        POST https://{num}.push.ft07.com/send/{sendkey}.send
        {
            "title": "message title",
            "desp": "message content",
            "options": {}
        }

        ServerChan3 features:
        - Domestic (China) push service supporting several Chinese vendor push channels, can push without a background app
        - Simple, easy-to-use API

        Args:
            content: message content (Markdown)
            title: message title (optional)

        Returns:
            Whether the send succeeded
        """
        if not self._serverchan3_sendkey:
            logger.warning("Server酱3 SendKey 未配置，跳过推送")
            return False

        # Resolve the message title
        if title is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
            title = f"📈 股票分析报告 - {date_str}"
        sanitized_content = strip_hidden_markdown_metadata(content).strip()

        try:
            # Build the URL based on the sendkey format
            sendkey = self._serverchan3_sendkey
            if sendkey.startswith('sctp'):
                match = re.match(r'sctp(\d+)t', sendkey)
                if match:
                    num = match.group(1)
                    url = f"https://{num}.push.ft07.com/send/{sendkey}.send"
                else:
                    logger.error("Invalid sendkey format for sctp")
                    return False
            else:
                url = f"https://sctapi.ftqq.com/{sendkey}.send"

            # Build the request parameters
            params = {
                'title': title,
                'desp': sanitized_content,
                'options': {}
            }

            # Send the request
            headers = {
                'Content-Type': 'application/json;charset=utf-8'
            }
            response = requests.post(url, json=params, headers=headers, timeout=timeout_seconds or 10)

            if response.status_code == 200:
                result = response.json()
                logger.info(f"Server酱3 消息发送成功: {result}")
                return True
            else:
                logger.error(f"Server酱3 请求失败: HTTP {response.status_code}")
                logger.error(f"响应内容: {response.text}")
                return False

        except Exception as e:
            logger.error(f"发送 Server酱3 消息失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False
