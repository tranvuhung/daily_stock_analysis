# -*- coding: utf-8 -*-
"""
===================================
Platform adapter base class
===================================

Defines the abstract base class for platform adapters; every platform must subclass it.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple

from bot.models import BotMessage, BotResponse, WebhookResponse


class BotPlatform(ABC):
    """
    Abstract base class for platform adapters
    
    Responsibilities:
    1. Verify Webhook request signatures
    2. Parse platform messages into the unified format
    3. Convert responses into the platform format
    
    Example:
        class MyPlatform(BotPlatform):
            @property
            def platform_name(self) -> str:
                return "myplatform"
            
            def verify_request(self, headers, body) -> bool:
                # Signature verification logic
                return True
            
            def parse_message(self, data) -> Optional[BotMessage]:
                # Message parsing logic
                return BotMessage(...)
            
            def format_response(self, response, message) -> WebhookResponse:
                # Response formatting logic
                return WebhookResponse.success({"text": response.text})
    """
    
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """
        Platform identifier name
        
        Used for route matching and log identification, e.g. "feishu", "dingtalk"
        """
        pass
    
    @abstractmethod
    def verify_request(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        Verify the request signature
        
        Each platform has its own signature scheme and needs its own implementation.
        
        Args:
            headers: HTTP request headers
            body: raw request body bytes
            
        Returns:
            Whether the signature is valid
        """
        pass
    
    @abstractmethod
    def parse_message(self, data: Dict[str, Any]) -> Optional[BotMessage]:
        """
        Parse a platform message into the unified format
        
        Converts the platform-specific message format into a BotMessage.
        Returns None if the message type does not need handling (e.g. event callbacks).
        
        Args:
            data: parsed JSON data
            
        Returns:
            A BotMessage object, or None (no handling needed)
        """
        pass
    
    @abstractmethod
    def format_response(
        self, 
        response: BotResponse, 
        message: BotMessage
    ) -> WebhookResponse:
        """
        Convert a unified response into the platform format
        
        Args:
            response: unified response object
            message: original message object (used to get the reply target, etc.)
            
        Returns:
            A WebhookResponse object
        """
        pass
    
    def send_followup(
        self,
        response: 'BotResponse',
        message: 'BotMessage',
    ) -> bool:
        """Send a follow-up message after a deferred webhook response.

        Override in platforms that return a deferred acknowledgement
        (e.g. Discord type 5) so the final command result can be delivered
        asynchronously.  The default implementation is a no-op.

        Returns:
            ``True`` if the follow-up was sent successfully.
        """
        return False

    def handle_challenge(self, data: Dict[str, Any]) -> Optional[WebhookResponse]:
        """
        Handle a platform verification request
        
        Some platforms send a verification request when the Webhook is configured and expect a specific response.
        Subclasses may override this method.
        
        Args:
            data: request data
            
        Returns:
            A verification response, or None (not a verification request)
        """
        return None
    
    def handle_webhook(
        self, 
        headers: Dict[str, str], 
        body: bytes,
        data: Dict[str, Any]
    ) -> Tuple[Optional[BotMessage], Optional[WebhookResponse]]:
        """
        Handle a Webhook request
        
        This is the main entry point; it coordinates verification, parsing, and so on.
        
        Args:
            headers: HTTP request headers
            body: raw request body bytes
            data: parsed JSON data
            
        Returns:
            A (BotMessage, WebhookResponse) tuple
            - Verification request: (None, challenge_response)
            - Regular message: (message, None) - the response is generated after command handling
            - Verification failed or nothing to handle: (None, error_response or None)
        """
        # 1. Check whether this is a verification request
        challenge_response = self.handle_challenge(data)
        if challenge_response:
            return None, challenge_response
        
        # 2. Verify the request signature
        if not self.verify_request(headers, body):
            return None, WebhookResponse.error("Invalid signature", 403)
        
        # 3. Parse the message
        message = self.parse_message(data)
        
        return message, None
