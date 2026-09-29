import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { StockIndexItem } from '../../../types/stockIndex';
import { AlertRuleForm } from '../AlertRuleForm';

const vnIndex: StockIndexItem[] = [
  {
    canonicalCode: 'HPG',
    displayCode: 'HPG',
    nameZh: 'Tập đoàn Hòa Phát',
    pinyinFull: 'tapdoanhoaphat',
    pinyinAbbr: 'tdhp',
    aliases: [],
    market: 'VN',
    assetType: 'stock',
    active: true,
    popularity: 100,
  },
];

vi.mock('../../../hooks/useStockIndex', () => ({
  useStockIndex: () => ({ index: vnIndex, loading: false, fallback: false, error: null, loaded: true }),
}));

vi.mock('../../../api/portfolio', () => ({
  portfolioApi: { getAccounts: vi.fn().mockResolvedValue({ accounts: [] }) },
}));

vi.mock('../../../api/alerts', () => ({
  alertsApi: { listMarketRegions: vi.fn().mockResolvedValue({ regions: ['vn'], default: 'vn' }) },
}));

describe('AlertRuleForm symbol autocomplete', () => {
  const onSubmit = vi.fn();

  beforeEach(() => {
    onSubmit.mockReset();
    onSubmit.mockResolvedValue(undefined);
  });

  it('suggests VN symbols by unaccented name and fills the picked code', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    const input = screen.getByLabelText('Symbol');
    expect(input).toHaveAttribute('role', 'combobox');
    // Gợi ý chỉ mở khi người dùng đang gõ trong ô (ô phải được focus).
    input.focus();
    fireEvent.change(input, { target: { value: 'hoaphat' } });

    const option = await screen.findByText('Tập đoàn Hòa Phát');
    fireEvent.click(option);
    await waitFor(() => expect(screen.getByLabelText('Symbol')).toHaveValue('HPG'));

    fireEvent.change(screen.getByLabelText('Price threshold'), { target: { value: '30000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'single_symbol',
        target: 'HPG',
        alertType: 'price_cross',
      }));
    });
  });

  it('does not open suggestions when the symbol is set programmatically (edit mode)', async () => {
    const rule = {
      id: 7,
      name: 'HPG price',
      targetScope: 'single_symbol' as const,
      target: 'HPG',
      alertType: 'price_cross' as const,
      parameters: { direction: 'above' as const, price: 30000 },
      severity: 'warning' as const,
      enabled: true,
      source: 'api',
    };
    render(<AlertRuleForm onSubmit={onSubmit} editingRule={rule} />);

    await waitFor(() => expect(screen.getByLabelText('Symbol')).toHaveValue('HPG'));
    // Chờ quá thời gian debounce của tìm kiếm gợi ý. Wait past the search debounce.
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(screen.queryByText('Tập đoàn Hòa Phát')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Symbol')).toHaveAttribute('aria-expanded', 'false');
  });
});
