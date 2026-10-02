# -*- coding: utf-8 -*-
"""
===================================
DingTalk platform adapter
===================================

Handles DingTalk bot webhook callbacks.

DingTalk bot documentation:
https://open.dingtalk.com/document/robots/robot-overview
"""

import hashlib
import hmac
import base64
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from urllib.parse import quote_plus

from bot.platforms.base import BotPlatform
from bot.models import BotMessage, BotResponse, WebhookResponse, ChatType

logger = logging.getLogger(__name__)


class DingtalkPlatform(BotPlatform):
    """
    DingTalk platform adapter
    
    Supports:
    - Enterprise internal bot callbacks
    - Group bot outgoing callbacks
    - Message signature verification
    
    Configuration:
    - DINGTALK_APP_KEY: app AppKey
    - DINGTALK_APP_SECRET: app AppSecret (used for signature verification)
    """
    
    def __init__(self):
        from src.config import get_config
        config = get_config()
        
        self._app_key = getattr(config, 'dingtalk_app_key', None)
        self._app_secret = getattr(config, 'dingtalk_app_secret', None)
    
    @property
    def platform_name(self) -> str:
        return "dingtalk"
    
    def verify_request(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        Verify the DingTalk request signature
        
        DingTalk signing algorithm:
        1. Get timestamp and sign
        2. Compute: base64(hmac_sha256(timestamp + "\n" + app_secret))
        3. Compare signatures
        """
        if not self._app_secret:
            logger.warning("[DingTalk] 未配置 app_secret，跳过签名验证")
            return True
        
        timestamp = headers.get('timestamp', '')
        sign = headers.get('sign', '')
        
        if not timestamp or not sign:
            logger.warning("[DingTalk] 缺少签名参数")
            return True  # May be a request that does not require a signature
        
        # Validate timestamp (valid within 1 hour)
        try:
            request_time = int(timestamp)
            current_time = int(time.time() * 1000)
            if abs(current_time - request_time) > 3600 * 1000:
                logger.warning("[DingTalk] 时间戳过期")
                return False
        except ValueError:
            logger.warning("[DingTalk] 无效的时间戳")
            return False
        
        # Compute the signature
        string_to_sign = f"{timestamp}\n{self._app_secret}"
        hmac_code = hmac.new(
            self._app_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        expected_sign = base64.b64encode(hmac_code).decode('utf-8')
        
        if sign != expected_sign:
            logger.warning(f"[DingTalk] 签名验证失败")
            return False
        
        return True
    
    def handle_challenge(self, data: Dict[str, Any]) -> Optional[WebhookResponse]:
        """DingTalk does not require URL verification"""
        return None
    
    def parse_message(self, data: Dict[str, Any]) -> Optional[BotMessage]:
        """
        Parse a DingTalk message
        
        DingTalk outgoing bot message format:
        {
            "msgtype": "text",
            "text": {
                "content": "@bot /analyze 600519"
            },
            "msgId": "xxx",
            "createAt": "1234567890",
            "conversationType": "2",  # 1=direct chat, 2=group chat
            "conversationId": "xxx",
            "conversationTitle": "group name",
            "senderId": "xxx",
            "senderNick": "user nickname",
            "senderCorpId": "xxx",
            "senderStaffId": "xxx",
            "chatbotUserId": "xxx",
            "atUsers": [{"dingtalkId": "xxx", "staffId": "xxx"}],
            "isAdmin": false,
            "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=xxx",
            "sessionWebhookExpiredTime": 1234567890
        }
        """
        # Check message type
        msg_type = data.get('msgtype', '')
        if msg_type != 'text':
            logger.debug(f"[DingTalk] 忽略非文本消息: {msg_type}")
            return None
        
        # Get message content
        text_content = data.get('text', {})
        raw_content = text_content.get('content', '')
        
        # Extract the command (strip @bot)
        content = self._extract_command(raw_content)
        
        # Check whether the bot was @-mentioned
        at_users = data.get('atUsers', [])
        mentioned = len(at_users) > 0
        
        # Conversation type
        conversation_type = data.get('conversationType', '')
        if conversation_type == '1':
            chat_type = ChatType.PRIVATE
        elif conversation_type == '2':
            chat_type = ChatType.GROUP
        else:
            chat_type = ChatType.UNKNOWN
        
        # Creation time
        create_at = data.get('createAt', '')
        try:
            timestamp = datetime.fromtimestamp(int(create_at) / 1000)
        except (ValueError, TypeError):
            timestamp = datetime.now()
        
        # Save the session webhook for replies
        session_webhook = data.get('sessionWebhook', '')
        
        return BotMessage(
            platform=self.platform_name,
            message_id=data.get('msgId', ''),
            user_id=data.get('senderId', ''),
            user_name=data.get('senderNick', ''),
            chat_id=data.get('conversationId', ''),
            chat_type=chat_type,
            content=content,
            raw_content=raw_content,
            mentioned=mentioned,
            mentions=[u.get('dingtalkId', '') for u in at_users],
            timestamp=timestamp,
            raw_data={
                **data,
                '_session_webhook': session_webhook,
            },
        )
    
    def _extract_command(self, text: str) -> str:
        """
        Extract command content (strip @bot)
        
        DingTalk @user mentions usually look like @nickname followed by a space
        """
        # Simple handling: remove the leading @xxx part
        import re
        # Match a leading @xxx (may be Chinese or English)
        text = re.sub(r'^@[\S]+\s*', '', text.strip())
        return text.strip()
    
    def format_response(
        self, 
        response: BotResponse, 
        message: BotMessage
    ) -> WebhookResponse:
        """
        Format the DingTalk response
        
        DingTalk outgoing bots can return a message directly in the response.
        Messages can also be sent asynchronously via sessionWebhook.
        
        Response format:
        {
            "msgtype": "text" | "markdown",
            "text": {"content": "xxx"},
            "markdown": {"title": "xxx", "text": "xxx"},
            "at": {"atUserIds": ["xxx"], "isAtAll": false}
        }
        """
        if not response.text:
            return WebhookResponse.success()
        
        # Build the response
        if response.markdown:
            body = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "股票分析助手",
                    "text": response.text,
                }
            }
        else:
            body = {
                "msgtype": "text",
                "text": {
                    "content": response.text,
                }
            }
        
        # @ the sender
        if response.at_user and message.user_id:
            body["at"] = {
                "atUserIds": [message.user_id],
                "isAtAll": False,
            }
        
        return WebhookResponse.success(body)
    
    def send_by_session_webhook(
        self, 
        session_webhook: str, 
        response: BotResponse,
        message: BotMessage
    ) -> bool:
        """
        Send a message via sessionWebhook
        
        Suitable for async sending or multiple messages.
        
        Args:
            session_webhook: session webhook URL provided by DingTalk
            response: response object
            message: original message object
            
        Returns:
            Whether sending succeeded
        """
        if not session_webhook:
            logger.warning("[DingTalk] 没有可用的 sessionWebhook")
            return False
        
        import requests
        
        try:
            # Build the message
            if response.markdown:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": "股票分析助手",
                        "text": response.text,
                    }
                }
            else:
                payload = {
                    "msgtype": "text",
                    "text": {
                        "content": response.text,
                    }
                }
            
            # @ the sender
            if response.at_user and message.user_id:
                payload["at"] = {
                    "atUserIds": [message.user_id],
                    "isAtAll": False,
                }
            
            # Send the request
            resp = requests.post(
                session_webhook,
                json=payload,
                timeout=10
            )
            
            if resp.status_code == 200:
                result = resp.json()
                if result.get('errcode') == 0:
                    logger.info("[DingTalk] sessionWebhook 发送成功")
                    return True
                else:
                    logger.error(f"[DingTalk] sessionWebhook 发送失败: {result}")
                    return False
            else:
                logger.error(f"[DingTalk] sessionWebhook 请求失败: {resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"[DingTalk] sessionWebhook 发送异常: {e}")
            return False
