<div align="center">

# AI Stock Analysis System

[![GitHub stars](https://img.shields.io/github/stars/ZhuLinsen/daily_stock_analysis?style=social)](https://github.com/ZhuLinsen/daily_stock_analysis/stargazers)
[![arXiv](https://img.shields.io/badge/arXiv-2608.26990-b31b1b.svg)](https://arxiv.org/abs/2608.26990)
[![CI](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Ready-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/zhulinsen/daily_stock_analysis)

<p align="center">
  <img src="https://trendshift.io/api/badge/trendshift/repositories/18527/daily?language=Python" alt="#1 Python Repository Of The Day | Trendshift" width="250" height="55"/>&nbsp;<a href="https://hellogithub.com/repository/ZhuLinsen/daily_stock_analysis" target="_blank"><img src="https://api.hellogithub.com/v1/widgets/recommend.svg?rid=6daa16e405ce46ed97b4a57706aeb29f&claim_uid=pfiJMqhR9uvDGlT&theme=neutral" alt="Featured｜HelloGitHub" width="230" /></a>
</p>

**AI-powered stock analysis system for the Vietnam stock market (HOSE / HNX / UPCoM)**

Analyze your watchlist daily -> generate a decision dashboard -> push to Telegram / Discord / Slack / Email / webhooks.

[**Product Preview**](#-product-preview) · [**Key Features**](#-key-features) · [**Quick Start**](#-quick-start) · [**Sample Output**](#-sample-output) · [**Documentation Index**](./INDEX_EN.md) · [**Full Guide**](./full-guide_EN.md)

English | [Tiếng Việt (tài liệu)](INDEX.md)

</div>

## 💖 Sponsors

<div align="center">
  <p align="center">
    <a href="https://open.anspire.cn/?share_code=QFBC0FYC" target="_blank"><img src="assets/anspire.png" alt="Anspire Open all-in-one model and search service" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
    <a href="https://serpapi.com/github-daily-stock-analysis" target="_blank"><img src="assets/serpapi_banner_en.png" alt="Easily scrape real-time financial news data from search engines - SerpApi" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
  </p>
</div>

## 🖥️ Product Preview

<p align="center">
  <img src="assets/readme_workspace_tour_20260510.gif" alt="DSA Web workspace demo" width="720">
</p>

## ✨ Key Features

| Capability | Coverage |
|------------|----------|
| AI decision reports | Core conclusion, score, trend, entry/exit levels, risk alerts, catalysts, and action checklist |
| Vietnam market data | Covers HOSE / HNX / UPCoM stocks, ETFs / fund certificates, and VN indices, with quotes, K-lines, technical indicators, news, announcements, fundamentals, and report context. Data-source coverage and market boundaries are documented in [market scope](market-support.md) (Vietnamese) |
| Web / desktop workspace | Manual analysis, task progress, history, full Markdown reports, backtest, portfolio, settings, and light/dark themes |
| Agent strategy chat | Multi-turn Q&A with 15 built-in strategies across Web/Bot/API |
| Smart import & autocomplete | Image, CSV/Excel, clipboard import; code/name/alias autocomplete |
| Automation & notifications | GitHub Actions, Docker, local scheduler, FastAPI service, and Telegram / Discord / Slack / Email / webhook delivery |

> Detailed fields, fundamental P0 timeout semantics, trading rules, data-source priority, Web/API behavior, and troubleshooting live in the [Full Guide](./full-guide_EN.md).

### Tech Stack & Data Sources

| Type | Supported |
|------|-----------|
| AI Models | [Anspire](https://open.anspire.cn/?share_code=QFBC0FYC), [AIHubMix](https://inferera.com/?aff=CfMq), Gemini, OpenAI-compatible providers, DeepSeek, Qwen, Claude, Ollama |
| Market Data | VCI (Vietcap), SSI iBoard, [vnstock](../requirements-vn-optional.txt) (optional), YFinance (HOSE daily fallback) |
| News Search | [Anspire](https://open.anspire.cn/?share_code=QFBC0FYC), [SerpAPI](https://serpapi.com/github-daily-stock-analysis), [Tavily](https://tavily.com/), [Brave](https://brave.com/search/api/), SearXNG |

> Vietnam market data comes from the public VCI and SSI endpoints and needs no API key. Public endpoints can be rate-limited or change upstream contracts; the fallback chain is documented in [Data-source stability](data-source-stability.md) (Vietnamese).

## 🚀 Quick Start

### Option 1: GitHub Actions (Recommended)

> Deploy in about 5 minutes, with no server and no infrastructure cost.

#### 1. Fork this repository

Click `Fork` in the upper-right corner. A star is very welcome if this project helps you.

#### 2. Configure Secrets

Open your forked repository, then go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`.

**AI model configuration (configure at least one)**

Start with one provider and one API key. For multi-model routing, image recognition, local models, or advanced routing, see the [LLM Config Guide](./LLM_CONFIG_GUIDE_EN.md).

| Secret Name | Description | Required |
|-------------|-------------|:--------:|
| `ANSPIRE_API_KEYS` | [Anspire](https://open.anspire.cn/?share_code=QFBC0FYC) API key, one key for popular LLMs and web search with free quota for this project | **Recommended** |
| `AIHUBMIX_KEY` | [AIHubMix](https://inferera.com/?aff=CfMq) API key, one key for multiple model families and a 10% top-up discount for this project | **Recommended** |
| `GEMINI_API_KEY` | Google Gemini API key | Optional |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | Optional |
| `OPENAI_API_KEY` | OpenAI-compatible API key, including DeepSeek and Qwen-compatible services | Optional |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | Fill these when using an OpenAI-compatible provider | Optional |

> Ollama is better suited for local or Docker deployment. GitHub Actions is usually smoother with a cloud API.

**Notification channels (configure at least one)**

| Secret Name | Description |
|-------------|-------------|
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Telegram |
| `DISCORD_WEBHOOK_URL` | Discord webhook |
| `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` | Slack bot |
| `EMAIL_SENDER` + `EMAIL_PASSWORD` | Email push |

More channels, signatures, email groups, and Markdown-to-image settings are in [Notification Configuration](./full-guide_EN.md#notification-channel-configuration).

**Watchlist (required)**

| Secret Name | Description | Required |
|-------------|-------------|:--------:|
| `STOCK_LIST` | Watchlist codes, such as `HPG,FPT,VCB,VNINDEX`; code formats are listed in [market scope](market-support.md) | ✅ |

**News sources (recommended)**

News search strongly improves sentiment, announcements, events, and catalyst quality. Configure at least one search provider if possible.

| Secret Name | Description | Required |
|-------------|-------------|:--------:|
| `ANSPIRE_API_KEYS` | [Anspire AI Search](https://aisearch.anspire.cn/), aggregated news search; the same key can also be used for Anspire LLM fallback examples | **Recommended** |
| `SERPAPI_API_KEYS` | [SerpAPI](https://serpapi.com/github-daily-stock-analysis), search-engine results for realtime financial news | **Recommended** |
| `TAVILY_API_KEYS` | [Tavily](https://tavily.com/), general news search API | Optional |
| `BRAVE_API_KEYS` | [Brave Search](https://brave.com/search/api/), privacy-first web search | Optional |
| `SEARXNG_BASE_URLS` | Self-hosted SearXNG instances for quota-free fallback | Optional |

More search providers and fallback behavior are in [Search Configuration](./full-guide_EN.md#search-service-configuration).

**Market data sources (optional)**

> VCI and SSI are public and need no key. Optional data-source switches:

| Secret Name | Market | Description |
|-------------|:------:|-------------|
| `FIINTRADE_ORIGIN` | Vietnam | Activates FiinTrade (on by default) ahead of VCI for daily bars, realtime quotes, valuation, corporate events and market-review indices/breadth/fund flows/market drivers; must be an origin whitelisted by FiinTrade. Set `FIINTRADE_ENABLED=false` to turn it off |
| `VNSTOCK_ENABLED` | Vietnam | Enables the optional `vnstock` source (requires `requirements-vn-optional.txt`; vnstock is licensed for personal use and ships telemetry) |

> See [Data-source stability](data-source-stability.md).

#### 3. Enable Actions

Open the `Actions` tab and click `I understand my workflows, go ahead and enable them`.

#### 4. Manual Test

`Actions` -> `Daily Stock Analysis` -> `Run workflow` -> `Run workflow`.

#### Done

By default, the workflow runs every weekday at 18:00 Vietnam time (UTC+7); weekends are skipped (there is no HOSE/HNX holiday calendar). Forced runs, trading-day checks, and resume rules are covered in the [Full Guide](./full-guide_EN.md#scheduled-task-configuration).

### Option 2: Local / Docker Deployment

```bash
# Clone the project
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git && cd daily_stock_analysis

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env && vim .env

# Run analysis
python main.py
```

Common commands:

```bash
python main.py --debug
python main.py --dry-run
python main.py --stocks HPG,FPT,VCB,VNINDEX
python main.py --market-review
python main.py --schedule
python main.py --serve-only
```

> Docker deployment, scheduling, and cloud-server WebUI access are documented in the [Full Guide](./full-guide_EN.md).

## 📱 Sample Output

### Decision Dashboard

```markdown
🎯 2026-02-08 Decision Dashboard
Analyzed 3 stocks | 🟢 Buy:0 🟡 Watch:2 🔴 Sell:1

📊 Summary
🟡 000657: Watch | Score 65 | Bullish
🟡 600105: Watch | Score 48 | Range-bound
🔴 300260: Sell | Score 35 | Bearish

🚨 Risk Alerts:
Risk 1: Main-force funds showed notable outflow.
Risk 2: Chip concentration suggests short-term resistance.

✨ Positive Catalysts:
Catalyst 1: AI-server supply-chain exposure remains a market focus.
Catalyst 2: Recent earnings growth provides fundamental support.
```

### Market Review

```markdown
🎯 2026-01-10 Market Review

📊 Major Indices
- SSE Composite: 3250.12 (+0.85%)
- SZSE Component: 10521.36 (+1.02%)
- ChiNext: 2156.78 (+1.35%)

📈 Market Breadth
Up: 3920 | Down: 1349 | Limit up: 155 | Limit down: 3
```

## ⚙️ Configuration

Full environment variables, model routing, notification channels, data-source priority, trading rules, fundamental P0 semantics, and deployment details are in the [Full Guide](./full-guide_EN.md).

## 🖥️ Web UI

The Web workspace supports settings, task monitoring, manual analysis, history reports, full Markdown reports, Agent strategy chat, backtest, portfolio management, smart import, and light/dark themes.

```bash
python main.py --webui
python main.py --webui-only
```

Visit `http://127.0.0.1:8000`. Authentication, smart import, autocomplete, report copying, and cloud-server access are documented in [Local WebUI Management](./full-guide_EN.md#local-webui-management-interface).

## 🤖 Agent Strategy Chat

After configuring any available AI API key, the Web `/chat` page can use strategy chat. Set `AGENT_MODE=false` only if you want to disable it explicitly.

- Built-in strategies include moving-average crossovers, Chan theory, Elliott wave, bull trend, hot themes, event-driven, growth quality, expectation repricing, and more
- Calls realtime quotes, K-line data, technical indicators, news, and risk context
- Supports follow-up questions, session export, notification sending, and background execution
- Supports custom strategy files and experimental multi-agent orchestration

> Agent parameters, `skill` naming compatibility, multi-agent mode, and budget guards are covered in the [Full Guide](./full-guide_EN.md#local-webui-management-interface) and [LLM Config Guide](./LLM_CONFIG_GUIDE_EN.md).

## 🧩 Related Projects

> DSA focuses on daily analysis reports and no longer ships a stock screener. AlphaSift is a separate screening project, while AlphaEvo covers strategy validation and evolution.

| Project | Focus |
|---------|-------|
| [AlphaSift](https://github.com/ZhuLinsen/alphasift) | Separate stock screening project (not integrated into DSA) |
| [AlphaEvo](https://github.com/ZhuLinsen/alphaevo) | Strategy backtesting and self-evolution experiments for validating rules and iteratively exploring strategy parameters and combinations |

## 📞 Contact

<table>
  <tr>
    <td width="92" valign="top"><strong>Email</strong></td>
    <td valign="top">
      <a href="mailto:zhuls345@gmail.com">zhuls345@gmail.com</a><br>
      Project consulting, deployment support, and feature extensions
    </td>
    <td align="center" rowspan="3" valign="middle" width="148">
      <a href="http://xhslink.com/m/tU520DWCKT" target="_blank"><img src="assets/xiaohongshu_tick.jpg" width="112" alt="Xiaohongshu QR code"></a><br>
      <sub>Follow on Xiaohongshu</sub>
    </td>
  </tr>
  <tr>
    <td width="92" valign="top"><strong>Xiaohongshu</strong></td>
    <td valign="top"><a href="http://xhslink.com/m/tU520DWCKT">Follow on Xiaohongshu</a></td>
  </tr>
  <tr>
    <td width="92" valign="top"><strong>Feedback</strong></td>
    <td valign="top"><a href="https://github.com/ZhuLinsen/daily_stock_analysis/issues">GitHub Issues</a> · <a href="https://github.com/ZhuLinsen/daily_stock_analysis/discussions">Discussions</a></td>
  </tr>
</table>

## 📄 License

[MIT License](../LICENSE) © 2026 ZhuLinsen

If you use or build on this project, attribution with a link back to this repository is appreciated.

## ⚠️ Disclaimer

This project is for informational and educational purposes only. AI-generated analysis is not investment advice. Stock market investing involves risk; do your own research and consult a licensed financial advisor when needed.
