# -*- coding: utf-8 -*-

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.share_image import (
    DEFAULT_XIAOHONGSHU_HANDLE,
    DEFAULT_XIAOHONGSHU_QR_PATH,
    PROJECT_DISPLAY_NAME,
    PROJECT_REPOSITORY,
    ShareImageBranding,
    build_share_image_html,
    share_image_branding_from_config,
)

XIAOHONGSHU_HANDLE = "@示例账号"
XIAOHONGSHU_BRANDING = ShareImageBranding(
    xiaohongshu_url="https://example.com/xiaohongshu",
    xiaohongshu_handle=XIAOHONGSHU_HANDLE,
    xiaohongshu_id="123456",
    xiaohongshu_qr_path=str(
        Path(__file__).parents[1] / "src" / "assets" / "share_image" / "xiaohongshu_qr.jpg"
    ),
)


def test_stock_share_image_uses_configured_xiaohongshu_branding():
    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        generated_on=date(2026, 7, 31),
        branding=XIAOHONGSHU_BRANDING,
    )

    assert 'class="poster stock"' in html
    assert "Thẻ quyết định cổ phiếu" in html
    assert "贵州茅台" in html
    assert '<span class="code">600519</span>' in html
    assert html.count('class="qr-frame"') == 1
    assert html.count("data:image/jpeg;base64,") == 1
    assert "项目主页二维码" not in html
    assert "GitHub 项目" not in html
    assert PROJECT_REPOSITORY in html
    assert "Xiaohongshu QR" in html
    assert PROJECT_DISPLAY_NAME in html
    assert f"<b>Xiaohongshu</b>{XIAOHONGSHU_HANDLE}" in html
    assert "123456" not in html
    assert 'href="https://example.com/xiaohongshu"' in html
    assert "2026-07-31" in html
    assert html.count("<h1>") == 1


def test_stock_share_image_omits_unconfigured_social_account():
    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="qr-card' not in html
    assert "Xiaohongshu" not in html
    assert 'class="footer-brand full"' in html


def test_runtime_branding_defaults_to_bundled_xiaohongshu_qr():
    branding = share_image_branding_from_config(object())

    assert branding.xiaohongshu_handle == DEFAULT_XIAOHONGSHU_HANDLE
    assert branding.xiaohongshu_id == ""
    assert branding.xiaohongshu_qr_path == DEFAULT_XIAOHONGSHU_QR_PATH
    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        generated_on=date(2026, 7, 31),
        branding=branding,
    )

    assert html.count('class="qr-frame"') == 1
    assert html.count("data:image/jpeg;base64,") == 1
    assert f"<b>Xiaohongshu</b>{DEFAULT_XIAOHONGSHU_HANDLE}" in html
    assert " ID " not in html


def test_runtime_branding_does_not_mix_default_qr_with_custom_identity():
    branding = share_image_branding_from_config(
        SimpleNamespace(
            share_image_xiaohongshu_url="https://example.com/custom",
            share_image_xiaohongshu_handle="@自定义账号",
            share_image_xiaohongshu_id="custom-id",
        )
    )

    assert branding.xiaohongshu_id == "custom-id"
    assert branding.xiaohongshu_qr_path == ""

    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        generated_on=date(2026, 7, 31),
        branding=branding,
    )

    assert "<b>Xiaohongshu</b>@自定义账号" in html
    assert "custom-id" not in html
    assert "data:image/jpeg;base64," not in html
    assert DEFAULT_XIAOHONGSHU_HANDLE not in html


def test_runtime_branding_does_not_mix_default_pair_with_custom_handle_or_url():
    branding = share_image_branding_from_config(
        SimpleNamespace(
            share_image_xiaohongshu_url="https://example.com/custom",
            share_image_xiaohongshu_handle="@自定义账号",
        )
    )

    assert branding.xiaohongshu_handle == "@自定义账号"
    assert branding.xiaohongshu_id == ""
    assert branding.xiaohongshu_qr_path == ""

    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        generated_on=date(2026, 7, 31),
        branding=branding,
    )

    assert "@自定义账号" in html
    assert 'href="https://example.com/custom"' in html
    assert "data:image/jpeg;base64," not in html
    assert DEFAULT_XIAOHONGSHU_HANDLE not in html


def test_runtime_branding_does_not_mix_default_handle_with_custom_qr():
    branding = share_image_branding_from_config(
        SimpleNamespace(
            share_image_xiaohongshu_qr_path=str(
                Path(__file__).parents[1] / "src" / "assets" / "share_image" / "xiaohongshu_qr.jpg"
            ),
        )
    )

    assert branding.xiaohongshu_id == ""
    assert branding.xiaohongshu_qr_path

    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        generated_on=date(2026, 7, 31),
        branding=branding,
    )

    assert html.count("data:image/jpeg;base64,") == 1
    assert DEFAULT_XIAOHONGSHU_HANDLE not in html


