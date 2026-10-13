import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AnalysisReport, MarketReviewPayload } from '../../../types/analysis';
import { MarketReviewReportView } from '../MarketReviewReportView';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getMarkdown: vi.fn(),
  },
}));

const englishMarketReviewReport: AnalysisReport = {
  meta: {
    queryId: 'market-review-q-1',
    stockCode: 'MARKET',
    stockName: 'Market Review',
    reportType: 'market_review',
    reportLanguage: 'en',
    createdAt: '2026-03-18T08:00:00Z',
  },
  summary: {
    analysisSummary: '',
    operationAdvice: '',
    trendPrediction: '',
    sentimentScore: undefined as unknown as number,
  },
};

const vnMarketReviewPayload: MarketReviewPayload = {
  version: 1,
  kind: 'market_review',
  region: 'vn',
  language: 'vi',
  title: 'Điểm lại thị trường',
  rootTitle: 'Điểm lại thị trường',
  breadth: {
    upCount: 312,
    downCount: 142,
    limitUpCount: 12,
    limitDownCount: 4,
    totalAmount: 21500,
    turnoverUnit: 'tỷ đồng',
  },
  indices: [{
    code: 'VNINDEX',
    name: 'VN-Index',
    current: 1280.2,
    changePct: 1.2,
    high: 1285.5,
    low: 1270.1,
  }, {
    code: 'VN30',
    name: 'VN30',
    current: 1320.4,
    changePct: -0.5,
    high: 1330.2,
    low: 1310.3,
  }],
};

const noBreadthMarketReviewPayload: MarketReviewPayload = {
  version: 1,
  kind: 'market_review',
  region: 'vn',
  language: 'en',
  title: 'Market Review',
  rootTitle: 'Market Review',
  indices: [{
    code: 'VNINDEX',
    name: 'VN-Index',
    current: 1280,
    changePct: 0.68,
    high: 1285.2,
    low: 1270.4,
  }],
  news: [],
  sections: [],
};

