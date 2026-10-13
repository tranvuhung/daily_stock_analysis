import { describe, expect, test } from 'vitest';

import { buildChatFollowUpContext } from '../chatFollowUp';
import type { AnalysisReport } from '../../types/analysis';

describe('chat follow-up context', () => {
  test('carries summary, strategy and price from the history report', () => {
    const report = {
      meta: {
        queryId: 'q-123',
        stockCode: 'FPT',
        stockName: 'FPT Corp',
        reportType: 'full',
        createdAt: '2026-07-05T00:00:00Z',
        currentPrice: 120.5,
        changePct: 1.2,
      },
      summary: {
        analysisSummary: 'summary',
        operationAdvice: 'Nắm giữ',
        trendPrediction: 'Trung tính',
        sentimentScore: 55,
      },
      strategy: { idealBuy: '118' },
      details: {},
    } as AnalysisReport;

    const context = buildChatFollowUpContext('FPT', 'FPT Corp', report);

    expect(context).toMatchObject({
      stock_code: 'FPT',
      stock_name: 'FPT Corp',
      previous_price: 120.5,
      previous_change_pct: 1.2,
      previous_strategy: { idealBuy: '118' },
    });
    // Không còn gửi ngữ cảnh cấu trúc thị trường (tính năng A-share đã bỏ).
    // Market-structure context is no longer sent (A-share feature removed).
    expect(context).not.toHaveProperty('market_structure_context');
  });
});
