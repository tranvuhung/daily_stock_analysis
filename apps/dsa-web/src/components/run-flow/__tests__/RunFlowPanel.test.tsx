import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { analysisApi } from '../../../api/analysis';
import { historyApi } from '../../../api/history';
import type { RunFlowSnapshot } from '../../../types/runFlow';
import { RunFlowPanel } from '../RunFlowPanel';

vi.mock('../../../api/analysis', () => ({
  analysisApi: {
    getTaskFlow: vi.fn(),
    getTaskStreamUrl: vi.fn(() => 'http://localhost/api/v1/analysis/tasks/stream'),
  },
}));

vi.mock('../../../api/history', () => ({
  historyApi: {
    getRecordFlow: vi.fn(),
  },
}));

const snapshot: RunFlowSnapshot = {
  taskId: 'task-1',
  traceId: 'trace-1',
  stockCode: 'HPG',
  stockName: 'Hoa Phat',
  status: 'degraded',
  generatedAt: '2026-06-08T08:00:00Z',
  summary: {
    elapsedMs: 3250,
    failedAttempts: 1,
    fallbackCount: 1,
    model: 'DeepSeek',
    dataSourceCount: 2,
    eventCount: 3,
  },
  lanes: [
    { id: 'entry', label: 'Entry', order: 1 },
    { id: 'data_source', label: 'Data source', order: 2 },
    { id: 'analysis', label: 'Bộ máy phân tích', order: 3 },
    { id: 'artifact', label: 'Artifact', order: 4 },
  ],
  nodes: [
    {
      id: 'request',
      lane: 'entry',
      kind: 'entry',
      label: 'Yêu cầu người dùng',
      status: 'success',
      message: 'Task Đã tạo yêu cầu',
    },
    {
      id: 'news',
      lane: 'data_source',
      kind: 'data_source',
      label: 'News · AkShare',
      provider: 'AkShare',
      status: 'fallback',
      durationMs: 1200,
      attempts: 2,
      recordCount: 8,
      message: 'Primary source failed, fallback succeeded',
      metadata: {
        fallbackFrom: 'Tushare',
        fallbackTo: 'AkShare',
      },
    },
    {
      id: 'llm',
      lane: 'analysis',
      kind: 'model',
      label: 'LLM generation',
      provider: 'DeepSeek',
      status: 'success',
      durationMs: 1800,
    },
  ],
  edges: [
    {
      id: 'request-news',
      from: 'request',
      to: 'news',
      kind: 'control',
      status: 'success',
      label: 'Điều phối',
    },
    {
      id: 'news-llm',
      from: 'news',
      to: 'llm',
      kind: 'fallback',
      status: 'fallback',
      label: 'Đầu vào suy giảm',
    },
  ],
  events: [
    {
      id: 'evt-1',
      timestamp: '2026-06-08T08:00:01Z',
      severity: 'info',
      type: 'task_created',
      nodeId: 'request',
      title: 'Task created',
    },
    {
      id: 'evt-2',
      timestamp: '2026-06-08T08:00:02Z',
      severity: 'warning',
      type: 'provider_fallback',
      nodeId: 'news',
      title: 'Nguồn tin tức suy giảm',
      message: 'Chuyển nguồn dữ liệu sau khi thử lại',
    },
  ],
};

