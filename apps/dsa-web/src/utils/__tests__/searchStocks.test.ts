/**
 * searchStocks unit tests (Vietnam market only).
 */

import { searchStocks } from '../searchStocks';
import type { StockIndexItem } from '../../types/stockIndex';
import { describe, expect, test } from 'vitest';

const mockIndex: StockIndexItem[] = [
  {
    canonicalCode: 'HPG',
    displayCode: 'HPG',
    nameZh: 'Hòa Phát',
    pinyinFull: 'hoaphat',
    pinyinAbbr: 'hph',
    aliases: ['Hoa Phat Group'],
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
    aliases: ['Ngân hàng Ngoại thương'],
    market: 'VN',
    assetType: 'stock',
    active: true,
    popularity: 90,
  },
  {
    canonicalCode: 'VNM',
    displayCode: 'VNM',
    nameZh: 'Vinamilk',
    pinyinFull: 'vinamilk',
    pinyinAbbr: 'vnmk',
    aliases: [],
    market: 'VN',
    assetType: 'stock',
    active: true,
    popularity: 92,
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
    canonicalCode: 'FUEVN100',
    displayCode: 'FUEVN100',
    nameZh: 'Quỹ ETF VinaCapital VN100',
    pinyinFull: 'quyetfvinacapitalvn100',
    pinyinAbbr: 'qevv',
    aliases: ['VN100 ETF'],
    market: 'ETF',
    assetType: 'etf',
    active: true,
    popularity: 70,
  },
  {
    canonicalCode: 'HPX',
    displayCode: 'HPX',
    nameZh: 'Hải Phát',
    pinyinFull: 'haiphat',
    pinyinAbbr: 'hpx',
    aliases: [],
    market: 'VN',
    assetType: 'stock',
    active: false, // Inactive
    popularity: 80,
  },
];

