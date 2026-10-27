import { describe, expect, it } from 'vitest';
import {
  applyLivePrices,
  countMqttPricedPositions,
  formatMoney,
  formatQuantity,
  formatSharePrice,
  formatPositionMoney,
  formatPositionPrice,
  formatSignedPct,
  getPositionPriceLabel,
} from '../portfolioFormat';
import type { PortfolioPositionItem, PortfolioSnapshotResponse } from '../../types/portfolio';

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
    expect(formatMoney(1234.5, 'VND')).toBe('VND 1,234.5');
    expect(formatMoney(-18789000)).toBe('VND -18,789,000');
    expect(formatMoney(null)).toBe('--');
    expect(formatMoney(25000)).toBe('VND 25,000');
    expect(formatSignedPct(3.456)).toBe('+3.46%');
    expect(formatSignedPct(-1.2)).toBe('-1.20%');
  });

  it('formats quantities with commas and share prices with dots, keeping real decimals', () => {
    expect(formatQuantity(1200)).toBe('1,200');
    expect(formatQuantity(1300.0)).toBe('1,300');
    expect(formatQuantity(999)).toBe('999');
    expect(formatSharePrice(70050)).toBe('70.050');
    expect(formatSharePrice(1234567.6)).toBe('1.234.567,6');
    expect(formatSharePrice(24630)).toBe('24.630');
    expect(formatQuantity(1200.5)).toBe('1,200.5');
    expect(formatSharePrice(24.63)).toBe('24,63');
    expect(formatSharePrice(null)).toBe('--');
  });

  it('formats position price fields based on price availability', () => {
    expect(formatPositionPrice(pricedPosition)).toBe('321,1234');
    expect(formatPositionPrice({ ...pricedPosition, lastPrice: 70050 })).toBe('70.050');
    expect(formatPositionMoney(123, pricedPosition)).toBe('VND 123');
    expect(getPositionPriceLabel(pricedPosition)).toBe('Live price · ssi');

    const missingPosition = { ...pricedPosition, priceAvailable: false, priceSource: 'missing' };
    expect(formatPositionPrice(missingPosition)).toBe('--');
    expect(formatPositionMoney(123, missingPosition)).toBe('--');
    expect(getPositionPriceLabel(missingPosition)).toBe('No price');
  });
});

describe('applyLivePrices', () => {
  const position = (overrides: Partial<PortfolioPositionItem>): PortfolioPositionItem => ({
    symbol: 'HPG',
    market: 'vn',
    currency: 'VND',
    quantity: 100,
    avgCost: 25000,
    totalCost: 2500000,
    lastPrice: 26000,
    marketValueBase: 2600000,
    unrealizedPnlBase: 100000,
    unrealizedPnlPct: 4,
    valuationCurrency: 'VND',
    priceSource: 'history_close',
    priceDate: '2026-10-26',
    priceStale: false,
    priceAvailable: true,
    dataQuality: 'ok',
    limitations: [],
    ...overrides,
  });

  const snapshot = (positions: PortfolioPositionItem[], limitations: string[] = []): PortfolioSnapshotResponse => {
    const marketValue = positions.reduce((sum, item) => sum + item.marketValueBase, 0);
    const pnl = positions.reduce((sum, item) => sum + item.unrealizedPnlBase, 0);
    const account = {
      accountId: 1, accountName: 'Main', market: 'vn', baseCurrency: 'VND', asOf: '2026-10-27', costMethod: 'fifo' as const,
      totalCash: 1000000, totalMarketValue: marketValue, totalEquity: 1000000 + marketValue, realizedPnl: 0,
      unrealizedPnl: pnl, feeTotal: 0, taxTotal: 0, fxStale: false,
      dataQuality: limitations.length ? 'partial' : 'ok', limitations, positions,
    };
    return {
      asOf: '2026-10-27', costMethod: 'fifo', currency: 'VND', accountCount: 1,
      totalCash: account.totalCash, totalMarketValue: marketValue, totalEquity: account.totalEquity,
      realizedPnl: 0, unrealizedPnl: pnl, feeTotal: 0, taxTotal: 0, fxStale: false,
      dataQuality: account.dataQuality, limitations, accounts: [account],
    };
  };

  it('reprices positions and shifts account and aggregate totals by the delta', () => {
    const base = snapshot([position({}), position({ symbol: 'FPT', marketValueBase: 1000000, totalCost: 900000, unrealizedPnlBase: 100000 })]);
    const result = applyLivePrices(base, {
      HPG: { symbol: 'HPG', price: 27000, ts: '2026-10-27T10:15:00+07:00', source: 'tvsi_mqtt' },
    })!;

    const hpg = result.accounts[0].positions[0];
    expect(hpg.lastPrice).toBe(27000);
    expect(hpg.marketValueBase).toBe(2700000);
    expect(hpg.unrealizedPnlBase).toBe(200000);
    expect(hpg.unrealizedPnlPct).toBeCloseTo(8);
    expect(hpg.priceProvider).toBe('tvsi_mqtt');
    expect(result.accounts[0].positions[1]).toBe(base.accounts[0].positions[1]);
    expect(result.totalMarketValue).toBe(base.totalMarketValue + 100000);
    expect(result.totalEquity).toBe(base.totalEquity + 100000);
    expect(result.unrealizedPnl).toBe(base.unrealizedPnl + 100000);
    expect(result.totalCash).toBe(base.totalCash);
    expect(countMqttPricedPositions(result)).toBe(1);
  });

  it('ignores ticks from another trading day or without a price', () => {
    const base = snapshot([position({})]);
    expect(applyLivePrices(base, {
      HPG: { symbol: 'HPG', price: 27000, ts: '2026-10-26T14:45:00+07:00', source: 'tvsi_mqtt' },
    })).toBe(base);
    expect(applyLivePrices(base, { HPG: { symbol: 'HPG', price: null, ts: '2026-10-27T10:00:00+07:00' } })).toBe(base);
  });

  it('clears price_unavailable once a missing-price position gets a live tick', () => {
    const missing = position({
      lastPrice: 0, marketValueBase: 0, unrealizedPnlBase: 0, unrealizedPnlPct: null,
      priceSource: 'missing', priceAvailable: false, priceStale: true,
      dataQuality: 'partial', limitations: ['price_unavailable'],
    });
    const result = applyLivePrices(snapshot([missing], ['price_unavailable']), {
      HPG: { symbol: 'HPG', price: 25500, ts: '2026-10-27T09:30:00+07:00', source: 'tvsi_mqtt' },
    })!;

    expect(result.accounts[0].positions[0].limitations).toEqual([]);
    expect(result.accounts[0].dataQuality).toBe('ok');
    expect(result.dataQuality).toBe('ok');
    expect(result.limitations).toEqual([]);
    expect(result.totalMarketValue).toBe(2550000);
    expect(result.unrealizedPnl).toBe(50000);
  });
});
