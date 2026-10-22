// Mã chứng khoán VN: 3 ký tự (HPG) hoặc chứng chỉ quỹ (FUEVN100 / E1VFVN30).
// Vietnam security code: 3-char ticker or fund certificate code.
const VN_SECURITY_CODE_RE = /^(?:[A-Z][A-Z0-9]{2}|FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})$/;
const VN_FUND_CODE_RE = /^(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})$/;
const VN_INDEX_SYMBOLS = new Set(['VNINDEX', 'VN30', 'VN100', 'HNXINDEX', 'HNX30', 'UPCOMINDEX', 'UPCOM']);

/**
 * Chuẩn hoá mã chứng khoán Việt Nam (khớp data_provider.base.normalize_stock_code).
 * Normalize a Vietnam security code (mirrors data_provider.base.normalize_stock_code).
 *
 *   HPG         → HPG        HPG.VN      → HPG
 *   vnHPG       → HPG        FUEVN100.VN → FUEVN100
 *   VNFUEVN100  → FUEVN100   VNINDEX     → VNINDEX
 */
export function normalizeStockCode(stockCode: string): string {
  const code = stockCode.trim();
  const upper = code.toUpperCase();

  // Hậu tố .VN (HPG.VN → HPG). / Strip the explicit .VN suffix.
  if (upper.endsWith('.VN')) {
    const candidate = upper.slice(0, -3);
    if (VN_SECURITY_CODE_RE.test(candidate)) {
      return candidate;
    }
  }

  // Tiền tố ``vn`` viết thường (vnHPG → HPG); VN30/VNINDEX là mã chỉ số nên giữ nguyên.
  // Lower-case ``vn`` prefix; index symbols such as VN30 are kept as-is.
  if (code.startsWith('vn') && code.length > 2 && !VN_INDEX_SYMBOLS.has(upper)) {
    const candidate = upper.slice(2);
    if (VN_SECURITY_CODE_RE.test(candidate)) {
      return candidate;
    }
  }

  // Tiền tố ``VN`` viết hoa + mã quỹ (VNFUEVN100 → FUEVN100). / Upper-case VN + fund code.
  if (upper.startsWith('VN') && VN_FUND_CODE_RE.test(upper.slice(2))) {
    return upper.slice(2);
  }

  return code;
}

function stockCodeMatchKey(stockCode: string): string {
  return normalizeStockCode(stockCode).toUpperCase();
}

export function areStockCodesEquivalent(left: string, right: string): boolean {
  if (!left.trim() || !right.trim()) return false;
  return stockCodeMatchKey(left) === stockCodeMatchKey(right);
}

export function findMatchingStockCode(codes: string[], stockCode: string): string | undefined {
  if (!stockCode.trim()) return undefined;
  const targetKey = stockCodeMatchKey(stockCode);
  return codes.find((code) => code.trim() && stockCodeMatchKey(code) === targetKey);
}

export function includesStockCode(codes: string[], stockCode: string): boolean {
  return findMatchingStockCode(codes, stockCode) !== undefined;
}

// ============ Asset-aware identity key ============
//
// Dashboard grouping keeps registered indices (`VNINDEX`, `VN30`) in their own
// identity namespace. The backend exposes an optional `asset_type` on tasks /
// history items / stock-bar rows and reports; the Web trusts that field first and
// only falls back to the loaded stock index registry for raw watchlist strings
// that carry no type. `normalizeStockCode` is NOT applied to index identities.

export type AssetAwareAssetType = 'stock' | 'index';

export interface RegisteredIndexIdentity {
  canonicalCode: string;
  displayCode: string;
  aliases?: string[];
  assetType?: string;
}

/**
 * Fold an already-identified index code into its lowercase identity namespace.
 *
 * The backend guarantees that any API/task/report code tagged ``assetType=index``
 * is already the parser canonical (``VNINDEX`` / ``VN30``). The frontend therefore
 * only case-folds; it must NEVER derive a canonical from prefixes/suffixes. Only
 * ``resolveRegisteredIndexCanonical`` (exact registry hit) maps aliases, and
 * it is used solely for raw watchlist strings that carry no asset type.
 */
function foldIndexKey(code: string): string {
  // Locale-independent: `toLocaleLowerCase()` under the Turkish locale maps
  // `I` to dotless `ı`, which would break `VNINDEX` -> `vnindex`. The
  // canonical namespace is plain ASCII, so `toLowerCase()` is always correct.
  return code.trim().toLowerCase();
}

/**
 * Asset-aware identity key shared by stock-bar, watchlist fallback, active
 * task, completed-refresh and batch-dedupe grouping.
 *
 * - `assetType === 'index'` -> case-folded canonical bucket (never runs stock
 *   normalization; never regex-derives a canonical from an alias form).
 * - otherwise (stock or unknown) -> existing stock normalization semantics.
 */
export function toAssetAwareCodeKey(
  code: string | null | undefined,
  assetType?: AssetAwareAssetType | null,
): string {
  const trimmed = (code ?? '').trim();
  if (!trimmed) return '';
  if (assetType === 'index') {
    return foldIndexKey(trimmed);
  }
  return normalizeStockCode(trimmed).toUpperCase();
}

/**
 * Exact registry hit used only for raw codes WITHOUT an asset type (e.g. raw
 * watchlist strings). Only an `assetType=index` row whose canonical/display/
 * explicit alias matches the code *exactly* (case-insensitive, no
 * normalization, no prefix guessing) buckets it as an index. Returns the
 * matched row's canonical verbatim (VN canonicals are upper-case, e.g. VNINDEX)
 * or null. Identity keys must fold it through `toAssetAwareCodeKey(..., 'index')`.
 * Trả về canonical nguyên dạng (VNINDEX viết hoa); khoá định danh phải gập qua
 * `toAssetAwareCodeKey(..., 'index')`.
 */
export function resolveRegisteredIndexCanonical(
  index: ReadonlyArray<RegisteredIndexIdentity>,
  code: string | null | undefined,
): string | null {
  const trimmed = (code ?? '').trim();
  if (!trimmed) return null;
  const folded = trimmed.toLowerCase();
  for (const item of index) {
    if (!item || item.assetType !== 'index') continue;
    const candidates = [item.canonicalCode, item.displayCode, ...(item.aliases ?? [])];
    for (const candidate of candidates) {
      if (candidate && candidate.trim().toLowerCase() === folded) {
        const canonical = (item.canonicalCode ?? '').trim();
        return canonical || null;
      }
    }
  }
  return null;
}

/**
 * Asset-aware equivalence used by row selection / notice matching: an index
 * row never matches a same-code stock row, because each side carries its own
 * asset type.
 */
export function areAssetAwareCodesEquivalent(
  left: string | null | undefined,
  leftAssetType: AssetAwareAssetType | null | undefined,
  right: string | null | undefined,
  rightAssetType: AssetAwareAssetType | null | undefined,
): boolean {
  const leftKey = toAssetAwareCodeKey(left, leftAssetType);
  const rightKey = toAssetAwareCodeKey(right, rightAssetType);
  return Boolean(leftKey && rightKey && leftKey === rightKey);
}
