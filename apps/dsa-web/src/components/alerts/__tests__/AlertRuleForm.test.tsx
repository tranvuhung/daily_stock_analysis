import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { AlertRuleForm } from '../AlertRuleForm';

const { getAccounts, listMarketRegions } = vi.hoisted(() => ({
  getAccounts: vi.fn(),
  listMarketRegions: vi.fn(),
}));

vi.mock('../../../api/portfolio', () => ({
  portfolioApi: {
    getAccounts,
  },
}));

vi.mock('../../../api/alerts', () => ({
  alertsApi: {
    listMarketRegions,
  },
}));

describe('AlertRuleForm', () => {
  const onSubmit = vi.fn();

  beforeEach(() => {
    onSubmit.mockReset();
    onSubmit.mockResolvedValue(undefined);
    getAccounts.mockReset();
    window.localStorage.clear();
    getAccounts.mockResolvedValue({ accounts: [{ id: 9, name: 'Main', market: 'vn', baseCurrency: 'VND', isActive: true }] });
    listMarketRegions.mockReset();
    listMarketRegions.mockResolvedValue({ regions: ['vn'], default: 'vn' });
  });

  function renderEnglishForm() {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    render(
      <UiLanguageProvider>
        <AlertRuleForm onSubmit={onSubmit} />
      </UiLanguageProvider>,
    );
  }

  it('submits a price_cross rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Rule name'), { target: { value: 'HPG price breakout' } });
    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '1800' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        name: 'HPG price breakout',
        targetScope: 'single_symbol',
        target: 'HPG',
        alertType: 'price_cross',
        parameters: { direction: 'above', price: 1800 },
        severity: 'warning',
        enabled: true,
      });
    });
  });

  it('submits a price_change_percent rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'fpt' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'price_change_percent' } });
    fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'down' } });
    fireEvent.change(screen.getByLabelText('Change threshold (%)'), { target: { value: '3.5' } });
    fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'critical' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        target: 'FPT',
        alertType: 'price_change_percent',
        parameters: { direction: 'down', changePct: 3.5 },
        severity: 'critical',
      }));
    });
  });

  it('submits a volume_spike rule payload and supports disabled creation', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'vcb' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'volume_spike' } });
    fireEvent.change(screen.getByLabelText('Volume multiplier'), { target: { value: '2.5' } });
    fireEvent.click(screen.getByLabelText('Enable immediately'));
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        target: 'VCB',
        alertType: 'volume_spike',
        parameters: { multiplier: 2.5 },
        enabled: false,
      }));
    });
  });

  it('submits technical indicator rule payloads', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'macd_cross' } });
    fireEvent.change(screen.getByLabelText('Cross direction'), { target: { value: 'bearish_cross' } });
    fireEvent.change(screen.getByLabelText('Fast period'), { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText('Slow period'), { target: { value: '13' } });
    fireEvent.change(screen.getByLabelText('Signal period'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        target: 'HPG',
        alertType: 'macd_cross',
        parameters: {
          direction: 'bearish_cross',
          fastPeriod: 6,
          slowPeriod: 13,
          signalPeriod: 5,
        },
      }));
    });
  });

  it('rejects invalid technical indicator boundaries before submit', () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'rsi_threshold' } });
    fireEvent.change(screen.getByLabelText('RSI threshold'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    expect(screen.getByRole('alert')).toHaveTextContent('RSI threshold must be between 0 and 100');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects indicator period combinations that exceed fetchable history', () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'macd_cross' } });
    fireEvent.change(screen.getByLabelText('Fast period'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Slow period'), { target: { value: '250' } });
    fireEvent.change(screen.getByLabelText('Signal period'), { target: { value: '250' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    expect(screen.getByRole('alert')).toHaveTextContent('MACD requires 501 daily bars, up to 365 are supported');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects empty required technical indicator thresholds before submit', () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'rsi_threshold' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    expect(screen.getByRole('alert')).toHaveTextContent('RSI threshold is required');
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'cci_threshold' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    expect(screen.getByRole('alert')).toHaveTextContent('CCI threshold is required');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects invalid numeric thresholds before submit', () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'HPG' } });
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Price threshold must be a number greater than 0');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects invalid stock code format before submit', () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'fpt-2026' } });
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Invalid stock code format');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('filters alert types and submits a watchlist rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'watchlist' } });
    expect(screen.queryByText('Portfolio stop loss')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'watchlist',
        target: 'default',
        alertType: 'price_cross',
        parameters: { direction: 'above', price: 10 },
      }));
    });
  });

  it('loads accounts and submits portfolio stop-loss mode', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'portfolio_account' } });
    await waitFor(() => expect(getAccounts).toHaveBeenCalledWith(false));
    expect(screen.queryByText('Price crossing')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: '9' } });
    fireEvent.change(screen.getByLabelText('Stop-loss mode'), { target: { value: 'breach' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'portfolio_account',
        target: '9',
        alertType: 'portfolio_stop_loss',
        parameters: { mode: 'breach' },
      }));
    });
  });

  it('renders portfolio alert type options in English UI mode', async () => {
    renderEnglishForm();

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'portfolio_account' } });

    await waitFor(() => expect(getAccounts).toHaveBeenCalledWith(false));
    expect(screen.getByRole('option', { name: 'Portfolio drawdown' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Portfolio stop loss' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Info' })).toBeInTheDocument();
    expect(screen.queryByText('Mức sụt giảm danh mục')).not.toBeInTheDocument();
  });

  it('defaults market region to Vietnam and only lists the vn region', async () => {
    listMarketRegions.mockResolvedValue({ regions: ['vn'], default: 'vn' });
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'market' } });

    await waitFor(() => expect(listMarketRegions).toHaveBeenCalled());
    expect(screen.getByRole('option', { name: 'Vietnam (vn)' })).toBeInTheDocument();
    expect(screen.getByLabelText('Market region')).toHaveValue('vn');

    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ targetScope: 'market', target: 'vn' }));
    });
  });

  it('blocks market rules when no alert market is enabled', async () => {
    listMarketRegions.mockResolvedValue({ regions: [], default: null });
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'market' } });

    expect(await screen.findByText(/No market with alert support is enabled/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('falls back to the vn market region when the region list fails to load', async () => {
    listMarketRegions.mockRejectedValue(new Error('offline'));
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'market' } });

    await waitFor(() => expect(listMarketRegions).toHaveBeenCalled());
    expect(screen.getByRole('option', { name: 'Vietnam (vn)' })).toBeInTheDocument();
    expect(screen.getByLabelText('Market region')).toHaveValue('vn');
  });

  it('submits a market light status rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'market' } });
    await waitFor(() => expect(listMarketRegions).toHaveBeenCalled());
    expect(screen.getByRole('option', { name: 'Vietnam (vn)' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'market',
        target: 'vn',
        alertType: 'market_light_status',
        parameters: { statuses: ['red', 'yellow'] },
      }));
    });
  });

  it('lists only the Vietnam market region in English UI mode', () => {
    renderEnglishForm();

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'market' } });

    expect(screen.getByRole('option', { name: 'Vietnam (vn)' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'US (us)' })).not.toBeInTheDocument();
  });

  it('submits a market light score-drop rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'market' } });
    fireEvent.change(screen.getByLabelText('Rule type'), { target: { value: 'market_light_score_drop' } });
    fireEvent.change(screen.getByLabelText('Score drop threshold'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'market',
        target: 'vn',
        alertType: 'market_light_score_drop',
        parameters: { minDrop: 12 },
      }));
    });
  });

  it('keeps all account option when account loading fails', async () => {
    getAccounts.mockRejectedValueOnce(new Error('boom'));
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Target scope'), { target: { value: 'portfolio_holdings' } });
    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
    expect(screen.getByLabelText('Account')).toHaveValue('all');
  });

  it('keeps form values when submit reports failure', async () => {
    onSubmit.mockResolvedValueOnce(false);
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'fpt' } });
    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(screen.getByLabelText('Symbol')).toHaveValue('fpt');
    expect(screen.getByLabelText('Price threshold')).toHaveValue(200);
  });
});
