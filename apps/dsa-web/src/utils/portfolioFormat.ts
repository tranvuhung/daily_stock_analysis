import type {
  PortfolioCashDirection,
  PortfolioCorporateActionType,
  PortfolioPositionItem,
  PortfolioSide,
} from '../types/portfolio';
import { toDateInputValue } from './format';

export type PortfolioAlertVariant = 'info' | 'success' | 'warning' | 'danger';

export function getTodayIso(): string {
  return toDateInputValue(new Date());
}

// Mặc định tiền tệ VND (chỉ còn thị trường Việt Nam). / Defaults to VND (Vietnam-only).
export function formatMoney(value: number | undefined | null, currency = 'VND'): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${currency} ${Number(value).toLocaleString('en-US', {
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
