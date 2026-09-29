import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { AlertRuleForm } from '../AlertRuleForm';
import { ALERT_SEVERITY_OPTIONS, ALERT_TARGET_SCOPE_OPTIONS, ALERT_TYPE_FILTER_OPTIONS } from '../../../locales/featureText';

vi.mock('../../../api/portfolio', () => ({
  portfolioApi: { getAccounts: vi.fn().mockResolvedValue({ accounts: [] }) },
}));

vi.mock('../../../api/alerts', () => ({
  alertsApi: { listMarketRegions: vi.fn().mockResolvedValue({ regions: ['vn'], default: 'vn' }) },
}));

// Dropdown loại quy tắc / phạm vi / mức độ phải dùng nhãn tiếng Việt khi giao diện là vi.
describe('AlertRuleForm Vietnamese labels', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'vi');
  });

  it('shows Vietnamese rule type, scope and severity options', () => {
    render(
      <UiLanguageProvider>
        <AlertRuleForm onSubmit={vi.fn()} />
      </UiLanguageProvider>,
    );

    expect(screen.getByRole('option', { name: 'Giá cắt ngưỡng' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Chạm trần/sàn' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Một mã' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Nghiêm trọng' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Price crossing' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Phạm vi đối tượng'), { target: { value: 'market' } });
    expect(screen.getByRole('option', { name: 'Trạng thái đèn tín hiệu thị trường' })).toBeInTheDocument();
  });

  it('keeps per-language option lists in sync with their labels', () => {
    expect(ALERT_TYPE_FILTER_OPTIONS.vi.find((option) => option.value === 'rsi_threshold')?.label).toBe('Ngưỡng RSI');
    expect(ALERT_TYPE_FILTER_OPTIONS.en.find((option) => option.value === 'rsi_threshold')?.label).toBe('RSI threshold');
    expect(ALERT_TARGET_SCOPE_OPTIONS.vi.map((option) => option.label)).toContain('Thị trường');
    expect(ALERT_SEVERITY_OPTIONS.vi.map((option) => option.label)).toEqual(['Thông tin', 'Cảnh báo', 'Nghiêm trọng']);
  });
});
