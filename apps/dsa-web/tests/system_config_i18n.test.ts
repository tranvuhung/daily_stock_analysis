import { describe, expect, it } from 'vitest';
import { UI_TEXT } from '../src/i18n/uiText';
import { getSettingsHelpContent } from '../src/locales/settingsHelp';
import { getFieldOptionLabel } from '../src/utils/systemConfigI18n';

const CHINESE = /[\u4e00-\u9fff]/;

const REQUIRED_LOCALIZED_KEYS = [
  'TICKFLOW_API_KEY',
  'TICKFLOW_PRIORITY',
  'TICKFLOW_KLINE_ADJUST',
  'TICKFLOW_BATCH_DAILY_ENABLED',
  'TICKFLOW_BATCH_SIZE',
  'STOCK_INDEX_REMOTE_UPDATE_ENABLED',
  'SEARXNG_BASE_URLS',
  'ENABLE_REALTIME_QUOTE',
  'ENABLE_CHIP_DISTRIBUTION',
  'PYTDX_HOST',
  'PYTDX_PORT',
  'PYTDX_SERVERS',
  'BIAS_THRESHOLD',
  'GENERATION_BACKEND',
  'GENERATION_FALLBACK_BACKEND',
  'GENERATION_BACKEND_TIMEOUT_SECONDS',
  'GENERATION_BACKEND_MAX_OUTPUT_BYTES',
  'GENERATION_BACKEND_MAX_CONCURRENCY',
  'LOCAL_CLI_BACKEND_MAX_CONCURRENCY',
  'LLM_PROMPT_CACHE_TELEMETRY_ENABLED',
  'LLM_PROMPT_CACHE_HINTS_ENABLED',
  'LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL',
  'LLM_USAGE_HMAC_SECRET',
  'LLM_USAGE_HMAC_KEY_VERSION',
  'TELEGRAM_BOT_TOKEN',
  'TELEGRAM_CHAT_ID',
  'TELEGRAM_MESSAGE_THREAD_ID',
  'FEISHU_STREAM_ENABLED',
  'DINGTALK_STREAM_ENABLED',
  'EMAIL_SENDER',
  'EMAIL_PASSWORD',
  'EMAIL_RECEIVERS',
  'DISCORD_WEBHOOK_URL',
  'DISCORD_BOT_TOKEN',
  'DISCORD_MAIN_CHANNEL_ID',
  'DISCORD_INTERACTIONS_PUBLIC_KEY',
  'SLACK_BOT_TOKEN',
  'SLACK_CHANNEL_ID',
  'SLACK_WEBHOOK_URL',
  'PUSHPLUS_TOPIC',
  'PUSHOVER_USER_KEY',
  'PUSHOVER_API_TOKEN',
  'SERVERCHAN3_SENDKEY',
  'ASTRBOT_URL',
  'ASTRBOT_TOKEN',
  'CUSTOM_WEBHOOK_BEARER_TOKEN',
  'WEBHOOK_VERIFY_SSL',
  'SINGLE_STOCK_NOTIFY',
  'REPORT_TYPE',
  'REPORT_LANGUAGE',
  'REPORT_TEMPLATES_DIR',
  'REPORT_INTEGRITY_ENABLED',
  'REPORT_RENDERER_ENABLED',
  'REPORT_INTEGRITY_RETRY',
  'REPORT_HISTORY_COMPARE_N',
  'MERGE_EMAIL_NOTIFICATION',
  'NOTIFICATION_REPORT_CHANNELS',
  'NOTIFICATION_ALERT_CHANNELS',
  'NOTIFICATION_SYSTEM_ERROR_CHANNELS',
  'NOTIFICATION_DEDUP_TTL_SECONDS',
  'NOTIFICATION_COOLDOWN_SECONDS',
  'NOTIFICATION_QUIET_HOURS',
  'NOTIFICATION_TIMEZONE',
  'NOTIFICATION_MIN_SEVERITY',
  'NOTIFICATION_DAILY_DIGEST_ENABLED',
  'SCHEDULE_ENABLED',
  'DSA_RUNTIME_SCHEDULER_TIMEOUT_SECONDS',
  'SCHEDULE_RUN_IMMEDIATELY',
  'TRADING_DAY_CHECK_ENABLED',
  'WEBUI_HOST',
  'LOG_DIR',
  'WEBUI_ENABLED',
  'WEBUI_AUTO_BUILD',
  'ADMIN_AUTH_ENABLED',
  'TRUST_X_FORWARDED_FOR',
  'RUN_IMMEDIATELY',
  'MARKET_REVIEW_ENABLED',
  'DAILY_MARKET_CONTEXT_ENABLED',
  'MARKET_REVIEW_REGION',
  'ANALYSIS_DELAY',
  'SAVE_CONTEXT_SNAPSHOT',
  'DEBUG',
  'AGENT_GENERATION_BACKEND',
  'AGENT_NL_ROUTING',
  'AGENT_DEEP_RESEARCH_BUDGET',
  'AGENT_DEEP_RESEARCH_TIMEOUT',
  'AGENT_EVENT_MONITOR_ENABLED',
  'AGENT_EVENT_MONITOR_INTERVAL_MINUTES',
  'AGENT_EVENT_ALERT_RULES_JSON',
] as const;

