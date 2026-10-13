import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../utils/uiLanguage';
import BacktestPage from '../BacktestPage';

const {
  mockGetResults,
  mockGetOverallPerformance,
  mockGetStockPerformance,
  mockRun,
} = vi.hoisted(() => ({
  mockGetResults: vi.fn(),
  mockGetOverallPerformance: vi.fn(),
  mockGetStockPerformance: vi.fn(),
  mockRun: vi.fn(),
}));

vi.mock('../../api/backtest', () => ({
  backtestApi: {
    getResults: mockGetResults,
    getOverallPerformance: mockGetOverallPerformance,
    getStockPerformance: mockGetStockPerformance,
    run: mockRun,
  },
}));

const basePerformance = {
  scope: 'overall',
  evalWindowDays: 10,
  engineVersion: 'test-engine',
  totalEvaluations: 3,
  completedCount: 2,
  insufficientCount: 1,
  longCount: 2,
  cashCount: 1,
  winCount: 1,
  lossCount: 1,
  neutralCount: 0,
  directionAccuracyPct: 66.7,
  winRatePct: 50,
  neutralRatePct: 0,
  avgStockReturnPct: 2.4,
  avgSimulatedReturnPct: 1.2,
  stopLossTriggerRate: 10,
  takeProfitTriggerRate: 20,
  ambiguousRate: 0,
  avgDaysToFirstHit: 3.5,
  adviceBreakdown: {},
  diagnostics: {},
};

const baseResultItem = {
  analysisHistoryId: 101,
  code: 'FPT',
  stockName: 'FPT Corp',
  analysisDate: '2026-03-20',
  evalWindowDays: 10,
  engineVersion: 'test-engine',
  evalStatus: 'completed',
  operationAdvice: 'Tiếp tục nắm giữ',
  action: 'watch',
  actionLabel: 'Watch',
  trendPrediction: 'Dao động tích cực',
  actualMovement: 'up',
  actualReturnPct: 3.8,
  directionExpected: 'long',
  directionCorrect: true,
  outcome: 'win',
  simulatedReturnPct: 3.8,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockGetOverallPerformance.mockResolvedValue(basePerformance);
  mockGetStockPerformance.mockResolvedValue(null);
  mockGetResults.mockResolvedValue({
    total: 1,
    page: 1,
    limit: 20,
    items: [baseResultItem],
  });
  mockRun.mockResolvedValue({
    processed: 1,
    saved: 1,
    completed: 1,
    insufficient: 0,
    errors: 0,
  });
});

