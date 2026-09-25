# -*- coding: utf-8 -*-
"""Nhãn hiển thị của cảnh báo theo ngôn ngữ báo cáo (vi / en / zh).

Localized display labels for alert notifications, dry-run results and trigger
history. The language follows ``REPORT_LANGUAGE`` (default ``vi``); ``en`` and
``ko`` use English, ``zh`` keeps the historical Chinese labels.
"""

from __future__ import annotations

from typing import Any, Optional


_LABELS = {
    "vi": {
        "title": "Cảnh báo sự kiện",
        "default_content": "Cảnh báo đã kích hoạt",
        "watchlist": "Danh mục theo dõi",
        "holdings": "Đang nắm giữ",
        "all_accounts": "Tất cả tài khoản",
        "account": "Tài khoản {target}",
        "overflow": "Vượt giới hạn số đối tượng",
    },
    "en": {
        "title": "Event Alert",
        "default_content": "Alert triggered",
        "watchlist": "Watchlist",
        "holdings": "Holdings",
        "all_accounts": "All accounts",
        "account": "Account {target}",
        "overflow": "Expanded targets over limit",
    },
    "zh": {
        "title": "事件告警",
        "default_content": "告警已触发",
        "watchlist": "自选股",
        "holdings": "持仓",
        "all_accounts": "全部账户",
        "account": "账户 {target}",
        "overflow": "展开目标超限",
    },
}


def alert_language(config: Optional[Any] = None) -> str:
    """Trả về ngôn ngữ nhãn cảnh báo: ``vi`` / ``en`` / ``zh``."""

    if config is None:
        try:
            from src.config import get_config

            config = get_config()
        except Exception:
            config = None
    raw = str(getattr(config, "report_language", "") or "vi").strip().lower()
    if raw.startswith(("en", "ko")):
        return "en"
    if raw.startswith("zh"):
        return "zh"
    return "vi"


def alert_label(key: str, *, config: Optional[Any] = None, language: Optional[str] = None, **kwargs: Any) -> str:
    """Nhãn cảnh báo đã bản địa hoá; ``kwargs`` dùng để điền placeholder."""

    lang = language or alert_language(config)
    template = _LABELS.get(lang, _LABELS["vi"]).get(key) or _LABELS["en"][key]
    return template.format(**kwargs) if kwargs else template
