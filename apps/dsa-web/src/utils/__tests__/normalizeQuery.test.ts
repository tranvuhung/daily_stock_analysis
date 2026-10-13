/**
 * normalizeQuery Unit Tests
 *
 * Test various edge cases for query string normalization functions (Vietnam market).
 */

import { normalizeQuery, normalizeStockCode } from '../normalizeQuery';
import { describe, expect, test } from 'vitest';

describe('normalizeQuery', () => {
  describe('normalizeQuery - Query normalization', () => {
    test('removes leading and trailing spaces', () => {
      expect(normalizeQuery('  HPG  ')).toBe('hpg');
      expect(normalizeQuery('  Hòa Phát  ')).toBe('hòaphát');
    });

    test('converts to lowercase', () => {
      expect(normalizeQuery('FPT')).toBe('fpt');
      expect(normalizeQuery('VNINDEX')).toBe('vnindex');
    });

    test('removes internal extra spaces', () => {
      expect(normalizeQuery('H P G')).toBe('hpg');
      expect(normalizeQuery('hoa  phat')).toBe('hoaphat');
    });

    test('normalizes full-width latin characters to ASCII', () => {
      expect(normalizeQuery('ＨＰＧ')).toBe('hpg');
    });

    test('handles empty strings', () => {
      expect(normalizeQuery('')).toBe('');
      expect(normalizeQuery('   ')).toBe('');
    });

    test('preserves special characters', () => {
      expect(normalizeQuery('HPG.VN')).toBe('hpg.vn');
    });
  });

  describe('normalizeStockCode - Stock code normalization', () => {
    test('converts to uppercase', () => {
      expect(normalizeStockCode('hpg')).toBe('HPG');
      expect(normalizeStockCode('fuevn100')).toBe('FUEVN100');
    });

    test('removes spaces', () => {
      expect(normalizeStockCode('H PG')).toBe('HPG');
    });

    test('preserves market suffix', () => {
      expect(normalizeStockCode('hpg.vn')).toBe('HPG.VN');
    });

    test('removes leading and trailing spaces', () => {
      expect(normalizeStockCode('  FPT.VN  ')).toBe('FPT.VN');
    });
  });
});
