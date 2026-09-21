<div align="center">

# 📈 AI Stock Analysis System

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

> 🤖 An LLM-powered analysis system for your stock watchlist. It runs daily and pushes a "decision dashboard" to WeCom / Feishu / Telegram / Discord / Slack / email.
>
> The Vietnam market is enabled by default (`SUPPORTED_MARKETS=vn`). A-shares, Hong Kong, US, Japan, Korea, and Taiwan remain fully supported and can be switched back on in the configuration.

[**Product Preview**](#-product-preview) · [**Features**](#-features) · [**Quick Start**](#-quick-start) · [**Sample Output**](#-sample-output) · [**Documentation Index**](docs/INDEX_EN.md) · [**Full Guide**](docs/full-guide_EN.md)

English | [简体中文](docs/README_CN.md) | [繁體中文](docs/README_CHT.md)

</div>

## 💖 Sponsors
<div align="center">
  <p align="center">
    <a href="https://open.anspire.cn/dsa?share_code=QFBC0FYC" target="_blank"><img src="./docs/assets/anspire.png" alt="Anspire Open - one-stop model and search service" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
    <a href="https://serpapi.com/github-daily-stock-analysis" target="_blank"><img src="./docs/assets/serpapi_banner_zh.png" alt="Scrape real-time financial news from search engines - SerpApi" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
  </p>
</div>


## 🖥️ Product Preview

<p align="center">
  <img src="docs/assets/readme_workspace_tour_20260510.gif" alt="DSA web workspace tour" width="720">
</p>

## ✨ Features

| Capability | What it covers |
|------|------|
| AI decision report | Core conclusion, score, trend, entry/exit levels, risk alerts, catalysts, action checklist |
| Multi-market data aggregation | Scope is controlled by `SUPPORTED_MARKETS`, defaulting to Vietnam. A-shares, Hong Kong, US, Japan, Korea, Taiwan, and ETFs are optional. Covers quotes, candles, technical indicators, news, disclosures, fundamentals, and supporting report data. Per-market sources and capability limits are documented in [Market support](docs/market-support.md) |
| Web / desktop workspace | Manual analysis, task progress, report history, full Markdown, backtesting, positions, configuration management, light/dark themes |
| Agent strategy Q&A | Multi-turn follow-ups with 15 built-in strategies — moving averages, Chan theory, Elliott waves, trend, hot sectors, events, growth, expectations — across Web, bot, and API |
| Smart import and completion | Import from images, CSV/Excel, or clipboard; completion by stock code, name, pinyin, or alias |
| Automation and delivery | GitHub Actions, Docker, local scheduled jobs, a FastAPI service, and delivery to WeCom / Feishu / Telegram / Discord / Slack / email |

> For feature details, field contracts, fundamental P0 timeout semantics, trading discipline, data source priority, and Web/API behaviour, see the [full configuration and deployment guide](docs/full-guide_EN.md).

### Stack and data sources

| Type | Supported |
|------|------|
| AI models | [Anspire](https://open.anspire.cn/dsa?share_code=QFBC0FYC), [AIHubMix](https://inferera.com/?aff=CfMq), Gemini, OpenAI-compatible, DeepSeek, Qwen, Claude, local Ollama models, and more |
| Market data | VCI (Vietcap), SSI iBoard, [vnstock](requirements-vn-optional.txt) (optional), [TickFlow](https://tickflow.org/auth/register?ref=WDSGSPS5XC), AkShare, Tushare, Pytdx, Baostock, YFinance, Longbridge |
| News search | [Anspire](https://open.anspire.cn/dsa/?share_code=QFBC0FYC), [SerpAPI](https://serpapi.com/github-daily-stock-analysis), [Tavily](https://tavily.com/), [Bocha](https://open.bocha.cn/), [Brave](https://brave.com/search/api/), [MiniMax](https://platform.minimaxi.com/), SearXNG |
| Social sentiment | [Stock Sentiment API](https://api.adanos.org/docs) (Reddit / X / Polymarket, US market only, optional) |

> Vietnam market data comes from the public VCI and SSI endpoints and needs no API key. For other markets the project ships with free sources such as AkShare, Baostock, and YFinance, so it runs with zero configuration. Free sources are subject to upstream rate limits, interface changes, and network variability, so their stability is not guaranteed. For long-running schedules, batch analysis, or more reliable quotes, configure a token-based source such as TickFlow, Tushare, or Longbridge. Applicable markets, Actions mapping, and fallback rules are covered in [data source configuration](docs/full-guide_EN.md).

## 🚀 Quick Start

### Option 1: [GitHub Actions (recommended)](https://www.bilibili.com/video/BV11FEb66EXG/)

> Deployed in five minutes, at no cost, with no server required.


#### 1. Fork this repository

Click `Fork` in the top right (a Star ⭐ is appreciated too).

#### 2. Configure secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

**AI model configuration (at least one required)**

Start by picking one model provider and filling in its API key. If you need multiple models, image recognition, local models, or advanced routing, see the [LLM configuration guide](docs/LLM_CONFIG_GUIDE.md).

| Secret | Description | Required |
|------------|------|:----:|
| `ANSPIRE_API_KEYS` | [Anspire](https://open.anspire.cn/dsa?share_code=QFBC0FYC) API key. One key enables both popular global LLMs and web search; new users of this project get free credit worth ¥30 (GLM5.2, GPT, and others are currently discounted) | **Recommended** |
| `AIHUBMIX_KEY` | [AIHubMix](https://inferera.com/?aff=CfMq) API key. One key to switch across the full model lineup with no VPN required; this project gets a 10% discount | **Recommended** |
| `GEMINI_API_KEY` | Google Gemini API key | Optional |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | Optional |
| `OPENAI_API_KEY` | OpenAI-compatible API key (supports DeepSeek, Qwen, and others) | Optional |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | Set these when using an OpenAI-compatible service | Optional |

> Ollama suits local and Docker deployments better. For GitHub Actions, a cloud API is recommended.

**Notification channels (at least one required)**

| Secret | Description |
|------------|------|
| `WECHAT_WEBHOOK_URL` | WeCom (WeChat Work) bot |
| `FEISHU_WEBHOOK_URL` | Feishu bot |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Telegram |
| `DISCORD_WEBHOOK_URL` | Discord webhook |
| `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` | Slack bot |
| `EMAIL_SENDER` + `EMAIL_PASSWORD` | Email delivery |

For more channels, signature verification, grouped email, and Markdown-to-image rendering, see [notification channel configuration](docs/full-guide_EN.md).

**Watchlist configuration (required)**

| Secret | Description | Required |
|------------|------|:----:|
| `SUPPORTED_MARKETS` | Which markets are enabled. Defaults to `vn` (Vietnam only); `all` enables every market. A disabled market never issues a single request — see [Market support](docs/market-support.md) | ✅ |
| `STOCK_LIST` | Watchlist codes, such as `HPG,FPT,VNINDEX` (Vietnam) or `600519,hk00700,AAPL` once the matching markets are enabled. Explicit index codes such as `sh000016` or `930606.CSI` are supported through a one-off `--stocks` run or the GitHub Actions entry point; see [index watchlist configuration](docs/full-guide_EN.md) | ✅ |

**News source configuration (recommended)**

News sources have a large effect on the quality of sentiment, disclosures, events, and catalysts. Configuring at least one search service is recommended.

| Secret | Description | Required |
|------------|------|:----:|
| `ANSPIRE_API_KEYS` | [Anspire AI Search](https://open.anspire.cn/dsa?share_code=QFBC0FYC): aggregated global sentiment, suited to news and sentiment retrieval for A-shares, US, Hong Kong, and more. The same key also works for the LLM service; new users of this project get free credit worth ¥30 | **Recommended** |
| `SERPAPI_API_KEYS` | [SerpAPI](https://serpapi.com/github-daily-stock-analysis): search engine results, good for real-time financial news | **Recommended** |
| `TAVILY_API_KEYS` | [Tavily](https://tavily.com/): general-purpose news search API | Optional |
| `BOCHA_API_KEYS` | [Bocha Search](https://open.bocha.cn/): optimised for Chinese, with AI summaries | Optional |
| `BRAVE_API_KEYS` | [Brave Search](https://brave.com/search/api/): privacy-first, strengthens US market coverage | Optional |
| `MINIMAX_API_KEYS` | [MiniMax](https://platform.minimaxi.com/): structured search results | Optional |
| `SEARXNG_BASE_URLS` | Self-hosted SearXNG instance: quota-free fallback, suited to private deployments | Optional |

For more search sources, social sentiment, and degradation rules, see [search service configuration](docs/full-guide_EN.md).

**Market data source configuration (optional)**

> The Vietnam sources (VCI, SSI) are public and need no key. Other markets fall back to free sources such as AkShare, Baostock, and YFinance, so "not configured" messages in the log do not affect operation.
> For more reliable quotes, configure the following secrets per market:

| Secret | Market | Description |
|------------|:--------:|------|
| `TUSHARE_TOKEN` | A-shares | Improves historical quote stability |
| `LONGBRIDGE_OAUTH_CLIENT_ID` + `LONGBRIDGE_OAUTH_TOKEN_CACHE_B64` | Hong Kong / US | Fills in volume ratio, turnover rate, PE, and similar fields |
| `VNSTOCK_ENABLED` | Vietnam | Enables the optional `vnstock` source. It also requires `requirements-vn-optional.txt`; note that vnstock is licensed "Custom (Personal Use)" and pulls in the `vnai` telemetry package |

> See [data source configuration](docs/full-guide_EN.md) for details.

#### 3. Enable Actions

`Actions` tab → `I understand my workflows, go ahead and enable them`

#### 4. Run a manual test

`Actions` → `Daily stock analysis` → `Run workflow` → `Run workflow`

#### Done

By default it runs automatically every **weekday at 18:00 (Beijing time)**, and can also be triggered manually. Non-trading days, including A-share, Hong Kong, and US holidays, are skipped by default. Forced runs, trading-day checks, and resume behaviour are covered in the [full guide](docs/full-guide_EN.md).

### Option 2: [Client setup tutorial](https://www.bilibili.com/video/BV11FEb66Eyr/) / local run / Docker

```bash
# Clone the project
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git && cd daily_stock_analysis

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env && vim .env

# Run the analysis
python main.py
```

Common commands:

```bash
python main.py --debug
python main.py --dry-run
python main.py --stocks HPG,FPT,VNINDEX
python main.py --market-review
python main.py --schedule
python main.py --serve-only
```

> There is also `./scripts/run-app.sh`, which wraps the verified launch and smoke-test procedure: `serve`, `dry-run`, `analyze`, `smoke`, `stop`, and `build-web`.
>
> For Docker deployment, scheduled jobs, and cloud server access, see the [full guide](docs/full-guide_EN.md). For desktop packaging, see the [desktop packaging notes](docs/desktop-package.md).

## 📱 Sample Output

> The samples below are verbatim captures from an A-share run, kept as they were produced. With the default Vietnamese settings the same structure is rendered in Vietnamese with Vietnamese tickers.

### Decision dashboard
```
🎯 2026-02-08 决策仪表盘
共分析3只股票 | 🟢买入:0 🟡观望:2 🔴卖出:1

📊 分析结果摘要
⚪ 中钨高新(000657): 观望 | 评分 65 | 看多
⚪ 永鼎股份(600105): 观望 | 评分 48 | 震荡
🟡 新莱应材(300260): 卖出 | 评分 35 | 看空

⚪ 中钨高新 (000657)
📰 重要信息速览
💭 舆情情绪: 市场关注其AI属性与业绩高增长，情绪偏积极，但需消化短期获利盘和主力流出压力。
📊 业绩预期: 基于舆情信息，公司2025年前三季度业绩同比大幅增长，基本面强劲，为股价提供支撑。

🚨 风险警报:

风险点1：2月5日主力资金大幅净卖出3.63亿元，需警惕短期抛压。
风险点2：筹码集中度高达35.15%，表明筹码分散，拉升阻力可能较大。
风险点3：舆情中提及公司历史违规记录及重组相关风险提示，需保持关注。
✨ 利好催化:

利好1：公司被市场定位为AI服务器HDI核心供应商，受益于AI产业发展。
利好2：2025年前三季度扣非净利润同比暴涨407.52%，业绩表现强劲。
📢 最新动态: 【最新消息】舆情显示公司是AI PCB微钻领域龙头，深度绑定全球头部PCB/载板厂。2月5日主力资金净卖出3.63亿元，需关注后续资金流向。

---
生成时间: 18:00
```

### Market review
```
🎯 2026-01-10 大盘复盘

📊 主要指数
- 上证指数: 3250.12 (🟢+0.85%)
- 深证成指: 10521.36 (🟢+1.02%)
- 创业板指: 2156.78 (🟢+1.35%)

📈 市场概况
上涨: 3920 | 下跌: 1349 | 涨停: 155 | 跌停: 3

🔥 板块表现
领涨: 互联网服务、文化传媒、小金属
领跌: 保险、航空机场、光伏设备
```

## ⚙️ Configuration

For the complete list of environment variables, model providers, notification channels, data source priority, trading discipline, fundamental P0 semantics, and deployment notes, see the [full configuration guide](docs/full-guide_EN.md).

## 🖥️ Web Interface

The web workspace provides configuration management, task monitoring, manual analysis, report history, full Markdown reports, agent Q&A, backtesting, position management, smart import, and light/dark themes. To start it:

```bash
python main.py --webui
python main.py --webui-only
```

Then open `http://127.0.0.1:8000`. Authentication, smart import, search completion, copying report history, and cloud server access are covered in [the local WebUI admin interface](docs/full-guide_EN.md).

## 🤖 Agent Strategy Q&A

Once any working AI API key is configured, the `/chat` page in the web UI can answer strategy questions. To turn it off explicitly, set `AGENT_MODE=false`.

- Built-in strategies include moving-average crossovers, Chan theory, Elliott waves, bullish trends, hot themes, event-driven setups, growth quality, and expectation re-rating
- Can call real-time quotes, candles, technical indicators, news, and risk information
- Supports multi-turn follow-ups, session export, sending to notification channels, and background execution
- Supports custom strategy files and multi-agent orchestration (experimental)

> For agent parameters, `skill` naming compatibility, multi-agent mode, and budget guardrails, see the [full guide](docs/full-guide_EN.md) and the [LLM configuration guide](docs/LLM_CONFIG_GUIDE.md).

## 🧩 Related Projects

> DSA focuses on the daily analysis report. AlphaSift is the reference implementation for stock screening, and AlphaEvo is used for strategy validation and evolution.

| Project | Purpose |
|------|------|
| [AlphaSift](https://github.com/ZhuLinsen/alphasift) | Reference project for DSA's stock screening implementation |
| [AlphaEvo](https://github.com/ZhuLinsen/alphaevo) | Strategy backtesting and self-evolution; validates strategy rules and iteratively explores parameters and combinations |

## 📬 Contact and Collaboration

<table>
  <tr>
    <td width="92" valign="top"><strong>Email</strong></td>
    <td valign="top">
      <a href="mailto:zhuls345@gmail.com">zhuls345@gmail.com</a><br>
      Project enquiries, deployment support, and feature extensions
    </td>
    <td align="center" rowspan="3" valign="middle" width="148">
      <a href="http://xhslink.com/m/tU520DWCKT" target="_blank"><img src="./docs/assets/xiaohongshu_tick.jpg" width="112" alt="Xiaohongshu QR code"></a><br>
      <sub>Scan to follow on Xiaohongshu</sub>
    </td>
  </tr>
  <tr>
    <td width="92" valign="top"><strong>Xiaohongshu</strong></td>
    <td valign="top"><a href="http://xhslink.com/m/tU520DWCKT">Follow on Xiaohongshu</a></td>
  </tr>
  <tr>
    <td width="92" valign="top"><strong>Feedback</strong></td>
    <td valign="top"><a href="https://github.com/ZhuLinsen/daily_stock_analysis/issues">Open an issue</a></td>
  </tr>
</table>

## 📄 License

[MIT License](LICENSE) © 2026 ZhuLinsen

If you build on or cite this project, a note crediting this repository is appreciated and helps keep it maintained.

## ⚠️ Disclaimer

This project is for learning and research only and does not constitute investment advice. Markets carry risk; invest with care. The author is not responsible for any loss arising from use of this project.

---
