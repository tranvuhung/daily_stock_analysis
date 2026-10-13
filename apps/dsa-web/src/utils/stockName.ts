/**
 * Cấu hình cắt ngắn tên cổ phiếu (tên Latin/tiếng Việt): tối đa 15 ký tự.
 * Stock name truncation configuration (Latin / Vietnamese names): 15 chars max.
 */
export const STOCK_NAME_MAX_LENGTH = {
  LATIN: 15,
} as const;

function getMaxLength(): number {
  return STOCK_NAME_MAX_LENGTH.LATIN;
}

/**
 * Truncate stock name to at most 15 characters.
 */
export function truncateStockName(name: string): string {
  if (!name) return name;
  const maxLen = getMaxLength();
  if (name.length <= maxLen) return name;
  return name.slice(0, maxLen) + '.';
}

/**
 * Check if stock name will be truncated
 */
export function isStockNameTruncated(name: string): boolean {
  if (!name) return false;
  return name.length > getMaxLength();
}
