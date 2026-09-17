import type {
  PortfolioCashDirection,
  PortfolioCorporateActionType,
  PortfolioFxRefreshResponse,
  PortfolioImportCommitResponse,
  PortfolioImportParseResponse,
  PortfolioPositionItem,
  PortfolioSide,
} from '../types/portfolio';
import { toDateInputValue } from './format';

export type FxRefreshFeedback = {
  tone: 'neutral' | 'success' | 'warning';
  text: string;
};

export type PortfolioAlertVariant = 'info' | 'success' | 'warning' | 'danger';

export function getTodayIso(): string {
  return toDateInputValue(new Date());
}

export function formatMoney(value: number | undefined | null, currency = 'CNY'): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${currency} ${Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatPct(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${value.toFixed(2)}%`;
}

export function formatSignedPct(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

export function hasPositionPrice(row: PortfolioPositionItem): boolean {
  return row.priceAvailable !== false && row.priceSource !== 'missing';
}

export function formatPositionPrice(row: PortfolioPositionItem): string {
  if (!hasPositionPrice(row)) return '--';
  return row.lastPrice.toFixed(4);
}

export function formatPositionMoney(value: number, row: PortfolioPositionItem): string {
  if (!hasPositionPrice(row)) return '--';
  return formatMoney(value, row.valuationCurrency);
}

export function getPositionPriceLabel(row: PortfolioPositionItem): string {
  if (!hasPositionPrice(row)) return 'No price';
  if (row.priceSource === 'realtime_quote') {
    return row.priceProvider ? `Live price · ${row.priceProvider}` : 'Live price';
  }
  if (row.priceSource === 'history_close') {
    return row.priceStale && row.priceDate ? `Close · ${row.priceDate}` : 'Close';
  }
  return row.priceSource || 'Unknown source';
}

export function formatSideLabel(value: PortfolioSide): string {
  return value === 'buy' ? 'Buy' : 'Sell';
}

export function formatCashDirectionLabel(value: PortfolioCashDirection): string {
  return value === 'in' ? 'Inflow' : 'Outflow';
}

export function formatCorporateActionLabel(value: PortfolioCorporateActionType): string {
  return value === 'cash_dividend' ? 'Cash dividend' : 'Split/merge adjustment';
}

export function formatBrokerLabel(value: string, displayName?: string): string {
  if (displayName && displayName.trim()) return `${value} (${displayName.trim()})`;
  if (value === 'huatai') return 'huatai (Huatai)';
  if (value === 'citic') return 'citic (CITIC)';
  if (value === 'cmb') return 'cmb (CMB)';
  return value;
}

export function buildFxRefreshFeedback(data: PortfolioFxRefreshResponse): FxRefreshFeedback {
  if (data.refreshEnabled === false) {
    return {
      tone: 'neutral',
      text: 'Online FX-rate refresh is disabled.',
    };
  }

  if (data.pairCount === 0) {
    return {
      tone: 'neutral',
      text: 'No FX pair in the current scope needs refreshing.',
    };
  }

  if (data.updatedCount > 0 && data.staleCount === 0 && data.errorCount === 0) {
    return {
      tone: 'success',
      text: `FX rates refreshed; ${data.updatedCount} pair(s) updated.`,
    };
  }

  const summary = `${data.updatedCount} pair(s) updated, ${data.staleCount} still stale, ${data.errorCount} failed.`;
  if (data.staleCount > 0) {
    return {
      tone: 'warning',
      text: `Refresh attempted, but some currency pairs still use stale/fallback rates. ${summary}`,
    };
  }

  return {
    tone: 'warning',
    text: `Online refresh did not fully succeed. ${summary}`,
  };
}

export function getFxRefreshFeedbackVariant(tone: FxRefreshFeedback['tone']): PortfolioAlertVariant {
  if (tone === 'success') return 'success';
  if (tone === 'warning') return 'warning';
  return 'info';
}

export function getCsvParseVariant(result: PortfolioImportParseResponse): PortfolioAlertVariant {
  return result.errorCount > 0 || result.skippedCount > 0 ? 'warning' : 'info';
}

export function getCsvCommitVariant(result: PortfolioImportCommitResponse, isDryRun: boolean): PortfolioAlertVariant {
  if (isDryRun) return 'info';
  return result.failedCount > 0 || result.duplicateCount > 0 ? 'warning' : 'success';
}
