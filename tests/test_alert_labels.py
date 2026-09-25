# -*- coding: utf-8 -*-
"""Tests for localized alert display labels."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.alert_labels import alert_label, alert_language


def test_alert_language_follows_report_language_and_defaults_to_vi() -> None:
    assert alert_language(SimpleNamespace(report_language="vi")) == "vi"
    assert alert_language(SimpleNamespace(report_language="en")) == "en"
    assert alert_language(SimpleNamespace(report_language="ko")) == "en"
    assert alert_language(SimpleNamespace(report_language="zh")) == "zh"
    assert alert_language(SimpleNamespace(report_language="")) == "vi"
    assert alert_language(SimpleNamespace()) == "vi"


def test_alert_labels_vietnamese_and_english() -> None:
    vi = SimpleNamespace(report_language="vi")
    en = SimpleNamespace(report_language="en")

    assert alert_label("title", config=vi) == "Cảnh báo sự kiện"
    assert alert_label("title", config=en) == "Event Alert"
    assert alert_label("account", config=vi, target="9") == "Tài khoản 9"
    assert alert_label("account", config=en, target="9") == "Account 9"
    assert alert_label("watchlist", config=vi) == "Danh mục theo dõi"
    assert alert_label("all_accounts", config=en) == "All accounts"
