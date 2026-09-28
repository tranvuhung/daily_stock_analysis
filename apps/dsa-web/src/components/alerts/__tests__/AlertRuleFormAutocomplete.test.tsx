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
});