def test_runtime_branding_treats_whitespace_only_values_as_unconfigured():
    branding = share_image_branding_from_config(
        SimpleNamespace(
            share_image_xiaohongshu_url="  ",
            share_image_xiaohongshu_handle="  ",
            share_image_xiaohongshu_id="  ",
            share_image_xiaohongshu_qr_path="  ",
        )
    )

    assert branding.xiaohongshu_handle == DEFAULT_XIAOHONGSHU_HANDLE
    assert branding.xiaohongshu_id == ""
    assert branding.xiaohongshu_qr_path == DEFAULT_XIAOHONGSHU_QR_PATH


def test_stock_share_image_does_not_link_unsafe_social_url():
    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        branding=ShareImageBranding(
            xiaohongshu_url="javascript:alert(1)",
            xiaohongshu_handle="@自定义账号",
        ),
    )

    assert "@自定义账号" in html
    assert "javascript:" not in html


def test_stock_share_image_prefers_structured_json_and_compacts_trade_points():
    payload = {
        "name": "中钨高新",
        "code": "000657",
        "sentiment_score": 40,
        "trend_prediction": "看空",
        "confidence_level": "中",
        "operation_advice": "观望",
        "current_price": 49.51,
        "change_pct": 2.29,
        "dashboard": {
            "core_conclusion": {
                "one_sentence": "空头趋势未止跌，冲高回落收最低，观望待企稳",
                "position_advice": {
                    "no_position": "等待右侧企稳信号（收复MA5并放量或回踩47.5缩量止跌）后再考虑",
                    "has_position": "反弹至MA10（55元附近）逢高减仓，跌破47.47前低务必止损离场",
                },
            },
            "data_perspective": {
                "trend_status": {"ma_alignment": "空头排列 MA5<MA10<MA20", "trend_score": 25},
                "price_position": {"support_level": 47.47, "resistance_level": 53.24, "bias_ma5": -6.02},
                "volume_analysis": {"volume_ratio": 1.13, "turnover_rate": 4.48},
            },
            "intelligence": {
                "positive_catalysts": ["2026H1归母净利预增261%~298%，基本面景气度高"],
                "risk_alerts": ["近7个交易日累计下跌16.07%，趋势破位未止跌"],
            },
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "暂无理想买点：需先收复并站稳MA5（52.68）且放量",
                    "secondary_buy": "放量收复MA10（55.13）确认趋势反转后介入",
                    "stop_loss": "跌破47.47即止损",
                    "take_profit": "第一压力53.24-55.13，第二压力63.76",
                }
            },
            "phase_decision": {
                "action_window": "非交易日观察（周六休市）",
                "next_check_time": "2026-08-03 09:15集合竞价及09:30开盘",
                "watch_conditions": [
                    "08-03能否守住47.47前低支撑，不破为短线企稳前提",
                    "能否放量收复MA5（52.68），上攻53.24-55.13压力区",
                ],
            },
        },
    }

    html = build_share_image_html(
        "# 中钨高新 000657 分析报告\n\n## 核心结论\n\n观望",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "中钨高新" in html
    assert 'class="signal-score warning"' in html
    assert 'class="signal-trend negative"' in html
    assert "Độ tin cậy 中" in html
    assert 'class="metric red"><span>Biến động</span><strong>+2.29%</strong>' in html
    assert "等待企稳" in html
    assert "Điểm mua xác nhận" in html
    assert "Điểm mua xác nhận</span><strong>55.13" in html
    assert "47.47" in html
    assert "53.24–55.13" in html
    assert "25/100" in html
    assert "-6.02%" in html
    assert "Theo dõi tiếp theo" in html
    assert "非交易日观察" in html
    assert "2026-08-03 09:15集合竞价" in html
    assert "需先收复并站稳" not in html
    assert "建仓策略" not in html
    assert "风控策略" not in html


def test_stock_share_image_merges_partial_intelligence_lists_with_markdown_fallback():
    payload = {
        "name": "中钨高新",
        "code": "000657",
        "dashboard": {
            "intelligence": {
                "positive_catalysts": ["订单修复信号开始出现，板块景气边际改善"],
                "risk_alerts": [],
            },
        },
    }

    html = build_share_image_html(
        """# 中钨高新 000657 分析报告

## 重要信息

**利好催化：**
- 钨价企稳叠加军工需求回暖，盈利弹性有望释放。
- 海外订单恢复，出口占比回升。

## 风险提示

- 下游需求恢复不及预期。
- 跌破关键支撑后可能继续放量回撤。
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "订单修复信号开始出现" in html
    assert "钨价企稳叠加军工需求回暖" in html
    assert "下游需求恢复不及预期" in html
    assert "跌破关键支撑后可能继续放量回撤" in html


def test_market_share_image_hides_unavailable_dimensions_and_uses_payload_color_scheme():
    payload = {
        "kind": "market_review",
        "region": "vn",
        "color_scheme": "red_up",
        "market_light": {
            "score": 65,
            "dimensions": {
                "breadth": {"score": 50, "available": False},
                "index": {"score": 72, "available": True},
                "limit": {"score": 50, "available": False},
            },
        },
        "indices": [
            {"name": "VN-Index", "current": 6500, "change_pct": 0.8},
        ],
    }

    html = build_share_image_html(
        "# Vietnam Market Recap\n\n## Major Indices\n\n- VN-Index closed higher.",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "Index Strength" in html
    assert "72/100" in html
    assert "Breadth Score" not in html
    assert "Limit Structure" not in html
    assert '<strong class="red">+0.80%</strong>' in html


def test_market_share_image_skips_unavailable_market_light_overlay():
    payload = {
        "kind": "market_review",
        "region": "vn",
        "market_light": {
            "score": 50,
            "temperature_label": "mixed",
            "label": "balanced",
            "data_quality": "unavailable",
            "dimensions": {
                "breadth": {"score": 50, "available": False},
                "index": {"score": 50, "available": False},
                "limit": {"score": 50, "available": False},
            },
        },
    }

    html = build_share_image_html(
        """# Vietnam Market Recap

> Thiếu dữ liệu chỉ số, chờ bổ sung trước khi đánh giá.
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert 'class="poster market"' in html
    assert 'class="market-signal"' not in html
    assert '<div class="market-label">balanced</div>' not in html
    assert "<small>/100</small>" not in html


def test_generic_share_image_wraps_long_preformatted_lines_and_urls_in_fallback():
    html = build_share_image_html(
        """# 完整报告

## 原始链接

https://example.com/this-is-a-very-long-url-with-no-natural-breakpoints/abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ

## 调试片段

```text
TRACEBACK_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz_TRACEBACK_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz
```
""",
        generated_on=date(2026, 8, 1),
    )

    assert 'class="poster dashboard"' in html
    assert '<section class="report-fallback">' in html
    assert "<pre>" in html
    assert "<code>" in html
    assert ".report-content{overflow-wrap:anywhere}" in html
    assert ".report-content h1,.report-content h2,.report-content h3{color:#153d78;overflow-wrap:anywhere;word-break:break-word}" in html
    assert ".report-content p,.report-content li,.report-content th,.report-content td,.report-content blockquote,.report-content a{overflow-wrap:anywhere;word-break:break-word}" in html
    assert ".report-content pre{max-width:100%;" in html
    assert "white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word" in html


def test_real_single_stock_shape_uses_h2_title_score_and_sniper_contract():
    html = build_share_image_html(
        """## 🟢 贵州茅台 (600519)

> 2026-07-31 15:30 | 评分: **72** | 看多

### 📌 核心结论

**买入**: 回调到支撑区可分批关注。

### 🎯 操作点位

| 操作点位 | 当前价格 |
| --- | --- |
| 🎯 理想买入点 | 1420-1450 |
| 🔵 次优买入点 | 1380-1400 |
| 🛑 止损位 | 1350 |
| 🎊 目标位 | 1580 |
""",
        generated_on=date(2026, 7, 31),
    )

    assert "贵州茅台" in html
    assert '<span class="code">600519</span>' in html
    assert "Thẻ quyết định cổ phiếu" in html
    assert '<strong>72</strong><small>/100</small>' in html
    assert "回调到支撑区可分批关注" in html
    assert "综合评分" not in html
    assert 'class="signal-trend positive"' in html
    assert ">看多<" in html
    assert "sniper-table" in html
    assert 'class="metric sniper buy"' in html
    assert 'class="metric sniper stop"' in html
    assert 'class="metric sniper target"' in html


def test_stock_share_image_reads_notification_service_chinese_market_snapshot_heading():
    html = build_share_image_html(
        """# 贵州茅台 600519 分析报告

## 核心结论

**买入**：回调到支撑区可分批关注。

### 当日行情

| 当前价 | 涨跌幅 | 量比 | 换手率 | 行情来源 |
| --- | --- | --- | --- | --- |
| 1425.00 | +1.20% | 1.35 | 0.82% | 腾讯财经 |
""",
        generated_on=date(2026, 7, 31),
    )

    assert "Ảnh chụp thị trường" in html
    assert "1425.00" in html
    assert "+1.20%" in html
    assert "1.35" in html
    assert "0.82%" in html
    assert "Nguồn dữ liệu: 腾讯财经" in html


def test_stock_share_image_reads_close_only_market_snapshot_column():
    html = build_share_image_html(
        """# 贵州茅台 600519 分析报告

## 核心结论

**买入**：回调到支撑区可分批关注。

### 当日行情

| 收盘 | 涨跌幅 | 量比 | 换手率 |
| --- | --- | --- | --- |
| 1420.00 | +0.90% | 1.18 | 0.76% |
""",
        generated_on=date(2026, 7, 31),
    )

    assert "Ảnh chụp thị trường" in html
    assert "1420.00" in html
    assert "+0.90%" in html
    assert "1.18" in html
    assert "0.76%" in html


def test_partial_stock_payload_preserves_markdown_snapshot_technical_and_trade_points():
    payload = {
        "name": "贵州茅台",
        "code": "600519",
        "dashboard": {
            "core_conclusion": {"one_sentence": "等待支撑确认。"},
        },
    }
    html = build_share_image_html(
        """# 贵州茅台 600519 分析报告

## 核心结论

**买入**：等待支撑确认。

### 当日行情

| 当前价 | 涨跌幅 | 量比 | 换手率 |
| --- | --- | --- | --- |
| 1425.00 | +1.20% | 1.35 | 0.82% |

### 数据透视

**成交量**: 量比 1.35 | 换手率 0.82%

### 作战计划

| 点位类型 | 价格 |
| --- | --- |
| 理想买入点 | 1420-1450 |
| 止损位 | 1350 |
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "1425.00" in html
    assert "+1.20%" in html
    assert "量比 1.35" in html
    assert "<strong>1420</strong>" in html
    assert "1350" in html


def test_complete_stock_volume_payload_does_not_duplicate_verbose_volume_card():
    payload = {
        "name": "贵州茅台",
        "code": "600519",
        "dashboard": {
            "data_perspective": {
                "volume_analysis": {
                    "volume_ratio": 1.35,
                    "turnover_rate": 0.82,
                },
            },
        },
    }
    html = build_share_image_html(
        """# 贵州茅台 600519 分析报告

## 核心结论

**持有**：继续观察。

### 数据透视

**成交量**: 量比 1.35，量能正常，较 5 日均量放大约 5% | 换手率 0.82%
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "较 5 日均量放大" not in html
    assert "1.35" in html
    assert "0.82%" in html


def test_stock_payload_market_snapshot_overlays_only_populated_fields():
    payload = {
        "name": "贵州茅台",
        "code": "600519",
        "dashboard": {},
        "market_snapshot": {"price": "1430.00", "source": "tencent"},
    }
    html = build_share_image_html(
        """# 贵州茅台 600519 分析报告

## 核心结论

**持有**：继续观察。

### 当日行情

| 当前价 | 涨跌幅 | 量比 | 换手率 |
| --- | --- | --- | --- |
| 1425.00 | +1.20% | 1.35 | 0.82% |
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "1430.00" in html
    assert "1425.00" not in html
    assert "+1.20%" in html
    assert "1.35" in html
    assert "0.82%" in html
    assert "Nguồn dữ liệu: tencent" in html


def test_stock_share_image_prefers_realtime_price_over_close_in_combined_snapshot():
    html = build_share_image_html(
        """# 贵州茅台 600519 分析报告

## 核心结论

**买入**：回调到支撑区可分批关注。

### 当日行情

| 收盘 | 当前价 | 涨跌幅 | 量比 | 换手率 |
| --- | --- | --- | --- | --- |
| 1420.00 | 1425.00 | +1.20% | 1.35 | 0.82% |
""",
        generated_on=date(2026, 7, 31),
    )

    assert "Ảnh chụp thị trường" in html
    assert "1425.00" in html
    assert "1420.00" not in html


def test_stock_share_image_reads_chinese_volume_label_from_data_view():
    html = build_share_image_html(
        """## 🟢 贵州茅台 (600519)

> 2026-07-31 15:30 | 评分: **72** | 看多

### 📌 核心结论

**买入**: 回调到支撑区可分批关注。

### 📊 数据透视

**均线排列**: 多头排列

**成交量**: 量比 1.35 (放量) | 换手率 0.82%
""",
        generated_on=date(2026, 7, 31),
    )

    assert "Tham chiếu kỹ thuật" in html
    assert ">Khối lượng<" in html
    assert "1.35" in html
    assert "0.82%" in html


def test_share_image_escapes_title_but_keeps_markdown_body_markup():
    html = build_share_image_html(
        "# AAPL <script>alert(1)</script>\n\n**结论**：关注。",
        generated_on=date(2026, 7, 31),
    )

    assert "AAPL alert(1)" in html
    assert "<script>" not in html
    assert "<strong>结论</strong>" in html


def test_english_company_name_is_not_mistaken_for_a_ticker():
    html = build_share_image_html(
        "## Apple Inc. (AAPL)\n\n> 2026-07-31 | Score: **70** | Bullish",
        generated_on=date(2026, 7, 31),
    )

    assert "Apple Inc." in html
    assert '<span class="code">AAPL</span>' in html


def test_dotted_us_ticker_is_preserved_in_stock_heading():
    html = build_share_image_html(
        "## Berkshire Hathaway (BRK.B)\n\n> 2026-07-31 | Score: **70** | Bullish",
        generated_on=date(2026, 7, 31),
    )

    assert "Berkshire Hathaway" in html
    assert '<span class="code">BRK.B</span>' in html


@pytest.mark.parametrize(
    ("heading", "company", "code"),
    [
        ("## 台积电 (2330.TW)", "台积电", "2330.TW"),
        ("## 台塑化 (6505.TWO)", "台塑化", "6505.TWO"),
        ("## 国泰永续高股息 (00878.TW)", "国泰永续高股息", "00878.TW"),
    ],
)


def test_suffix_stock_code_is_preserved_in_stock_heading(heading, company, code):
    html = build_share_image_html(
        f"{heading}\n\n> 2026-07-31 | Score: **70** | Bullish",
        generated_on=date(2026, 7, 31),
    )

    assert company in html
    assert f'<span class="code">{code}</span>' in html


def test_stock_report_market_snapshot_does_not_route_to_market_poster():
    html = build_share_image_html(
        """# Apple Inc. (AAPL)

## Core Conclusion

**Buy**: Pullbacks into support remain actionable.

## Market Snapshot

| Current Price | Change % | Volume Ratio | Source |
| --- | --- | --- | --- |
| 210.15 | +1.5% | 1.2x | IEX |

## Action Levels

| Ideal Entry | Secondary Entry | Stop Loss | Target |
| --- | --- | --- | --- |
| 205-207 | 202-204 | 198 | 220 |

## Risk Alerts

- A failed earnings guide can break momentum.
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster stock"' in html
    assert "Stock decision card" in html
    assert "Apple Inc." in html
    assert "Ideal Entry" in html
    assert "failed earnings guide" in html


@pytest.mark.parametrize("action", ["强烈买入", "Strong Buy"])


def test_stock_share_image_preserves_compound_buy_action(action):
    html = build_share_image_html(
        f"## Apple (AAPL)\n\n## Core Conclusion\n\n**{action}**: Momentum remains constructive.",
        generated_on=date(2026, 8, 1),
    )

    assert f'<div class="action-chip positive">{action}</div>' in html


def test_stock_share_image_finds_action_after_other_bold_labeled_fields():
    html = build_share_image_html(
        """## Apple (AAPL)

## Core Conclusion

**One-line Decision**: Wait for confirmation.

**Strong Buy**: Momentum remains constructive.
""",
        generated_on=date(2026, 8, 1),
    )

    assert '<div class="action-chip positive">Strong Buy</div>' in html


def test_english_breadth_line_populates_structured_market_breadth_cards():
    html = build_share_image_html(
        """# Vietnam Market Recap

## 2026-07-31 Vietnam Market Recap

> Breadth and liquidity both improved.

### 1. Market Summary

- **Market Signal**: 66/100 (constructive, risk-on)
- **Breadth**: Advancers 3200 / Decliners 1800 / Flat 100; Limit-up 88 / Limit-down 5; Turnover 21345 (VND bn)

### 2. Major Indices

| Index | Last | Change % |
| --- | --- | --- |
| VN-Index | 6500 | +0.80% |
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster market"' in html
    assert "Vietnam Market Recap" in html
    assert "Market Breadth" in html
    assert "3200" in html
    assert "1800" in html
    assert "88" in html
    assert "21345 (VND bn)" in html


def test_market_share_image_keeps_signal_card_without_breadth():
    html = build_share_image_html(
        """# Vietnam Market Recap

## 2026-07-31 Vietnam Market Recap

> Banks outperformed into the close.

### 1. Market Summary

- **Market Signal**: 58/100 (neutral, selective)
- **Drivers**: VN-Index held above prior support; average major-index change +0.42%
- **Guidance**: Signals are mixed; keep position sizing moderate and wait for confirmation.

### 2. Major Indices

| Index | Last | Change % |
| --- | --- | --- |
| VN-Index | 18200 | +1.20% |
| VN30 | 3900 | +1.85% |
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster market"' in html
    assert '<section class="market-signal">' in html
    assert '<strong>58</strong><small>/100</small>' in html
    assert "selective" in html
    assert "VN-Index held above prior support" not in html
    assert "Signals are mixed" in html
    assert "Độ rộng thị trường" not in html


def test_market_share_image_parses_fallback_major_indices_bullets():
    html = build_share_image_html(
        """# Vietnam Market Recap

## 2026-07-31 Vietnam Market Recap

> Breadth and liquidity both improved.

### 1. Market Summary

- **Market Signal**: 66/100 (constructive, risk-on)
- **Drivers**: Large-cap banks led into the close.
- **Guidance**: Stay selective and wait for confirmation.

### 2. Major Indices
- **VN-Index**: 5000.00 (↑0.50%)
- **VN30**: 18000.00 (↓0.20%)
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster market"' in html
    assert "VN-Index" in html
    assert "5000.00" in html
    assert "↑0.50%" in html
    assert "VN30" in html
    assert "↓0.20%" in html


def test_arrow_only_fallback_keeps_default_gain_color_when_first_index_declines():
    html = build_share_image_html(
        """# Vietnam Market Recap

## Market Summary

- **Breadth**: Advancers 1200 / Decliners 3800

## Major Indices

- **VN-Index**: 5000.00 (↓0.80%)
""",
        generated_on=date(2026, 8, 1),
    )

    assert 'class="metric green"><span>Advancers</span>' in html
    assert 'class="metric red"><span>Decliners</span>' in html


def test_market_share_image_preserves_red_up_colors_for_english_fallback_index_bullets():
    html = build_share_image_html(
        """# Vietnam Market Recap

## 2026-07-31 Vietnam Market Recap

> Breadth improved while leadership stayed concentrated.

### 1. Market Summary

- **Market Signal**: 66/100 (constructive, risk-on)
- **Breadth**: Advancers 3200 / Decliners 1800; Limit-up 88 / Limit-down 5

### 2. Major Indices

- **VN-Index**: 5000.00 (🔴 +0.80%)
- **VN30**: 18000.00 (🟢 -0.20%)
""",
        generated_on=date(2026, 7, 31),
    )

    assert '<strong class="red">🔴 +0.80%</strong>' in html
    assert '<strong class="green">🟢 -0.20%</strong>' in html
    assert 'class="metric red"><span>Advancers</span>' in html
    assert 'class="metric green"><span>Decliners</span>' in html


def test_single_stock_dashboard_title_routes_to_stock_poster():
    html = build_share_image_html(
        """# 2026-07-31 Decision Dashboard

## Apple Inc. (AAPL)

> 2026-07-31 15:30 | Score: **70** | Bullish

### Core Conclusion

**Buy**: Pullbacks into support remain actionable.

### Action Levels

| Ideal Entry | Secondary Entry | Stop Loss | Target |
| --- | --- | --- | --- |
| 205-207 | 202-204 | 198 | 220 |
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster stock"' in html
    assert "Stock decision card" in html
    assert "Apple Inc." in html
    assert '<span class="code">AAPL</span>' in html


def test_single_stock_dashboard_prefers_parenthesized_numeric_code_after_st_prefix():
    html = build_share_image_html(
        """# 2026-07-31 决策仪表盘

## 🟡 *ST 海越 (600387)

> 2026-07-31 15:30 | 评分：**62** | 中性

### 核心结论

**观望**：等待趋势进一步确认。
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster stock"' in html
    assert "Thẻ quyết định cổ phiếu" in html
    assert "ST 海越" in html
    assert '<span class="code">600387</span>' in html


def test_single_stock_dashboard_uses_trailing_parenthesized_us_ticker():
    html = build_share_image_html(
        """# 2026-07-31 Decision Dashboard

## IBM (IBM)

> 2026-07-31 15:30 | Score: **64** | Neutral

### Core Conclusion

**Hold**: Wait for a clearer breakout.
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster stock"' in html
    assert "Stock decision card" in html
    assert "IBM" in html
    assert '<span class="code">IBM</span>' in html


def test_single_stock_dashboard_uses_numeric_code_prefix_with_trailing_name():
    html = build_share_image_html(
        """# 2026-07-31 决策仪表盘

## 600519 贵州茅台

> 2026-07-31 15:30 | 评分：**72** | 看多

### 核心结论

**买入**：回调到支撑区可分批关注。
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster stock"' in html
    assert "Thẻ quyết định cổ phiếu" in html
    assert "贵州茅台" in html
    assert '<span class="code">600519</span>' in html


def test_multi_stock_daily_report_uses_generic_poster_and_keeps_all_stocks():
    html = build_share_image_html(
        """# 股票分析报告

## 📈 股票分析报告

### 贵州茅台 (600519)

**操作建议：买入** | **评分：72**

### 宁德时代 (300750)

**操作建议：观望** | **评分：61**
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster dashboard"' in html
    assert "贵州茅台" in html
    assert "宁德时代" in html
    assert '<span class="code">' not in html


def test_brief_aggregate_report_without_single_stock_heading_uses_generic_poster():
    html = build_share_image_html(
        """# Stock Analysis Report

## Summary

- Buy leaders on pullbacks.
- Avoid weak late-cycle names.
""",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster dashboard"' in html
    assert "Summary" in html
    assert "Buy leaders on pullbacks." in html


def test_desktop_backend_build_scripts_bundle_share_image_assets():
    root = Path(__file__).resolve().parents[1]
    for relative_path in ("scripts/build-backend.ps1", "scripts/build-backend-macos.sh"):
        content = (root / relative_path).read_text(encoding="utf-8")
        assert "src/assets/share_image" in content


def test_share_image_uses_vietnamese_lang_and_docker_installs_cjk_fonts():
    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        generated_on=date(2026, 8, 24),
    )

    # Chỉ còn vi/en: không còn quy tắc font theo lang zh-CN/ko.
    # Only vi/en remain: no zh-CN/ko lang-specific font rules.
    assert '<html lang="vi">' in html
    assert 'html[lang="zh-CN"] body' not in html
    assert 'html[lang="ko"] body' not in html

    dockerfile = (
        Path(__file__).resolve().parents[1] / "docker" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "fonts-noto-cjk \\" in dockerfile


def test_japanese_stock_share_image_uses_english_report_cjk_fallback():
    html = build_share_image_html(
        "# トヨタ自動車 7203.T Analysis Report\n\n## Core Conclusion\n\n- Bullish trend.\n",
        generated_on=date(2026, 8, 24),
        structured_payload={
            "code": "7203.T",
            "name": "トヨタ自動車",
            "report_language": "en",
        },
    )

    assert 'class="poster stock"' in html
    assert '<html lang="en">' in html
    assert '"Segoe UI", "Noto Sans CJK SC", "Noto Sans CJK KR"' in html
    assert '"Noto Sans CJK SC"' in html
    assert "トヨタ自動車" in html


def test_market_share_image_prefers_structured_market_metrics():
    payload = {
        "kind": "market_review",
        "region": "vn",
        "language": "vi",
        "title": "2026-08-01 Vietnam Market Recap",
        "date": "2026-08-01",
        "market_light": {
            "score": 83,
            "temperature_label": "risk-on",
            "label": "risk-on",
            "dimensions": {
                "breadth": {"score": 86},
                "index": {"score": 69},
                "limit": {"score": 100},
            },
        },
        "indices": [
            {"name": "VN-Index", "current": 1301.25, "change_pct": 0.72},
            {"name": "VN30", "current": 1385.4, "change_pct": 1.21},
            {"name": "HNX-Index", "current": 232.1, "change_pct": -0.3},
            {"name": "UPCoM-Index", "current": 98.6, "change_pct": 0.1},
        ],
        "breadth": {
            "up_count": 469,
            "down_count": 72,
            "limit_up_count": 21,
            "limit_down_count": 0,
            "total_amount": 25591,
            "turnover_unit": "VND bn",
        },
    }
    html = build_share_image_html(
        """# Vietnam Market Recap

## 2026-08-01 Vietnam Market Recap

> Các chỉ số đồng loạt tăng, tâm lý thị trường cải thiện rõ.

### 1. Market Summary
- **Market Signal**: 20/100 (defensive, risk-off)
- **Guidance**: Theo dõi thanh khoản và tính bền vững của dòng dẫn dắt.

### 6. Strategy Plan
- Focus: ngân hàng, chứng khoán; bất động sản khu công nghiệp
- Avoid: cổ phiếu đầu cơ
- Duy trì tỷ trọng 50-70%.

### 5. Risk Alerts
- Nhóm tăng nóng điều chỉnh.
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert '<html lang="vi">' in html
    assert "<strong>83</strong><small>/100</small>" in html
    assert '<div class="market-label">risk-on</div>' in html
    assert "+0.72%" in html
    assert "VN30" in html
    assert "469" in html
    assert "25,591 VND bn" in html
    assert "Phân rã tín hiệu" in html
    assert "86/100" in html
    assert "Trọng tâm theo dõi" in html
    assert "ngân hàng" in html
    assert "cổ phiếu đầu cơ" in html
    assert "Nhóm tụt lại" not in html
    assert "Nhóm dẫn dắt" not in html
    assert "20/100" not in html


def test_market_share_image_ignores_legacy_sector_payload():
    payload = {
        "kind": "market_review",
        "region": "vn",
        "sectors": {
            "top": [{"name": "Ngân hàng", "change_pct": 2.1}],
            "bottom": [{"name": "Bất động sản", "change_pct": -1.3}],
        },
    }

    html = build_share_image_html(
        "# Vietnam Market Recap\n\n## Major Indices\n\n- **VN-Index**: 1300.00 (🟢 +0.50%)\n",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert 'class="poster market"' in html
    assert '<div class="ranking-row' not in html
    assert "Bất động sản" not in html


def test_market_share_image_merges_partial_structured_indices_with_markdown_fallback():
    payload = {
        "kind": "market_review",
        "region": "vn",
        "indices": [
            {"name": "VN-Index", "change_pct": 0.72},
            {"name": "HNX-Index", "current": 232.1, "change_pct": 1.06},
        ],
    }

    html = build_share_image_html(
        """# Vietnam Market Recap

## Major Indices

| Index | Last | Change % |
| --- | --- | --- |
| VN-Index | 1301.25 | +0.80% |
| VN30 | 1385.40 | +1.21% |
| HNX-Index | 230.00 | +0.50% |
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "1301.25" in html
    assert "+0.72%" in html
    assert "1385.40" in html
    assert "+1.21%" in html
    assert "232.1" in html
    assert "+1.06%" in html


def test_market_share_image_merges_partial_structured_breadth_with_markdown_fallback():
    payload = {
        "kind": "market_review",
        "region": "vn",
        "breadth": {
            "up_count": 320,
            "total_amount": 21345,
            "turnover_unit": "VND bn",
        },
    }

    html = build_share_image_html(
        """# Vietnam Market Recap

## Market Summary

- **Breadth**: Advancers 300 / Decliners 180 / Flat 40; Limit-up 8 / Limit-down 6; Turnover 19000 (VND bn)
""",
        generated_on=date(2026, 8, 1),
        structured_payload=payload,
    )

    assert "320" in html
    assert "180" in html
    assert ">8<" in html
    assert ">6<" in html
    assert "21,345 VND bn" in html
    assert "19000 (VND bn)" not in html


def test_market_share_image_uses_market_variant_and_preserves_tables():
    html = build_share_image_html(
        "# Điểm lại thị trường Việt Nam\n\n## Bảng chỉ số\n\n| Chỉ số | Biến động |\n| --- | --- |\n| VN-Index | +0.8% |",
        generated_on=date(2026, 7, 31),
    )

    assert 'class="poster market"' in html
    assert "Điểm lại thị trường Việt Nam" in html
    assert "<table>" in html
    assert "VN-Index" in html
    assert "+0.8%" in html


def test_market_share_image_populates_decision_modules_from_report_contract():
    html = build_share_image_html(
        """# Vietnam Market Recap

## 2026-07-31 Vietnam Market Recap

> VN-Index tăng nhẹ, cần theo dõi thanh khoản có duy trì được không.

### 1. Market Summary

- **Market Signal**: 66/100 (constructive, risk-on)
- **Drivers**: advancers ratio 66%, breadth is expanding; average major-index change +0.80%
- **Guidance**: Risk appetite is acceptable; focus on leading themes and position discipline.

- **Breadth**: Advancers 329 / Decliners 168 / Flat 70; Limit-up 12 / Limit-down 6; Turnover 21345 (VND bn)

### 2. Index Commentary

| Index | Last | Change % |
| --- | --- | --- |
| VN-Index | 1301.25 | 🟢 +0.80% |
| VN30 | 1385.40 | 🟢 +1.12% |

### 4. News Catalysts

- Đầu tư công được đẩy mạnh.

### 6. Strategy Plan

- Kết luận: tích cực có chọn lọc.

### 7. Risk Alerts

1. Nhóm tăng nóng điều chỉnh.
""",
        generated_on=date(2026, 7, 31),
    )

    assert '<section class="market-signal">' in html
    assert '<strong>66</strong><small>/100</small>' in html
    assert "VN-Index" in html
    assert "329" in html
    assert "Đầu tư công được đẩy mạnh" in html
    assert "tích cực có chọn lọc" in html
    assert "Nhóm tăng nóng điều chỉnh" in html
    assert '<section class="report-fallback">' not in html


def test_market_share_image_preserves_scoreless_overview_guidance_without_fallback():
    html = build_share_image_html(
        """# Vietnam Market Recap

> Thanh khoản chưa trở lại vùng tích cực.

## Market Summary

**Guidance:** Chờ thanh khoản xác nhận rồi mới nâng tỷ trọng.
**Drivers:** Thanh khoản dưới ngưỡng; nhóm vốn hóa lớn giữ nhịp.

- **Breadth**: Advancers 301 / Decliners 198; Limit-up 5 / Limit-down 9; Turnover 15000 (VND bn)

## Major Indices

| Index | Last | Change % |
| --- | --- | --- |
| VN-Index | 1290.11 | +0.34% |

## Strategy Plan

- Kết luận: quan sát thanh khoản có tiếp tục cải thiện.

## Risk Alerts

- Hồi phục với thanh khoản thấp rồi đảo chiều.
""",
        generated_on=date(2026, 8, 1),
    )

    assert '<section class="market-signal">' not in html
    assert "Chờ thanh khoản xác nhận rồi mới nâng tỷ trọng" in html
    assert "Thanh khoản dưới ngưỡng" in html
    assert "nhóm vốn hóa lớn giữ nhịp" in html
    assert '<section class="report-fallback">' not in html


def test_market_share_image_preserves_report_color_scheme_from_index_markers():
    html = build_share_image_html(
        """# Vietnam Market Recap

### 1. Market Summary

- **Market Signal**: 60/100 (constructive, risk-on)
- **Breadth**: Advancers 300 / Decliners 180 / Flat 20; Limit-up 6 / Limit-down 8; Turnover 18000 (VND bn)

### 2. Major Indices

| Index | Last | Change % |
| --- | --- | --- |
| VN-Index | 1300 | 🔴 +0.80% |
""",
        generated_on=date(2026, 7, 31),
    )

    assert '<strong class="red">+0.80%</strong>' in html
    assert 'class="metric red"><span>Advancers</span>' in html


def test_market_share_image_uses_red_up_gain_color_for_breadth_when_all_indices_decline():
    html = build_share_image_html(
        """# Vietnam Market Recap

### 1. Market Summary

- **Market Signal**: 30/100 (defensive, risk-off)
- **Breadth**: Advancers 120 / Decliners 380; Limit-up 3 / Limit-down 12; Turnover 17000 (VND bn)

### 2. Major Indices

| Index | Last | Change % |
| --- | --- | --- |
| VN-Index | 1250 | 🟢 -0.80% |
| VN30 | 1300 | 🟢 -1.20% |
""",
        generated_on=date(2026, 7, 31),
    )

    assert '<strong class="green">-0.80%</strong>' in html
    assert 'class="metric red"><span>Advancers</span>' in html
    assert 'class="metric green"><span>Decliners</span>' in html


def test_market_title_defaults_to_vietnam_when_heading_is_not_a_recap():
    from src.share_image import _market_title

    assert _market_title("# Some heading\n\nBody") == "Điểm lại thị trường Việt Nam"
    assert _market_title("# Major indices\n\nBody") == "Vietnam Market Recap"
    assert _market_title("# 2026-08-01 Vietnam Market Recap\n") == "2026-08-01 Vietnam Market Recap"


def test_parsed_breadth_metrics_reads_vietnamese_breadth_line():
    # Báo cáo tiếng Việt chèn khối độ rộng với nhãn tiếng Việt. / Vietnamese breadth labels.
    from src.share_image import _parsed_breadth_metrics

    line = "- **Độ rộng**: Tăng 337 / Giảm 330 / Đứng giá 248; Trần 39 / Sàn 26; Giá trị giao dịch 15130 (tỷ đồng)"

    metrics = dict(_parsed_breadth_metrics(line))

    assert metrics["上涨"] == "337"
    assert metrics["下跌"] == "330"
    assert metrics["涨停"] == "39"
    assert metrics["跌停"] == "26"
    assert metrics["成交额"].startswith("15130")
