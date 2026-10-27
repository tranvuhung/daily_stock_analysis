import type { AlertLiveQuote } from '../types/alerts';
import type {
  PortfolioAccountSnapshot,
  PortfolioCashDirection,
  PortfolioCorporateActionType,
  PortfolioPositionItem,
  PortfolioSide,
  PortfolioSnapshotResponse,
} from '../types/portfolio';
import { toDateInputValue } from './format';

export type PortfolioAlertVariant = 'info' | 'success' | 'warning' | 'danger';

export function getTodayIso(): string {
  return toDateInputValue(new Date());
}

// Nhóm hàng nghìn bằng `thousands`, giữ nguyên phần thập phân thật (không làm tròn về số nguyên,
// chỉ bỏ số 0 thừa ở cuối, tối đa 4 chữ số để tránh nhiễu dấu phẩy động).
// Group thousands and keep the real fractional part (trailing zeros trimmed, max 4 digits).
const MAX_FRACTION_DIGITS = 4;

function groupNumber(value: number, thousands: string, decimal: string): string {
  const fixed = Math.abs(value).toFixed(MAX_FRACTION_DIGITS);
  const [integerPart, fractionPart] = fixed.split('.');
  const grouped = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, thousands);
  const fraction = fractionPart.replace(/0+$/, '');
  const sign = value < 0 && (Number(integerPart) > 0 || fraction) ? '-' : '';
  return `${sign}${grouped}${fraction ? `${decimal}${fraction}` : ''}`;
}

// Mặc định tiền tệ VND (chỉ còn thị trường Việt Nam): VND 72,150,000 (không thêm .00).
// Defaults to VND (Vietnam-only); no padded decimals.
export function formatMoney(value: number | undefined | null, currency = 'VND'): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${currency} ${groupNumber(Number(value), ',', '.')}`;
}

/** Số lượng cổ phiếu: 1200 -> "1,200". Share quantity with comma grouping. */
export function formatQuantity(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  return groupNumber(Number(value), ',', '.');
}

/**
 * Giá cổ phiếu (giá vốn, giá hiện tại) theo kiểu Việt Nam: 70050 -> "70.050", 24.63 -> "24,63".
 * Share price, Vietnamese style (dot thousands, comma decimals).
 */
export function formatSharePrice(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  return groupNumber(Number(value), '.', ',');
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
  return formatSharePrice(row.lastPrice);
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

const PRICE_UNAVAILABLE = 'price_unavailable';

function liveSymbolKey(symbol: string): string {
  const value = String(symbol ?? '').trim().toUpperCase();
  return value.endsWith('.VN') ? value.slice(0, -3) : value;
}

/** Tick dùng được cho snapshot: có giá > 0 và cùng ngày giao dịch với snapshot (ts giờ VN). */
function usableLiveQuote(quote: AlertLiveQuote | undefined, asOf: string): quote is AlertLiveQuote & { price: number } {
  if (!quote || quote.price == null || !Number.isFinite(quote.price) || quote.price <= 0) return false;
  return Boolean(quote.ts && asOf && quote.ts.slice(0, 10) === asOf);
}

function dropPriceUnavailable(
  limitations: string[] | undefined,
  stillMissing: boolean,
): { limitations: string[]; dataQuality?: string } | null {
  if (!limitations?.includes(PRICE_UNAVAILABLE) || stillMissing) return null;
  const next = limitations.filter((item) => item !== PRICE_UNAVAILABLE);
  return { limitations: next, dataQuality: next.length === 0 ? 'ok' : 'partial' };
}

/**
 * Ghép giá live (tick MQTT qua SSE ``price_update``) vào snapshot danh mục, chỉ ở phía client:
 * cập nhật giá, giá trị thị trường, lãi/lỗ chưa thực hiện của vị thế và cộng phần chênh lệch
 * vào tổng tài khoản / tổng danh mục (tiền mặt không đổi). Tick khác ngày với snapshot bị bỏ qua.
 *
 * Overlay live ticks onto a portfolio snapshot (display only); returns the same object when
 * no tick applies.
 */
export function applyLivePrices(
  snapshot: PortfolioSnapshotResponse | null,
  livePrices: Record<string, AlertLiveQuote>,
): PortfolioSnapshotResponse | null {
  if (!snapshot || !livePrices || Object.keys(livePrices).length === 0) return snapshot;

  let changed = false;
  let totalMvDelta = 0;
  let totalPnlDelta = 0;
  let anyMissing = false;

  const accounts = (snapshot.accounts || []).map((account): PortfolioAccountSnapshot => {
    let mvDelta = 0;
    let pnlDelta = 0;
    let accountChanged = false;
    const positions = (account.positions || []).map((position): PortfolioPositionItem => {
      const quote = livePrices[liveSymbolKey(position.symbol)];
      if (!usableLiveQuote(quote, snapshot.asOf)) return position;
      const marketValue = position.quantity * quote.price;
      const pnl = marketValue - position.totalCost;
      mvDelta += marketValue - (position.marketValueBase || 0);
      pnlDelta += pnl - (position.unrealizedPnlBase || 0);
      accountChanged = true;
      const limitations = (position.limitations || []).filter((item) => item !== PRICE_UNAVAILABLE);
      return {
        ...position,
        lastPrice: quote.price,
        marketValueBase: marketValue,
        unrealizedPnlBase: pnl,
        unrealizedPnlPct: position.totalCost > 0 ? (pnl / position.totalCost) * 100 : position.unrealizedPnlPct,
        priceSource: 'realtime_quote',
        priceProvider: quote.source || position.priceProvider,
        priceDate: snapshot.asOf,
        priceStale: false,
        priceAvailable: true,
        limitations,
        dataQuality: limitations.length === 0 ? 'ok' : 'partial',
      };
    });
    const accountMissing = positions.some((position) => position.limitations?.includes(PRICE_UNAVAILABLE));
    anyMissing = anyMissing || accountMissing;
    if (!accountChanged) return account;
    changed = true;
    totalMvDelta += mvDelta;
    totalPnlDelta += pnlDelta;
    return {
      ...account,
      ...dropPriceUnavailable(account.limitations, accountMissing),
      totalMarketValue: account.totalMarketValue + mvDelta,
      totalEquity: account.totalEquity + mvDelta,
      unrealizedPnl: account.unrealizedPnl + pnlDelta,
      positions,
    };
  });

  if (!changed) return snapshot;
  return {
    ...snapshot,
    ...dropPriceUnavailable(snapshot.limitations, anyMissing),
    totalMarketValue: snapshot.totalMarketValue + totalMvDelta,
    totalEquity: snapshot.totalEquity + totalMvDelta,
    unrealizedPnl: snapshot.unrealizedPnl + totalPnlDelta,
    accounts,
  };
}

/** Số vị thế đang hiển thị giá từ feed MQTT. Count of positions priced by the MQTT feed. */
export function countMqttPricedPositions(snapshot: PortfolioSnapshotResponse | null): number {
  if (!snapshot) return 0;
  return (snapshot.accounts || []).reduce(
    (count, account) => count + (account.positions || []).filter(
      (position) => position.priceSource === 'realtime_quote' && position.priceProvider === 'tvsi_mqtt',
    ).length,
    0,
  );
}