describe('MarketReviewReportView', () => {
  it('uses localized summary card labels and fallbacks for English reports', () => {
    render(
      <MarketReviewReportView
        report={englishMarketReviewReport}
        content="# Market Review"
        reportLanguage="en"
      />,
    );

    expect(screen.getByText('Review Summary')).toBeInTheDocument();
    expect(screen.getByText('No review summary yet')).toBeInTheDocument();
    expect(screen.getByText('Market Sentiment')).toBeInTheDocument();
    expect(screen.getByText('No score yet')).toBeInTheDocument();
    expect(screen.getByText('Rotation & Funds')).toBeInTheDocument();
    expect(screen.getByText('No rotation view yet')).toBeInTheDocument();
    expect(screen.getByText('Risks & Watchlist')).toBeInTheDocument();
    expect(screen.getByText('No key observations yet')).toBeInTheDocument();
    expect(screen.queryByText('Tóm tắt điểm lại')).not.toBeInTheDocument();
    expect(screen.queryByText('Chưa có tóm tắt')).not.toBeInTheDocument();
  });

  it('renders structured breadth and index data for the Vietnam market review payload', () => {
    render(
      <MarketReviewReportView
        payload={vnMarketReviewPayload}
        content="# Điểm lại thị trường"
        reportLanguage="vi"
      />,
    );

    expect(screen.getByText('VN-Index')).toBeInTheDocument();
    expect(screen.getAllByText('VN30').length).toBeGreaterThan(0);
    expect(screen.getByText('312')).toBeInTheDocument();
    expect(screen.getByText('21500.00 tỷ đồng')).toBeInTheDocument();
    // Không còn bảng xếp hạng ngành / khái niệm. / No sector or concept ranking panels.
    expect(screen.queryByText('Nhóm ngành')).not.toBeInTheDocument();
    expect(screen.queryByText('Nhóm khái niệm')).not.toBeInTheDocument();
  });

  it('localizes structured market data labels for Vietnamese reports', () => {
    render(
      <MarketReviewReportView
        payload={vnMarketReviewPayload}
        content="# Điểm lại thị trường"
        reportLanguage="vi"
      />,
    );

    expect(screen.getByText('Dữ liệu thị trường có cấu trúc')).toBeInTheDocument();
    expect(screen.getByText('Số mã tăng')).toBeInTheDocument();
    expect(screen.getByText('Số mã giảm')).toBeInTheDocument();
    expect(screen.getByText('Trần/Sàn')).toBeInTheDocument();
    expect(screen.getByText('Giá trị giao dịch')).toBeInTheDocument();
    expect(screen.getByText('Chỉ số')).toBeInTheDocument();
    expect(screen.getByText('Mới nhất')).toBeInTheDocument();
    expect(screen.getByText('Biến động')).toBeInTheDocument();
    expect(screen.getByText('Cao/Thấp')).toBeInTheDocument();
    expect(screen.queryByText('Structured Market Data')).not.toBeInTheDocument();
    expect(screen.queryByText('Advancers')).not.toBeInTheDocument();
    expect(screen.queryByText('Index')).not.toBeInTheDocument();
  });

  it('shows "No data" when breadth is not available for a market review payload', () => {
    render(
      <MarketReviewReportView
        payload={noBreadthMarketReviewPayload}
        content="# Market Review"
        reportLanguage="en"
      />,
    );

    expect(screen.getByText('Structured Market Data')).toBeInTheDocument();
    expect(screen.getByText('No data')).toBeInTheDocument();
    expect(screen.getByText('VN-Index')).toBeInTheDocument();
    expect(screen.queryByText('Industry Sectors')).not.toBeInTheDocument();
    expect(screen.queryByText('Advancers')).not.toBeInTheDocument();
    expect(screen.queryByText('Decliners')).not.toBeInTheDocument();
  });

  it('formats structured market numbers to two decimal places', () => {
    const payload: MarketReviewPayload = {
      version: 1,
      kind: 'market_review',
      region: 'vn',
      language: 'en',
      title: 'Market Review',
      rootTitle: 'Market Review',
      breadth: {
        upCount: 4327,
        downCount: 1145,
        limitUpCount: 222,
        limitDownCount: 12,
        totalAmount: 36822.49698199988,
        turnoverUnit: 'bn',
      },
      indices: [{
        code: 'VNINDEX',
        name: 'VN-Index',
        current: 4112.446,
        changePct: 0.44079750937683315,
        high: 4143.314,
        low: 4087.536,
      }],
    };

    render(
      <MarketReviewReportView
        payload={payload}
        content="# Market Review"
        reportLanguage="en"
      />,
    );

    expect(screen.getByText('36822.50 bn')).toBeInTheDocument();
    expect(screen.getByText('4112.45')).toBeInTheDocument();
    expect(screen.getByText('0.44%')).toBeInTheDocument();
    expect(screen.getByText('4143.31 / 4087.54')).toBeInTheDocument();
    expect(screen.queryByText(/36822\.496/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.440797/)).not.toBeInTheDocument();
  });

  it('formats string-backed market numbers and hides missing high/low zeros', () => {
    const payload = {
      version: 1,
      kind: 'market_review',
      region: 'vn',
      language: 'en',
      title: 'Market Review',
      rootTitle: 'Market Review',
      breadth: {
        upCount: '4,327',
        downCount: '1,145',
        limitUpCount: '0',
        limitDownCount: '12',
        totalAmount: '36,822.49698199988',
        turnoverUnit: 'bn',
      },
      indices: [{
        code: 'VNINDEX',
        name: 'VN-Index',
        current: '4,112.446',
        changePct: '0.44079750937683315%',
        high: 0,
        low: '0',
      }],
    } as unknown as MarketReviewPayload;

    render(
      <MarketReviewReportView
        payload={payload}
        content="# Market Review"
        reportLanguage="en"
      />,
    );

    expect(screen.getByText('4327')).toBeInTheDocument();
    expect(screen.getByText('36822.50 bn')).toBeInTheDocument();
    expect(screen.getByText('4112.45')).toBeInTheDocument();
    expect(screen.getByText('0.44%')).toBeInTheDocument();
    expect(screen.queryByText('0.00 / 0.00')).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.440797/)).not.toBeInTheDocument();
  });

  it('opens run flow for historical market review records', () => {
    const onOpenRunFlow = vi.fn();

    render(
      <MarketReviewReportView
        payload={vnMarketReviewPayload}
        content="# Điểm lại thị trường"
        recordId={7}
        reportLanguage="vi"
        onOpenRunFlow={onOpenRunFlow}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'View run flow for history record 7' }));

    expect(onOpenRunFlow).toHaveBeenCalledWith(7);
  });
});
