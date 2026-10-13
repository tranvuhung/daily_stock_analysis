interface ValidationResult {
  valid: boolean;
  message?: string;
  normalized: string;
}

// Ký tự hợp lệ cho truy vấn tự do (mã hoặc tên công ty VN). Tên tiếng Việt có dấu được
// chấp nhận nhờ dải Latin mở rộng.
// Allowed characters for free-text queries (VN ticker or company name, incl. Vietnamese diacritics).
const SUPPORTED_QUERY_CHARACTERS = /^[A-Z0-9.\s\u00C0-\u024F\u1E00-\u1EFF]+$/;

// Chỉ còn quy tắc mã chứng khoán Việt Nam. / Vietnam-only security code rules.
const STOCK_CODE_PATTERNS = [
  // Mã cổ phiếu VN: đúng 3 ký tự chữ/số, bắt đầu bằng chữ (HPG / FPT / X77).
  // Vietnam tickers are exactly 3 uppercase alphanumerics starting with a letter.
  /^[A-Z][A-Z0-9]{2}$/, // Vietnam bare ticker
  /^[A-Z][A-Z0-9]{2}\.VN$/, // Vietnam explicit suffix, for example HPG.VN
  /^VN[A-Z][A-Z0-9]{2}$/, // Vietnam explicit prefix, for example vnHPG
  // Chứng chỉ quỹ ETF / quỹ đóng Việt Nam: 8 ký tự, tiền tố FU hoặc E1VF (FUEVN100, E1VFVN30).
  // Vietnam ETF / fund certificates.
  /^(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})$/, // Vietnam fund bare code
  /^(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})\.VN$/, // Vietnam fund explicit suffix, for example FUEVN100.VN
  /^VN(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})$/, // Vietnam fund explicit prefix, for example vnFUEVN100
  /^(VNINDEX|VN30|VN100|HNXINDEX|HNX30|HNXUPCOMINDEX|UPCOMINDEX)$/, // Vietnam indices
];

/**
 * Check whether the input looks like a stock code.
 */
export const looksLikeStockCode = (value: string): boolean => {
  const normalized = value.trim().toUpperCase();
  return STOCK_CODE_PATTERNS.some((regex) => regex.test(normalized));
};

/**
 * Validate Vietnam security code formats (stocks, ETF/fund certificates and indices).
 */
export const validateStockCode = (value: string): ValidationResult => {
  const normalized = value.trim().toUpperCase();

  if (!normalized) {
    return { valid: false, message: 'Enter a stock code', normalized };
  }

  const valid = looksLikeStockCode(normalized);

  return {
    valid,
    message: valid ? undefined : 'Invalid stock code format',
    normalized,
  };
};

/**
 * Reject obviously invalid free-text queries before they reach the backend.
 */
export const isObviouslyInvalidStockQuery = (value: string): boolean => {
  const normalized = value.trim().toUpperCase();

  if (!normalized || looksLikeStockCode(normalized)) {
    return false;
  }

  if (!SUPPORTED_QUERY_CHARACTERS.test(normalized)) {
    return true;
  }

  const hasLetters = /[A-Z]/.test(normalized);
  const hasDigits = /\d/.test(normalized);

  return hasLetters && hasDigits;
};
