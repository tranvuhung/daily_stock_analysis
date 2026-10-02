# -*- coding: utf-8 -*-
"""
WeChat notification sender service

Responsibilities:
1. Send text messages via WeCom Webhook
2. Send image messages via WeCom Webhook
"""
import logging
import base64
import hashlib
import requests
import time
from typing import Optional

from src.config import Config
from src.formatters import chunk_content_by_max_bytes, strip_hidden_markdown_metadata


logger = logging.getLogger(__name__)


# WeChat Work image msgtype limit ~2MB (base64 payload)
WECHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024

class WechatSender:
    
    def __init__(self, config: Config):
        """
        Initialize WeCom config

        Args:
            config: Config object
        """
        self._wechat_url = config.wechat_webhook_url
        self._wechat_max_bytes = getattr(config, 'wechat_max_bytes', 4000)
        self._wechat_msg_type = getattr(config, 'wechat_msg_type', 'markdown')
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        
    def send_to_wechat(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        Push a message to the WeCom bot
        
        WeCom Webhook message format:
        Supports markdown and text types; markdown cannot be displayed in WeChat, so text can be used instead,
        markdown type parses Markdown formatting, text type sends plain text directly.

        markdown type example:
        {
            "msgtype": "markdown",
            "markdown": {
                "content": "## Title\n\nContent"
            }
        }
        
        text type example:
        {
            "msgtype": "text",
            "text": {
                "content": "Content"
            }
        }

        Note: WeCom limits Markdown to 4096 bytes (not characters) and Text to 2048 bytes; overly long content is sent in batches automatically
        The limit can be adjusted via the WECHAT_MAX_BYTES environment variable
        
        Args:
            content: Message content in Markdown format
            
        Returns:
            Whether the send succeeded
        """
        if not self._wechat_url:
            logger.warning("企业微信 Webhook 未配置，跳过推送")
            return False
        
        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        if not sanitized_content:
            logger.warning("企业微信消息内容为空，跳过推送")
            return False

        # Set the limit dynamically by message type so text messages do not exceed WeCom's 2048-byte limit
        if self._wechat_msg_type == 'text':
            max_bytes = min(self._wechat_max_bytes, 2000)  # Reserve some bytes for system/pagination markers
        else:
            max_bytes = self._wechat_max_bytes  # markdown defaults to 4000 bytes

        # Check byte length; send in batches if too long
        content_bytes = len(sanitized_content.encode('utf-8'))
        if content_bytes > max_bytes:
            logger.info(f"消息内容超长({content_bytes}字节/{len(content)}字符)，将分批发送")
            return self._send_wechat_chunked(sanitized_content, max_bytes)
        
        try:
            return self._send_wechat_message(sanitized_content, timeout_seconds=timeout_seconds)
        except Exception as e:
            logger.error(f"发送企业微信消息失败: {e}")
            return False

    def _send_wechat_image(self, image_bytes: bytes) -> bool:
        """Send image via WeChat Work webhook msgtype image (Issue #289)."""
        if not self._wechat_url:
            return False
        if len(image_bytes) > WECHAT_IMAGE_MAX_BYTES:
            logger.warning(
                "企业微信图片超限 (%d > %d bytes)，拒绝发送，调用方应 fallback 为文本",
                len(image_bytes), WECHAT_IMAGE_MAX_BYTES,
            )
            return False
        try:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            md5_hash = hashlib.md5(image_bytes).hexdigest()
            payload = {
                "msgtype": "image",
                "image": {"base64": b64, "md5": md5_hash},
            }
            response = requests.post(
                self._wechat_url, json=payload, timeout=30, verify=self._webhook_verify_ssl
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    logger.info("企业微信图片发送成功")
                    return True
                logger.error("企业微信图片发送失败: %s", result.get("errmsg", ""))
            else:
                logger.error("企业微信请求失败: HTTP %s", response.status_code)
            return False
        except Exception as e:
            logger.error("企业微信图片发送异常: %s", e)
            return False
    
    def _send_wechat_message(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """Send a WeCom message"""
        payload = self._gen_wechat_payload(content)
        
        response = requests.post(
            self._wechat_url,
            json=payload,
            timeout=timeout_seconds or 10,
            verify=self._webhook_verify_ssl
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('errcode') == 0:
                logger.info("企业微信消息发送成功")
                return True
            else:
                logger.error(f"企业微信返回错误: {result}")
                return False
        else:
            logger.error(f"企业微信请求失败: {response.status_code}")
            return False
        
    def _send_wechat_chunked(self, content: str, max_bytes: int) -> bool:
        """
        Send a long message to WeCom in batches
        
        Splits smartly by stock analysis blocks (separated by --- or ###) so each batch stays within the limit
        
        Args:
            content: Full message content
            max_bytes: Maximum bytes per message
            
        Returns:
            Whether all batches were sent successfully
        """
        chunks = chunk_content_by_max_bytes(content, max_bytes, add_page_marker=True)
        total_chunks = len(chunks)
        success_count = 0
        for i, chunk in enumerate(chunks):
            if self._send_wechat_message(chunk):
                success_count += 1
            else:
                logger.error(f"企业微信第 {i+1}/{total_chunks} 批发送失败")
            if i < total_chunks - 1:
                time.sleep(1)
        return success_count == len(chunks)

    def _gen_wechat_payload(self, content: str) -> dict:
        """Build the WeCom message payload"""
        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        if self._wechat_msg_type == 'text':
            return {
                "msgtype": "text",
                "text": {
                    "content": sanitized_content
                }
            }
        else:
            return {
                "msgtype": "markdown",
                "markdown": {
                    "content": sanitized_content
                }
            }
