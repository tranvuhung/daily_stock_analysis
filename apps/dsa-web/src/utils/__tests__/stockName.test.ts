import {
  truncateStockName,
  isStockNameTruncated,
  STOCK_NAME_MAX_LENGTH,
} from '../stockName';
import { describe, expect, test } from 'vitest';

describe('truncateStockName', () => {
  describe('Latin / Vietnamese strings', () => {
    test('returns unchanged when at or below 15 chars', () => {
      expect(truncateStockName('FPT Corp')).toBe('FPT Corp');
      expect(truncateStockName('HPG')).toBe('HPG');
      expect(truncateStockName('123456789012345')).toBe('123456789012345');
      expect(truncateStockName('Hòa Phát')).toBe('Hòa Phát');
    });

    test('truncates names longer than 15 chars', () => {
      expect(truncateStockName('Vietcombank Joint Stock')).toBe('Vietcombank Joi.');
      expect(truncateStockName('1234567890123456')).toBe('123456789012345.');
      expect(truncateStockName('Tập đoàn Hòa Phát Group')).toBe('Tập đoàn Hòa Ph.');
    });
  });

  describe('edge cases', () => {
    test('handles empty string', () => {
      expect(truncateStockName('')).toBe('');
    });

    test('keeps stock codes untouched', () => {
      expect(truncateStockName('HPG.VN')).toBe('HPG.VN');
      expect(truncateStockName('FUEVN100')).toBe('FUEVN100');
      expect(truncateStockName('VNINDEX')).toBe('VNINDEX');
    });
  });
});

describe('isStockNameTruncated', () => {
  test('returns false for empty string', () => {
    expect(isStockNameTruncated('')).toBe(false);
  });

  test('returns false for names at or below max length', () => {
    expect(isStockNameTruncated('FPT Corp')).toBe(false);
    expect(isStockNameTruncated('Hòa Phát')).toBe(false);
  });

  test('returns true for names exceeding 15 chars', () => {
    expect(isStockNameTruncated('Vietcombank Joint Stock')).toBe(true);
  });
});

describe('STOCK_NAME_MAX_LENGTH constant', () => {
  test('has the expected value', () => {
    expect(STOCK_NAME_MAX_LENGTH.LATIN).toBe(15);
  });
});
