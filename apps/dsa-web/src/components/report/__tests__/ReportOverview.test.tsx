import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReportOverview } from '../ReportOverview';

const baseMeta = {
  queryId: 'q-1',
  stockCode: 'FPT',
  stockName: 'FPT Corp',
  reportType: 'detailed' as const,
  reportLanguage: 'vi' as const,
  createdAt: '2026-03-21T08:00:00Z',
};

const baseSummary = {
  analysisSummary: 'Xu hướng duy trì tích cực',
  operationAdvice: 'Tiếp tục theo dõi điểm mua',
  trendPrediction: 'Ngắn hạn dao động tích cực',
  sentimentScore: 78,
};

describe('ReportOverview', () => {
  it('renders final market phase and partial-bar labels from report metadata', () => {
    render(
      <ReportOverview
        meta={{
          ...baseMeta,
          marketPhaseSummary: {
            market: 'vn',
            phase: 'intraday',
            marketLocalTime: '2026-03-21T10:30:00+07:00',
            sessionDate: '2026-03-21',
            effectiveDailyBarDate: '2026-03-20',
            isTradingDay: true,
            isMarketOpenNow: true,
            isPartialBar: true,
            minutesToOpen: null,
            minutesToClose: 150,
            triggerSource: 'api',
            analysisIntent: 'auto',
            warnings: [],
          },
        }}
        summary={baseSummary}
      />,
    );

    expect(screen.getByLabelText('Giai đoạn thị trường: VN · Trong phiên')).toBeInTheDocument();
    expect(screen.getByText('Giai đoạn thị trường: VN · Trong phiên')).toBeVisible();
    expect(screen.getByLabelText('Nến chưa hoàn chỉnh')).toBeInTheDocument();
  });

  it('renders English final market phase and partial-bar labels', () => {
    render(
      <ReportOverview
        meta={{
          ...baseMeta,
          reportLanguage: 'en',
          marketPhaseSummary: {
            market: 'vn',
            phase: 'postmarket',
            marketLocalTime: '2026-03-21T15:30:00+07:00',
            sessionDate: '2026-03-21',
            effectiveDailyBarDate: '2026-03-21',
            isTradingDay: true,
            isMarketOpenNow: false,
            isPartialBar: true,
            minutesToOpen: null,
            minutesToClose: null,
            triggerSource: 'api',
            analysisIntent: 'auto',
            warnings: [],
          },
        }}
        summary={baseSummary}
      />,
    );

    expect(screen.getByLabelText('Market phase: VN · Post-market')).toBeInTheDocument();
    expect(screen.getByLabelText('Partial bar')).toBeInTheDocument();
  });

  it('renders unknown final phase without partial-bar label', () => {
    render(
      <ReportOverview
        meta={{
          ...baseMeta,
          marketPhaseSummary: {
            market: null,
            phase: 'unknown',
            marketLocalTime: null,
            sessionDate: null,
            effectiveDailyBarDate: null,
            isTradingDay: null,
            isMarketOpenNow: null,
            isPartialBar: false,
            minutesToOpen: null,
            minutesToClose: null,
            triggerSource: 'api',
            analysisIntent: 'auto',
            warnings: ['calendar_unavailable'],
          },
        }}
        summary={baseSummary}
      />,
    );

    expect(screen.getByText('Giai đoạn thị trường: Không rõ giai đoạn')).toBeVisible();
    expect(screen.queryByText('Nến chưa hoàn chỉnh')).not.toBeInTheDocument();
  });

  it('does not render a market phase placeholder for legacy reports', () => {
    render(<ReportOverview meta={baseMeta} summary={baseSummary} />);

    expect(screen.queryByText(/Giai đoạn thị trường/)).not.toBeInTheDocument();
    expect(screen.queryByText('Nến chưa hoàn chỉnh')).not.toBeInTheDocument();
  });

  it('renders the persisted empty-news disclosure beside the core conclusion', () => {
    render(
      <ReportOverview
        meta={baseMeta}
        summary={baseSummary}
        details={{
          emptyNewsDisclosure: '⚠️ Chưa cấu hình kênh tìm kiếm, lượt phân tích này không có bằng chứng tin tức.',
        }}
      />,
    );

    expect(screen.getByRole('note')).toHaveTextContent('Chưa cấu hình kênh tìm kiếm');
    expect(screen.getByRole('note')).toHaveTextContent('không có bằng chứng tin tức');
  });

  it('hides the stock-only watchlist card for index reports', () => {
    render(
      <ReportOverview
        meta={{ ...baseMeta, stockCode: 'VNINDEX', assetType: 'index' }}
        summary={baseSummary}
        watchlist={{
          isInWatchlist: () => false,
          onToggle: () => {},
          isActioning: false,
          actionMessage: null,
        }}
      />,
    );

    expect(screen.queryByText('Watchlist')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Watchlist/ })).not.toBeInTheDocument();
  });

  it('keeps the stock-only watchlist card for stock reports', () => {
    render(
      <ReportOverview
        meta={{ ...baseMeta, stockCode: 'FPT', assetType: 'stock' }}
        summary={baseSummary}
        watchlist={{
          isInWatchlist: () => false,
          onToggle: () => {},
          isActioning: false,
          actionMessage: null,
        }}
      />,
    );

    expect(screen.getByText('Watchlist')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add to watchlist' })).toBeInTheDocument();
  });

  it('keeps the stock-only watchlist card when assetType is absent (legacy)', () => {
    render(
      <ReportOverview
        meta={{ ...baseMeta, stockCode: 'FPT' }}
        summary={baseSummary}
        watchlist={{
          isInWatchlist: () => false,
          onToggle: () => {},
          isActioning: false,
          actionMessage: null,
        }}
      />,
    );

    expect(screen.getByText('Watchlist')).toBeInTheDocument();
  });
});
