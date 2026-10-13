/**
 * stockIndexLoader Unit Tests
 *
 * Test stock index loading, parsing, compression, and other functions
 */

import {
  loadStockIndex,
  compressIndex,
  findStockInIndex,
  getPopularStocks,
  groupStocksByMarket,
} from '../stockIndexLoader';
import type { StockIndexItem } from '../../types/stockIndex';
import { beforeEach, describe, expect, test, vi } from 'vitest';

// Mock fetch
const mockFetch = vi.fn();
globalThis.fetch = mockFetch as unknown as typeof fetch;

describe('stockIndexLoader', () => {
  const mockIndexData: StockIndexItem[] = [
    {
      canonicalCode: 'HPG',
      displayCode: 'HPG',
      nameZh: 'Hòa Phát',
      pinyinFull: 'hoaphat',
      pinyinAbbr: 'hp',
      aliases: ['Hoa Phat'],
      market: 'VN',
      assetType: 'stock',
      active: true,
      popularity: 100,
    },
    {
      canonicalCode: 'VCB',
      displayCode: 'VCB',
      nameZh: 'Vietcombank',
      pinyinFull: 'vietcombank',
      pinyinAbbr: 'vcb',
      aliases: ['Vietcombank'],
      market: 'VN',
      assetType: 'stock',
      active: true,
      popularity: 90,
    },
    {
      canonicalCode: 'FUEVN100',
      displayCode: 'FUEVN100',
      nameZh: 'Quỹ ETF VN100',
      pinyinFull: 'quyetfvn100',
      pinyinAbbr: 'qev',
      aliases: ['VN100 ETF'],
      market: 'ETF',
      assetType: 'etf',
      active: true,
      popularity: 95,
    },
    {
      canonicalCode: 'FPT',
      displayCode: 'FPT',
      nameZh: 'FPT Corp',
      pinyinFull: 'fptcorp',
      pinyinAbbr: 'fc',
      aliases: [],
      market: 'VN',
      assetType: 'stock',
      active: true,
      popularity: 98,
    },
    {
      canonicalCode: 'VNM',
      displayCode: 'VNM',
      nameZh: 'Vinamilk',
      pinyinFull: 'vinamilk',
      pinyinAbbr: 'vnm',
      aliases: ['Vinamilk'],
      market: 'VN',
      assetType: 'stock',
      active: false,
      popularity: 80,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('loadStockIndex - Load stock index', () => {
    test('successfully loads object format index', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockIndexData,
      } as unknown as Response);

      const result = await loadStockIndex();

      expect(result.loaded).toBe(true);
      expect(result.fallback).toBe(false);
      expect(result.data).toEqual(mockIndexData);
      expect(result.error).toBeUndefined();
    });

    test('successfully loads compressed format index (tuple format)', async () => {
      const compressedData = [
        ['HPG', 'HPG', 'Hòa Phát', 'hoaphat', 'hp', ['Hoa Phat'], 'VN', 'stock', true, 100],
        ['VCB', 'VCB', 'Vietcombank', 'vietcombank', 'vcb', ['Vietcombank'], 'VN', 'stock', true, 90],
      ];

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => compressedData,
      } as unknown as Response);

      const result = await loadStockIndex();

      expect(result.loaded).toBe(true);
      expect(result.fallback).toBe(false);
      expect(result.data).toHaveLength(2);
      expect(result.data[0].canonicalCode).toBe('HPG');
      expect(result.data[0].nameZh).toBe('Hòa Phát');
    });

    test('returns fallback mode on network error', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      const result = await loadStockIndex();

      expect(result.loaded).toBe(false);
      expect(result.fallback).toBe(true);
      expect(result.data).toEqual([]);
      expect(result.error).toBeInstanceOf(Error);
    });

    test('aborts a stalled index request and returns fallback mode', async () => {
      vi.useFakeTimers();
      let capturedSignal: AbortSignal | undefined;
      try {
        mockFetch.mockImplementationOnce((_input, init) => {
          capturedSignal = init?.signal ?? undefined;
          if (!capturedSignal) {
            return Promise.reject(new Error('missing AbortSignal'));
          }
          return new Promise((_resolve, reject) => {
            capturedSignal?.addEventListener('abort', () => reject(new Error('request aborted')));
          });
        });

        const resultPromise = loadStockIndex();

        expect(capturedSignal).toBeInstanceOf(AbortSignal);
        await vi.advanceTimersByTimeAsync(10_000);
        const result = await resultPromise;

        expect(capturedSignal?.aborted).toBe(true);
        expect(result.loaded).toBe(false);
        expect(result.fallback).toBe(true);
        expect(result.data).toEqual([]);
        expect(result.error).toBeInstanceOf(Error);
      } finally {
        vi.useRealTimers();
      }
    });

    test('returns fallback mode on HTTP error', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 404,
        statusText: 'Not Found',
      } as unknown as Response);

      const result = await loadStockIndex();

      expect(result.loaded).toBe(false);
      expect(result.fallback).toBe(true);
      expect(result.data).toEqual([]);
      expect(result.error).toBeDefined();
    });

    test('returns fallback mode on JSON parse error', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => {
          throw new Error('Invalid JSON');
        },
      } as unknown as Response);

      const result = await loadStockIndex();

      expect(result.loaded).toBe(false);
      expect(result.fallback).toBe(true);
      expect(result.data).toEqual([]);
      expect(result.error).toBeDefined();
    });

    test('handles empty array', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      } as unknown as Response);

      const result = await loadStockIndex();

      expect(result.loaded).toBe(true);
      expect(result.fallback).toBe(false);
      expect(result.data).toEqual([]);
    });

    test('fetch call includes cache-busting parameter', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockIndexData,
      } as unknown as Response);

      await loadStockIndex();

      const fetchCallArgs = mockFetch.mock.calls[0][0];
      expect(fetchCallArgs).toContain('?_t=');
    });

    test('keeps assetType=index rows in the returned data', async () => {
      const withIndex = [
        ...mockIndexData,
        {
          canonicalCode: 'VN30',
          displayCode: 'VN30',
          nameZh: 'VN30 Index',
          pinyinFull: 'vn30',
          pinyinAbbr: 'vn30',
          aliases: ['VN30-Index'],
          market: 'VN',
          assetType: 'index',
          active: true,
          popularity: 100,
        },
      ];

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => withIndex,
      } as unknown as Response);

      const result = await loadStockIndex();

      expect(result.loaded).toBe(true);
      expect(result.fallback).toBe(false);
      // Registered index rows flow to autocomplete/search/group consumers.
      expect(result.data.some(item => item.assetType === 'index')).toBe(true);
      const indexRow = result.data.find(item => item.assetType === 'index');
      expect(indexRow?.canonicalCode).toBe('VN30');
      expect(indexRow?.nameZh).toBe('VN30 Index');
      // Stock rows are preserved.
      expect(result.data).toHaveLength(mockIndexData.length + 1);
    });

    test('keeps index rows from compressed tuple payload', async () => {
      const compressedWithIndex = [
        ['HPG', 'HPG', 'Hòa Phát', 'hoaphat', 'hp', ['Hoa Phat'], 'VN', 'stock', true, 100],
        ['VN30', 'VN30', 'VN30 Index', 'vn30', 'vn30', ['VN30-Index'], 'INDEX', 'index', true, 100],
      ];

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => compressedWithIndex,
      } as unknown as Response);

      const result = await loadStockIndex();

      expect(result.loaded).toBe(true);
      expect(result.data).toHaveLength(2);
      expect(result.data[0].canonicalCode).toBe('HPG');
      expect(result.data[1].canonicalCode).toBe('VN30');
      expect(result.data[1].nameZh).toBe('VN30 Index');
    });
  });

  describe('compressIndex - Compress index', () => {
    test('converts object format to tuple format', () => {
      const compressed = compressIndex(mockIndexData);

      expect(compressed).toHaveLength(mockIndexData.length);
      expect(compressed[0]).toEqual([
        'HPG',
        'HPG',
        'Hòa Phát',
        'hoaphat',
        'hp',
        ['Hoa Phat'],
        'VN',
        'stock',
        true,
        100,
      ]);
    });

    test('handles empty aliases array', () => {
      const itemWithoutAliases: StockIndexItem[] = [
        {
          canonicalCode: 'TST',
          displayCode: 'TEST',
          nameZh: 'Test',
          pinyinFull: 'test',
          pinyinAbbr: 'test',
          aliases: [],
          market: 'VN',
          assetType: 'stock',
          active: true,
          popularity: 50,
        },
      ];

      const compressed = compressIndex(itemWithoutAliases);

      expect(compressed[0][5]).toEqual([]);
    });

    test('handles undefined aliases', () => {
      const itemWithUndefinedAliases: StockIndexItem[] = [
        {
          canonicalCode: 'TST',
          displayCode: 'TEST',
          nameZh: 'Test',
          pinyinFull: 'test',
          pinyinAbbr: 'test',
          aliases: undefined as unknown as string[],
          market: 'VN',
          assetType: 'stock',
          active: true,
          popularity: 50,
        },
      ];

      const compressed = compressIndex(itemWithUndefinedAliases);

      expect(compressed[0][5]).toEqual([]);
    });

    test('handles empty array', () => {
      const compressed = compressIndex([]);
      expect(compressed).toEqual([]);
    });
  });

  describe('findStockInIndex - Find stock', () => {
    test('finds existing stock', () => {
      const result = findStockInIndex('HPG', mockIndexData);
      expect(result).not.toBeNull();
      expect(result?.canonicalCode).toBe('HPG');
      expect(result?.nameZh).toBe('Hòa Phát');
    });

    test('returns null for non-existent stock', () => {
      const result = findStockInIndex('NOTFOUND.US', mockIndexData);
      expect(result).toBeNull();
    });

    test('finds inactive stock', () => {
      const result = findStockInIndex('VNM', mockIndexData);
      expect(result).not.toBeNull();
      expect(result?.active).toBe(false);
    });

    test('handles empty index', () => {
      const result = findStockInIndex('HPG', []);
      expect(result).toBeNull();
    });

    test('case-sensitive search', () => {
      const result = findStockInIndex('hpg', mockIndexData);
      expect(result).toBeNull();
    });
  });

  describe('getPopularStocks - Get popular stocks', () => {
    test('sorts by popularity descending', () => {
      const result = getPopularStocks(mockIndexData, 3);

      expect(result).toHaveLength(3);
      expect(result[0].canonicalCode).toBe('HPG'); // popularity: 100
      expect(result[1].canonicalCode).toBe('FPT'); // popularity: 98
      expect(result[2].canonicalCode).toBe('FUEVN100'); // popularity: 95
    });

    test('filters out inactive stocks', () => {
      const result = getPopularStocks(mockIndexData, 10);

      // VNM is inactive, should not appear
      const hasInactive = result.some(item => !item.active);
      expect(hasInactive).toBe(false);
    });

    test('limits return count', () => {
      const result = getPopularStocks(mockIndexData, 2);
      expect(result.length).toBeLessThanOrEqual(2);
    });

    test('defaults to limit of 20', () => {
      const result = getPopularStocks(mockIndexData);
      expect(result.length).toBeLessThanOrEqual(20);
    });

    test('handles empty index', () => {
      const result = getPopularStocks([]);
      expect(result).toEqual([]);
    });

    test('handles all inactive stocks', () => {
      const inactiveOnly: StockIndexItem[] = [
        {
          canonicalCode: 'TST',
          displayCode: 'TEST',
          nameZh: 'Test',
          pinyinFull: 'test',
          pinyinAbbr: 'test',
          aliases: [],
          market: 'VN',
          assetType: 'stock',
          active: false,
          popularity: 100,
        },
      ];

      const result = getPopularStocks(inactiveOnly);
      expect(result).toEqual([]);
    });

    test('maintains stable sorting for same popularity', () => {
      const samePopularity: StockIndexItem[] = [
        {
          canonicalCode: 'AAA',
          displayCode: 'A',
          nameZh: 'A',
          pinyinFull: 'a',
          pinyinAbbr: 'a',
          aliases: [],
          market: 'VN',
          assetType: 'stock',
          active: true,
          popularity: 100,
        },
        {
          canonicalCode: 'BBB',
          displayCode: 'B',
          nameZh: 'B',
          pinyinFull: 'b',
          pinyinAbbr: 'b',
          aliases: [],
          market: 'VN',
          assetType: 'stock',
          active: true,
          popularity: 100,
        },
      ];

      const result = getPopularStocks(samePopularity, 2);
      expect(result).toHaveLength(2);
      expect(result[0].popularity).toBe(100);
      expect(result[1].popularity).toBe(100);
    });
  });

  describe('groupStocksByMarket - Group stocks by market', () => {
    test('groups different markets correctly', () => {
      const result = groupStocksByMarket(mockIndexData);

      expect(result.size).toBe(2); // VN, ETF
      expect(result.get('VN')).toHaveLength(3);
      expect(result.get('ETF')).toHaveLength(1);
    });

    test('filters out inactive stocks', () => {
      const result = groupStocksByMarket(mockIndexData);

      const vnStocks = result.get('VN')!;
      const allActive = vnStocks.every(item => item.active);
      expect(allActive).toBe(true);
    });

    test('handles empty index', () => {
      const result = groupStocksByMarket([]);
      expect(result.size).toBe(0);
    });

    test('handles all inactive stocks', () => {
      const inactiveOnly: StockIndexItem[] = [
        {
          canonicalCode: 'AAA',
          displayCode: 'A',
          nameZh: 'A',
          pinyinFull: 'a',
          pinyinAbbr: 'a',
          aliases: [],
          market: 'VN',
          assetType: 'stock',
          active: false,
          popularity: 100,
        },
      ];

      const result = groupStocksByMarket(inactiveOnly);
      expect(result.size).toBe(0);
    });

    test('returns independent arrays for groups', () => {
      const result = groupStocksByMarket(mockIndexData);

      const vnStocks = result.get('VN')!;
      const originalLength = vnStocks.length;

      // Modifying returned array should not affect original data
      vnStocks.pop();

      const result2 = groupStocksByMarket(mockIndexData);
      const vnStocks2 = result2.get('VN')!;

      expect(vnStocks2.length).toBe(originalLength);
    });

    test('maintains order within groups', () => {
      const result = groupStocksByMarket(mockIndexData);

      const vnStocks = result.get('VN')!;
      expect(vnStocks[0].canonicalCode).toBe('HPG');
      expect(vnStocks[1].canonicalCode).toBe('VCB');
    });
  });

  describe('Edge case comprehensive tests', () => {
    test('handles very large datasets', () => {
      const largeIndex: StockIndexItem[] = Array.from({ length: 10000 }, (_, i) => ({
        canonicalCode: `TEST${i}.US`,
        displayCode: `TEST${i}`,
        nameZh: `Kiểm thử${i}`,
        pinyinFull: `test${i}`,
        pinyinAbbr: `t${i}`,
        aliases: [],
        market: 'VN',
        assetType: 'stock',
        active: i % 2 === 0,
        popularity: i % 100,
      }));

      expect(() => compressIndex(largeIndex)).not.toThrow();
      expect(() => findStockInIndex('TEST5000.US', largeIndex)).not.toThrow();
      expect(() => getPopularStocks(largeIndex, 10)).not.toThrow();
    });

    test('handles special characters', () => {
      const specialChars: StockIndexItem[] = [
        {
          canonicalCode: 'TST',
          displayCode: 'TEST',
          nameZh: 'Kiểm thử·Công ty',
          pinyinFull: 'test-gongsi',
          pinyinAbbr: 'test',
          aliases: ['Kiểm thử (Tập đoàn)'],
          market: 'VN',
          assetType: 'stock',
          active: true,
          popularity: 50,
        },
      ];

      const compressed = compressIndex(specialChars);
      expect(compressed[0][2]).toBe('Kiểm thử·Công ty');
      expect(compressed[0][5]).toEqual(['Kiểm thử (Tập đoàn)']);
    });
  });
});
