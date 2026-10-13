import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import type {
  AnalysisContextPackOverview,
  AnalysisReport,
  AnalysisResult,
} from '../../../types/analysis';
import { AnalysisContextSummary } from '../AnalysisContextSummary';
import { ReportSummary } from '../ReportSummary';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getDiagnostics: vi.fn(),
    getNews: vi.fn(),
  },
}));

const overview: AnalysisContextPackOverview = {
  packVersion: '1.0',
  createdAt: '2026-04-10T08:30:00+00:00',
  subject: {
    code: 'HPG',
    stockName: 'Hoa Phat',
    market: 'vn',
  },
  blocks: [
    {
      key: 'quote',
      label: 'giá',
      status: 'available',
      source: 'mock_quote',
      warnings: [],
      missingReasons: [],
    },
    {
      key: 'news',
      label: 'tin tức',
      status: 'missing',
      source: null,
      warnings: ['news_provider_timeout'],
      missingReasons: ['news_context_missing'],
    },
    {
      key: 'fundamentals',
      label: 'cơ bản',
      status: 'fetch_failed',
      source: 'fundamental_pipeline',
      warnings: [],
      missingReasons: ['fundamental_pipeline_failed'],
    },
  ],
  counts: {
    available: 1,
    missing: 1,
    notSupported: 0,
    fallback: 0,
    stale: 0,
    estimated: 0,
    partial: 0,
    fetchFailed: 1,
  },
  dataQuality: {
    overallScore: 82,
    level: 'usable',
    blockScores: {
      quote: 100,
      daily_bars: 100,
      technical: 100,
      news: 35,
      fundamentals: 25,
    },
    limitations: ['fundamentals: fetch_failed'],
  },
  warnings: ['intraday_realtime_overlay'],
  metadata: {
    triggerSource: 'api',
    newsResultCount: 3,
  },
};

