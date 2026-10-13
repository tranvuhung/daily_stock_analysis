import { describe, expect, test } from 'vitest';
import {
  isObviouslyInvalidStockQuery,
  looksLikeStockCode,
  validateStockCode,
} from '../validation';

describe('stock code validation', () => {
  // Mã cổ phiếu Việt Nam 3 ký tự, hậu tố .VN và tiền tố vn.
  test.each([
    ['HPG', 'HPG'],
    ['fpt', 'FPT'],
    ['X77', 'X77'],
    ['HPG.VN', 'HPG.VN'],
    ['vnHPG', 'VNHPG'],
  ])('accepts Vietnam ticker %s', (input, normalized) => {
    expect(looksLikeStockCode(input)).toBe(true);
    expect(validateStockCode(input)).toEqual({
      valid: true,
      normalized,
    });
    expect(isObviouslyInvalidStockQuery(input)).toBe(false);
  });

  // Chứng chỉ quỹ ETF Việt Nam 8 ký tự (FU*/E1VF*) phải hợp lệ.
  test.each([
    ['FUEVN100', 'FUEVN100'],
    ['fuevn100', 'FUEVN100'],
    ['E1VFVN30', 'E1VFVN30'],
    ['FUEVN100.VN', 'FUEVN100.VN'],
    ['vnFUEVN100', 'VNFUEVN100'],
  ])('accepts Vietnam ETF / fund certificate %s', (input, normalized) => {
    expect(looksLikeStockCode(input)).toBe(true);
    expect(validateStockCode(input)).toEqual({
      valid: true,
      normalized,
    });
    expect(isObviouslyInvalidStockQuery(input)).toBe(false);
  });

  test.each(['VNINDEX', 'vn30', 'HNXINDEX', 'HNXUPCOMINDEX', 'UPCOMINDEX'])(
    'accepts Vietnam index %s',
    (input) => {
      expect(validateStockCode(input).valid).toBe(true);
      expect(isObviouslyInvalidStockQuery(input)).toBe(false);
    },
  );

  // Chứng quyền và hợp đồng phái sinh vẫn nằm ngoài phạm vi.
  test.each(['CHPG2401', 'VN30F2410'])('rejects Vietnam warrant/futures code %s', (input) => {
    expect(validateStockCode(input).valid).toBe(false);
  });

  // Mã thị trường khác đã bị loại bỏ.
  test.each(['600519', 'SH600519', '600519.SH', '00700.HK', 'HK00700', 'AAPL', '7203.T', '005930.KS', 'csi930955'])(
    'rejects non-Vietnam code %s',
    (input) => {
      expect(validateStockCode(input).valid).toBe(false);
    },
  );

  test('requires a non-empty code', () => {
    expect(validateStockCode('  ')).toEqual({ valid: false, message: 'Enter a stock code', normalized: '' });
  });

  test('flags mixed letter-digit free text as obviously invalid', () => {
    expect(isObviouslyInvalidStockQuery('ABCD1234')).toBe(true);
    expect(isObviouslyInvalidStockQuery('Hòa Phát')).toBe(false);
    expect(isObviouslyInvalidStockQuery('')).toBe(false);
  });
});
