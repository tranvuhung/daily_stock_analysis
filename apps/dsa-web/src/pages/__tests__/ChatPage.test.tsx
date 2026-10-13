import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode, useState } from 'react';
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createParsedApiError } from '../../api/error';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import { historyApi } from '../../api/history';
import type { Message, ProgressStep } from '../../stores/agentChatStore';
import type { StockIndexItem } from '../../types/stockIndex';
import { UI_LANGUAGE_STORAGE_KEY } from '../../utils/uiLanguage';
import ChatPage from '../ChatPage';
import { extractStockCodeFromMessage, extractStockCodesFromMessage } from '../../utils/chatStockCode';

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const {
  mockGetSkills,
  mockGetStatus,
  mockDeleteChatSession,
  mockSendChat,
  mockGetSystemConfig,
  mockUpdateSystemConfig,
  mockGetWatchlist,
  mockAddToWatchlist,
  mockRemoveFromWatchlist,
  mockDownloadSession,
  mockFormatSessionAsMarkdown,
  mockStockIndex,
  mockStockIndexState,
} = vi.hoisted(() => {
  const mockStockIndex = [
    { canonicalCode: 'HPG', displayCode: 'HPG', nameZh: 'Hòa Phát', aliases: ['Hoa Phat'], market: 'VN', assetType: 'stock', active: true },
    { canonicalCode: 'FPT', displayCode: 'FPT', nameZh: 'FPT Corp', aliases: [], market: 'VN', assetType: 'stock', active: true },
    { canonicalCode: 'VCB', displayCode: 'VCB', nameZh: 'Vietcombank', aliases: [], market: 'VN', assetType: 'stock', active: true },
    { canonicalCode: 'VNM', displayCode: 'VNM', nameZh: 'Vinamilk', aliases: [], market: 'VN', assetType: 'stock', active: true },
    { canonicalCode: 'MSN', displayCode: 'MSN', nameZh: 'Masan', aliases: [], market: 'VN', assetType: 'stock', active: true },
    { canonicalCode: 'MCH', displayCode: 'MCH', nameZh: 'Masan', aliases: [], market: 'VN', assetType: 'stock', active: true },
    { canonicalCode: 'VNINDEX', displayCode: 'VNINDEX', nameZh: 'VN-Index', aliases: ['VNIndex'], market: 'INDEX', assetType: 'index', active: true },
    { canonicalCode: 'VN30', displayCode: 'VN30', nameZh: 'Rổ VN30', aliases: ['VN30-Index'], market: 'INDEX', assetType: 'index', active: true },
  ];
  return {
    mockGetSkills: vi.fn(),
    mockGetStatus: vi.fn(),
    mockDeleteChatSession: vi.fn(),
    mockSendChat: vi.fn(),
    mockGetSystemConfig: vi.fn(),
    mockUpdateSystemConfig: vi.fn(),
    mockGetWatchlist: vi.fn(),
    mockAddToWatchlist: vi.fn(),
    mockRemoveFromWatchlist: vi.fn(),
    mockDownloadSession: vi.fn(),
    mockFormatSessionAsMarkdown: vi.fn(),
    mockStockIndex,
    // Mutable registry-load state for the async window shared by every backend.
    mockStockIndexState: {
      index: mockStockIndex,
      loading: false,
      error: null as Error | null,
      fallback: false,
      loaded: true,
    },
  };
});

const mockLoadSessions = vi.fn();
const mockLoadInitialSession = vi.fn();
const mockSwitchSession = vi.fn();
const mockStartStream = vi.fn();
const mockStopStream = vi.fn();
const mockClearCompletionBadge = vi.fn();
const mockStartNewChat = vi.fn();

const mockStoreState = {
  messages: [] as Message[],
  selectedSkillIds: null as string[] | null,
  loading: false,
  progressSteps: [] as ProgressStep[],
  sessionId: 'session-1',
  sessions: [
    {
      session_id: 'session-1',
      title: 'Phân tích nhanh HPG',
      message_count: 2,
      created_at: '2026-03-15T09:00:00Z',
      last_active: '2026-03-15T09:05:00Z',
    },
  ],
  sessionsLoading: false,
  chatError: null,
  stopping: false,
  terminalStatus: null as 'cancelled' | 'timeout' | null,
  stopError: false,
  loadSessions: mockLoadSessions,
  loadInitialSession: mockLoadInitialSession,
  switchSession: mockSwitchSession,
  stopStream: mockStopStream,
  startStream: mockStartStream,
  clearCompletionBadge: mockClearCompletionBadge,
};

vi.mock('../../api/agent', () => ({
  agentApi: {
    getSkills: mockGetSkills,
    getStatus: mockGetStatus,
    deleteChatSession: mockDeleteChatSession,
    sendChat: mockSendChat,
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getConfig: mockGetSystemConfig,
    update: mockUpdateSystemConfig,
    getWatchlist: mockGetWatchlist,
    addToWatchlist: mockAddToWatchlist,
    removeFromWatchlist: mockRemoveFromWatchlist,
  },
}));

vi.mock('../../utils/chatExport', () => ({
  downloadSession: mockDownloadSession,
  formatSessionAsMarkdown: mockFormatSessionAsMarkdown,
}));

vi.mock('../../api/history', () => ({
  historyApi: {
    getDetail: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => ({
    index: mockStockIndexState.index,
    loading: mockStockIndexState.loading,
    error: mockStockIndexState.error,
    fallback: mockStockIndexState.fallback,
    loaded: mockStockIndexState.loaded,
  }),
}));

vi.mock('../../stores/agentChatStore', () => {
  type MockStore = typeof mockStoreState & {
    setSelectedSkillIds: (skillIds: string[]) => void;
  };
  const useAgentChatStore = (
    selector?: (state: MockStore) => unknown
  ) => {
    const [selectedSkillIds, setSelectedSkillIdsState] = useState(
      mockStoreState.selectedSkillIds,
    );
    const state: MockStore = {
      ...mockStoreState,
      selectedSkillIds,
      setSelectedSkillIds: (skillIds) => {
        setSelectedSkillIdsState(skillIds);
      },
    };
    return typeof selector === 'function' ? selector(state) : state;
  };

  useAgentChatStore.getState = () => ({
    startNewChat: mockStartNewChat,
  });

  return { useAgentChatStore };
});

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-color-scheme: dark)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  Object.defineProperty(window, 'requestAnimationFrame', {
    writable: true,
    value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(0), 0),
  });

  Object.defineProperty(window, 'cancelAnimationFrame', {
    writable: true,
    value: (handle: number) => window.clearTimeout(handle),
  });

  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    writable: true,
    value: vi.fn(),
  });
});

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.removeItem(UI_LANGUAGE_STORAGE_KEY);
  mockGetStatus.mockReset();
  mockStoreState.messages = [];
  mockStoreState.selectedSkillIds = null;
  mockStoreState.loading = false;
  mockStoreState.progressSteps = [];
  mockStoreState.chatError = null;
  mockStoreState.stopping = false;
  mockStoreState.terminalStatus = null;
  mockStoreState.stopError = false;
  mockStoreState.sessionsLoading = false;
  mockStoreState.sessionId = 'session-1';
  mockStoreState.sessions = [
    {
      session_id: 'session-1',
      title: 'Phân tích nhanh HPG',
      message_count: 2,
      created_at: '2026-03-15T09:00:00Z',
      last_active: '2026-03-15T09:05:00Z',
    },
  ];
  mockGetSkills.mockResolvedValue({
    skills: [
      { id: 'bull_trend', name: 'Phân tích xu hướng', description: 'Kỹ năng thử nghiệm' },
    ],
    default_skill_id: 'bull_trend',
  });
  mockGetStatus.mockResolvedValue({
    backend: 'litellm',
    available: true,
    experimental: false,
    errorCode: null,
    message: null,
  });
  mockStartStream.mockImplementation(async (_payload, meta) => {
    meta?.onAccepted?.({
      type: 'accepted',
      backend: 'litellm',
      request_id: 'request-test',
      session_id: 'session-1',
    });
  });
  mockDeleteChatSession.mockResolvedValue(undefined);
  mockSendChat.mockResolvedValue({ success: true });
  mockGetWatchlist.mockResolvedValue([]);
  mockGetSystemConfig.mockResolvedValue({
    configVersion: 'cfg-v1',
    maskToken: 'mask-token',
    items: [
      {
        key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
        value: 'false',
        rawValueExists: true,
        isMasked: false,
      },
    ],
  });
  mockUpdateSystemConfig.mockResolvedValue({
    success: true,
    configVersion: 'cfg-v2',
    appliedCount: 1,
    skippedMaskedCount: 0,
    reloadTriggered: true,
    updatedKeys: ['AGENT_CONTEXT_COMPRESSION_ENABLED'],
    warnings: [],
  });
  mockDownloadSession.mockImplementation(() => {});
  mockFormatSessionAsMarkdown.mockReturnValue('# exported session');
  mockStockIndexState.index = mockStockIndex;
  mockStockIndexState.loading = false;
  mockStockIndexState.error = null;
  mockStockIndexState.fallback = false;
  mockStockIndexState.loaded = true;
});

