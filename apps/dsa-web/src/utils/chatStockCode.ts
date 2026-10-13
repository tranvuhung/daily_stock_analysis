import { validateStockCode } from './validation';
import { normalizeStockCode, resolveRegisteredIndexCanonical } from './stockCode';
import type { StockIndexItem } from '../types/stockIndex';

const CONTEXTUAL_INDICATOR_TOKENS = new Set(['MA']);
const INDICATOR_CONTEXT_RE = /chỉ báo|đường trung bình|hỗ trợ|kháng cự|indicator|moving average|support|resistance|MA\d|SMA|EMA/i;

// Mirrors backend _COMMON_WORDS for #1596 free-text extraction only.
// Explicit validation via validateStockCode() intentionally keeps its original contract.
const FREE_TEXT_TICKER_DENYLIST = new Set([
  'AM', 'AS', 'AT', 'BE', 'BY', 'DO', 'GO', 'HE', 'IF', 'IN',
  'IS', 'IT', 'ME', 'MY', 'NO', 'OF', 'ON', 'OR', 'SO', 'TO',
  'UP', 'US', 'WE',
  'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL',
  'CAN', 'HAD', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'HAS',
  'HIS', 'HOW', 'ITS', 'LET', 'MAY', 'NEW', 'NOW', 'OLD',
  'SEE', 'WAY', 'WHO', 'DID', 'GET', 'HIM', 'USE', 'SAY',
  'SHE', 'TOO', 'ANY', 'WITH', 'FROM', 'THAT', 'THAN',
  'THIS', 'WHAT', 'WHEN', 'WILL', 'JUST', 'ALSO',
  'BEEN', 'EACH', 'HAVE', 'MUCH', 'ONLY', 'OVER',
  'SOME', 'SUCH', 'THEM', 'THEN', 'THEY', 'VERY',
  'WERE', 'YOUR', 'ABOUT', 'AFTER', 'COULD', 'EVERY',
  'OTHER', 'THEIR', 'THERE', 'THESE', 'THOSE', 'WHICH',
  'WOULD', 'BEING', 'STILL', 'WHERE',
  'BUY', 'SELL', 'HOLD', 'LONG', 'PUT', 'CALL',
  'ETF', 'IPO', 'RSI', 'EPS', 'PEG', 'ROE', 'ROA',
  'USA', 'USD', 'VND', 'EUR', 'GBP',
  'STOCK', 'TRADE', 'PRICE', 'INDEX', 'FUND',
  'HIGH', 'LOW', 'OPEN', 'CLOSE', 'STOP', 'LOSS',
  'TREND', 'BULL', 'BEAR', 'RISK', 'CASH', 'BOND',
  'MACD', 'VWAP', 'BOLL', 'KDJ',
  'TTM', 'LTM', 'NTM', 'FWD', 'YOY', 'QOQ', 'YTD',
  'EBIT', 'EBITDA', 'DCF', 'CAGR', 'FCF', 'NAV', 'AUM',
  'PE', 'PB',
  'HELLO', 'PLEASE', 'THANKS', 'CHECK', 'LOOK', 'THINK',
  'MAYBE', 'GUESS', 'TELL', 'SHOW', 'WHATS',
  'WHY', 'HOWDY', 'HEY', 'HI',
]);

function isDeniedTickerCandidate(value: string, message: string): boolean {
  const token = value.trim().toUpperCase();
  return (
    FREE_TEXT_TICKER_DENYLIST.has(token) ||
    (CONTEXTUAL_INDICATOR_TOKENS.has(token) && INDICATOR_CONTEXT_RE.test(message))
  );
}

export function extractStockCodeFromMessage(
  message: string,
  index?: ReadonlyArray<StockIndexItem>,
): string | null {
  return extractStockCodesFromMessage(message, index)[0] ?? null;
}

export function extractStockCodesFromMessage(
  message: string,
  index?: ReadonlyArray<StockIndexItem>,
): string[] {
  // Mã VN: chứng chỉ quỹ (FUEVN100 / E1VFVN30), mã có hậu tố .VN, chỉ số VN, mã 3 ký tự.
  // Vietnam patterns: fund certificates, .VN suffixed codes, VN indices, 3-char tickers.
  const patterns = [
    /\b((?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})(?:\.VN)?)\b/g,
    /\b([A-Z][A-Z0-9]{2}\.VN)\b/g,
    /\b(VNINDEX|VN30|VN100|HNXINDEX|HNX30|HNXUPCOMINDEX|UPCOMINDEX)\b/g,
    /\b([A-Z][A-Z0-9]{2})\b/g,
  ];
  // Không nhận mã viết thường: tiếng Việt có nhiều từ 3 chữ cái (mua, xem, nen...).
  // Lower-case tickers are not extracted: many Vietnamese words are 3 letters long.

  const matches: Array<{ value: string; index: number; priority: number; end: number }> = [];
  patterns.forEach((pattern, priority) => {
    pattern.lastIndex = 0;
    for (const match of message.matchAll(pattern)) {
      const value = match[1] ?? match[0];
      const start = match.index ?? 0;
      const end = start + value.length;
      if (/^[A-Z][A-Z0-9]{2}$/.test(value) && (message[start - 1] === '.' || message[end] === '.')) {
        continue;
      }
      matches.push({
        value,
        index: start,
        priority,
        end,
      });
    }
  });

  matches.sort((a, b) => a.index - b.index || a.priority - b.priority);

  const stockCodes: string[] = [];
  const seen = new Set<string>();
  // Spans of already-accepted tokens suppress strictly-inner matches so a
  // suffixed code (HPG.VN) never leaks its inner parts as a separate stock.
  const acceptedSpans: Array<{ start: number; end: number }> = [];
  for (const match of matches) {
    if (acceptedSpans.some((span) => match.index >= span.start && match.end <= span.end)) {
      continue;
    }
    if (isDeniedTickerCandidate(match.value, message)) {
      continue;
    }
    // Registered index exact hit (canonical / display / explicit alias) — use
    // the registry canonical verbatim; never stock-normalize it.
    const registeredCanonical = index && index.length > 0
      ? resolveRegisteredIndexCanonical(index, match.value)
      : null;
    if (registeredCanonical) {
      acceptedSpans.push({ start: match.index, end: match.end });
      if (!seen.has(registeredCanonical)) {
        seen.add(registeredCanonical);
        stockCodes.push(registeredCanonical);
      }
      continue;
    }
    const { valid, normalized } = validateStockCode(match.value);
    if (!valid) {
      continue;
    }
    acceptedSpans.push({ start: match.index, end: match.end });
    const stockCode = normalizeStockCode(normalized);
    if (!seen.has(stockCode)) {
      seen.add(stockCode);
      stockCodes.push(stockCode);
    }
  }
  return stockCodes;
}
