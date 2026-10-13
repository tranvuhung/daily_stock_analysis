/**
 * Stock Index Type Definitions
 *
 * Stock data index for autocomplete functionality
 */

// Chỉ thị trường Việt Nam: mã cổ phiếu VN, chỉ số VN và chứng chỉ quỹ/ETF VN.
// Vietnam-only: VN stocks, VN indices and VN fund certificates / ETFs.
export type Market = 'VN' | 'INDEX' | 'ETF';
export type AssetType = 'stock' | 'index' | 'etf';

/**
 * Stock index item (full format)
 */
export interface StockIndexItem {
  /** Canonical code: FPT */
  canonicalCode: string;
  /** Display code: FPT */
  displayCode: string;
  /** Primary (Vietnamese) name: Công ty Cổ phần FPT */
  nameZh: string;
  /** English name: FPT Corporation */
  nameEn?: string;
  /** Accent-free full name: congtycophanfpt */
  pinyinFull?: string;
  /** Accent-free abbreviation: ctcpf */
  pinyinAbbr?: string;
  /** Aliases: ["FPT Corp"] */
  aliases?: string[];
  /** Market */
  market: Market;
  /** Asset type */
  assetType: AssetType;
  /** Is active */
  active: boolean;
  /** Popularity */
  popularity?: number;
}

/**
 * Stock search suggestion item
 */
export interface StockSuggestion {
  /** Canonical code */
  canonicalCode: string;
  /** Display code */
  displayCode: string;
  /** Primary name */
  nameZh: string;
  /** Market */
  market: Market;
  /** Match type */
  matchType: 'exact' | 'prefix' | 'contains' | 'fuzzy';
  /** Match field */
  matchField: 'code' | 'name' | 'pinyin' | 'alias';
  /** Sort score */
  score: number;
}

/**
 * Compressed format stock index item (for reducing file size)
 */
export type StockIndexTuple = [
  string,  // canonicalCode
  string,  // displayCode
  string,  // nameZh
  string | undefined, // pinyinFull
  string | undefined, // pinyinAbbr
  string[], // aliases (required, use empty array if none)
  Market,
  AssetType,
  boolean, // active
  number | undefined, // popularity
];

/**
 * Stock index data (supports two formats)
 */
export type StockIndexData = StockIndexItem[] | StockIndexTuple[];