describe('ChatPage', () => {
  it('lets the user stop an active Codex analysis from the existing Chat composer', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockStoreState.loading = true;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Stop analysis' }));

    expect(mockStopStream).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
  });

  it('keeps the existing waiting state for LiteLLM without offering a false stop', async () => {
    mockStoreState.loading = true;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: 'Processing...' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Stop analysis' })).not.toBeInTheDocument();
    expect(mockStopStream).not.toHaveBeenCalled();
  });

  it('labels the stop action in English when the UI language is English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockStoreState.loading = true;

    render(
      <UiLanguageProvider>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </UiLanguageProvider>,
    );

    expect(await screen.findByRole('button', { name: 'Stop analysis' })).toBeInTheDocument();
  });

  it('shows a disabled stopping state until Codex confirms cleanup', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockStoreState.loading = true;
    mockStoreState.stopping = true;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const button = await screen.findByRole('button', { name: 'Stopping…' });
    expect(button).toBeDisabled();
  });

  it('shows a plain-language terminal status after cancellation', async () => {
    mockStoreState.terminalStatus = 'cancelled';

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('status')).toHaveTextContent('This analysis has stopped and its background task has ended.');
  });

  it('shows the current backend in the existing Chat header', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Codex Agent · Experimental')).toBeInTheDocument();
    expect(screen.getByText('What Codex can use now')).toBeInTheDocument();
    expect(screen.getByText(/live quotes, news, market hotspots/)).toBeInTheDocument();
    expect(screen.getByText('Ask Codex about a stock using saved analysis context and backtest summaries.')).toBeInTheDocument();
    expect(screen.getByText(/Codex will answer from saved analysis context and backtest summaries/)).toBeInTheDocument();
    expect(screen.queryByText(/AI will use live data tools/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change ask-stock method' })).toBeInTheDocument();
    expect(mockGetStatus).toHaveBeenCalledTimes(1);
    expect(screen.getByPlaceholderText(/Analyze FPT/)).toBeEnabled();
  });

  it('finishes the compatibility check when React Strict Mode remounts effects', async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </StrictMode>,
    );

    expect(await screen.findByPlaceholderText(/Analyze FPT/)).toBeEnabled();
    expect(screen.queryByText('Checking the ask-stock runtime')).not.toBeInTheDocument();
  });

  it('preserves the draft and disables sending while the compatibility check is pending', async () => {
    const status = createDeferred<{
      backend: string;
      available: boolean;
      experimental: boolean;
      errorCode: null;
      message: null;
    }>();
    mockGetStatus.mockReturnValueOnce(status.promise);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Checking the ask-stock runtime')).toBeInTheDocument();
    const input = screen.getByPlaceholderText(/Analyze FPT/);
    expect(input).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Analyze the trend of VNM' })).toBeDisabled();
    expect(screen.getByText(/does not call a model or read stock data/)).toBeInTheDocument();
    status.resolve({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });

    await waitFor(() => expect(input).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Analyze the trend of VNM' })).toBeEnabled();
    expect(mockGetStatus).toHaveBeenCalledTimes(1);
  });

  it('blocks sending only when backend status confirms unavailability and links to Agent settings', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: false,
      experimental: true,
      errorCode: 'command_not_found',
      message: 'Codex was not found',
    });
    const router = createMemoryRouter(
      [
        { path: '/chat', element: <ChatPage /> },
        { path: '/settings', element: <div>Agent settings destination</div> },
      ],
      { initialEntries: ['/chat'] },
    );
    render(<RouterProvider router={router} />);

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    expect(input).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Open Agent settings' }));
    expect(await screen.findByText('Agent settings destination')).toBeInTheDocument();
    // React Router v7 applies navigations asynchronously; waitFor keeps the
    // assertion in an act-wrapped retry loop instead of reading a stale router state.
    await waitFor(() => {
      expect(router.state.location.search).toBe('?category=agent');
    });
  });

  it('keeps sending disabled when backend status cannot be established', async () => {
    mockGetStatus.mockRejectedValueOnce(new Error('temporary status failure'));
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Ask-stock status is temporarily unavailable')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Analyze FPT/)).toBeDisabled();
    expect(mockGetStatus).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    await waitFor(() => expect(screen.getByPlaceholderText(/Analyze FPT/)).toBeEnabled());
    expect(mockGetStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps the draft until the server accepts the turn', async () => {
    const stream = createDeferred<void>();
    let onAccepted: ((event: {
      type: 'accepted';
      backend: 'litellm' | 'codex_app_server';
      request_id: string;
      session_id: string;
    }) => void) | undefined;
    mockStartStream.mockImplementationOnce(async (_payload, meta) => {
      onAccepted = meta?.onAccepted;
      await stream.promise;
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Analyze FPT' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    expect(input).toHaveValue('Analyze FPT');
    expect(onAccepted).toBeTypeOf('function');
    act(() => {
      onAccepted?.({
        type: 'accepted',
        backend: 'codex_app_server',
        request_id: 'request-accepted',
        session_id: 'session-1',
      });
    });
    expect(input).toHaveValue('');

    stream.resolve();
    await act(async () => {
      await stream.promise;
    });
  });

  it('resolves a registered index name to its canonical code', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    let sentPayload: { context?: { stock_code: string; stock_name: string | null } } | undefined;
    mockStartStream.mockImplementation(async (payload) => {
      sentPayload = payload as typeof sentPayload;
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Phân tích VN-Index hôm nay' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    // Mã chỉ số giữ nguyên canonical. / Index canonical is preserved verbatim.
    expect(sentPayload?.context?.stock_code).toBe('VNINDEX');
    expect(sentPayload?.context?.stock_name).toBe('VN-Index');
  });

  it('resolves a registered index alias to its canonical code', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    let sentPayload: { context?: { stock_code: string; stock_name: string | null } } | undefined;
    mockStartStream.mockImplementation(async (payload) => {
      sentPayload = payload as typeof sentPayload;
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Phân tích vn30-index' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    expect(sentPayload?.context?.stock_code).toBe('VN30');
    expect(sentPayload?.context?.stock_name).toBe('Rổ VN30');
  });

  it('hides the watchlist action for a registered index canonical in Codex mode', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockStartStream.mockImplementation(async (_payload, meta) => {
      meta?.onAccepted?.({
        type: 'accepted',
        backend: 'codex_app_server',
        request_id: 'request-index',
        session_id: 'session-1',
      });
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Phân tích VN-Index' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    // VNINDEX là mã chỉ số → ẩn nút danh mục theo dõi (chỉ dành cho cổ phiếu).
    expect(screen.queryByText('Add to watchlist')).not.toBeInTheDocument();
    expect(screen.queryByText('Remove from watchlist')).not.toBeInTheDocument();
  });

  it('keeps the watchlist action for a plain Vietnam stock code', async () => {
    mockGetWatchlist.mockResolvedValue([]);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Analyze HPG' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(await screen.findByText('Add to watchlist')).toBeInTheDocument();
  });

  const CODEX_STATUS = {
    backend: 'codex_app_server',
    available: true,
    experimental: true,
    errorCode: null,
    message: null,
  };
  const acceptedEvent = (requestId: string) => ({
    type: 'accepted' as const,
    backend: 'codex_app_server' as const,
    request_id: requestId,
    session_id: 'session-1' as const,
  });

  // Mã chỉ số đã đăng ký giữ nguyên canonical của registry (VNINDEX viết hoa).
  // Registered index codes are sent as the registry canonical verbatim.
  it.each([
    ['VNINDEX', 'VNINDEX'],
    ['VN30', 'VN30'],
  ] as const)('sends an explicit index code %s as its registry canonical', async (inputCode, expectedCanonical) => {
    mockGetStatus.mockResolvedValueOnce(CODEX_STATUS);
    let sentPayload: { context?: { stock_code: string; stock_name: string | null } } | undefined;
    mockStartStream.mockImplementation(async (payload) => {
      sentPayload = payload as typeof sentPayload;
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: `Analyze ${inputCode}` } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    expect(sentPayload?.context?.stock_code).toBe(expectedCanonical);
    expect(sentPayload?.context?.stock_name).toBeNull();
  });

  it.each([
    ['VNINDEX', 'VNINDEX'],
    ['VN30', 'VN30'],
    ['HNXINDEX', 'HNXINDEX'],
    ['HPG.VN', 'HPG'],
    ['FUEVN100', 'FUEVN100'],
  ] as const)('sends %s as %s under the default LiteLLM backend', async (inputCode, expectedCanonical) => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: `Analyze ${inputCode}` } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          context: { stock_code: expectedCanonical, stock_name: null },
        }),
        expect.any(Object),
      );
    });
  });

  it('blocks every chat send entry point while the index registry is loading', async () => {
    mockStockIndexState.index = [];
    mockStockIndexState.loading = true;
    mockStockIndexState.loaded = false;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Analyze VNINDEX' } });
    const sendButton = screen.getByRole('button', { name: 'Processing...' });
    const quickQuestion = screen.getByRole('button', { name: 'Analyze the trend of VNM' });

    expect(sendButton).toBeDisabled();
    expect(quickQuestion).toBeDisabled();
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.click(sendButton);
    fireEvent.click(quickQuestion);
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it.each([
    ['success', mockStockIndex, null, false, 'VNINDEX'],
    ['failure', [], new Error('registry unavailable'), true, 'VNINDEX'],
    ['empty', [], null, false, 'VNINDEX'],
  ] as const)('releases direct sends after registry %s settle', async (_scenario, index, error, fallback, expectedCode) => {
    mockStockIndexState.index = [];
    mockStockIndexState.loading = true;
    mockStockIndexState.loaded = false;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );
    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Analyze VNINDEX' } });

    mockStockIndexState.index = [...index];
    mockStockIndexState.loading = false;
    mockStockIndexState.error = error;
    mockStockIndexState.fallback = fallback;
    mockStockIndexState.loaded = true;
    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const sendButton = screen.getByRole('button', { name: 'Send' });
    await waitFor(() => expect(sendButton).toBeEnabled());
    fireEvent.click(sendButton);
    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          context: { stock_code: expectedCode, stock_name: null },
        }),
        expect.any(Object),
      );
    });
  });

  it('keeps the active index context untouched for compare messages with a stock', async () => {
    mockGetStatus.mockResolvedValueOnce(CODEX_STATUS);
    mockStartStream.mockImplementation(async (_payload, meta) => {
      meta?.onAccepted?.(acceptedEvent('request-index'));
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Analyze VNINDEX' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    fireEvent.change(input, { target: { value: 'So sánh VNINDEX và HPG' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(2));

    expect(mockStartStream.mock.calls[1][0].context).toEqual({
      stock_code: 'VNINDEX',
      stock_name: null,
    });
  });

  it('hides the stock-only watchlist action after an explicit index code is sent', async () => {
    mockGetStatus.mockResolvedValueOnce(CODEX_STATUS);
    mockStartStream.mockImplementation(async (_payload, meta) => {
      meta?.onAccepted?.(acceptedEvent('request-index'));
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Analyze VN30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));

    expect(screen.queryByText('Add to watchlist')).not.toBeInTheDocument();
    expect(screen.queryByText('Remove from watchlist')).not.toBeInTheDocument();
  });

  it('renders the new Codex status copy in English when the UI language is English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: false,
      experimental: true,
      errorCode: 'command_not_found',
      message: 'Codex was not found',
    });

    render(
      <UiLanguageProvider>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </UiLanguageProvider>,
    );

    expect(await screen.findByText('Codex Agent · Experimental')).toBeInTheDocument();
    expect(screen.getByText('This device does not currently meet the basic Codex ask-stock requirements. Open Agent settings to check installation and Single Agent mode.')).toBeInTheDocument();
    expect(screen.queryByText(/Thiết bị này hiện chưa đáp ứng/)).not.toBeInTheDocument();
  });

  it('renders status-read failure copy in English', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    mockGetStatus.mockRejectedValueOnce(new Error('temporary status failure'));

    render(
      <UiLanguageProvider>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </UiLanguageProvider>,
    );

    expect(await screen.findByText('Ask-stock status is temporarily unavailable')).toBeInTheDocument();
    expect(screen.getByText('The ask-stock runtime cannot be confirmed, so sending is paused. You can check again manually; your question will be preserved.')).toBeInTheDocument();
    expect(screen.queryByText('Tạm thời không lấy được trạng thái hỏi cổ phiếu')).not.toBeInTheDocument();
  });

  it('renders a fixed workspace shell with independent session and message viewports', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByTestId('chat-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-list-scroll')).toBeInTheDocument();
    expect(screen.getByTestId('chat-message-scroll')).toBeInTheDocument();
    expect(mockLoadInitialSession).toHaveBeenCalled();
    expect(mockClearCompletionBadge).toHaveBeenCalled();
  });

  it('loads and saves the global context compression setting from the chat input area', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const compressionToggle = await screen.findByRole('checkbox', { name: /Context compression/ });

    await waitFor(() => {
      expect(compressionToggle).not.toBeDisabled();
    });

    expect(compressionToggle).not.toBeChecked();

    fireEvent.click(compressionToggle);

    await waitFor(() => {
      expect(mockUpdateSystemConfig).toHaveBeenCalledWith({
        configVersion: 'cfg-v1',
        maskToken: 'mask-token',
        reloadNow: true,
        items: [
          {
            key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
            value: 'true',
          },
        ],
      });
    });

    expect(compressionToggle).toBeChecked();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
  });

  it('rolls back the context compression switch when saving fails', async () => {
    mockGetSystemConfig.mockResolvedValue({
      configVersion: 'cfg-v1',
      maskToken: 'mask-token',
      items: [
        {
          key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
          value: 'true',
          rawValueExists: true,
          isMasked: false,
        },
      ],
    });
    mockUpdateSystemConfig.mockRejectedValue(
      createParsedApiError({
        title: 'Save failed',
        message: 'Config service unavailable',
        category: 'unknown',
      }),
    );

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const compressionToggle = await screen.findByRole('checkbox', { name: /Context compression/ });

    await waitFor(() => {
      expect(compressionToggle).toBeChecked();
      expect(compressionToggle).not.toBeDisabled();
    });

    fireEvent.click(compressionToggle);

    await waitFor(() => {
      expect(mockUpdateSystemConfig).toHaveBeenCalledWith(expect.objectContaining({
        items: [
          {
            key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
            value: 'false',
          },
        ],
      }));
      expect(compressionToggle).toBeChecked();
    });
    expect(screen.getByText('Config service unavailable')).toBeInTheDocument();
  });

  it('does not switch when clicking the current session card', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const sessionCard = await screen.findByRole('button', {
      name: /Switch to conversation Phân tích nhanh HPG/,
    });

    fireEvent.click(sessionCard);
    expect(mockSwitchSession).not.toHaveBeenCalled();
    expect(sessionCard).toHaveAttribute('aria-current', 'page');
  });

  it('renders a separate delete button for each session and opens confirmation without switching', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const deleteButton = await screen.findByRole('button', {
      name: /Delete conversation Phân tích nhanh HPG/,
    });

    fireEvent.click(deleteButton);

    expect(mockSwitchSession).not.toHaveBeenCalled();
    expect(await screen.findByText('This conversation cannot be recovered after deletion. Delete it?')).toBeInTheDocument();
  });

  it('hides header actions when there are no messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('heading', { name: 'Ask' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Export session' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send to the configured notification bots / mailboxes' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Past conversations' })).toBeInTheDocument();
  });

  it('exports the current session from the header action', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: 'Phân tích HPG' },
      { id: 'assistant-1', role: 'assistant', content: 'Trend is strong', skillName: 'Bullish trend (default)' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Export the session as a Markdown file' }));

    expect(mockDownloadSession).toHaveBeenCalledWith(mockStoreState.messages, 'en');
    expect(mockFormatSessionAsMarkdown).not.toHaveBeenCalled();
  });

  it('renders assistant skill labels with shared badge semantics', async () => {
    mockStoreState.messages = [
      { id: 'assistant-1', role: 'assistant', content: 'Trend is strong', skillName: 'Bullish trend (default)' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const skillBadge = await screen.findByLabelText('Skill Bullish trend (default)');
    expect(skillBadge).toBeInTheDocument();
    expect(skillBadge).toHaveTextContent('Bullish trend (default)');
  });

  it('renders assistant multi-skill labels with shared badge semantics', async () => {
    mockStoreState.messages = [
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'Trend is strong',
        skills: ['bull_trend', 'ma_golden_cross'],
        skillNames: ['Bullish trend (default)', 'MA golden cross'],
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const skillBadge = await screen.findByLabelText('Skill Bullish trend (default), MA golden cross');
    expect(skillBadge).toBeInTheDocument();
    expect(skillBadge).toHaveTextContent('Bullish trend (default), MA golden cross');
  });

  it('localizes backend progress and strategy names in Vietnamese', async () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'vi');
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'ma_golden_cross', name: 'MA golden cross', description: 'MA cross' }],
      default_skill_id: 'ma_golden_cross',
    });
    mockStoreState.loading = true;
    mockStoreState.progressSteps = [
      { type: 'tool_done', tool: 'get_realtime_quote', display_name: 'Get realtime quote', success: true, duration: 0.5 },
      { type: 'thinking', step: 2, message: 'Quote fetch done, continuing deeper analysis...' },
    ];

    render(
      <UiLanguageProvider>
        <MemoryRouter initialEntries={['/chat']}>
          <ChatPage />
        </MemoryRouter>
      </UiLanguageProvider>,
    );

    expect(await screen.findByText('Đã xong «Lấy giá thời gian thực», tiếp tục phân tích...')).toBeInTheDocument();
    expect(screen.queryByText(/continuing deeper analysis/)).not.toBeInTheDocument();
    expect(await screen.findByRole('checkbox', { name: 'Giao cắt vàng MA' })).toBeInTheDocument();
  });

  it('renders failed stage_done progress as a non-success state', async () => {
    mockStoreState.loading = true;
    mockStoreState.progressSteps = [
      { type: 'stage_done', stage: 'risk', status: 'failed' },
    ];
    mockStoreState.messages = [
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'Partial answer',
        thinkingSteps: [
          { type: 'stage_done', stage: 'risk', status: 'failed' },
        ],
      },
    ];

    const { container } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findAllByText('risk review failed')).toHaveLength(1);

    const thinkingToggle = container.querySelector('button[class*="mb-2"][class*="w-full"]') as HTMLButtonElement;
    fireEvent.click(thinkingToggle);

    const failedStage = screen.getAllByText('risk review failed').find((node) =>
      node.closest('.chat-progress-item'),
    );
    expect(failedStage).toBeDefined();
    expect(failedStage?.closest('.chat-progress-item')).toHaveClass('chat-progress-item-danger');
    expect(failedStage?.closest('.chat-progress-item')).not.toHaveClass('chat-progress-item-success');
  });

  it('renders pipeline budget skip progress without timeout severity', async () => {
    mockStoreState.loading = true;
    mockStoreState.progressSteps = [
      { type: 'pipeline_budget_skipped', stage: 'decision' },
    ];
    mockStoreState.messages = [
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'Partial answer',
        thinkingSteps: [
          { type: 'pipeline_budget_skipped', stage: 'decision' },
        ],
      },
    ];

    const { container } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findAllByText('decision skipped: insufficient budget')).toHaveLength(1);
    expect(screen.queryByText('decision timed out')).not.toBeInTheDocument();

    const thinkingToggle = container.querySelector('button[class*="mb-2"][class*="w-full"]') as HTMLButtonElement;
    fireEvent.click(thinkingToggle);

    const budgetSkipped = screen.getAllByText('decision skipped: insufficient budget').find((node) =>
      node.closest('.chat-progress-item'),
    );
    expect(budgetSkipped).toBeDefined();
    expect(budgetSkipped?.closest('.chat-progress-item')).toHaveClass('chat-progress-item-muted');
    expect(budgetSkipped?.closest('.chat-progress-item')).not.toHaveClass('chat-progress-item-danger');
  });

  it('selects the default skill after loading skills', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: 'Bullish trend (default)' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'General analysis' })).not.toBeChecked();
  });

  it('keeps the restored session skills when the Skill catalog finishes loading', async () => {
    mockStoreState.selectedSkillIds = ['ma_golden_cross'];
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: 'Bullish trend (default)', description: 'Default trend' },
        { id: 'ma_golden_cross', name: 'MA golden cross', description: 'MA cross' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: 'MA golden cross' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Bullish trend (default)' })).not.toBeChecked();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục phân tích' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({ skills: ['ma_golden_cross'] }),
        expect.any(Object),
      );
    });
  });

  it('omits skills for an untouched new session so the server resolves its default', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: 'Bullish trend (default)' })).toBeChecked();
    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Analyze FPT' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(mockStartStream).toHaveBeenCalled());
    expect(mockStartStream.mock.calls.at(-1)?.[0]).not.toHaveProperty('skills');
  });

  it('omits skills when continuing a legacy session without persisted Skill state', async () => {
    mockStoreState.messages = [
      { id: 'legacy-user', role: 'user', content: 'Analyze FPT' },
      { id: 'legacy-assistant', role: 'assistant', content: 'Previous analysis result' },
    ];
    mockStoreState.selectedSkillIds = null;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: 'Bullish trend (default)' })).toBeChecked();
    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục phân tích' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(mockStartStream).toHaveBeenCalled());
    expect(mockStartStream.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        message: 'Tiếp tục phân tích',
        session_id: 'session-1',
      }),
    );
    expect(mockStartStream.mock.calls.at(-1)?.[0]).not.toHaveProperty('skills');
  });

  it('sends multiple selected skills in order', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: 'Bullish trend (default)', description: 'Default trend' },
        { id: 'ma_golden_cross', name: 'MA golden cross', description: 'MA cross' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: 'MA golden cross' }));
    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Analyze HPG' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Analyze HPG',
          skills: ['bull_trend', 'ma_golden_cross'],
        }),
        expect.objectContaining({
          skillNames: ['Bullish trend (default)', 'MA golden cross'],
          skillName: 'Bullish trend (default), MA golden cross',
        }),
      );
    });
  });

  it('adds the quick-question stock context only for Codex', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'chan_theory', name: 'Chan theory', description: 'Structure analysis' }],
      default_skill_id: 'chan_theory',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Analyze FPT with Chan theory' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          context: { stock_code: 'FPT', stock_name: 'CTCP FPT' },
        }),
        expect.any(Object),
      );
    });
  });

  it('collapses the mobile skill picker by default and keeps selected skills when sending', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: 'Bullish trend (default)', description: 'Default trend' },
        { id: 'ma_golden_cross', name: 'MA golden cross', description: 'MA cross' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const mobileToggle = await screen.findByRole('button', { name: 'Expand strategy picker' });
    const skillPanel = screen.getByTestId('chat-skill-picker-panel');
    expect(mobileToggle).toHaveAttribute('aria-expanded', 'false');
    expect(skillPanel).toHaveClass('hidden');

    fireEvent.click(mobileToggle);

    expect(screen.getByRole('button', { name: 'Collapse strategy picker' })).toHaveAttribute('aria-expanded', 'true');
    expect(skillPanel).not.toHaveClass('hidden');
    expect(skillPanel).toHaveClass('flex');

    fireEvent.click(screen.getByRole('checkbox', { name: 'MA golden cross' }));
    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Analyze HPG' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Analyze HPG',
          skills: ['bull_trend', 'ma_golden_cross'],
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default), MA golden cross',
        }),
      );
    });

    expect(screen.getByRole('button', { name: 'Expand strategy picker' })).toHaveAttribute('aria-expanded', 'false');
    expect(skillPanel).toHaveClass('hidden');
  });

  it('sends an explicit empty skills list when all concrete skills are cleared', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: 'Bullish trend (default)' }));
    expect(screen.getByRole('checkbox', { name: 'General analysis' })).toBeChecked();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Analyze FPT' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalled();
    });
    const lastCall = mockStartStream.mock.calls[mockStartStream.mock.calls.length - 1];
    expect(lastCall[0]).toEqual(expect.objectContaining({
      message: 'Analyze FPT',
      skills: [],
    }));
    expect(lastCall[1]).toEqual(expect.objectContaining({
      skillNames: ['General'],
      skillName: 'General',
    }));
  });

  it('caps concrete skill selection at three and re-enables choices after unselecting', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: 'Bullish trend (default)', description: 'Default trend' },
        { id: 'ma_golden_cross', name: 'MA golden cross', description: 'MA cross' },
        { id: 'chan_theory', name: 'Chan theory', description: 'Structure analysis' },
        { id: 'wave_theory', name: 'Elliott wave theory', description: 'Wave analysis' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: 'MA golden cross' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Chan theory' }));

    const wave = screen.getByRole('checkbox', { name: 'Elliott wave theory' });
    expect(wave).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox', { name: 'MA golden cross' }));
    expect(wave).not.toBeDisabled();
  });

  it('quick questions override the current multi-skill selection', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: 'Bullish trend (default)', description: 'Default trend' },
        { id: 'ma_golden_cross', name: 'MA golden cross', description: 'MA cross' },
        { id: 'chan_theory', name: 'Chan theory', description: 'Structure analysis' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: 'MA golden cross' }));
    fireEvent.click(screen.getByRole('button', { name: 'Analyze FPT with Chan theory' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Analyze FPT with Chan theory',
          skills: ['chan_theory'],
        }),
        expect.objectContaining({
          skillNames: ['Chan theory'],
          skillName: 'Chan theory',
        }),
      );
    });
    // Câu gợi ý nêu rõ mã (FPT) nên được gửi kèm ngữ cảnh mã đó.
    // The quick question names a ticker (FPT), so its stock context is sent.
    expect(mockStartStream.mock.calls.at(-1)?.[0]?.context).toEqual({ stock_code: 'FPT', stock_name: null });
  });

  it('keeps a quick question in the input until the server accepts it', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'chan_theory', name: 'Chan theory', description: 'Structure analysis' }],
      default_skill_id: 'chan_theory',
    });
    mockStartStream.mockResolvedValueOnce(undefined);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const quickQuestion = await screen.findByRole('button', { name: 'Analyze FPT with Chan theory' });
    await waitFor(() => expect(quickQuestion).toBeEnabled());
    fireEvent.click(quickQuestion);

    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));
    expect(screen.getByPlaceholderText(/Analyze FPT/)).toHaveValue('Analyze FPT with Chan theory');
  });

  it('submits the MWG quick question with an unambiguous Vietnamese stock context', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    mockGetSkills.mockResolvedValue({
      skills: [{ id: 'box_oscillation', name: 'Box-range oscillation', description: 'Range' }],
      default_skill_id: 'box_oscillation',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    await screen.findByText('Codex Agent · Experimental');
    fireEvent.click(await screen.findByRole('button', { name: 'Analyze MWG with box-range oscillation' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Analyze MWG with box-range oscillation',
          skills: ['box_oscillation'],
          context: {
            stock_code: 'MWG',
            stock_name: 'CTCP Đầu tư Thế Giới Di Động',
          },
        }),
        expect.objectContaining({
          skillNames: ['Box-range oscillation'],
          skillName: 'Box-range oscillation',
        }),
      );
    });
  });

  it('reuses the stock index for one unambiguous stock name', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.change(await screen.findByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Vinamilk có nên mua không?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          context: {
            stock_code: 'VNM',
            stock_name: 'Vinamilk',
          },
        }),
        expect.any(Object),
      );
    });
  });

  it('does not guess when one stock name maps to multiple markets', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.change(await screen.findByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Phân tích Masan' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({ context: undefined }),
        expect.any(Object),
      );
    });
  });

  it('keeps assistant message actions directly activatable in the DOM', async () => {
    mockStoreState.messages = [
      { id: 'assistant-1', role: 'assistant', content: 'Trend is strong', skillName: 'Bullish trend (default)' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const exportButton = await screen.findByRole('button', { name: 'Export this message as Markdown' });
    const actionGroup = exportButton.parentElement;

    expect(actionGroup).toHaveClass('chat-message-actions');
    expect(actionGroup?.className).not.toMatch(/pointer-events-none|opacity-0/);
  });

  it('sends exported markdown to notification channel and shows success feedback', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: 'Phân tích HPG' },
      { id: 'assistant-1', role: 'assistant', content: 'Trend is strong', skillName: 'Bullish trend (default)' },
    ];
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: false,
      experimental: true,
      errorCode: 'command_not_found',
      message: 'Codex was not found',
    });
    mockFormatSessionAsMarkdown.mockReturnValue('# exported markdown');

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Send to the configured notification bots / mailboxes' }));

    await waitFor(() => {
      expect(mockFormatSessionAsMarkdown).toHaveBeenCalledWith(mockStoreState.messages, 'en');
      expect(mockSendChat).toHaveBeenCalledWith('# exported markdown');
    });

    expect(await screen.findByText('Sent to the notification channels')).toBeInTheDocument();
  });

  it('shows parsed error feedback when notification delivery fails', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: 'Phân tích FPT' },
      { id: 'assistant-1', role: 'assistant', content: 'Short-term range', skillName: 'Bullish trend (default)' },
    ];
    mockSendChat.mockRejectedValue(
      createParsedApiError({
        title: 'Failed to send',
        message: 'Notification channel unavailable',
        category: 'unknown',
      }),
    );

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Send to the configured notification bots / mailboxes' }));

    expect(await screen.findByText('Notification channel unavailable')).toBeInTheDocument();
  });

  it('prevents duplicate notification sends while the request is in flight', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: 'Phân tích VCB' },
      { id: 'assistant-1', role: 'assistant', content: 'High volatility', skillName: 'Bullish trend (default)' },
    ];
    const deferred = createDeferred<{ success: boolean }>();
    mockSendChat.mockImplementation(() => deferred.promise);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const sendButton = await screen.findByRole('button', { name: 'Send to the configured notification bots / mailboxes' });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockSendChat).toHaveBeenCalledTimes(1);
      expect(sendButton).toBeDisabled();
    });

    fireEvent.click(sendButton);
    expect(mockSendChat).toHaveBeenCalledTimes(1);

    deferred.resolve({ success: true });

    await waitFor(() => {
      expect(sendButton).not.toBeDisabled();
    });
  });

  it('allows sending with base follow-up context before report hydration completes', async () => {
    const deferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();

    vi.mocked(historyApi.getDetail).mockImplementation(() => deferred.promise);

    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat&recordId=1']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    const sendButton = screen.getByRole('button', { name: /Send|Processing\.\.\./ });
    expect(sendButton).not.toBeDisabled();
    expect(screen.getByText('Loading the historical analysis context; you can already send a follow-up.')).toBeInTheDocument();

    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Give an in-depth analysis of Hoa Phat(HPG)',
          context: {
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });

    deferred.resolve({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: 'HPG',
        stockName: 'Hoa Phat',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: 'Trend continues',
        operationAdvice: 'Keep watching',
        trendPrediction: 'Choppy near highs',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading the historical analysis context; you can already send a follow-up.')).not.toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem khối lượng' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem khối lượng',
          context: expect.objectContaining({
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          }),
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Nếu không tính TTM thì sao' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Nếu không tính TTM thì sao',
          context: expect.objectContaining({
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          }),
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('uses hydrated report context when it finishes before sending', async () => {
    vi.mocked(historyApi.getDetail).mockResolvedValue({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: 'HPG',
        stockName: 'Hoa Phat',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: 'Trend continues',
        operationAdvice: 'Keep watching',
        trendPrediction: 'Choppy near highs',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat&recordId=1']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.queryByText('Loading the historical analysis context; you can already send a follow-up.')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Give an in-depth analysis of Hoa Phat(HPG)',
          context: expect.objectContaining({
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
            previous_price: 1523.6,
            previous_change_pct: 1.8,
            previous_strategy: expect.objectContaining({
              stopLoss: '1450',
            }),
          }),
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('falls back to base stock context when recordId is missing', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=FPT']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of FPT')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Give an in-depth analysis of FPT',
          context: {
            stock_code: 'FPT',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
    expect(historyApi.getDetail).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem định giá' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem định giá',
          context: {
            stock_code: 'FPT',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('switches active stock context for explicit switch messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Chuyển sang FPT xem' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Chuyển sang FPT xem',
          context: {
            stock_code: 'FPT',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it.each([
    ['Phân tích mã LAS'],
    ['LAS có nên mua không?'],
  ])('switches active stock context for Vietnamese switch message %s', async (message) => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HAG&name=HAG']}>
        <ChatPage />
      </MemoryRouter>
    );

    await screen.findByDisplayValue(/HAG/);

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: message },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message,
          context: {
            stock_code: 'LAS',
            stock_name: null,
          },
        }),
        expect.anything(),
      );
    });
  });

  it('keeps both stocks out of the active context for Vietnamese compare messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HAG&name=HAG']}>
        <ChatPage />
      </MemoryRouter>
    );

    await screen.findByDisplayValue(/HAG/);

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'So sánh HAG và LAS' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'So sánh HAG và LAS',
          context: expect.objectContaining({ stock_code: 'HAG' }),
        }),
        expect.anything(),
      );
    });
  });

  it('switches Codex stock context when an explicit switch names one stock', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Phân tích Vinamilk' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Phân tích Vinamilk',
          context: {
            stock_code: 'VNM',
            stock_name: 'Vinamilk',
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('switches to the single new stock when the current stock appears first', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Bỏ qua HPG, chuyển sang FPT xem' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Bỏ qua HPG, chuyển sang FPT xem',
          context: {
            stock_code: 'FPT',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem hỗ trợ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem hỗ trợ',
          context: {
            stock_code: 'FPT',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('keeps active stock context for compare messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'So sánh HPG và FPT' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'So sánh HPG và FPT',
          context: {
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('keeps active stock context for difference-style compare messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Analyze HPG và FPT khác nhau thế nào' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Analyze HPG và FPT khác nhau thế nào',
          context: {
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('keeps active stock context when the compared stock appears first', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Analyze FPT và HPG khác nhau thế nào' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Analyze FPT và HPG khác nhau thế nào',
          context: {
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('keeps active stock context for choice-style multi-stock messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'FPT hay VCB đáng mua hơn' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'FPT hay VCB đáng mua hơn',
          context: {
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('switches active stock context for single-stock difference phrasing', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Analyze lợi thế khác biệt của FPT' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Analyze lợi thế khác biệt của FPT',
          context: {
            stock_code: 'FPT',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('keeps active stock context when clicking the current session', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Switch to conversation Phân tích nhanh HPG' }));
    expect(mockSwitchSession).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem khối lượng' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem khối lượng',
          context: {
            stock_code: 'HPG',
            stock_name: 'Hoa Phat',
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('restores active stock context from loaded session messages', async () => {
    mockStoreState.messages = [
      { id: 'm-1', role: 'user', content: 'Phân tích HPG' },
      { id: 'm-2', role: 'assistant', content: 'HPG analysis result' },
      { id: 'm-3', role: 'user', content: 'Bỏ qua HPG, chuyển sang FPT xem' },
      { id: 'm-4', role: 'assistant', content: 'FPT analysis result' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByTestId('chat-workspace')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem hỗ trợ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem hỗ trợ',
          context: {
            stock_code: 'FPT',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('clears active stock context when starting a new chat or switching sessions', async () => {
    mockStoreState.sessions = [
      ...mockStoreState.sessions,
      {
        session_id: 'session-2',
        title: 'Old session',
        message_count: 1,
        created_at: '2026-03-16T09:00:00Z',
        last_active: '2026-03-16T09:05:00Z',
      },
    ];

    const { unmount } = render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Start a new conversation' }));
    expect(mockStartNewChat).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem khối lượng' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem khối lượng',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });

    unmount();
    mockStartStream.mockClear();

    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Switch to conversation Old session' }));
    expect(mockSwitchSession).toHaveBeenCalledWith('session-2');

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem khối lượng' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem khối lượng',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('clears active stock context when deleting the current session', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=HPG&name=Hoa%20Phat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete conversation Phân tích nhanh HPG' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(mockDeleteChatSession).toHaveBeenCalledWith('session-1');
    });
    expect(mockStartNewChat).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem khối lượng' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem khối lượng',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('ignores malformed follow-up query params', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=%3Cscript%3E&name=Bad%0AName&recordId=abc']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('heading', { name: 'Ask' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Analyze FPT/)).toHaveValue('');
    expect(historyApi.getDetail).not.toHaveBeenCalled();
  });

  it('reprocesses follow-up query params when navigating to the same chat route again', async () => {
    const firstDeferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();
    const secondDeferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();

    vi.mocked(historyApi.getDetail)
      .mockImplementationOnce(() => firstDeferred.promise)
      .mockImplementationOnce(() => secondDeferred.promise);

    const router = createMemoryRouter(
      [{ path: '/chat', element: <ChatPage /> }],
      {
        initialEntries: ['/chat?stock=HPG&name=Hoa%20Phat&recordId=1'],
      },
    );

    render(<RouterProvider router={router} />);

    expect(await screen.findByDisplayValue('Give an in-depth analysis of Hoa Phat(HPG)')).toBeInTheDocument();
    expect(screen.getByText('Loading the historical analysis context; you can already send a follow-up.')).toBeInTheDocument();

    await act(async () => {
      await router.navigate('/chat?stock=FPT&name=FPT%20Corp&recordId=2');
    });

    expect(await screen.findByDisplayValue('Give an in-depth analysis of FPT Corp(FPT)')).toBeInTheDocument();

    firstDeferred.resolve({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: 'HPG',
        stockName: 'Hoa Phat',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: 'Trend continues',
        operationAdvice: 'Keep watching',
        trendPrediction: 'Choppy near highs',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    secondDeferred.resolve({
      meta: {
        id: 2,
        queryId: 'q-2',
        stockCode: 'FPT',
        stockName: 'FPT Corp',
        reportType: 'detailed',
        createdAt: '2026-03-18T09:00:00Z',
        currentPrice: 211.5,
        changePct: 2.4,
      },
      summary: {
        analysisSummary: 'Trend strengthening',
        operationAdvice: 'Keep holding',
        trendPrediction: 'Short-term bullish',
        sentimentScore: 81,
      },
      strategy: {
        stopLoss: '205',
      },
    });

    await waitFor(() => {
      expect(screen.queryByText('Loading the historical analysis context; you can already send a follow-up.')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Give an in-depth analysis of FPT Corp(FPT)',
          context: expect.objectContaining({
            stock_code: 'FPT',
            stock_name: 'FPT Corp',
            previous_price: 211.5,
            previous_change_pct: 2.4,
            previous_strategy: expect.objectContaining({
              stopLoss: '205',
            }),
          }),
        }),
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it.each([
    ['VN30', 'VN30'],
    ['vn30-index', 'VN30'],
    ['VNIndex', 'VNINDEX'],
  ] as const)('restores the %s follow-up URL to the registry canonical and hides the stock-only watchlist action', async (stockParam, expectedCanonical) => {
    render(
      <MemoryRouter initialEntries={[`/chat?stock=${stockParam}`]}>
        <ChatPage />
      </MemoryRouter>,
    );

    const expectedPrompt = `Give an in-depth analysis of ${expectedCanonical}`;
    expect(await screen.findByDisplayValue(expectedPrompt)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: expectedPrompt,
          context: {
            stock_code: expectedCanonical,
            stock_name: null,
          },
        }),
        expect.any(Object),
      );
    });

    // The index canonical hides the stock-only watchlist action immediately.
    expect(screen.queryByText('Add to watchlist')).not.toBeInTheDocument();
    expect(screen.queryByText('Remove from watchlist')).not.toBeInTheDocument();
  });

  it('defers a default-backend index follow-up until the registry settles', async () => {
    mockStockIndexState.index = [];
    mockStockIndexState.loading = true;
    mockStockIndexState.loaded = false;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat?stock=VN30']}>
        <ChatPage />
      </MemoryRouter>,
    );
    expect(screen.queryByDisplayValue(/Give an in-depth analysis/)).not.toBeInTheDocument();

    mockStockIndexState.index = mockStockIndex;
    mockStockIndexState.loading = false;
    mockStockIndexState.loaded = true;
    rerender(
      <MemoryRouter initialEntries={['/chat?stock=VN30']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByDisplayValue('Give an in-depth analysis of VN30')).toBeInTheDocument();
  });

  type RegistrySettleRow = [
    string,
    { index: typeof mockStockIndexState.index; error: Error | null; fallback: boolean },
    string,
  ];

  it.each<RegistrySettleRow>([
    [
      'success settle with index data',
      { index: mockStockIndex, error: null, fallback: false },
      'Give an in-depth analysis of VN30',
    ],
    [
      'explicit load failure fail-open',
      { index: [], error: new Error('registry unavailable'), fallback: true },
      'Give an in-depth analysis of VN30',
    ],
    [
      'successful-empty registry fail-open',
      { index: [], error: null, fallback: false },
      'Give an in-depth analysis of VN30',
    ],
  ])(
    'releases the default-backend index follow-up after the shared registry settles: %s',
    async (_scenario, finalRegistry, expectedPrompt) => {
      mockStockIndexState.index = [];
      mockStockIndexState.loading = true;
      mockStockIndexState.error = null;
      mockStockIndexState.fallback = false;
      mockStockIndexState.loaded = false;

      const { rerender } = render(
        <MemoryRouter initialEntries={['/chat?stock=VN30']}>
          <ChatPage />
        </MemoryRouter>,
      );

      await waitFor(() => {
        expect(screen.queryByDisplayValue(/Give an in-depth analysis of VN30/)).not.toBeInTheDocument();
      });

      // Settle regardless of outcome: success consumes the registry canonical;
      // failure and an empty registry fail open to the stock path.
      mockStockIndexState.index = finalRegistry.index;
      mockStockIndexState.loading = false;
      mockStockIndexState.error = finalRegistry.error;
      mockStockIndexState.fallback = finalRegistry.fallback;
      mockStockIndexState.loaded = true;
      rerender(
        <MemoryRouter initialEntries={['/chat?stock=VN30']}>
          <ChatPage />
        </MemoryRouter>,
      );

      expect(await screen.findByDisplayValue(expectedPrompt)).toBeInTheDocument();
    },
  );

  it('restores the active Codex index canonical from a loaded session message and hides the stock-only watchlist action', async () => {
    mockGetStatus.mockResolvedValueOnce({
      backend: 'codex_app_server',
      available: true,
      experimental: true,
      errorCode: null,
      message: null,
    });
    // Registry already settled with index data before the session loads — the
    // approved message-restore path must resolve the explicit VN index
    // canonical so the follow-up context and watchlist gating stay consistent.
    mockStockIndexState.index = mockStockIndex;
    mockStockIndexState.loading = false;
    mockStockIndexState.error = null;
    mockStockIndexState.fallback = false;
    mockStockIndexState.loaded = true;
    mockStoreState.messages = [
      { id: 'm-1', role: 'user', content: 'Analyze VN30' },
      { id: 'm-2', role: 'assistant', content: 'VN30 analysis result', skillName: 'Index analysis' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('chat-workspace')).toBeInTheDocument();

    // Restored canonical keeps the registry index identity → the stock-only
    // watchlist button is hidden, exactly like a direct index follow-up.
    expect(screen.queryByText('Add to watchlist')).not.toBeInTheDocument();
    expect(screen.queryByText('Remove from watchlist')).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/Analyze FPT/), {
      target: { value: 'Tiếp tục xem hỗ trợ của VN30' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'Tiếp tục xem hỗ trợ của VN30',
          context: {
            stock_code: 'VN30',
            stock_name: null,
          },
        }),
        // The send meta uses the session's default skill, not the historical
        // assistant message's skill label.
        expect.objectContaining({
          skillName: 'Bullish trend (default)',
        }),
      );
    });
  });

  it('defers default-backend history restoration until the registry settles', async () => {
    mockStockIndexState.index = [];
    mockStockIndexState.loading = true;
    mockStockIndexState.loaded = false;
    mockStoreState.messages = [
      { id: 'm-1', role: 'user', content: 'Analyze VN30' },
      { id: 'm-2', role: 'assistant', content: 'VN30 analysis result', skillName: 'Index analysis' },
    ];

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: 'Processing...' })).toBeDisabled();

    mockStockIndexState.index = mockStockIndex;
    mockStockIndexState.loading = false;
    mockStockIndexState.loaded = true;
    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const input = screen.getByPlaceholderText(/Analyze FPT/);
    fireEvent.change(input, { target: { value: 'Tiếp tục xem hỗ trợ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          context: { stock_code: 'VN30', stock_name: null },
        }),
        expect.any(Object),
      );
    });
  });

  it('shows a jump-to-latest action when new content arrives while the user is away from bottom', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: 'Phân tích HPG' },
      { id: 'assistant-1', role: 'assistant', content: 'Trend is strong', skillName: 'Bullish trend (default)' },
    ];

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const viewport = await screen.findByTestId('chat-message-scroll');
    Object.defineProperty(viewport, 'scrollTop', { configurable: true, value: 0 });
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: 400 });
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 1200 });

    fireEvent.scroll(viewport);

    mockStoreState.messages = [
      ...mockStoreState.messages,
      { id: 'assistant-2', role: 'assistant', content: 'New follow-up analysis', skillName: 'Bullish trend (default)' },
    ];

    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const jumpButton = await screen.findByRole('button', { name: 'Jump to the latest message' });
    expect(jumpButton).toBeInTheDocument();

    fireEvent.click(jumpButton);

    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });
});

describe('extractStockCodeFromMessage', () => {
  it('returns a 3-character Vietnam ticker', () => {
    expect(extractStockCodeFromMessage('Phân tích HPG xu hướng')).toBe('HPG');
    expect(extractStockCodeFromMessage('FPT')).toBe('FPT');
  });

  it('normalizes the explicit .VN suffix', () => {
    expect(extractStockCodeFromMessage('Xem HPG.VN')).toBe('HPG');
    expect(extractStockCodeFromMessage('FUEVN100.VN')).toBe('FUEVN100');
  });

  it('returns Vietnam fund certificate codes', () => {
    expect(extractStockCodeFromMessage('Phân tích FUEVN100')).toBe('FUEVN100');
    expect(extractStockCodeFromMessage('E1VFVN30 thế nào')).toBe('E1VFVN30');
  });

  it('returns Vietnam index symbols', () => {
    expect(extractStockCodeFromMessage('VNINDEX hôm nay')).toBe('VNINDEX');
    expect(extractStockCodeFromMessage('Rổ VN30 ra sao')).toBe('VN30');
  });

  it('does NOT return finance abbreviations as tickers', () => {
    expect(extractStockCodeFromMessage('Nếu không tính TTM thì sao')).toBeNull();
    expect(extractStockCodeFromMessage('Chỉ số RSI thế nào')).toBeNull();
    expect(extractStockCodeFromMessage('MACD đã cắt lên chưa')).toBeNull();
    expect(extractStockCodeFromMessage('WHAT IS PE')).toBeNull();
    expect(extractStockCodeFromMessage('USD tăng mạnh')).toBeNull();
    expect(extractStockCodeFromMessage('Mua ETF nào')).toBeNull();
  });

  it('does NOT return moving-average or indicator tokens as tickers', () => {
    expect(extractStockCodeFromMessage('Đường MA20 cắt lên')).toBeNull();
    expect(extractStockCodesFromMessage('MA và RSI nên xem thế nào')).toEqual([]);
    expect(extractStockCodeFromMessage('Chỉ báo KDJ thế nào')).toBeNull();
  });

  it('skips finance abbreviations before a real ticker', () => {
    expect(extractStockCodeFromMessage('TTM HPG thế nào')).toBe('HPG');
    expect(extractStockCodeFromMessage('RSI FPT thế nào')).toBe('FPT');
    expect(extractStockCodeFromMessage('WHAT IS PE VCB')).toBe('VCB');
  });

  it('returns null for messages without stock codes', () => {
    expect(extractStockCodeFromMessage('Thị trường hôm nay thế nào')).toBeNull();
    expect(extractStockCodeFromMessage('Có nên mua cổ phiếu ngân hàng không')).toBeNull();
  });

  it('returns all stock codes in message order', () => {
    expect(extractStockCodesFromMessage('So sánh HPG và FPT')).toEqual(['HPG', 'FPT']);
    expect(extractStockCodesFromMessage('FPT hay HPG tốt hơn')).toEqual(['FPT', 'HPG']);
    expect(extractStockCodesFromMessage('So sánh HPG.VN và FUEVN100')).toEqual(['HPG', 'FUEVN100']);
  });

  it('deduplicates suffix variants of the same ticker', () => {
    expect(extractStockCodesFromMessage('HPG và HPG.VN')).toEqual(['HPG']);
  });

  it('does not extract lower-case words as tickers', () => {
    expect(extractStockCodesFromMessage('phân tích hpg giúp tôi')).toEqual([]);
    expect(extractStockCodesFromMessage('xem mua nen')).toEqual([]);
  });

  it('does not return denied abbreviations in multi-code extraction', () => {
    expect(extractStockCodesFromMessage('Nếu không tính TTM và PE')).toEqual([]);
    expect(extractStockCodesFromMessage('MACD HPG và RSI')).toEqual(['HPG']);
  });
});

describe('extractStockCodesFromMessage with index registry', () => {
  // The hoisted mock widens market/assetType to plain strings; the extraction
  // parameter is StockIndexItem[], so a structural cast keeps the registry rows.
  const registeredIndex = mockStockIndex as unknown as StockIndexItem[];

  it('resolves registered Vietnam index symbols to the registry canonical', () => {
    expect(extractStockCodesFromMessage('Phân tích VNINDEX', registeredIndex)).toEqual(['VNINDEX']);
    expect(extractStockCodesFromMessage('Phân tích VN30', registeredIndex)).toEqual(['VN30']);
  });

  it('keeps stocks and indices distinct in the same message', () => {
    expect(extractStockCodesFromMessage('HPG.VN và VN30', registeredIndex)).toEqual(['HPG', 'VN30']);
    expect(extractStockCodesFromMessage('VNINDEX và FPT', registeredIndex)).toEqual(['VNINDEX', 'FPT']);
  });

  it('fails open to the plain code path when no registry is passed', () => {
    expect(extractStockCodesFromMessage('Phân tích VN30')).toEqual(['VN30']);
    expect(extractStockCodesFromMessage('Phân tích HPG')).toEqual(['HPG']);
  });
});

describe('watchlist button with code variants', () => {
  it('shows the remove action when the canonical code is in the watchlist and the user inputs a variant', async () => {
    mockGetWatchlist.mockResolvedValue(['HPG', 'FPT']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/e\.g\./);
    fireEvent.change(textarea, { target: { value: 'Analyze HPG.VN' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('Remove from watchlist')).toBeInTheDocument();
  });

  it('matches raw suffixed watchlist entries before rendering the watchlist action', async () => {
    mockGetWatchlist.mockResolvedValue(['FPT.VN']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/e\.g\./);
    fireEvent.change(textarea, { target: { value: 'Analyze FPT' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('Remove from watchlist')).toBeInTheDocument();
  });

  it('removes the matched raw watchlist entry instead of adding a duplicate variant', async () => {
    mockGetWatchlist.mockResolvedValue(['VCB.VN']);
    mockRemoveFromWatchlist.mockResolvedValue([]);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/e\.g\./);
    fireEvent.change(textarea, { target: { value: 'Analyze VCB' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    fireEvent.click(await screen.findByText('Remove from watchlist'));

    await waitFor(() => {
      expect(mockRemoveFromWatchlist).toHaveBeenCalledWith('VCB.VN');
    });
    expect(mockAddToWatchlist).not.toHaveBeenCalled();
  });
});
