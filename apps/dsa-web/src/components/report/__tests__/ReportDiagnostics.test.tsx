import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import type { RunDiagnosticSummary } from '../../../types/analysis';
import { ReportDiagnostics } from '../ReportDiagnostics';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getDiagnostics: vi.fn(),
  },
}));

const diagnosticSummary: RunDiagnosticSummary = {
  traceId: 'trace-1234567890abcdef',
  taskId: 'task-1',
  queryId: 'query-1',
  stockCode: 'HPG',
  triggerSource: 'web',
  status: 'degraded',
  statusLabel: 'Suy giảm một phần',
  reason: 'Realtime quote baostock succeeded after earlier sources failed',
  copyText: 'trace_id: trace-1234567890abcdef\ndata_status: degraded',
  components: {
    realtimeQuote: {
      key: 'realtime_quote',
      label: 'Realtime quote',
      status: 'degraded',
      message: 'Realtime quote baostock succeeded after earlier sources failed',
      messageCode: 'provider_ok_after_fallback',
      details: {
        provider: 'baostock',
        attempts: 2,
      },
    },
    notification: {
      key: 'notification',
      label: 'Notification',
      status: 'not_configured',
      message: 'Notification not configured or skipped for this run',
      messageCode: 'notification_skipped',
    },
  },
};

describe('ReportDiagnostics', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it('loads historical diagnostics in a collapsed panel and copies sanitized text', async () => {
    vi.mocked(historyApi.getDiagnostics).mockResolvedValue(diagnosticSummary);

    render(<ReportDiagnostics recordId={1} />);

    expect(historyApi.getDiagnostics).toHaveBeenCalledWith(1);
    expect(await screen.findByText('Trạng thái chạy')).toBeInTheDocument();
    const panel = screen.getByTestId('run-diagnostics');
    expect(panel).not.toHaveAttribute('open');
    expect(screen.getByText('Suy giảm một phần')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Trạng thái chạy'));

    expect(panel).toHaveAttribute('open');
    expect(screen.getByText('Đã suy giảm sau lần lỗi gần đây')).toBeInTheDocument();
    expect(screen.getByText('Chưa cấu hình')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Sao chép thông tin gỡ lỗi' }));

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(diagnosticSummary.copyText);
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Đã sao chép' })).toBeInTheDocument();
    });
  });

  it('uses the provided summary without fetching history diagnostics', () => {
    render(<ReportDiagnostics summary={diagnosticSummary} language="en" />);

    expect(historyApi.getDiagnostics).not.toHaveBeenCalled();
    expect(screen.getByText('Run Status')).toBeInTheDocument();
    expect(screen.getByText('Degraded')).toBeInTheDocument();
    expect(screen.getByText('Fetch / LLM / save / notification path')).toBeInTheDocument();
  });

  it('localizes component labels, messages and the reason from stable codes', () => {
    render(<ReportDiagnostics summary={diagnosticSummary} language="vi" />);

    expect(screen.getByText('Giá thời gian thực')).toBeInTheDocument();
    expect(screen.getAllByText('Giá thời gian thực: baostock thành công sau khi các nguồn trước đó lỗi')).toHaveLength(2);
    expect(screen.getByText('Thông báo chưa cấu hình hoặc bị bỏ qua ở lượt này')).toBeInTheDocument();
    expect(screen.queryByText(/[\u4e00-\u9fff]/)).not.toBeInTheDocument();
  });

  it('localizes parameterized messages and input status', () => {
    const summary: RunDiagnosticSummary = {
      ...diagnosticSummary,
      status: 'degraded',
      components: {
        news: {
          key: 'news',
          label: 'News search',
          status: 'degraded',
          message: 'server text',
          messageCode: 'news_results_input_degraded',
          details: { recordCount: 10, analysisInputStatus: 'missing' },
        },
      },
    };
    render(<ReportDiagnostics summary={summary} language="vi" />);

    expect(screen.getAllByText(/Tìm kiếm tin tức trả về 10 kết quả, nhưng tin tức chưa được đưa vào đầu vào phân tích/)).toHaveLength(2);
  });

  it('localizes a known provider error type instead of the stored server error', () => {
    const summary: RunDiagnosticSummary = {
      ...diagnosticSummary,
      status: 'failed',
      components: {
        daily_data: {
          key: 'daily_data',
          label: 'Daily data',
          status: 'failed',
          message: 'Daily data failed: Không còn tin hợp lệ sau khi lọc',
          messageCode: 'provider_failed',
          details: { provider: 'Tavily', errorType: 'NoUsableNews', error: 'Không còn tin hợp lệ sau khi lọc' },
        },
      },
    };
    render(<ReportDiagnostics summary={summary} language="vi" />);

    // Khối JSON thô để sao chép cố ý giữ nguyên dữ liệu server, nên chỉ kiểm tra text hiển thị.
    expect(screen.getAllByText('Dữ liệu ngày thất bại: không còn tin hợp lệ sau khi lọc').length).toBeGreaterThan(0);
  });

  it('falls back to server text for legacy components without a message code', () => {
    const summary: RunDiagnosticSummary = {
      ...diagnosticSummary,
      status: 'normal',
      reason: 'Normal',
      components: {
        history: {
          key: 'history',
          label: 'History save',
          status: 'ok',
          message: 'legacy server message',
        },
      },
    };
    render(<ReportDiagnostics summary={summary} language="vi" />);

    expect(screen.getByText('Lưu lịch sử')).toBeInTheDocument();
    expect(screen.getByText('legacy server message')).toBeInTheDocument();
    expect(screen.getAllByText('Bình thường').length).toBeGreaterThan(0);
  });

  it('opens historical run flow from the diagnostics body', async () => {
    const onOpenRunFlow = vi.fn();
    vi.mocked(historyApi.getDiagnostics).mockResolvedValue(diagnosticSummary);

    render(<ReportDiagnostics recordId={1} onOpenRunFlow={onOpenRunFlow} />);

    fireEvent.click(await screen.findByText('Trạng thái chạy'));
    fireEvent.click(screen.getByRole('button', { name: 'View run flow for history record 1' }));

    expect(onOpenRunFlow).toHaveBeenCalledWith(1);
  });

  it('refetches diagnostics after StrictMode cleans up the first effect run', async () => {
    vi.mocked(historyApi.getDiagnostics).mockResolvedValue(diagnosticSummary);

    render(
      <StrictMode>
        <ReportDiagnostics recordId={1} />
      </StrictMode>,
    );

    await waitFor(() => {
      expect(historyApi.getDiagnostics).toHaveBeenCalledTimes(2);
    });
    expect(await screen.findByText('Trạng thái chạy')).toBeInTheDocument();
  });
});
