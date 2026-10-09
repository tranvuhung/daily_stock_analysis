interface ValidationResult {
  valid: boolean;
  message?: string;
  normalized: string;
}

const SUPPORTED_QUERY_CHARACTERS = /^[A-Z0-9.\u3400-\u9FFF\s]+$/;

const STOCK_CODE_PATTERNS = [
  // Vietnam: tickers are exactly 3 uppercase alphanumerics (HPG / FPT / X77).
  // Pure-letter codes also match the US ticker pattern below; that ambiguity is
  // resolved server-side by SUPPORTED_MARKETS.
  /^[A-Z][A-Z0-9]{2}$/, // Vietnam bare ticker
  /^[A-Z][A-Z0-9]{2}\.VN$/, // Vietnam explicit suffix, for example HPG.VN
  /^VN[A-Z][A-Z0-9]{2}$/, // Vietnam explicit prefix, for example vnHPG
  // Chứng chỉ quỹ ETF / quỹ đóng Việt Nam: 8 ký tự, tiền tố FU hoặc E1VF (FUEVN100, E1VFVN30).
  // Vietnam ETF / fund certificates; they never overlap US tickers.
  /^(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})$/, // Vietnam fund bare code
  /^(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})\.VN$/, // Vietnam fund explicit suffix, for example FUEVN100.VN
  /^VN(?:FU[A-Z0-9]{6}|E1VF[A-Z0-9]{4})$/, // Vietnam fund explicit prefix, for example vnFUEVN100
  /^(VNINDEX|VN30|VN100|HNXINDEX|HNX30|HNXUPCOMINDEX|UPCOMINDEX)$/, // Vietnam indices
  /^\d{6}$/, // A-share 6-digit code
  /^(SH|SZ|BJ)\d{6}$/, // A-share code with exchange prefix (also covers index canonical sh000016 / sh000300)
  /^\d{6}\.(SH|SZ|SS|BJ)$/, // A-share code with exchange suffix
  /^\d{5}$/, // HK code without prefix
  /^HK\d{1,5}$/, // HK-prefixed code, for example HK00700
  /^\d{1,5}\.HK$/, // HK suffix format, for example 00700.HK
  /^\d{4,5}\.T$/, // Japan Yahoo suffix format, for example 7203.T
  /^\d{6}\.(KS|KQ)$/, // Korea Yahoo suffix format, for example 005930.KS or 035720.KQ
  /^[A-Z]{1,5}(?:\.(?:US|[A-Z]))?$/, // Common US ticker format
  /^CSI\d{6}$/, // Registered CSI index canonical (e.g. csi930955)
  /^\d{6}\.CSI$/, // Registered CSI index display form (e.g. 930955.CSI)
];

/**
 * Check whether the input looks like a stock code.
 */
export const looksLikeStockCode = (value: string): boolean => {
  const normalized = value.trim().toUpperCase();
  return STOCK_CODE_PATTERNS.some((regex) => regex.test(normalized));
};

/**
 * Validate common Vietnam (stocks and ETF/fund certificates), A-share, HK, US, JP, and KR stock code formats.
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
