import type {
  AnalysisPhase,
  MarketPhaseSummary,
  MarketPhaseValue,
} from '../types/analysis';
import type { UiLanguage } from '../i18n/uiText';

/**
 * Phase labels are UI chrome, so they follow the UI language (vi/en) rather than the
 * report content language. Callers may pass either a UiLanguage or a backend
 * ReportLanguage ('zh' | 'en' | 'ko'); anything that is not Vietnamese renders in
 * English, the UI fallback language.
 */
const toUiLanguage = (value?: string | null): UiLanguage =>
  value?.toLowerCase().startsWith('vi') ? 'vi' : 'en';

const REQUEST_PHASE_LABELS: Record<UiLanguage, Record<AnalysisPhase, string>> = {
  vi: { auto: 'Tự động', premarket: 'Trước phiên', intraday: 'Trong phiên', postmarket: 'Sau phiên' },
  en: { auto: 'Auto', premarket: 'Pre-market', intraday: 'Intraday', postmarket: 'Post-market' },
};

const MARKET_PHASE_LABELS: Record<UiLanguage, Record<MarketPhaseValue, string>> = {
  vi: {
    premarket: 'Trước phiên',
    intraday: 'Trong phiên',
    lunch_break: 'Nghỉ trưa',
    closing_auction: 'Phiên khớp lệnh đóng cửa',
    postmarket: 'Sau phiên',
    non_trading: 'Ngày không giao dịch',
    unknown: 'Không rõ giai đoạn',
  },
  en: {
    premarket: 'Pre-market',
    intraday: 'Intraday',
    lunch_break: 'Lunch break',
    closing_auction: 'Closing auction',
    postmarket: 'Post-market',
    non_trading: 'Non-trading day',
    unknown: 'Unknown phase',
  },
};

const TEXT: Record<UiLanguage, { requestPrefix: string; finalPrefix: string; partialBar: string }> = {
  vi: { requestPrefix: 'Giai đoạn yêu cầu', finalPrefix: 'Giai đoạn thị trường', partialBar: 'Nến chưa hoàn chỉnh' },
  en: { requestPrefix: 'Requested phase', finalPrefix: 'Market phase', partialBar: 'Partial bar' },
};

export const getRequestedPhaseLabel = (
  phase?: AnalysisPhase | null,
  language?: string | null,
): string | null => {
  if (!phase) {
    return null;
  }

  const uiLanguage = toUiLanguage(language);
  const label = REQUEST_PHASE_LABELS[uiLanguage][phase];
  if (!label) {
    return null;
  }

  return `${TEXT[uiLanguage].requestPrefix}: ${label}`;
};

export const getMarketPhaseSummaryLabel = (
  summary?: MarketPhaseSummary | null,
  language?: string | null,
): string | null => {
  if (!summary) {
    return null;
  }

  const uiLanguage = toUiLanguage(language);
  const phaseLabel = MARKET_PHASE_LABELS[uiLanguage][summary.phase];
  if (!phaseLabel) {
    return null;
  }

  const market = (summary.market || '').trim().toUpperCase();
  const value = market ? `${market} · ${phaseLabel}` : phaseLabel;
  return `${TEXT[uiLanguage].finalPrefix}: ${value}`;
};

export const getPartialBarLabel = (language?: string | null): string =>
  TEXT[toUiLanguage(language)].partialBar;

/** Same as getMarketPhaseSummaryLabel but without the "Market phase: " prefix. */
export const getMarketPhaseValueLabel = (
  summary?: MarketPhaseSummary | null,
  language?: string | null,
): string | null => {
  if (!summary) {
    return null;
  }

  const uiLanguage = toUiLanguage(language);
  const phaseLabel = MARKET_PHASE_LABELS[uiLanguage][summary.phase];
  if (!phaseLabel) {
    return null;
  }

  const market = (summary.market || '').trim().toUpperCase();
  return market ? `${market} · ${phaseLabel}` : phaseLabel;
};