const providerAttemptSnapshot: RunFlowSnapshot = {
  ...snapshot,
  nodes: [
    {
      id: 'task_queue',
      lane: 'entry',
      kind: 'queue',
      label: 'Task Hàng đợi',
      status: 'success',
    },
    {
      id: 'provider_news_search_tavily_1',
      lane: 'data_source',
      kind: 'data_source',
      label: 'Tin tức dư luận · Tavily',
      provider: 'Tavily',
      status: 'failed',
      durationMs: 1200,
      metadata: { data_type: 'news_search', attempt: 1 },
    },
    {
      id: 'provider_news_search_searxng_2',
      lane: 'data_source',
      kind: 'data_source',
      label: 'Tin tức dư luận · SearXNG',
      provider: 'SearXNG',
      status: 'success',
      durationMs: 800,
      recordCount: 6,
      metadata: { data_type: 'news_search', attempt: 2 },
    },
    {
      id: 'context_pack',
      lane: 'analysis',
      kind: 'analysis',
      label: 'ContextPack',
      status: 'success',
    },
  ],
  edges: [
    {
      id: 'queue-news-1',
      from: 'task_queue',
      to: 'provider_news_search_tavily_1',
      kind: 'control',
      status: 'failed',
    },
    {
      id: 'news-1-news-2',
      from: 'provider_news_search_tavily_1',
      to: 'provider_news_search_searxng_2',
      kind: 'fallback',
      status: 'success',
    },
    {
      id: 'news-context',
      from: 'provider_news_search_searxng_2',
      to: 'context_pack',
      kind: 'data',
      status: 'success',
    },
  ],
  events: [
    {
      id: 'evt-news-1',
      timestamp: '2026-06-08T08:00:02Z',
      severity: 'warning',
      type: 'provider_run',
      nodeId: 'provider_news_search_tavily_1',
      title: 'Tin tức dư luận thất bại',
    },
  ],
};

const contextBlockSnapshot: RunFlowSnapshot = {
  ...snapshot,
  status: 'degraded',
  nodes: [
    {
      id: 'context_block_news',
      lane: 'data_source',
      kind: 'data_source',
      label: 'Tin tức',
      status: 'success',
      recordCount: 6,
      metadata: { block_key: 'news' },
    },
    {
      id: 'context_block_fundamental',
      lane: 'data_source',
      kind: 'data_source',
      label: 'Cơ bản',
      status: 'degraded',
      metadata: { block_key: 'fundamental' },
    },
    {
      id: 'context_pack',
      lane: 'analysis',
      kind: 'analysis',
      label: 'ContextPack',
      status: 'degraded',
    },
  ],
  edges: [
    {
      id: 'news-context',
      from: 'context_block_news',
      to: 'context_pack',
      kind: 'data',
      status: 'success',
    },
    {
      id: 'fundamental-context',
      from: 'context_block_fundamental',
      to: 'context_pack',
      kind: 'data',
      status: 'degraded',
    },
  ],
  events: [],
};

