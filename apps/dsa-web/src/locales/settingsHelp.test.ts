import { describe, expect, it } from 'vitest';

import { getSettingsHelpContent } from './settingsHelp';

const flattenHelp = (help: ReturnType<typeof getSettingsHelpContent>) => [
  help?.summary,
  help?.usage,
  ...(help?.valueNotes ?? []),
  ...(help?.impact ?? []),
  ...(help?.notes ?? []),
].filter(Boolean).join(' ');

describe('Skill Outcome auto-weight settings help', () => {
  it('describes the attributable Outcome threshold (en)', () => {
    const visibleCopy = flattenHelp(getSettingsHelpContent(
      'settings.agent.AGENT_SKILL_AUTOWEIGHT',
      undefined,
      'en',
    ));

    expect(visibleCopy).toContain('Outcome');
    expect(visibleCopy).toContain('30');
    expect(visibleCopy).toContain('1.0');
    expect(visibleCopy).toContain('Global backtest win rates never substitute');
    expect(visibleCopy).not.toContain('Depends on backtest');
  });

  it('does not present Backtest as the Skill auto-weight data source (en)', () => {
    const help = flattenHelp(getSettingsHelpContent(
      'settings.backtest.BACKTEST_ENABLED',
      undefined,
      'en',
    ));

    expect(help).toContain('Skill Outcome');
    expect(help).toContain('does not directly');
  });

  it('keeps the numeric Outcome contract identical in Vietnamese', () => {
    const visibleCopy = flattenHelp(getSettingsHelpContent(
      'settings.agent.AGENT_SKILL_AUTOWEIGHT',
      undefined,
      'vi',
    ));

    expect(visibleCopy).toContain('Outcome');
    expect(visibleCopy).toContain('30');
    expect(visibleCopy).toContain('1.0');
    expect(visibleCopy).toMatch(/[\u00C0-\u1EF9]/);
  });
});

describe('settings help localisation', () => {
  it('serves content for every supported UI language and never falls back to null', () => {
    for (const locale of ['vi', 'en', undefined]) {
      const help = getSettingsHelpContent('settings.agent.AGENT_SKILL_AUTOWEIGHT', undefined, locale);
      expect(help?.title).toBeTruthy();
    }
  });

  it('does not ship Chinese copy in the settings help catalogue', () => {
    for (const locale of ['vi', 'en']) {
      const help = flattenHelp(getSettingsHelpContent('settings.agent.AGENT_SKILL_AUTOWEIGHT', undefined, locale));
      expect(help).not.toMatch(/[一-鿿]/);
    }
  });
});
