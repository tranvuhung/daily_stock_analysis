import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AlertsPage from '../AlertsPage';
import { useAlertStore } from '../../stores/alertStore';
import { act } from 'react';

const {
  listRules,
  createRule,
  deleteRule,
  enableRule,
  disableRule,
  testRule,
  listTriggers,
  listNotifications,
  listLiveQuotes,
  updateRule,
} = vi.hoisted(() => ({
  listRules: vi.fn(),
  createRule: vi.fn(),
  deleteRule: vi.fn(),
  enableRule: vi.fn(),
  disableRule: vi.fn(),
  testRule: vi.fn(),
  listTriggers: vi.fn(),
  listNotifications: vi.fn(),
  listLiveQuotes: vi.fn(),
  updateRule: vi.fn(),
}));

vi.mock('../../api/alerts', () => ({
  alertsApi: {
    listRules,
    createRule,
    deleteRule,
    enableRule,
    disableRule,
    testRule,
    listTriggers,
    listNotifications,
    listLiveQuotes,
    updateRule,
    listMarketRegions: vi.fn().mockResolvedValue({ regions: ['vn'], default: 'vn' }),
  },
}));

vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    getAccounts: vi.fn().mockResolvedValue({ accounts: [] }),
  },
}));

const parsedError = {
  title: 'tải thất bại',
  message: 'Alert API unavailable',
  rawMessage: 'Alert API unavailable',
  category: 'http_error' as const,
  status: 500,
};

const rule = {
  id: 1,
  name: 'HPG price breakout',
  targetScope: 'single_symbol' as const,
  target: 'HPG',
  alertType: 'price_cross' as const,
  parameters: { direction: 'above' as const, price: 1800 },
  severity: 'warning' as const,
  enabled: true,
  source: 'api',
  createdAt: '2026-05-18T09:00:00',
  updatedAt: '2026-05-18T09:30:00',
};

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  listRules.mockResolvedValue({ items: [rule], total: 1, page: 1, pageSize: 20 });
  listTriggers.mockResolvedValue({
    items: [
      {
        id: 10,
        ruleId: 1,
        target: 'HPG',
        observedValue: 1801,
        threshold: 1800,
        reason: 'HPG price above 1800',
        dataSource: 'realtime_quote',
        dataTimestamp: '2026-05-18T09:30:00',
        triggeredAt: '2026-05-18T09:30:01',
        status: 'triggered',
      },
    ],
    total: 1,
    page: 1,
    pageSize: 20,
  });
  listNotifications.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
  listLiveQuotes.mockResolvedValue({
    feed: { enabled: true, connected: true, inSession: true },
    quotes: [{ target: 'HPG', symbol: 'HPG', price: 1750, changePct: 1.25, source: 'tvsi_mqtt' }],
  });
  useAlertStore.setState({ unreadCount: 0, toasts: [], livePrices: {}, feedStatus: null, triggerVersion: 0 });
  testRule.mockResolvedValue({
    ruleId: 1,
    status: 'triggered',
    triggered: true,
    observedValue: 1801,
    message: 'HPG price above 1800',
  });
  createRule.mockResolvedValue(rule);
  disableRule.mockResolvedValue({ ...rule, enabled: false });
  enableRule.mockResolvedValue(rule);
  deleteRule.mockResolvedValue({ deleted: 1 });
});