describe('BacktestPage', () => {
  function renderEnglishPage() {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    render(
      <UiLanguageProvider>
        <BacktestPage />
      </UiLanguageProvider>,
    );
  }

  it('renders shared surface inputs and prediction tracking outputs', async () => {
    render(<BacktestPage />);

    const filterInput = await screen.findByPlaceholderText('Filter by stock code (leave empty for all)');
    const windowInput = screen.getByPlaceholderText('10');

    expect(filterInput).toHaveClass('input-surface');
    expect(filterInput).toHaveClass('input-focus-glow');
    expect(windowInput).toHaveClass('input-surface');
    expect(windowInput).toHaveClass('input-focus-glow');

    expect(await screen.findByText('Win')).toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('FPT')).toBeInTheDocument();
    expect(screen.getByText('FPT Corp')).toBeInTheDocument();
    const resultRow = screen.getByText('FPT').closest('tr');
    expect(resultRow).not.toBeNull();
    const rowScope = within(resultRow as HTMLElement);
    expect(rowScope.getByText('Watch')).toBeInTheDocument();
    expect(rowScope.getByText('Dao động tích cực')).toBeInTheDocument();
    expect(rowScope.getByText('Tiếp tục nắm giữ')).toBeInTheDocument();
    expect(screen.getByText('Up')).toBeInTheDocument();
    expect(screen.getByText('Window return')).toBeInTheDocument();
    expect(screen.getByText('Direction match')).toBeInTheDocument();
    expect(screen.getByText('Long')).toBeInTheDocument();
    expect(screen.getAllByLabelText('Yes').length).toBeGreaterThan(0);
    expect(screen.getByText('Direction accuracy')).toBeInTheDocument();
    expect(screen.getByText('Avg simulated return')).toBeInTheDocument();
  });

  it('falls back to the taxonomy label when backtest actionLabel is missing', async () => {
    mockGetResults.mockResolvedValueOnce({
      total: 1,
      page: 1,
      limit: 20,
      items: [
        {
          ...baseResultItem,
          action: 'watch',
          actionLabel: null,
        },
      ],
    });

    render(<BacktestPage />);

    const codeCell = await screen.findByText('FPT');
    const resultRow = codeCell.closest('tr');
    expect(resultRow).not.toBeNull();
    const rowScope = within(resultRow as HTMLElement);
    expect(rowScope.getByText('Watch')).toBeInTheDocument();
    expect(rowScope.getByText('Tiếp tục nắm giữ')).toBeInTheDocument();
  });

  it('uses localized taxonomy labels before server labels in English UI mode', async () => {
    mockGetResults.mockResolvedValueOnce({
      total: 1,
      page: 1,
      limit: 20,
      items: [
        {
          ...baseResultItem,
          operationAdvice: 'continue holding',
          action: 'watch',
          actionLabel: 'Theo dõi',
          trendPrediction: 'range-bound',
        },
      ],
    });

    renderEnglishPage();

    const codeCell = await screen.findByText('FPT');
    const resultRow = codeCell.closest('tr');
    expect(resultRow).not.toBeNull();
    const rowScope = within(resultRow as HTMLElement);
    expect(rowScope.getByText('Watch')).toBeInTheDocument();
    expect(rowScope.getByText('continue holding')).toBeInTheDocument();
    expect(rowScope.queryByText('Theo dõi')).not.toBeInTheDocument();
  });

  it('keeps operation advice visible when backtest action fields are absent for multi-guard advice', async () => {
    mockGetResults.mockResolvedValueOnce({
      total: 1,
      page: 1,
      limit: 20,
      items: [
        {
          ...baseResultItem,
          operationAdvice: 'risk alert, avoid buying',
          action: null,
          actionLabel: null,
        },
      ],
    });

    render(<BacktestPage />);

    const codeCell = await screen.findByText('FPT');
    const resultRow = codeCell.closest('tr');
    expect(resultRow).not.toBeNull();
    const rowScope = within(resultRow as HTMLElement);
    expect(rowScope.getByText('Dao động tích cực')).toBeInTheDocument();
    expect(rowScope.getByText('risk alert, avoid buying')).toBeInTheDocument();
    expect(rowScope.queryByText('Avoid')).not.toBeInTheDocument();
    expect(rowScope.queryByText('Alert')).not.toBeInTheDocument();
  });

  it('renders backtest controls and result headings in English UI mode', async () => {
    renderEnglishPage();

    expect(await screen.findByPlaceholderText('Filter by stock code (leave empty for all)')).toBeInTheDocument();
    expect(screen.getByText('Evaluation window')).toBeInTheDocument();
    expect(screen.getAllByText('Phase').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Run backtest' })).toBeInTheDocument();
    expect(screen.getByText('Window return')).toBeInTheDocument();
    expect(screen.getByText('Direction match')).toBeInTheDocument();
    expect(screen.getByText('Direction accuracy')).toBeInTheDocument();
    expect(screen.queryByText('Chạy backtest')).not.toBeInTheDocument();
    expect(screen.queryByText('Lợi suất trong kỳ')).not.toBeInTheDocument();
  });

  it('filters results with stock code, window, phase, and analysis date range when clicking Filter', async () => {
    render(<BacktestPage />);

    const filterInput = await screen.findByPlaceholderText('Filter by stock code (leave empty for all)');
    const windowInput = screen.getByPlaceholderText('10');
    const phaseSelect = screen.getByDisplayValue('All phases');
    const fromInput = screen.getByLabelText('Analysis start date');
    const toInput = screen.getByLabelText('Analysis end date');

    fireEvent.change(filterInput, { target: { value: 'vcb' } });
    fireEvent.change(windowInput, { target: { value: '20' } });
    fireEvent.change(phaseSelect, { target: { value: 'intraday' } });
    fireEvent.change(fromInput, { target: { value: '2026-03-01' } });
    fireEvent.change(toInput, { target: { value: '2026-03-31' } });
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenLastCalledWith({
        code: 'VCB',
        evalWindowDays: 20,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: 'intraday',
        page: 1,
        limit: 20,
      });
      expect(mockGetStockPerformance).toHaveBeenLastCalledWith('VCB', {
        evalWindowDays: 20,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: 'intraday',
      });
    });
  });

  it('runs a backtest and refreshes results using the shared filter values', async () => {
    mockRun.mockResolvedValueOnce({
      processed: 0,
      saved: 0,
      completed: 0,
      insufficient: 0,
      errors: 0,
      message: 'Không tìm thấy bản ghi phân tích phù hợp',
      diagnostics: { emptyReason: 'no_matching_analysis' },
    });
    render(<BacktestPage />);

    const filterInput = await screen.findByPlaceholderText('Filter by stock code (leave empty for all)');
    const windowInput = screen.getByPlaceholderText('10');
    const fromInput = screen.getByLabelText('Analysis start date');
    const toInput = screen.getByLabelText('Analysis end date');

    fireEvent.change(filterInput, { target: { value: 'HPG.VN' } });
    fireEvent.change(windowInput, { target: { value: '15' } });
    fireEvent.change(fromInput, { target: { value: '2026-03-01' } });
    fireEvent.change(toInput, { target: { value: '2026-03-31' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run backtest' }));

    await waitFor(() => {
      expect(mockRun).toHaveBeenCalledWith({
        code: 'HPG.VN',
        force: undefined,
        minAgeDays: undefined,
        evalWindowDays: 15,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
      });
    });

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenLastCalledWith({
        code: 'HPG.VN',
        evalWindowDays: 15,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: undefined,
        page: 1,
        limit: 20,
      });
      expect(mockGetStockPerformance).toHaveBeenLastCalledWith('HPG.VN', {
        evalWindowDays: 15,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: undefined,
      });
    });

    expect(await screen.findByText('Processed:')).toBeInTheDocument();
    expect(screen.getByText('Saved:')).toBeInTheDocument();
    expect(screen.getByText('Không tìm thấy bản ghi phân tích phù hợp')).toBeInTheDocument();
  });

  it('uses backend-applied eval window when run input is empty', async () => {
    mockRun.mockResolvedValueOnce({
      processed: 0,
      saved: 0,
      completed: 0,
      insufficient: 0,
      errors: 0,
      appliedEvalWindowDays: 10,
      message: 'Không tìm thấy bản ghi phân tích phù hợp',
      diagnostics: { emptyReason: 'no_matching_analysis' },
    });
    render(<BacktestPage />);

    const filterInput = await screen.findByPlaceholderText('Filter by stock code (leave empty for all)');
    const windowInput = screen.getByPlaceholderText('10');
    const fromInput = screen.getByLabelText('Analysis start date');
    const toInput = screen.getByLabelText('Analysis end date');

    fireEvent.change(filterInput, { target: { value: 'HPG.VN' } });
    fireEvent.change(windowInput, { target: { value: '' } });
    fireEvent.change(fromInput, { target: { value: '2026-03-01' } });
    fireEvent.change(toInput, { target: { value: '2026-03-31' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run backtest' }));

    await waitFor(() => {
      expect(mockRun).toHaveBeenCalledWith({
        code: 'HPG.VN',
        force: undefined,
        minAgeDays: undefined,
        evalWindowDays: undefined,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
      });
    });

    await waitFor(() => {
      expect(windowInput).toHaveValue(10);
      expect(mockGetResults).toHaveBeenLastCalledWith({
        code: 'HPG.VN',
        evalWindowDays: 10,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: undefined,
        page: 1,
        limit: 20,
      });
      expect(mockGetStockPerformance).toHaveBeenLastCalledWith('HPG.VN', {
        evalWindowDays: 10,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: undefined,
      });
      expect(mockGetOverallPerformance).toHaveBeenLastCalledWith({
        evalWindowDays: 10,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: undefined,
      });
    });

    expect(await screen.findByText('Không tìm thấy bản ghi phân tích phù hợp')).toBeInTheDocument();
  });

  it('switches to next-day validation with the 1D shortcut', async () => {
    render(<BacktestPage />);

    await screen.findByPlaceholderText('Filter by stock code (leave empty for all)');
    fireEvent.click(screen.getByRole('button', { name: '1D validation' }));

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenLastCalledWith({
        code: undefined,
        evalWindowDays: 1,
        analysisDateFrom: undefined,
        analysisDateTo: undefined,
        analysisPhase: undefined,
        page: 1,
        limit: 20,
      });
      expect(mockGetOverallPerformance).toHaveBeenLastCalledWith({
        evalWindowDays: 1,
        analysisDateFrom: undefined,
        analysisDateTo: undefined,
        analysisPhase: undefined,
      });
    });

    expect(screen.getByText('Actual performance')).toBeInTheDocument();
    expect(screen.getByText('Accuracy')).toBeInTheDocument();
    expect(screen.getByText('1D validation checks AI predictions against the next trading day close.')).toBeInTheDocument();
  });
});