describe('searchStocks', () => {
  test('exact code match', () => {
    const results = searchStocks('HPG', mockIndex);
    expect(results).toHaveLength(1);
    expect(results[0].canonicalCode).toBe('HPG');
    expect(results[0].matchType).toBe('exact');
    expect(results[0].matchField).toBe('code');
  });

  test('exact Vietnamese name match', () => {
    const results = searchStocks('Hòa Phát', mockIndex);
    expect(results).toHaveLength(1);
    expect(results[0].canonicalCode).toBe('HPG');
    expect(results[0].matchType).toBe('exact');
    expect(results[0].matchField).toBe('name');
  });

  test('alias match', () => {
    const results = searchStocks('Hoa Phat Group', mockIndex);
    expect(results).toHaveLength(1);
    expect(results[0].canonicalCode).toBe('HPG');
    expect(results[0].matchType).toBe('exact');
  });

  test('code prefix match', () => {
    const results = searchStocks('HP', mockIndex);
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].matchType).toBe('prefix');
    expect(results[0].matchField).toBe('code');
  });

  test('name prefix match', () => {
    const results = searchStocks('Vina', mockIndex);
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].canonicalCode).toBe('VNM');
    expect(results[0].matchType).toBe('prefix');
    expect(results[0].matchField).toBe('name');
  });

  test('accent-free full-name contains match', () => {
    const results = searchStocks('phat', mockIndex);
    expect(results).toHaveLength(1);
    expect(results[0].canonicalCode).toBe('HPG');
    expect(results[0].matchType).toBe('contains');
  });

  test('activeOnly option filters inactive stocks', () => {
    const results = searchStocks('HP', mockIndex, { activeOnly: true });
    for (const result of results) {
      const item = mockIndex.find(i => i.canonicalCode === result.canonicalCode);
      expect(item?.active).toBe(true);
    }
  });

  test('limit option caps the result count', () => {
    const results = searchStocks('V', mockIndex, { limit: 1 });
    expect(results.length).toBeLessThanOrEqual(1);
  });

  test('returns an empty array when nothing matches', () => {
    const results = searchStocks('NOTFOUND', mockIndex);
    expect(results).toHaveLength(0);
  });

  test('returns an empty array for an empty query', () => {
    const results = searchStocks('', mockIndex);
    expect(results).toHaveLength(0);
  });

  test('is case-insensitive', () => {
    const results1 = searchStocks('fpt', mockIndex);
    const results2 = searchStocks('FPT', mockIndex);
    expect(results1).toHaveLength(1);
    expect(results2).toHaveLength(1);
    expect(results1[0].canonicalCode).toBe(results2[0].canonicalCode);
  });

  test('sorts by popularity when scores are tied', () => {
    const results = searchStocks('V', mockIndex);
    if (results.length > 1) {
      for (let index = 0; index < results.length - 1; index++) {
        const currentItem = mockIndex.find((item) => item.canonicalCode === results[index].canonicalCode);
        const nextItem = mockIndex.find((item) => item.canonicalCode === results[index + 1].canonicalCode);
        if (results[index].score === results[index + 1].score) {
          expect((currentItem?.popularity || 0)).toBeGreaterThanOrEqual(nextItem?.popularity || 0);
        }
      }
    }
  });

  test('supports half-width queries for full-width codes', () => {
    const results = searchStocks('ＶＣＢ', mockIndex);
    expect(results[0].canonicalCode).toBe('VCB');
  });

  test('matches Vietnam fund certificate codes', () => {
    const results = searchStocks('FUEVN100', mockIndex);
    expect(results).toHaveLength(1);
    expect(results[0].canonicalCode).toBe('FUEVN100');
    expect(results[0].market).toBe('ETF');
  });

  describe('Edge case tests', () => {
    test('special character query', () => {
      const results = searchStocks('@#$%', mockIndex);
      expect(results).toHaveLength(0);
    });

    test('pure space query', () => {
      const results = searchStocks('   ', mockIndex);
      expect(results).toHaveLength(0);
    });

    test('Unicode character query', () => {
      const results = searchStocks('cổ phiếu🚀', mockIndex);
      expect(results).toHaveLength(0);
    });

    test('extra long query string', () => {
      const longQuery = 'a'.repeat(1000);
      const results = searchStocks(longQuery, mockIndex);
      expect(results).toHaveLength(0);
    });

    test('abbreviation prefix match', () => {
      const results = searchStocks('vnm', mockIndex);
      expect(results.length).toBeGreaterThan(0);
    });

    test('alias contains match', () => {
      const results = searchStocks('ngoại', mockIndex);
      expect(results.length).toBeGreaterThan(0);
      expect(results[0].canonicalCode).toBe('VCB');
    });
  });

  describe('Scoring system tests', () => {
    test('exact match has highest score', () => {
      const exactResults = searchStocks('HPG', mockIndex);
      const prefixResults = searchStocks('HP', mockIndex);

      expect(exactResults[0].score).toBeGreaterThan(prefixResults[0].score);
    });

    test('code match prioritized over name match', () => {
      const codeResults = searchStocks('VNM', mockIndex);
      const nameResults = searchStocks('Vina', mockIndex);

      // Canonical code exact match is 100 points.
      expect(codeResults[0].score).toBe(100);
      expect(nameResults[0].score).toBeLessThan(99);
    });

    test('sorts by popularity when scores are equal', () => {
      const tieIndex: StockIndexItem[] = [
        {
          canonicalCode: 'TES',
          displayCode: 'TES',
          nameZh: 'Test 1',
          pinyinFull: 'test1',
          pinyinAbbr: 'ts1',
          aliases: [],
          market: 'VN',
          assetType: 'stock',
          active: true,
          popularity: 50,
        },
        {
          canonicalCode: 'TET',
          displayCode: 'TET',
          nameZh: 'Test 2',
          pinyinFull: 'test2',
          pinyinAbbr: 'ts2',
          aliases: [],
          market: 'VN',
          assetType: 'stock',
          active: true,
          popularity: 100,
        },
      ];

      const results = searchStocks('TE', tieIndex);
      if (results.length > 1) {
        expect(results[0].canonicalCode).toBe('TET');
      }
    });
  });

  describe('Inactive stock tests', () => {
    test('filters out inactive stocks by default', () => {
      const results = searchStocks('HPX', mockIndex);
      expect(results).toHaveLength(0);
    });

    test('shows inactive stocks when activeOnly=false', () => {
      const results = searchStocks('HPX', mockIndex, { activeOnly: false });
      expect(results).toHaveLength(1);
      expect(results[0].canonicalCode).toBe('HPX');
    });

    test('active stocks prioritized over inactive stocks', () => {
      const results = searchStocks('HP', mockIndex, { activeOnly: false });
      if (results.length > 1) {
        const firstItem = mockIndex.find(i => i.canonicalCode === results[0].canonicalCode);
        expect(firstItem?.active).toBe(true);
      }
    });
  });

  describe('Performance tests', () => {
    test('large index search performance', () => {
      const largeIndex: StockIndexItem[] = Array.from({ length: 5000 }, (_, i) => ({
        canonicalCode: `S${i}`,
        displayCode: `S${i}`,
        nameZh: `Stock ${i}`,
        pinyinFull: `stock${i}`,
        pinyinAbbr: `s${i}`,
        aliases: [],
        market: 'VN',
        assetType: 'stock',
        active: true,
        popularity: i % 100,
      }));

      const startTime = Date.now();
      const results = searchStocks('1', largeIndex);
      const endTime = Date.now();

      expect(endTime - startTime).toBeLessThan(100);
      expect(results.length).toBeGreaterThan(0);
    });

    test('multiple search performance', () => {
      const iterations = 100;
      const startTime = Date.now();

      for (let i = 0; i < iterations; i++) {
        searchStocks('HP', mockIndex);
      }

      const endTime = Date.now();
      const avgTime = (endTime - startTime) / iterations;

      expect(avgTime).toBeLessThan(10);
    });
  });

  describe('Match field tests', () => {
    test('code field match', () => {
      const results = searchStocks('HPG', mockIndex);
      expect(results[0].matchField).toBe('code');
    });

    test('name field match', () => {
      const results = searchStocks('Vina', mockIndex);
      expect(results[0].matchField).toBe('name');
    });

    test('alias field match', () => {
      const results = searchStocks('VN100 ETF', mockIndex);
      expect(results.length).toBeGreaterThan(0);
      expect(results[0].canonicalCode).toBe('FUEVN100');
    });
  });

  describe('Index row visibility', () => {
    const indexRows: StockIndexItem[] = [
      ...mockIndex,
      {
        canonicalCode: 'VNINDEX',
        displayCode: 'VNINDEX',
        nameZh: 'VN-Index',
        pinyinFull: 'vnindex',
        pinyinAbbr: 'vni',
        aliases: ['VNINDEX.VN'],
        market: 'INDEX',
        assetType: 'index',
        active: true,
        popularity: 99,
      },
      {
        canonicalCode: 'VN30',
        displayCode: 'VN30',
        nameZh: 'VN30 Index',
        pinyinFull: 'vn30index',
        pinyinAbbr: 'vn30',
        aliases: [],
        market: 'INDEX',
        assetType: 'index',
        active: true,
        popularity: 98,
      },
    ];

    test('search by registered index name returns the index row', () => {
      const results = searchStocks('VN-Index', indexRows);
      const hit = results.find(r => r.canonicalCode === 'VNINDEX');
      expect(hit?.nameZh).toBe('VN-Index');
    });

    test('search by registered index canonical code returns the index row', () => {
      const results = searchStocks('VN30', indexRows);
      expect(results.some(r => r.canonicalCode === 'VN30')).toBe(true);
    });

    test('search by registered index alias returns the index row', () => {
      const results = searchStocks('VNINDEX.VN', indexRows);
      expect(results.some(r => r.canonicalCode === 'VNINDEX')).toBe(true);
    });
  });
});