describe('AlertsPage', () => {
  it('loads rules, trigger history, and notification empty state', async () => {
    render(<AlertsPage />);

    expect(screen.getByText('Manage event alerts, daily technical indicators, watchlist, portfolio/account linkage and market traffic-light rules; run one-off tests and review the trigger history recorded by the background evaluation job.')).toBeInTheDocument();
    expect(await screen.findByText('HPG price breakout')).toBeInTheDocument();
    expect(await screen.findByText('HPG price above 1800')).toBeInTheDocument();
    expect(await screen.findByText('No notification attempts yet')).toBeInTheDocument();
    expect(listRules).toHaveBeenCalledWith({
      enabled: undefined,
      alertType: undefined,
      page: 1,
      pageSize: 20,
    });
    expect(listTriggers).toHaveBeenCalledWith({ page: 1, pageSize: 20 });
    expect(listNotifications).toHaveBeenCalledWith({ page: 1, pageSize: 20 });
  });

  it('runs a dry-run test and renders only declared response fields', async () => {
    listTriggers.mockResolvedValueOnce({ items: [], total: 0, page: 1, pageSize: 20 });
    render(<AlertsPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Test' }));

    await waitFor(() => expect(testRule).toHaveBeenCalledWith(1));
    expect(await screen.findByText('Test result')).toBeInTheDocument();
    expect(screen.getByText(/HPG price above 1800/)).toBeInTheDocument();
    expect(screen.getByText(/observed: 1801/)).toBeInTheDocument();
    expect(screen.queryByText(/realtime_quote/)).not.toBeInTheDocument();
  });

  it('renders batch dry-run summary and target results', async () => {
    testRule.mockResolvedValueOnce({
      ruleId: 1,
      targetScope: 'watchlist',
      status: 'triggered',
      triggered: true,
      observedValue: 11,
      message: 'Evaluated 2 targets',
      evaluatedCount: 2,
      triggeredCount: 1,
      degradedCount: 1,
      skippedCount: 0,
      targetResults: [
        {
          target: 'HPG',
          displayTarget: 'Watchlist - HPG',
          status: 'triggered',
          recordStatus: 'triggered',
          triggered: true,
          observedValue: 11,
          message: 'triggered',
        },
        {
          target: 'VCB',
          displayTarget: 'Watchlist - VCB',
          status: 'not_triggered',
          recordStatus: 'degraded',
          triggered: false,
          observedValue: null,
          message: 'degraded',
        },
      ],
    });
    render(<AlertsPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Test' }));

    expect(await screen.findByText(/Evaluated 2 · triggered 1 · degraded 1 · skipped 0/)).toBeInTheDocument();
    expect(screen.getByText('Watchlist - HPG')).toBeInTheDocument();
    expect(screen.getByText(/not_triggered \/ degraded/)).toBeInTheDocument();
  });

  it('creates a rule through the page form and reloads rules', async () => {
    render(<AlertsPage />);

    await screen.findByText('HPG price breakout');
    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'fpt' } });
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(createRule).toHaveBeenCalledWith(expect.objectContaining({
        target: 'FPT',
        alertType: 'price_cross',
        parameters: { direction: 'above', price: 200 },
      }));
    });
    expect(await screen.findByText(/Alert rule .* created/)).toBeInTheDocument();
  });

  it('keeps create form values when create API fails', async () => {
    createRule.mockRejectedValueOnce({ parsedError });
    render(<AlertsPage />);

    await screen.findByText('HPG price breakout');
    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'fpt' } });
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    expect(await screen.findByText('tải thất bại')).toBeInTheDocument();
    expect(screen.getByLabelText('Symbol')).toHaveValue('fpt');
    expect(screen.getByLabelText('Price threshold')).toHaveValue(200);
  });

  it('clamps rules pagination when a mutation leaves the current page empty', async () => {
    const page2Rule = { ...rule, id: 2, name: 'Page two rule', target: 'FPT' };
    listRules
      .mockResolvedValueOnce({ items: [rule], total: 21, page: 1, pageSize: 20 })
      .mockResolvedValueOnce({ items: [page2Rule], total: 21, page: 2, pageSize: 20 })
      .mockResolvedValueOnce({ items: [], total: 20, page: 2, pageSize: 20 })
      .mockResolvedValue({ items: [rule], total: 20, page: 1, pageSize: 20 });

    render(<AlertsPage />);

    expect(await screen.findByText('HPG price breakout')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '2' }));
    expect(await screen.findByText('Page two rule')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Delete Page two rule'));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(deleteRule).toHaveBeenCalledWith(2));
    await waitFor(() => {
      expect(listRules).toHaveBeenCalledWith({
        enabled: undefined,
        alertType: undefined,
        page: 1,
        pageSize: 20,
      });
    });
    expect(await screen.findByText('HPG price breakout')).toBeInTheDocument();
  });

  it('keeps the latest rules response when filter requests resolve out of order', async () => {
    const initialRequest = createDeferred<{ items: Array<typeof rule>; total: number; page: number; pageSize: number }>();
    const filteredRequest = createDeferred<{ items: Array<typeof rule>; total: number; page: number; pageSize: number }>();
    const staleRule = { ...rule, id: 3, name: 'Stale filter rule', enabled: true };
    const filteredRule = { ...rule, id: 4, name: 'Disabled rule', enabled: false };
    listRules
      .mockReset()
      .mockReturnValueOnce(initialRequest.promise)
      .mockReturnValueOnce(filteredRequest.promise);

    render(<AlertsPage />);

    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'disabled' } });
    await waitFor(() => expect(listRules).toHaveBeenCalledTimes(2));

    filteredRequest.resolve({ items: [filteredRule], total: 1, page: 1, pageSize: 20 });
    expect(await screen.findByText('Disabled rule')).toBeInTheDocument();

    initialRequest.resolve({ items: [staleRule], total: 1, page: 1, pageSize: 20 });
    await waitFor(() => expect(screen.queryByText('Stale filter rule')).not.toBeInTheDocument());
    expect(screen.getByText('Disabled rule')).toBeInTheDocument();
  });

  it('renders API errors through ApiErrorAlert', async () => {
    listRules.mockRejectedValueOnce({ parsedError });

    render(<AlertsPage />);

    expect(await screen.findByText('tải thất bại')).toBeInTheDocument();
    expect(screen.getByText('Alert API unavailable')).toBeInTheDocument();
  });

  it('shows live prices, feed status and reloads history on realtime alerts', async () => {
    useAlertStore.setState({ unreadCount: 3 });
    render(<AlertsPage />);

    const cell = await screen.findByTestId('live-price-1');
    expect(cell).toHaveTextContent('1.750');
    expect(cell).toHaveTextContent('+1.25%');
    expect(cell).toHaveTextContent('+2.86%');
    expect(screen.getByTestId('alert-feed-status')).toHaveTextContent('connected');
    await waitFor(() => expect(useAlertStore.getState().unreadCount).toBe(0));
    await waitFor(() => expect(listTriggers).toHaveBeenCalledTimes(1));

    act(() => {
      useAlertStore.getState().pushAlert({
        ruleId: 1,
        target: 'HPG',
        alertType: 'price_cross',
        severity: 'warning',
        reason: 'HPG price crossed above 1800',
        triggeredAt: '2026-09-28T10:00:00',
      });
      useAlertStore.getState().upsertPrice({ symbol: 'HPG', price: 1805, changePct: 4.1 });
    });

    await waitFor(() => expect(listTriggers).toHaveBeenCalledTimes(2));
    expect(listNotifications).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('live-price-1')).toHaveTextContent('1.805');
    await waitFor(() => expect(useAlertStore.getState().unreadCount).toBe(0));
  });
});

