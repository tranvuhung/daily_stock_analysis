import type { SystemConfigCategory } from '../types/systemConfig';
import type { UiLanguage } from '../i18n/uiText';

const categoryTitleMap: Record<UiLanguage, Record<SystemConfigCategory, string>> = {
  vi:{
    base: 'Cài đặt cơ bản',
    data_source: 'Nguồn dữ liệu',
    ai_model: 'Mô hình AI',
    notification: 'Thông báo',
    system: 'Hệ thống',
    agent: 'Agent',
    backtest: 'Backtest',
    uncategorized: 'Khác',
  },
  en: {
    base: 'Base settings',
    data_source: 'Data sources',
    ai_model: 'AI models',
    notification: 'Notifications',
    system: 'System',
    agent: 'Agent',
    backtest: 'Backtest',
    uncategorized: 'Other',
  },
};

const categoryDescriptionMap: Record<UiLanguage, Partial<Record<SystemConfigCategory, string>>> = {
  vi:{
    base: 'Quản lý danh mục theo dõi và các tham số chạy cơ bản.',
    data_source: 'Quản lý nguồn dữ liệu thị trường và thứ tự ưu tiên.',
    ai_model: 'Quản lý dịch vụ mô hình, tên mô hình và tham số suy luận.',
    notification: 'Quản lý bot, webhook và việc gửi thông báo.',
    system: 'Quản lý lịch chạy, nhật ký, cổng và tham số hệ thống.',
    agent: 'Quản lý chế độ Agent, chiến lược và điều phối nhiều Agent.',
    backtest: 'Quản lý các công tắc backtest, kỳ đánh giá và tham số bộ máy.',
    uncategorized: 'Các cài đặt khác chưa phân loại.',
  },
  en: {
    base: 'Manage watchlists and base runtime parameters.',
    data_source: 'Manage market data sources and priority strategies.',
    ai_model: 'Manage model services, model names, and inference parameters.',
    notification: 'Manage bots, webhooks, and notification delivery.',
    system: 'Manage scheduling, logging, ports, and system parameters.',
    agent: 'Manage Agent mode, strategies, and multi-agent orchestration.',
    backtest: 'Manage backtest switches, evaluation windows, and engine parameters.',
    uncategorized: 'Other uncategorized settings.',
  },
};

const fieldOptionLabelMap: Record<string, Record<string, string>> = {
  NEWS_STRATEGY_PROFILE: {
    ultra_short: 'Ultra short (1 day)',
    short: 'Short (3 days)',
    medium: 'Medium (7 days)',
    long: 'Long (30 days)',
  },
  REPORT_TYPE: {
    simple: 'Simple',
    full: 'Full',
    brief: 'Brief',
  },
  REPORT_LANGUAGE: {
    zh: 'Chinese',
    en: 'English',
    ko: 'Korean',
    chinese: 'Chinese',
    english: 'English',
  },
  NOTIFICATION_MIN_SEVERITY: {
    '': 'Not set',
    'not set': 'Not set',
    info: 'Info',
    warning: 'Warning',
    error: 'Error',
    critical: 'Critical',
  },
  MARKET_REVIEW_COLOR_SCHEME: {
    green_up: 'Green up / red down',
    red_up: 'Red up / green down',
    'green up / red down': 'Green up / red down',
    'red up / green down': 'Red up / green down',
  },
  GENERATION_BACKEND: {
    litellm: 'Default model settings',
    codex_cli: 'Codex CLI (experimental)',
    claude_code_cli: 'Claude Code CLI (experimental)',
    opencode_cli: 'OpenCode CLI (experimental)',
  },
  GENERATION_FALLBACK_BACKEND: {
    '': 'Disabled',
    litellm: 'Default model settings',
  },
  AGENT_GENERATION_BACKEND: {
    auto: 'Auto',
    litellm: 'Default model settings',
  },
  AGENT_BACKEND: {
    auto: 'Auto (recommended)',
    litellm: 'Default model settings',
    codex_app_server: 'Codex local Agent (experimental)',
  },
  LOG_LEVEL: {
    debug: 'Debug',
    info: 'Info',
    warning: 'Warning',
    error: 'Error',
    critical: 'Critical',
  },
  LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL: {
    off: 'Off',
    basic: 'Basic',
    debug: 'Debug',
  },
  AGENT_ARCH: {
    single: 'Single Agent',
    multi: 'Multi Agent (orchestrator)',
    'single agent': 'Single Agent',
    'multi agent (orchestrator)': 'Multi Agent (orchestrator)',
  },
  AGENT_ORCHESTRATOR_MODE: {
    quick: 'Quick',
    standard: 'Standard',
    full: 'Full',
    specialist: 'Specialist',
  },
  AGENT_SKILL_ROUTING: {
    auto: 'Auto (regime-based)',
    manual: 'Manual (use AGENT_SKILLS)',
    'auto (regime-based)': 'Auto (regime-based)',
    'manual (use agent_skills)': 'Manual (use AGENT_SKILLS)',
  },
};

function normalizeOptionToken(raw: string): string {
  return raw.trim().toLowerCase();
}

export function getCategoryTitle(category: SystemConfigCategory, fallback?: string, locale: UiLanguage = 'en'): string {
  return categoryTitleMap[locale][category] || fallback || category;
}

export function getCategoryDescription(category: SystemConfigCategory, fallback?: string, locale: UiLanguage = 'en'): string {
  return categoryDescriptionMap[locale][category] || fallback || '';
}

export function getFieldOptionLabel(
  key: string,
  value: string,
  fallbackLabel?: string,
  locale: UiLanguage = 'en',
): string {
  const map = locale === 'en' ? fieldOptionLabelMap[key] : fieldOptionLabelMap[key];
  if (!map) {
    return fallbackLabel ?? value;
  }

  const byValue = map[normalizeOptionToken(value)];
  if (byValue) {
    return byValue;
  }

  if (fallbackLabel) {
    const byLabel = map[normalizeOptionToken(fallbackLabel)];
    if (byLabel) {
      return byLabel;
    }
  }

  return fallbackLabel ?? value;
}