describe('AnalysisContextSummary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a collapsed summary and expands overview details on demand', () => {
    render(<AnalysisContextSummary overview={overview} />);

    const panel = screen.getByTestId('analysis-context-summary');
    expect(panel).not.toHaveAttribute('open');
    expect(within(panel).getAllByText('Khối dữ liệu đầu vào')[0]).toBeVisible();
    expect(screen.getAllByText('Có sẵn 1')[0]).toBeVisible();
    expect(screen.getAllByText('Thiếu 1')[0]).toBeVisible();
    expect(screen.getAllByText('Lấy dữ liệu thất bại 1')[0]).toBeVisible();
    expect(screen.getAllByText('Điểm chất lượng 82/100 Dùng được')[0]).toBeVisible();
    expect(screen.getByText('Nguồn kích hoạt: api')).toBeVisible();
    expect(screen.getByText('Nguồn: mock_quote')).not.toBeVisible();

    fireEvent.click(within(panel).getAllByText('Khối dữ liệu đầu vào')[0]);

    expect(panel).toHaveAttribute('open');
    expect(screen.getByText('giá')).toBeInTheDocument();
    expect(screen.getByText('Nguồn: mock_quote')).toBeVisible();
    expect(screen.getByText('Cảnh báo:')).toBeInTheDocument();
    expect(screen.getByText(/intraday_realtime_overlay/)).toBeInTheDocument();
    expect(screen.getByText('Giới hạn dữ liệu:')).toBeInTheDocument();
    expect(screen.getByText(/cơ bản: Lấy dữ liệu thất bại/)).toBeInTheDocument();
    expect(screen.getByText(/news_provider_timeout/)).toBeInTheDocument();
    expect(screen.getByText(/Diễn giải: Tin tức không đi vào lượt chạy LLM này/)).toBeInTheDocument();
    expect(screen.getByText(/Mã chẩn đoán: news_context_missing/)).toBeInTheDocument();
    expect(screen.getByText(/mục tin liên quan trên trang báo cáo do một API riêng bổ sung/)).toBeInTheDocument();
    expect(screen.getByText('Nguồn: Không ghi nhận nguồn đầu vào')).toBeInTheDocument();
    expect(screen.queryByText(/^Xử lý:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Phạm vi:/)).not.toBeInTheDocument();
    const fundamentalsBlock = screen.getByText('cơ bản').closest('.home-subpanel');
    expect(fundamentalsBlock).not.toBeNull();
    const fundamentals = within(fundamentalsBlock as HTMLElement);
    expect(fundamentals.getByText(/Diễn giải: Lấy dữ liệu cơ bản thất bại/)).toBeInTheDocument();
    expect(fundamentals.getByText(/Mã chẩn đoán: fundamental_pipeline_failed/)).toBeInTheDocument();
    expect(screen.getAllByText('Số kết quả tin tức: 3').some((item) => item.textContent === 'Số kết quả tin tức: 3')).toBe(true);
    expect(screen.getAllByText('Đầu vào của lượt phân tích này')[0]).toBeVisible();
  });

  it('localizes the collapsed summary for english reports', () => {
    render(<AnalysisContextSummary overview={overview} language="en" />);

    const panel = screen.getByTestId('analysis-context-summary');
    expect(panel).not.toHaveAttribute('open');
    expect(screen.getAllByText('Input Blocks')[0]).toBeVisible();
    expect(screen.getByText('Shows inputs included in this LLM run, not provider run success')).toBeVisible();
    expect(screen.getAllByText('Available 1')[0]).toBeVisible();
    expect(screen.getAllByText('Missing 1')[0]).toBeVisible();
    expect(screen.getAllByText('Fetch failed 1')[0]).toBeVisible();
    expect(screen.getAllByText('Quality 82/100 Usable')[0]).toBeVisible();
    expect(screen.getByText('Trigger: api')).toBeVisible();

    fireEvent.click(within(panel).getAllByText('Input Blocks')[0]);

    expect(screen.getByText('Data Limitations:')).toBeInTheDocument();
    expect(screen.getByText(/fundamentals: Fetch failed/)).toBeInTheDocument();
    expect(screen.getByText(/Details: News was not included in this LLM run, so the conclusion did not use news context/)).toBeInTheDocument();
    expect(screen.getByText(/related news on the report page is loaded separately and does not indicate that it was used in this analysis/)).toBeInTheDocument();
    expect(screen.getByText(/Diagnostic code: news_context_missing/)).toBeInTheDocument();
    expect(screen.queryByText(/^Action:/)).not.toBeInTheDocument();
  });

  it('does not claim available fundamentals were unused when only provenance is missing', () => {
    const availableFundamentalsOverview: AnalysisContextPackOverview = {
      ...overview,
      blocks: [{
        key: 'fundamentals',
        label: 'cơ bản',
        status: 'available',
        source: null,
        warnings: [],
        missingReasons: ['fundamental_source_chain_missing'],
      }],
      counts: {
        available: 1,
        missing: 0,
        notSupported: 0,
        fallback: 0,
        stale: 0,
        estimated: 0,
        partial: 0,
        fetchFailed: 0,
      },
    };

    render(<AnalysisContextSummary overview={availableFundamentalsOverview} />);

    fireEvent.click(screen.getAllByText('Khối dữ liệu đầu vào')[0]);

    expect(screen.getByText(/Diễn giải: Không ghi nhận metadata chuỗi nguồn của dữ liệu cơ bản/)).toBeInTheDocument();
    expect(screen.getByText(/lấy theo trạng thái hiện tại/)).toBeInTheDocument();
    expect(screen.getByText(/Mã chẩn đoán: fundamental_source_chain_missing/)).toBeInTheDocument();
    expect(screen.queryByText(/Lần phân tích này không dùng dữ liệu cơ bản/)).not.toBeInTheDocument();
  });

  it('uses status guidance for unknown reason codes without adding another field', () => {
    const unknownReasonOverview: AnalysisContextPackOverview = {
      ...overview,
      blocks: [{
        key: 'fundamentals',
        label: 'cơ bản',
        status: 'fetch_failed',
        source: 'fundamental_pipeline',
        warnings: [],
        missingReasons: ['brand_new_internal_code'],
      }],
      counts: {
        available: 0,
        missing: 0,
        notSupported: 0,
        fallback: 0,
        stale: 0,
        estimated: 0,
        partial: 0,
        fetchFailed: 1,
      },
    };

    render(<AnalysisContextSummary overview={unknownReasonOverview} />);

    fireEvent.click(screen.getAllByText('Khối dữ liệu đầu vào')[0]);

    expect(screen.getByText(/Diễn giải: Lấy dữ liệu thất bại, lượt phân tích này không dùng dữ liệu đó/)).toBeInTheDocument();
    expect(screen.getByText(/Mã chẩn đoán: brand_new_internal_code/)).toBeInTheDocument();
    expect(screen.queryByText(/^Xử lý:/)).not.toBeInTheDocument();
  });

  it('surfaces degraded non-zero states in the collapsed summary', () => {
    const degradedOverview: AnalysisContextPackOverview = {
      ...overview,
      blocks: [
        {
          key: 'quote',
          label: 'giá',
          status: 'fallback',
          source: 'cached_quote',
          warnings: ['quote_fallback'],
          missingReasons: [],
        },
        {
          key: 'fundamental',
          label: 'cơ bản',
          status: 'stale',
          source: 'fundamental_cache',
          warnings: ['stale_fundamental'],
          missingReasons: [],
        },
        {
          key: 'technical',
          label: 'kỹ thuật',
          status: 'partial',
          source: 'technical_pipeline',
          warnings: ['technical_partial'],
          missingReasons: [],
        },
        {
          key: 'news',
          label: 'tin tức',
          status: 'estimated',
          source: 'estimated_news',
          warnings: [],
          missingReasons: [],
        },
        {
          key: 'daily_bars',
          label: 'nến ngày',
          status: 'not_supported',
          source: null,
          warnings: [],
          missingReasons: [],
        },
      ],
      counts: {
        available: 0,
        missing: 0,
        notSupported: 1,
        fallback: 1,
        stale: 1,
        estimated: 1,
        partial: 1,
        fetchFailed: 0,
      },
    };

    render(<AnalysisContextSummary overview={degradedOverview} />);

    const panel = screen.getByTestId('analysis-context-summary');
    expect(panel).not.toHaveAttribute('open');
    expect(within(panel).getByText('Có sẵn 0')).toBeVisible();
    expect(within(panel).getByText('Thiếu 0')).toBeVisible();
    expect(within(panel).getAllByText('Dự phòng 1')[0]).toBeVisible();
    expect(within(panel).getAllByText('Quá hạn 1')[0]).toBeVisible();
    expect(within(panel).getAllByText('Ước tính 1')[0]).toBeVisible();
    expect(within(panel).getAllByText('Có một phần 1')[0]).toBeVisible();
    expect(within(panel).getAllByText('Không hỗ trợ 1')[0]).toBeVisible();

    fireEvent.click(within(panel).getAllByText('Khối dữ liệu đầu vào')[0]);

    const quoteBlock = screen.getByText('giá').closest('.home-subpanel');
    expect(quoteBlock).not.toBeNull();
    expect(within(quoteBlock as HTMLElement).getByText('Diễn giải: Lượt phân tích này dùng đường dữ liệu dự phòng; hãy đối chiếu nguồn và cảnh báo để rà soát kết quả')).toBeInTheDocument();
    expect(within(quoteBlock as HTMLElement).queryByText(/^Xử lý:/)).not.toBeInTheDocument();

    expect(screen.getByText('Diễn giải: Lượt phân tích này dùng dữ liệu có thể không phải mới nhất; hãy kiểm tra thời điểm cập nhật và phân tích lại nếu cần')).toBeInTheDocument();
    expect(screen.getByText('Diễn giải: Chỉ một phần dữ liệu đi vào lượt phân tích này nên kết luận liên quan có thể thiếu; hãy kiểm tra cảnh báo và nguồn rồi phân tích lại')).toBeInTheDocument();
    expect(screen.getByText('Diễn giải: Lượt phân tích này dùng dữ liệu ước tính; hãy đối chiếu với dữ liệu gốc để rà soát kết quả')).toBeInTheDocument();
    expect(screen.getByText('Diễn giải: Thị trường hoặc mã hiện tại không hỗ trợ dữ liệu này nên lượt phân tích không dùng; hãy kết hợp các chỉ báo khác')).toBeInTheDocument();
  });

  it('does not render without an overview', () => {
    const { container } = render(<AnalysisContextSummary overview={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('does not render raw values or unexpected sensitive fields', () => {
    const unsafeOverview = {
      ...overview,
      value: 'raw trend payload',
      content: 'Không được hiện toàn văn tin tức',
      apiKey: 'secret-key',
      blocks: [
        {
          ...overview.blocks[0],
          items: {
            price: {
              value: 1880,
              apiKey: 'secret-key',
            },
          },
        },
      ],
    } as unknown as AnalysisContextPackOverview;

    render(<AnalysisContextSummary overview={unsafeOverview} />);

    fireEvent.click(screen.getAllByText('Khối dữ liệu đầu vào')[0]);

    expect(screen.queryByText('raw trend payload')).not.toBeInTheDocument();
    expect(screen.queryByText('Không được hiện toàn văn tin tức')).not.toBeInTheDocument();
    expect(screen.queryByText('secret-key')).not.toBeInTheDocument();
  });
});

describe('ReportSummary analysis context placement', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders strategy and news before context, diagnostics and traceability', async () => {
    vi.mocked(historyApi.getNews).mockResolvedValue({
      total: 0,
      items: [],
    });

    const report: AnalysisReport = {
      meta: {
        id: 1,
        queryId: 'q1',
        stockCode: 'HPG',
        stockName: 'Hoa Phat',
        reportType: 'detailed',
        reportLanguage: 'vi',
        createdAt: '2026-04-10T12:00:00',
        marketPhaseSummary: {
          market: 'vn',
          phase: 'intraday',
          marketLocalTime: '2026-04-10T10:30:00+07:00',
          sessionDate: '2026-04-10',
          effectiveDailyBarDate: '2026-04-09',
          isTradingDay: true,
          isMarketOpenNow: true,
          isPartialBar: true,
          minutesToOpen: null,
          minutesToClose: 150,
          triggerSource: 'api',
          analysisIntent: 'auto',
          warnings: [],
        },
      },
      summary: {
        analysisSummary: 'summary',
        operationAdvice: 'Nắm giữ',
        trendPrediction: 'Dao động',
        sentimentScore: 70,
      },
      strategy: {
        idealBuy: '120',
      },
      details: {
        analysisContextPackOverview: overview,
      },
    };
    const result: AnalysisResult = {
      queryId: 'q1',
      stockCode: 'HPG',
      stockName: 'Hoa Phat',
      report,
      diagnosticSummary: {
        status: 'normal',
        statusLabel: 'Bình thường',
        reason: 'Hoạt động bình thường',
        components: {},
        copyText: '',
      },
      createdAt: '2026-04-10T12:00:00',
    };

    render(<ReportSummary data={result} />);

    await waitFor(() => {
      expect(screen.getByText('Chưa có tin liên quan')).toBeInTheDocument();
    });

    expect(screen.getByText('Giai đoạn thị trường: VN · Trong phiên')).toBeInTheDocument();
    expect(screen.getByText('Nến chưa hoàn chỉnh')).toBeInTheDocument();
    expect(screen.getAllByText('Điểm chất lượng 82/100 Dùng được')[0]).toBeInTheDocument();

    const strategy = screen.getByText('Vùng giá hành động');
    const news = screen.getByText('Tin liên quan');
    const diagnostics = screen.getByTestId('run-diagnostics');
    const contextSummary = screen.getByTestId('analysis-context-summary');
    expect(contextSummary).not.toHaveAttribute('open');
    expect(diagnostics).not.toHaveAttribute('open');
    const traceability = screen.getByText('Truy vết dữ liệu');

    expect(strategy.compareDocumentPosition(news) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(news.compareDocumentPosition(contextSummary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(contextSummary.compareDocumentPosition(diagnostics) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(diagnostics.compareDocumentPosition(traceability) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(within(contextSummary).getAllByText('Khối dữ liệu đầu vào')[0]);
    expect(within(contextSummary).getByText(/Diễn giải: Tin tức không đi vào lượt chạy LLM này/)).toBeInTheDocument();
    expect(within(contextSummary).getByText(/mục tin liên quan trên trang báo cáo do một API riêng bổ sung/)).toBeInTheDocument();
    expect(screen.queryByText('AI Khuyến nghị / Tín hiệu quyết định')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Dòng chủ đề và vị thế cổ phiếu' })).not.toBeInTheDocument();
    expect(screen.queryByText('Robotics')).not.toBeInTheDocument();
  });
});