describe('systemConfigI18n required key coverage', () => {
  it('documents LLM usage HMAC privacy boundaries', () => {
    const vi = getSettingsHelpContent('settings.ai_model.LLM_USAGE_HMAC_SECRET', undefined, 'vi');
    const viText = JSON.stringify(vi);
    const en = getSettingsHelpContent('settings.ai_model.LLM_USAGE_HMAC_SECRET', undefined, 'en');

    expect(vi?.summary).toContain('HMAC');
    expect(viText).not.toMatch(CHINESE);
    expect(en?.summary).toContain('HMAC');
    expect(en?.notes?.join(' ')).toContain('Do not');
  });

  it('documents the runtime scheduler timeout in both UI languages', () => {
    const vi = getSettingsHelpContent('settings.system.schedule', undefined, 'vi');
    const en = getSettingsHelpContent('settings.system.schedule', undefined, 'en');
    const viText = JSON.stringify(vi);
    const enText = JSON.stringify(en);

    expect(viText).toContain('DSA_RUNTIME_SCHEDULER_TIMEOUT_SECONDS');
    expect(viText).toContain('2700');
    expect(viText).toContain('60');
    expect(enText).toContain('DSA_RUNTIME_SCHEDULER_TIMEOUT_SECONDS');
    expect(enText).toContain('2700');
    expect(enText).toContain('60');
  });
});

describe('systemConfigI18n required key coverage', () => {
  it('exposes an option label or passthrough for every required config key', () => {
    REQUIRED_LOCALIZED_KEYS.forEach((key) => {
      expect(getFieldOptionLabel(key, 'unused-value')).toBeTruthy();
    });
  });
});

describe('systemConfigI18n option label localization', () => {
  const knownValues: Record<string, string[]> = {
    NEWS_STRATEGY_PROFILE: ['ultra_short', 'short', 'medium', 'long'],
    REPORT_TYPE: ['simple', 'full', 'brief'],
    REPORT_LANGUAGE: ['zh', 'en', 'ko'],
    NOTIFICATION_MIN_SEVERITY: ['', 'info', 'warning', 'error', 'critical'],
    LOG_LEVEL: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
    LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL: ['off', 'basic', 'debug'],
    MARKET_REVIEW_COLOR_SCHEME: ['green_up', 'red_up'],
    GENERATION_BACKEND: ['litellm'],
    GENERATION_FALLBACK_BACKEND: ['litellm'],
    AGENT_GENERATION_BACKEND: ['auto', 'litellm'],
    AGENT_ARCH: ['single', 'multi'],
    AGENT_ORCHESTRATOR_MODE: ['quick', 'standard', 'full', 'specialist'],
    AGENT_SKILL_ROUTING: ['auto', 'manual'],
  };
  const eachOption = (fn: (key: string, value: string) => void) => {
    Object.entries(knownValues).forEach(([key, values]) => values.forEach((value) => fn(key, value)));
  };

  it('resolves a human-readable label for every exposed select option', () => {
    eachOption((key, value) => {
      const label = getFieldOptionLabel(key, value);
      expect(label).toBeTruthy();
      if (value !== '') {
        expect(label).not.toBe(value);
      }
    });
  });

  it('never exposes Chinese option labels', () => {
    eachOption((key, value) => {
      expect(getFieldOptionLabel(key, value)).not.toMatch(CHINESE);
    });
  });

  it('treats free-text config keys as passthrough for option labels', () => {
    expect(getFieldOptionLabel('MARKET_REVIEW_REGION', 'cn')).toBe('cn');
    expect(getFieldOptionLabel('MARKET_REVIEW_REGION', 'cn,us,jp,kr')).toBe('cn,us,jp,kr');
  });
});

