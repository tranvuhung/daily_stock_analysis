/**
 * Query Normalization Utility Functions
 *
 * For processing user input stock codes or names (Vietnam market only).
 */

/**
 * Normalize query string
 * - Remove leading/trailing spaces
 * - Convert to lowercase
 * - Remove internal extra spaces
 */
export function normalizeQuery(query: string): string {
  return query
    .normalize('NFKC')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '');
}

/**
 * Normalize stock code
 * - Convert to uppercase
 * - Remove spaces
 * - Keep market suffix (e.g. HPG.VN)
 */
export function normalizeStockCode(code: string): string {
  return code.trim().toUpperCase().replace(/\s+/g, '');
}