describe('AlertsPage edit rule', () => {
  it('loads a rule into the form, PATCHes the changes and leaves edit mode', async () => {
    updateRule.mockResolvedValue({ ...rule, parameters: { direction: 'above', price: 1900 } });
    render(<AlertsPage />);

    fireEvent.click(await screen.findByRole('button', { name: `Edit ${rule.name}` }));
    expect(screen.getByText('Edit alert rule')).toBeInTheDocument();
    const priceInput = screen.getByLabelText('Price threshold') as HTMLInputElement;
    expect(priceInput.value).toBe('1800');
    expect((screen.getByLabelText('Rule name') as HTMLInputElement).value).toBe(rule.name);

    fireEvent.change(priceInput, { target: { value: '1900' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(updateRule).toHaveBeenCalledTimes(1));
    expect(updateRule).toHaveBeenCalledWith(1, expect.objectContaining({
      targetScope: 'single_symbol',
      target: 'HPG',
      alertType: 'price_cross',
      parameters: { direction: 'above', price: 1900 },
      enabled: true,
    }));
    expect(createRule).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText('Create alert rule')).toBeInTheDocument());
    // Form reset happens after the edit-mode switch; wait for it under heavy suite load.
    await waitFor(() => expect((screen.getByLabelText('Price threshold') as HTMLInputElement).value).toBe(''));
  });

  it('cancels edit mode without saving', async () => {
    render(<AlertsPage />);

    fireEvent.click(await screen.findByRole('button', { name: `Edit ${rule.name}` }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel edit' }));

    expect(screen.getByText('Create alert rule')).toBeInTheDocument();
    expect(updateRule).not.toHaveBeenCalled();
  });
});