describe('SAVE_CONTEXT_SNAPSHOT settings help contract', () => {
  it('describes the persistence boundary without implying old records are changed', () => {
    const help = getSettingsHelpContent('settings.system.SAVE_CONTEXT_SNAPSHOT', undefined, 'vi');
    const text = [
      help?.summary,
      help?.usage,
      ...(help?.valueNotes ?? []),
      ...(help?.impact ?? []),
      ...(help?.notes ?? []),
    ].join('\n');

    expect(text).toContain('AnalysisContextPack');
    expect(text).not.toMatch(CHINESE);
  });
});

describe('generation backend settings help contract', () => {
  const flatten = (help: ReturnType<typeof getSettingsHelpContent>) => [
    help?.title, help?.summary, help?.usage,
    ...(help?.valueNotes ?? []), ...(help?.impact ?? []), ...(help?.notes ?? []),
  ].filter(Boolean).join('\n');

  it('uses user-facing generation channel copy instead of implementation terms', () => {
    const backend = getSettingsHelpContent('settings.ai_model.GENERATION_BACKEND', undefined, 'en');
    const fallback = getSettingsHelpContent('settings.ai_model.GENERATION_FALLBACK_BACKEND', undefined, 'en');
    const agent = getSettingsHelpContent('settings.agent.AGENT_GENERATION_BACKEND', undefined, 'en');
    const text = [backend, fallback, agent].map(flatten).join('\n');

    expect(backend?.title).toBe('Analysis Generation Method');
    expect(fallback?.title).toBe('Fallback Generation Method');
    expect(agent?.title).toBe('Ask-Stock Generation Method');
    expect(backend?.showFieldKey).toBe(false);
    expect(fallback?.showFieldKey).toBe(false);
    expect(agent?.showFieldKey).toBe(false);
    expect(text).toContain('stock analysis');
    expect(text).toContain('market reviews');
    expect(text).toContain('Auto');
    expect(backend?.usage).toContain('Default model settings');
    expect(fallback?.usage).toContain('Default model settings');
    expect(agent?.usage).toContain('currently available method');
    expect(agent?.valueNotes).toContain('If you are unsure, choose Auto.');
    expect(backend?.notes?.join('\n')).toContain('Default model settings continue');
    expect(backend?.notes?.join('\n')).not.toContain('Advanced note');
    expect(backend?.notes?.join('\n')).not.toContain('LiteLLM');
    expect(text).not.toContain('unsupported_tool_calling');
    expect(text).not.toContain('run_agent_loop');
  });

  it('ships the same catalogue for every UI language and no Chinese copy', () => {
    for (const key of [
      'settings.ai_model.GENERATION_BACKEND',
      'settings.ai_model.GENERATION_FALLBACK_BACKEND',
      'settings.agent.AGENT_GENERATION_BACKEND',
    ]) {
      const vi = flatten(getSettingsHelpContent(key, undefined, 'vi'));
      expect(vi).toBeTruthy();
      expect(vi).not.toMatch(CHINESE);
    }
  });
});

describe('generation backend status panel i18n contract', () => {
  const keys = [
    'settings.generationBackendStatus',
    'settings.generationBackendSmokeTest',
    'settings.generationBackendPrimary',
    'settings.generationBackendFallback',
    'settings.generationBackendGenerationOnly',
    'settings.generationBackendStatusDescription',
    'settings.generationBackendSmokePassed',
  ] as const;

  it('keeps the new status panel copy present in both UI languages', () => {
    for (const key of keys) {
      expect(UI_TEXT.en[key]).toBeTruthy();
      expect(UI_TEXT.vi[key]).toBeTruthy();
      expect(UI_TEXT.vi[key]).not.toMatch(CHINESE);
    }

    expect(UI_TEXT.en['settings.generationBackendStatus']).toBe('Generation backend status');
    expect(UI_TEXT.en['settings.generationBackendSmokeTest']).toBe('JSON smoke test');
    expect(UI_TEXT.en['settings.generationBackendPrimary']).toBe('Primary backend');
    expect(UI_TEXT.en['settings.generationBackendFallback']).toBe('Fallback backend');
    expect(UI_TEXT.en['settings.generationBackendGenerationOnly']).toBe('Generation only');
  });
});

describe('decision signal settings guard', () => {
  it('does not add placeholder DecisionSignal setting help without a real schema field', () => {
    const placeholderKeys = [
      'DECISION_SIGNAL_ENABLED',
      'DECISION_SIGNALS_ENABLED',
      'DECISION_SIGNAL_WRITE_ENABLED',
      'DECISION_SIGNAL_EXTRACT_ENABLED',
    ];

    placeholderKeys.forEach((key) => {
      for (const locale of ['vi', 'en']) {
        expect(getSettingsHelpContent(`settings.system.${key}`, undefined, locale)).toBeNull();
      }
    });
  });
});