describe('RunFlowPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state while the snapshot request is pending', () => {
    vi.mocked(analysisApi.getTaskFlow).mockReturnValue(new Promise(() => undefined));

    render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} />);

    expect(screen.getByTestId('run-flow-panel-loading')).toBeInTheDocument();
    expect(screen.getByText('Loading run flow')).toBeInTheDocument();
  });

  it('renders an error state and reload action when the request fails', async () => {
    vi.mocked(analysisApi.getTaskFlow).mockRejectedValue({
      response: {
        status: 404,
        data: { message: 'Luồng chạy không tồn tại' },
      },
    });

    render(<RunFlowPanel source={{ type: 'task', taskId: 'missing-task' }} />);

    expect(await screen.findByTestId('run-flow-panel-error')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });

  it('renders an empty snapshot state when there are no nodes or events', async () => {
    vi.mocked(historyApi.getRecordFlow).mockResolvedValue({
      ...snapshot,
      nodes: [],
      edges: [],
      events: [],
      summary: { ...snapshot.summary, eventCount: 0 },
    });

    render(<RunFlowPanel source={{ type: 'history', recordId: 1 }} />);

    expect(await screen.findByText('No run-flow details yet')).toBeInTheDocument();
    expect(historyApi.getRecordFlow).toHaveBeenCalledWith(1);
  });

  it('renders a successful graph, event stream, and selectable node details', async () => {
    vi.mocked(analysisApi.getTaskFlow).mockResolvedValue(snapshot);

    render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} title="Luồng chạy Hoa Phat" />);

    expect(await screen.findByTestId('run-flow-panel')).toBeInTheDocument();
    expect(screen.getByText('Luồng chạy Hoa Phat')).toBeInTheDocument();
    expect(screen.getByTestId('run-flow-layout')).toHaveClass('xl:grid-cols-[minmax(0,1fr)_19.25rem]');
    expect(screen.getByTestId('run-flow-events-column')).toHaveClass('xl:max-h-[calc(100vh-18rem)]');
    expect(screen.getByTestId('run-flow-graph')).toBeInTheDocument();
    expect(screen.getByTestId('run-flow-events')).toBeInTheDocument();
    expect(await screen.findByTestId('run-flow-node-details')).toHaveTextContent('News');

    fireEvent.click(screen.getByRole('button', { name: 'LLM generation node, status Success' }));

    expect(screen.getByTestId('run-flow-node-details')).toHaveTextContent('LLM generation');
    expect(screen.getByTestId('run-flow-node-details')).toHaveTextContent('DeepSeek');

    fireEvent.click(screen.getByRole('button', { name: 'News · AkShare node, status Fallback' }));

    expect(screen.getByTestId('run-flow-node-details')).toHaveTextContent('fallbackFrom');
    expect(screen.getByTestId('run-flow-node-details')).toHaveTextContent('Tushare');
    expect(screen.getByTestId('run-flow-node-details')).toHaveTextContent('fallbackTo');
    expect(screen.getByTestId('run-flow-node-details')).toHaveTextContent('AkShare');
  });

  it('shows default node details without selecting the graph or hiding unrelated edge labels', async () => {
    vi.mocked(analysisApi.getTaskFlow).mockResolvedValue({
      ...snapshot,
      nodes: [
        ...snapshot.nodes,
        {
          id: 'artifact',
          lane: 'artifact',
          kind: 'artifact',
          label: 'Save report',
          status: 'success',
        },
      ],
      edges: [
        ...snapshot.edges,
        {
          id: 'llm-artifact',
          from: 'llm',
          to: 'artifact',
          kind: 'data',
          status: 'success',
          label: 'Save',
        },
      ],
    });

    render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} />);

    expect(await screen.findByTestId('run-flow-node-details')).toHaveTextContent('News');
    expect(screen.getAllByText('Data').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'News · AkShare node, status Fallback' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('expands provider attempt groups from node details', async () => {
    vi.mocked(analysisApi.getTaskFlow).mockResolvedValue(providerAttemptSnapshot);

    render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} />);

    expect(await screen.findByTestId('run-flow-node-topology_data_news_search')).toBeInTheDocument();
    expect(screen.queryByTestId('run-flow-node-provider_news_search_tavily_1')).not.toBeInTheDocument();
    expect(await screen.findByTestId('run-flow-node-details')).toHaveTextContent('Run attempts');

    fireEvent.click(screen.getByRole('button', { name: 'Expand attempts' }));

    expect(await screen.findByTestId('run-flow-node-provider_news_search_tavily_1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Collapse attempts' })).toBeInTheDocument();
  });

  it('renders TickFlow realtime fallback attempts through generic provider groups', async () => {
    const tickFlowProviderAttemptSnapshot: RunFlowSnapshot = {
      ...snapshot,
      nodes: [
        {
          id: 'task_queue',
          lane: 'entry',
          kind: 'queue',
          label: 'Task queue',
          status: 'success',
        },
        {
          id: 'provider_realtime_quote_tickflowfetcher_1',
          lane: 'data_source',
          kind: 'data_source',
          label: 'Báo giá thời gian thực · TickFlowFetcher',
          provider: 'TickFlowFetcher',
          status: 'failed',
          durationMs: 892,
          metadata: { data_type: 'realtime_quote', attempt: 1 },
        },
        {
          id: 'provider_realtime_quote_aksharefetcher_2',
          lane: 'data_source',
          kind: 'data_source',
          label: 'Báo giá thời gian thực · AkshareFetcher',
          provider: 'AkshareFetcher',
          status: 'success',
          durationMs: 8700,
          recordCount: 1,
          metadata: { data_type: 'realtime_quote', attempt: 2 },
        },
        {
          id: 'context_pack',
          lane: 'analysis',
          kind: 'analysis',
          label: 'ContextPack',
          status: 'success',
        },
      ],
      edges: [
        {
          id: 'queue-quote-1',
          from: 'task_queue',
          to: 'provider_realtime_quote_tickflowfetcher_1',
          kind: 'control',
          status: 'failed',
        },
        {
          id: 'quote-1-quote-2',
          from: 'provider_realtime_quote_tickflowfetcher_1',
          to: 'provider_realtime_quote_aksharefetcher_2',
          kind: 'fallback',
          status: 'success',
        },
        {
          id: 'quote-context',
          from: 'provider_realtime_quote_aksharefetcher_2',
          to: 'context_pack',
          kind: 'data',
          status: 'success',
        },
      ],
      events: [],
    };

    vi.mocked(analysisApi.getTaskFlow).mockResolvedValue(tickFlowProviderAttemptSnapshot);

    render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} />);

    const group = await screen.findByTestId('run-flow-node-topology_data_realtime_quote');
    expect(group).toHaveTextContent('TickFlowFetcher -> AkshareFetcher');
    expect(screen.queryByTestId('run-flow-node-provider_realtime_quote_tickflowfetcher_1')).not.toBeInTheDocument();

    const details = await screen.findByTestId('run-flow-node-details');
    expect(details).toHaveTextContent('TickFlowFetcher -> AkshareFetcher');
    expect(details).toHaveTextContent('TickFlowFetcher');
    expect(details).toHaveTextContent('AkshareFetcher');

    fireEvent.click(screen.getByTestId('run-flow-node-topology_data_realtime_quote-toggle'));

    expect(await screen.findByTestId('run-flow-node-provider_realtime_quote_tickflowfetcher_1')).toBeInTheDocument();
    expect(await screen.findByTestId('run-flow-node-provider_realtime_quote_aksharefetcher_2')).toBeInTheDocument();
    expect(screen.getByTestId('run-flow-node-provider_realtime_quote_tickflowfetcher_1')).toHaveTextContent('TickFlowFetcher');
    expect(screen.getByTestId('run-flow-node-provider_realtime_quote_aksharefetcher_2')).toHaveTextContent('AkshareFetcher');
  });
  it('hides topology summary metadata from aggregated node details', async () => {
    vi.mocked(analysisApi.getTaskFlow).mockResolvedValue(providerAttemptSnapshot);

    render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} />);

    const details = await screen.findByTestId('run-flow-node-details');

    expect(details).toHaveTextContent('Run attempts');
    expect(details).not.toHaveTextContent('data_type');
    expect(details).not.toHaveTextContent('provider_chain');
    expect(details).not.toHaveTextContent('success_count');
    expect(details).not.toHaveTextContent('failed_count');
    expect(details).not.toHaveTextContent('fallback_count');
    expect(details).not.toHaveTextContent('retry_count');
  });

  it('hides context-pack topology counts from raw metadata details', async () => {
    vi.mocked(analysisApi.getTaskFlow).mockResolvedValue(contextBlockSnapshot);

    render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} />);

    const details = await screen.findByTestId('run-flow-node-details');

    expect(details).toHaveTextContent('ContextPack');
    expect(details).toHaveTextContent('Context inputs');
    // `block_key: 'news'` tra được khoá nên hiển thị nhãn đã dịch; `block_key: 'fundamental'`
    // (số ít) không thuộc bộ khoá thật của backend nên lùi về nhãn thô — giữ cả hai nhánh.
    // A known block_key renders the localized label; an unknown one falls back to the raw label.
    expect(details).toHaveTextContent('News');
    expect(details).toHaveTextContent('Cơ bản');
    expect(details).not.toHaveTextContent('context_status_counts');
  });

  it('does not update state after a pending request is cleaned up', async () => {
    let resolveSnapshot: (value: RunFlowSnapshot) => void = () => undefined;
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(analysisApi.getTaskFlow).mockReturnValue(new Promise((resolve) => {
      resolveSnapshot = resolve;
    }));

    try {
      const { unmount } = render(<RunFlowPanel source={{ type: 'task', taskId: 'task-1' }} />);
      unmount();

      await act(async () => {
        resolveSnapshot(snapshot);
      });

      expect(analysisApi.getTaskFlow).toHaveBeenCalledWith('task-1');
      expect(consoleError).not.toHaveBeenCalled();
    } finally {
      consoleError.mockRestore();
    }
  });
});
