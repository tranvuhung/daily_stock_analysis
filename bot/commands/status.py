# -*- coding: utf-8 -*-
"""
===================================
Status command
===================================

Shows system running status and configuration info.
"""

import platform
import sys
from datetime import datetime
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


class StatusCommand(BotCommand):
    """
    Status command
    
    Shows system running status, including:
    - Service status
    - Configuration info
    - Available features
    """
    
    @property
    def name(self) -> str:
        return "status"
    
    @property
    def aliases(self) -> List[str]:
        return ["s", "trangthai", "info"]
    
    @property
    def description(self) -> str:
        return "Hiển thị trạng thái hệ thống"
    
    @property
    def usage(self) -> str:
        return "/status"
    
    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """Execute the status command"""
        from src.config import get_config
        
        config = get_config()
        
        # Collect status info
        status_info = self._collect_status(config)
        
        # Format output
        text = self._format_status(status_info, message.platform)
        
        return BotResponse.markdown_response(text)
    
    def _collect_status(self, config) -> dict:
        """Collect system status info"""
        from src.config import _uses_direct_env_provider, get_configured_llm_models

        status = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": platform.system(),
            "stock_count": len(config.stock_list),
            "stock_list": config.stock_list[:5],  # Show only the first 5
        }
        
        # AI configuration status
        llm_channels = getattr(config, "llm_channels", []) or []
        llm_model_list = getattr(config, "llm_model_list", []) or []
        llm_model = (getattr(config, "litellm_model", "") or "").strip()
        agent_model = (getattr(config, "agent_litellm_model", "") or "").strip()
        status["ai_primary_model"] = llm_model
        status["ai_agent_model"] = agent_model or ("Kế thừa mô hình chính" if llm_model else "")
        status["ai_channels"] = [
            str(channel.get("name") or "").strip()
            for channel in llm_channels
            if str(channel.get("name") or "").strip()
        ]
        status["ai_yaml"] = (
            getattr(config, "llm_models_source", "") == "litellm_config"
            and bool(llm_model_list)
        )
        status["ai_legacy_keys"] = {
            "Gemini": bool(getattr(config, "gemini_api_keys", [])),
            "OpenAI": bool(getattr(config, "openai_api_keys", [])),
            "Anthropic": bool(getattr(config, "anthropic_api_keys", [])),
            "DeepSeek": bool(getattr(config, "deepseek_api_keys", [])),
        }
        has_direct_env_model = bool(llm_model) and _uses_direct_env_provider(llm_model)
        available_router_model_set = set(get_configured_llm_models(llm_model_list))
        primary_model_reachable = not (
            available_router_model_set
            and llm_model
            and not _uses_direct_env_provider(llm_model)
            and llm_model not in available_router_model_set
        )
        status["ai_available"] = bool(
            llm_model
            and (has_direct_env_model or (llm_model_list and primary_model_reachable))
        )
        
        # Search service status
        status["search_tavily"] = len(config.tavily_api_keys) > 0
        status["search_brave"] = len(config.brave_api_keys) > 0
        status["search_serpapi"] = len(config.serpapi_keys) > 0
        status["search_searxng"] = config.has_searxng_enabled()
        
        # Notification channel status
        status["notify_telegram"] = bool(config.telegram_bot_token and config.telegram_chat_id)
        status["notify_email"] = bool(config.email_sender and config.email_password)
        status["notify_custom"] = bool(getattr(config, "custom_webhook_urls", []))
        status["notify_discord"] = bool(
            getattr(config, "discord_webhook_url", None)
            or (
                getattr(config, "discord_bot_token", None)
                and getattr(config, "discord_main_channel_id", None)
            )
        )
        status["notify_slack"] = bool(
            getattr(config, "slack_webhook_url", None)
            or (
                getattr(config, "slack_bot_token", None)
                and getattr(config, "slack_channel_id", None)
            )
        )
        status["notify_push"] = bool(
            getattr(config, "pushover_user_key", None)
            and getattr(config, "pushover_api_token", None)
        )
        
        return status
    
    def _format_status(self, status: dict, platform: str) -> str:
        """Format status info"""
        # Status icons
        def icon(enabled: bool) -> str:
            return "✅" if enabled else "❌"
        
        lines = [
            "📊 **Trợ lý phân tích chứng khoán - Trạng thái hệ thống**",
            "",
            f"🕐 Thời gian: {status['timestamp']}",
            f"🐍 Python: {status['python_version']}",
            f"💻 Nền tảng: {status['platform']}",
            "",
            "---",
            "",
            "**📈 Danh mục theo dõi**",
            f"• Số lượng mã: {status['stock_count']}",
        ]
        
        if status['stock_list']:
            stocks_preview = ", ".join(status['stock_list'])
            if status['stock_count'] > 5:
                stocks_preview += f" ... (tổng {status['stock_count']} mã)"
            lines.append(f"• Danh sách mã: {stocks_preview}")
        
        lines.extend([
            "",
            "**🤖 Dịch vụ phân tích AI**",
            f"• Mô hình chính: {status['ai_primary_model'] or 'Chưa cấu hình'}",
            f"• Mô hình Agent: {status['ai_agent_model'] or 'Chưa cấu hình'}",
            f"• Kênh LLM: {', '.join(status['ai_channels']) if status['ai_channels'] else 'Chưa cấu hình'}",
            f"• LiteLLM YAML: {icon(status['ai_yaml'])}",
            "• Legacy Key: "
            + ", ".join(
                f"{name}{icon(enabled)}"
                for name, enabled in status["ai_legacy_keys"].items()
            ),
            "",
            "**🔍 Dịch vụ tìm kiếm**",
            f"• Tavily: {icon(status['search_tavily'])}",
            f"• Brave: {icon(status['search_brave'])}",
            f"• SerpAPI: {icon(status['search_serpapi'])}",
            f"• SearXNG: {icon(status['search_searxng'])}",
            "",
            "**📢 Kênh thông báo**",
            f"• Telegram: {icon(status['notify_telegram'])}",
            f"• Email: {icon(status['notify_email'])}",
            f"• Webhook tuỳ chỉnh: {icon(status['notify_custom'])}",
            f"• Discord: {icon(status['notify_discord'])}",
            f"• Slack: {icon(status['notify_slack'])}",
            f"• Pushover: {icon(status['notify_push'])}",
        ])
        
        # Overall AI service status
        if status["ai_available"]:
            lines.extend([
                "",
                "---",
                "✅ **Hệ thống sẵn sàng, có thể bắt đầu phân tích!**",
            ])
        else:
            lines.extend([
                "",
                "---",
                "⚠️ **Chưa cấu hình dịch vụ AI, tính năng phân tích không khả dụng**",
                "Vui lòng cấu hình LITELLM_MODEL, LLM_CHANNELS, LITELLM_CONFIG hoặc API Key của một provider bất kỳ",
            ])
        
        return "\n".join(lines)
