import { describe, expect, it } from 'vitest';
import { parseStockListValue, serializeStockListValue } from '../stockList';

describe('stockList utils', () => {
  it('parses common copy/paste separators', () => {
    expect(parseStockListValue('HPG, FPT  VCB;FUEVN100\nVNINDEX')).toEqual([
      'HPG',
      'FPT',
      'VCB',
      'FUEVN100',
      'VNINDEX',
    ]);
  });

  it('serializes to canonical commas', () => {
    expect(serializeStockListValue('HPG, FPT\nVCB')).toBe('HPG,FPT,VCB');
  });
});
