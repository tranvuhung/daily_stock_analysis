const STOCK_LIST_SEPARATOR_RE = /[\s,;]+/;

export function parseStockListValue(value: string): string[] {
  return String(value ?? '')
    .split(STOCK_LIST_SEPARATOR_RE)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function serializeStockListValue(value: string): string {
  return parseStockListValue(value).join(',');
}
