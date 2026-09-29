import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAlertStream } from '../../../hooks/useAlertStream';
import { useAlertStore } from '../../../stores/alertStore';
import { AlertToasts } from '../AlertToasts';
import { AlertRuleForm } from '../AlertRuleForm';

vi.mock('../../../api/portfolio', () => ({
  portfolioApi: { getAccounts: vi.fn().mockResolvedValue({ accounts: [] }) },
}));

vi.mock('../../../api/alerts', () => ({
  alertsApi: {
    getStreamUrl: () => '/api/v1/alerts/stream',
    listMarketRegions: vi.fn().mockResolvedValue({ regions: ['vn'], default: 'vn' }),
  },
}));

type Listener = (event: MessageEvent) => void;

// EventSource giả để phát sự kiện SSE trong test. Fake EventSource for SSE events.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  withCredentials: boolean;
  listeners: Record<string, Listener[]> = {};
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = Boolean(init?.withCredentials);
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  emit(type: string, data: unknown) {
    (this.listeners[type] ?? []).forEach((listener) => listener({ data: JSON.stringify(data) } as MessageEvent));
  }

  close() {
    this.closed = true;
  }
}

function resetStore() {
  useAlertStore.setState({ unreadCount: 0, toasts: [], livePrices: {}, feedStatus: null, streamConnected: false, triggerVersion: 0 });
}

describe('realtime alerts (FE)', () => {
  const originalEventSource = window.EventSource;

  beforeEach(() => {
    FakeEventSource.instances = [];
    (window as unknown as { EventSource: unknown }).EventSource = FakeEventSource;
    resetStore();
  });

  afterEach(() => {
    (window as unknown as { EventSource: unknown }).EventSource = originalEventSource;
    vi.useRealTimers();
  });

  it('useAlertStream feeds alert, price and feed events into the store', () => {
    const { unmount } = renderHook(() => useAlertStream());
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/v1/alerts/stream');
    expect(source.withCredentials).toBe(true);

    act(() => {
      source.emit('connected', { message: 'ok' });
      source.emit('alert_triggered', {
        trigger_id: 7,
        rule_id: 3,
        target: 'HPG',
        display_target: 'HPG',
        alert_type: 'price_cross',
        severity: 'critical',
        reason: 'HPG price crossed above 20500',
        triggered_at: '2026-09-28T10:00:00',
      });
      source.emit('price_update', { symbol: 'HPG', price: 20600, change_pct: 1.2 });
      source.emit('feed_status', { enabled: true, connected: true, in_session: true });
    });

    const state = useAlertStore.getState();
    expect(state.streamConnected).toBe(true);
    expect(state.unreadCount).toBe(1);
    expect(state.toasts[0]).toMatchObject({ ruleId: 3, severity: 'critical', reason: 'HPG price crossed above 20500' });
    expect(state.livePrices.HPG).toMatchObject({ price: 20600, changePct: 1.2 });
    expect(state.feedStatus).toMatchObject({ connected: true, inSession: true });

    unmount();
    expect(source.closed).toBe(true);
  });

  it('reconnects with backoff after a stream error', () => {
    vi.useFakeTimers();
    renderHook(() => useAlertStream());
    const first = FakeEventSource.instances[0];
    act(() => {
      first.onerror?.(new Event('error'));
    });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it('renders alert toasts and dismisses them', () => {
    useAlertStore.getState().pushAlert({
      ruleId: 3,
      target: 'HPG',
      displayTarget: 'HPG',
      alertType: 'price_cross',
      severity: 'warning',
      reason: 'HPG price crossed above 20500',
      triggeredAt: '2026-09-28T10:00:00',
    });
    render(
      <MemoryRouter>
        <AlertToasts />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('alert-toast')).toHaveTextContent('HPG price crossed above 20500');
    expect(screen.getByRole('link', { name: 'View alerts' })).toHaveAttribute('href', '/alerts');
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByTestId('alert-toast')).not.toBeInTheDocument();
  });

  it('submits cross trigger mode and ceiling/floor rules', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '20500' } });
    fireEvent.change(screen.getByLabelText('Trigger mode'), { target: { value: 'cross' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        alertType: 'price_cross',
        parameters: { direction: 'above', price: 20500, triggerMode: 'cross' },
      }));
    });

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'price_limit_touch' } });
    fireEvent.change(screen.getByLabelText('Limit side'), { target: { value: 'ceiling' } });
    fireEvent.change(screen.getByLabelText('Trigger within (%) of the limit'), { target: { value: '1.5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({
        alertType: 'price_limit_touch',
        parameters: { side: 'ceiling', nearPct: 1.5 },
      }));
    });

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'price_limit_touch' } });
    fireEvent.change(screen.getByLabelText('Trigger within (%) of the limit'), { target: { value: '50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));
    expect(await screen.findByText('Distance must be between 0 and 20%')).toBeInTheDocument();
  });
});
