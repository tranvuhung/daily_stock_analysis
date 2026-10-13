import { describe, expect, it } from 'vitest';
import {
  areAssetAwareCodesEquivalent,
  areStockCodesEquivalent,
  findMatchingStockCode,
  includesStockCode,
  normalizeStockCode,
  resolveRegisteredIndexCanonical,
  toAssetAwareCodeKey,
} from '../stockCode';

describe('normalizeStockCode', () => {
  it('keeps clean Vietnam tickers as-is', () => {
    expect(normalizeStockCode('HPG')).toBe('HPG');
    expect(normalizeStockCode('FPT')).toBe('FPT');
    expect(normalizeStockCode('FUEVN100')).toBe('FUEVN100');
  });

  it('strips the .VN suffix', () => {
    expect(normalizeStockCode('HPG.VN')).toBe('HPG');
    expect(normalizeStockCode('hpg.vn')).toBe('HPG');
    expect(normalizeStockCode('FUEVN100.VN')).toBe('FUEVN100');
  });

  it('strips the lowercase vn prefix', () => {
    expect(normalizeStockCode('vnHPG')).toBe('HPG');
    expect(normalizeStockCode('vnFUEVN100')).toBe('FUEVN100');
  });

  it('strips the uppercase VN prefix only for fund codes', () => {
    expect(normalizeStockCode('VNFUEVN100')).toBe('FUEVN100');
    expect(normalizeStockCode('VNM')).toBe('VNM');
  });

  it('keeps Vietnam index symbols intact', () => {
    expect(normalizeStockCode('VNINDEX')).toBe('VNINDEX');
    expect(normalizeStockCode('VN30')).toBe('VN30');
    expect(normalizeStockCode('vn30')).toBe('vn30');
  });

  it('handles same-stock variants as equivalent', () => {
    const codes = ['HPG', 'HPG.VN', 'vnHPG', 'hpg.vn'];
    const normalized = codes.map(normalizeStockCode);
    expect(new Set(normalized).size).toBe(1);
    expect(normalized[0]).toBe('HPG');
  });

  it('compares stock-code variants with both sides normalized', () => {
    expect(areStockCodesEquivalent('HPG', 'HPG.VN')).toBe(true);
    expect(areStockCodesEquivalent('fpt', 'FPT')).toBe(true);
    expect(areStockCodesEquivalent('FUEVN100', 'vnFUEVN100')).toBe(true);
    expect(areStockCodesEquivalent('HPG', 'FPT')).toBe(false);
    expect(areStockCodesEquivalent('', 'HPG')).toBe(false);
  });

  it('finds raw watchlist entries that match normalized current codes', () => {
    const codes = ['HPG', 'fpt', 'VCB.VN'];

    expect(includesStockCode(codes, 'HPG.VN')).toBe(true);
    expect(includesStockCode(codes, 'FPT')).toBe(true);
    expect(includesStockCode(codes, 'VCB')).toBe(true);
    expect(includesStockCode(codes, 'VNM')).toBe(false);
    expect(findMatchingStockCode(codes, 'FPT')).toBe('fpt');
    expect(findMatchingStockCode(codes, 'VCB')).toBe('VCB.VN');
  });
});

describe('asset-aware identity keys', () => {
  const indexRows = [
    { canonicalCode: 'VNINDEX', displayCode: 'VNINDEX', aliases: ['VN-Index'], assetType: 'index' },
    { canonicalCode: 'VN30', displayCode: 'VN30', aliases: ['VN30-Index'], assetType: 'index' },
    { canonicalCode: 'HPG', displayCode: 'HPG', aliases: [], assetType: 'stock' },
  ];

  it('buckets registered indices by lowercase canonical without stock folding', () => {
    expect(toAssetAwareCodeKey('VNINDEX', 'index')).toBe('vnindex');
    expect(toAssetAwareCodeKey('VN30', 'index')).toBe('vn30');
    expect(toAssetAwareCodeKey('HPG', 'stock')).toBe('HPG');
  });

  it('keeps stock normalization for stock/unknown codes', () => {
    expect(toAssetAwareCodeKey('HPG.VN', 'stock')).toBe('HPG');
    expect(toAssetAwareCodeKey('vnFPT', undefined)).toBe('FPT');
    expect(toAssetAwareCodeKey('', undefined)).toBe('');
  });

  it('resolves raw codes only through exact registry hits', () => {
    expect(resolveRegisteredIndexCanonical(indexRows, 'VNINDEX')).toBe('VNINDEX');
    expect(resolveRegisteredIndexCanonical(indexRows, 'vnindex')).toBe('VNINDEX');
    expect(resolveRegisteredIndexCanonical(indexRows, 'VN-Index')).toBe('VNINDEX');
    expect(resolveRegisteredIndexCanonical(indexRows, 'vn30-index')).toBe('VN30');
    expect(resolveRegisteredIndexCanonical(indexRows, 'HPG')).toBeNull();
    expect(resolveRegisteredIndexCanonical(indexRows, 'VN100')).toBeNull();
    expect(resolveRegisteredIndexCanonical([], 'VNINDEX')).toBeNull();
  });

  it('never folds an index with a stock in equivalence', () => {
    expect(areAssetAwareCodesEquivalent('VNINDEX', 'index', 'vnindex', 'index')).toBe(true);
    expect(areAssetAwareCodesEquivalent('VN30', 'index', 'VN30', 'stock')).toBe(false);
    expect(areAssetAwareCodesEquivalent('HPG.VN', 'stock', 'HPG', 'stock')).toBe(true);
    expect(areAssetAwareCodesEquivalent('', 'index', 'VNINDEX', 'index')).toBe(false);
  });

  it('folds case independently of the host locale (Turkish dotless-i safe)', () => {
    const original = String.prototype.toLocaleLowerCase;
    String.prototype.toLocaleLowerCase = function (this: string) {
      return this.replace(/I/g, 'ı').replace(/İ/g, 'i');
    };
    try {
      expect(toAssetAwareCodeKey('VNINDEX', 'index')).toBe('vnindex');
      expect(resolveRegisteredIndexCanonical(indexRows, 'VNINDEX')).toBe('VNINDEX');
    } finally {
      String.prototype.toLocaleLowerCase = original;
    }
  });
});
