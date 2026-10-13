import { describe, expect, it } from 'vitest';
import {
  formatMoney,
  formatPositionMoney,
  formatPositionPrice,
  formatSignedPct,
  getPositionPriceLabel,
} from '../portfolioFormat';
import type { PortfolioPositionItem } from '../../types/portfolio';

const pricedPosition: PortfolioPositionItem = {
  symbol: 'HPG',
  market: 'vn',
  currency: 'VND',
  quantity: 100,
  avgCost: 300,
  totalCost: 30000,
  lastPrice: 321.12345,
  marketValueBase: 32112.345,
  unrealizedPnlBase: 2112.345,
  unrealizedPnlPct: 7.04,
  valuationCurrency: 'VND',
  priceSource: 'realtime_quote',
  priceProvider: 'ssi',
  priceAvailable: true,
};

describe('portfolioFormat', () => {
  it('formats money and signed percentages consistently', () => {
    expect(formatMoney(1234.5, 'VND')).toBe('VND 1,234.50');
    expect(formatMoney(null)).toBe('--');
    expect(formatMoney(25000)).toBe('VND 25,000.00');
    expect(formatSignedPct(3.456)).toBe('+3.46%');
    expect(formatSignedPct(-1.2)).toBe('-1.20%');
  });

  it('formats position price fields based on price availability', () => {
    expect(formatPositionPrice(pricedPosition)).toBe('321.1234');
    expect(formatPositionMoney(123, pricedPosition)).toBe('VND 123.00');
    expect(getPositionPriceLabel(pricedPosition)).toBe('Live price · ssi');

    const missingPosition = { ...pricedPosition, priceAvailable: false, priceSource: 'missing' };
    expect(formatPositionPrice(missingPosition)).toBe('--');
    expect(formatPositionMoney(123, missingPosition)).toBe('--');
    expect(getPositionPriceLabel(missingPosition)).toBe('No price');
  });
});
