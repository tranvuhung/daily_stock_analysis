# English Documentation Index

This is the entry point for project documentation. The README covers the project overview and quick start; detailed setup, configuration, deployment, feature usage, and troubleshooting docs are linked below.

> Scope: the project now supports **the Vietnam stock market only** (HOSE / HNX / UPCoM). See [Market scope](market-support.md) (Vietnamese).
>
> For the Vietnamese documentation index, see [docs/INDEX.md](INDEX.md).

## Choose By Goal

| I want to | Start with | Then read |
| --- | --- | --- |
| Understand what the project does | [README (EN)](README_EN.md) | [Full Guide (EN)](full-guide_EN.md) |
| Run the project for the first time | [README (EN)](README_EN.md) | [Full Guide (EN)](full-guide_EN.md) |
| Configure model providers | [LLM Config Guide (EN)](LLM_CONFIG_GUIDE_EN.md) | [Provider Configuration Guide](llm-providers.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) |
| Configure notifications | [Notification Baseline](notifications.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | [Full Guide (EN)](full-guide_EN.md) |
| Deploy to a server or cloud platform | [Deploy Guide (EN)](DEPLOY_EN.md) | [Cloud WebUI Deployment](deploy-webui-cloud.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only), [Zeabur Deployment](docker/zeabur-deployment.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) |
| Use Bot / IM integrations | [Bot Commands (EN)](bot-command_EN.md) | [Discord Bot setup](bot/discord-bot-config.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) |
| Troubleshoot runtime issues | [FAQ (EN)](FAQ_EN.md) | [Changelog](CHANGELOG.md) |
| Troubleshoot data-source failures | [Data-source stability](data-source-stability.md) (Vietnamese-only) | [Market scope](market-support.md) (Vietnamese-only) |
| Contribute code or docs | [Contributing Guide (EN)](CONTRIBUTING_EN.md) | [API Spec](architecture/api_spec.json) |

## Getting Started

| Document | Contents |
| --- | --- |
| [README (EN)](README_EN.md) | Project overview, key features, quick start, sample output |
| [Full Guide (EN)](full-guide_EN.md) | Environment setup, run modes, configuration, deployment paths, and common issues |
| [FAQ (EN)](FAQ_EN.md) | Common configuration, model, notification, deployment, and runtime issues |
| [Market scope](market-support.md) (Vietnamese-only) | Vietnam market: code formats, exchanges, indices, data sources, removed features |
| [Data-source stability](data-source-stability.md) (Vietnamese-only) | VCI → SSI → optional vnstock → Yahoo chain, fallback, and recommended configuration |
| [Changelog](CHANGELOG.md) | Release notes, capability changes, and migration notes |

## Configuration

| Document | Contents |
| --- | --- |
| [LLM Config Guide (EN)](LLM_CONFIG_GUIDE_EN.md) | Model providers, three-tier configuration, Web settings, and common model setup |
| [Provider Configuration Guide](llm-providers.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Provider presets, GitHub Actions mapping, error categories, and diagnostics |
| [LiteLLM YAML Example](examples/litellm_config.example.yaml) | Example LiteLLM multi-provider configuration |
| [Notification Baseline](notifications.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Telegram, Email, Discord, Slack, Pushover, ntfy, Gotify, and Custom Webhook notification channels |

## Usage Topics

| Document | Contents |
| --- | --- |
| [Bot Commands (EN)](bot-command_EN.md) | Bot commands, webhooks, platform integration, and callback behavior |
| [Discord Bot setup](bot/discord-bot-config.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Discord Bot configuration notes |
| [Real-Time Alert Center](alerts.md) <sub><sub>![P4 Badge](https://img.shields.io/badge/P4-yellow?style=flat)</sub></sub> (Chinese-only) | EventMonitor baseline, Web rule management, notification attempts, cooldown state, and phase boundaries |
| [Alerts User Guide](alerts-user-guide.md) (Vietnamese-only) | Web user guide: enabling alerts, creating and managing rules, reading trigger history, troubleshooting |
| [Alert Center Mechanism](alerts-mechanism.md) (Vietnamese-only) | Architecture and flow: data model, rule types, periodic worker, realtime MQTT engine, notifications, API, Web, configuration, known gaps |
| [Stock Profile API](stock-profile-api.md) <sub><sub>![P1 Badge](https://img.shields.io/badge/P1-yellow?style=flat)</sub></sub> (Chinese-only) | Single stock-profile contract, per-block quality status, code normalization, and Web phase boundaries |
| [DecisionSignal Topic](decision-signals.md) <sub><sub>![P7 Badge](https://img.shields.io/badge/P7-orange?style=flat)</sub></sub> (Chinese-only) | AI signal fields, API, Web display, alert/notification/portfolio-risk linkage, outcome evaluation, redaction, migration, and rollback |
| [ResearchArtifact Contract](research-artifact.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Structured thesis, evidence, invalidation conditions, deterministic fallback identity, and the boundary before persistence/API integration |
| [Analysis Context Pack Contract, Runtime Consumption, And Visibility](analysis-context-pack.md) <sub><sub>![P6 Badge](https://img.shields.io/badge/P6-orange?style=flat)</sub></sub> (Chinese-only) | AnalysisContextPack first-scope boundaries, field quality states, P1/P2 internal contracts, P3 prompt-summary consumption, P4 history/API/Web low-sensitivity visibility, P5 data-quality scoring, and P6 migration/rollback notes, plus source anchors; the full guide adds #1386 market-phase analysis, migration, and rollback entry points |
| [Image Extraction Prompt](image-extract-prompt.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Prompt and boundaries for extracting stock information from images |
| [OpenClaw Skill Integration](openclaw-skill-integration.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | OpenClaw / Skill external integration notes |
| [Grok Bot Integration](grok-bot-integration.md) <sub><sub>![P0 Badge](https://img.shields.io/badge/P0-green?style=flat)</sub></sub> (Chinese-only) | 2026-08-11 Grok Bot (AI teammate) vs LiteLLM `xai/` provider; HTTP / Skill / DecisionSignal consumption |

## Deployment And Packaging

| Document | Contents |
| --- | --- |
| [Deploy Guide (EN)](DEPLOY_EN.md) | Server deployment, Docker, systemd, Supervisor, and related options |
| [Cloud WebUI Deployment](deploy-webui-cloud.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Cloud server WebUI access and deployment notes |
| [Zeabur Deployment](docker/zeabur-deployment.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Zeabur platform deployment |
| [Desktop Packaging](desktop-package.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Electron desktop app and Web artifact packaging |
| [Windows LAN Deployment](windows-lan-deploy.md) (Vietnamese-only) | Build on macOS, run FastAPI + Web UI + scheduler + realtime alerts on a Windows machine, reachable over the LAN IP |

## Reference And Development

| Document | Contents |
| --- | --- |
| [API Spec](architecture/api_spec.json) | FastAPI OpenAPI artifact |
| [Contributing Guide (EN)](CONTRIBUTING_EN.md) | Issues, pull requests, tests, documentation sync, and collaboration expectations |

## Languages

| Document | Contents |
| --- | --- |
| [Vietnamese Documentation Index](INDEX.md) | Vietnamese documentation entry point |

## Vietnam-Market Glossary

| Term | Meaning |
| --- | --- |
| **HOSE** | Ho Chi Minh City Stock Exchange (VN-Index, VN30) |
| **HNX** | Hanoi Stock Exchange (HNX-Index, HNX30) |
| **UPCoM** | Unlisted Public Company Market operated by HNX |
| **VCI** | Vietcap public market-data API (default primary source, no key required) |
| **SSI** | SSI iBoard public market-data API (default secondary source, no key required) |
| **vnstock** | Optional third-party Python library (custom personal-use licence, installed only via `requirements-vn-optional.txt`) |
