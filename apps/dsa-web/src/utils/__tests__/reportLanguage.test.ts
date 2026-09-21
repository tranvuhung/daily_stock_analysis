import { describe, expect, it } from 'vitest';

import { getReportText, normalizeReportLanguage } from '../reportLanguage';
import { getSentimentLabel } from '../../types/analysis';

// Bộ nhãn báo cáo nay chỉ còn vi | en. Backend chỉ phát `vi`
// (api/v1/schemas/analysis.py: Literal["vi"]), nên `zh`/`ko` đã được gỡ bỏ và mọi
// giá trị lạ — kể cả từ bản ghi cũ — phải lùi về `vi` thay vì rơi vào nhãn tiếng Trung.
describe('reportLanguage vi/en support', () => {
  it('normalizes to vi for anything that is not en', () => {
    expect(normalizeReportLanguage('vi')).toBe('vi');
    expect(normalizeReportLanguage('en')).toBe('en');
    expect(normalizeReportLanguage('fr')).toBe('vi');
    expect(normalizeReportLanguage(undefined)).toBe('vi');
  });

  it('falls back to vi for retired languages', () => {
    expect(normalizeReportLanguage('zh')).toBe('vi');
    expect(normalizeReportLanguage('ko')).toBe('vi');
  });

  it('returns Vietnamese report copy by default', () => {
    const vi = getReportText('vi');
    expect(vi.keyInsights).toBe('NHẬN ĐỊNH CHÍNH');
    expect(vi.actionAdvice).toBe('Khuyến nghị hành động');
    expect(vi.fullReport).toBe('Báo cáo phân tích đầy đủ');
  });

  it('keeps en report copy unchanged', () => {
    expect(getReportText('en').keyInsights).toBe('KEY INSIGHTS');
  });

  it('returns sentiment labels by band', () => {
    expect(getSentimentLabel(90, 'vi')).toBe('Rất lạc quan');
    expect(getSentimentLabel(50, 'vi')).toBe('Trung tính');
    expect(getSentimentLabel(10, 'vi')).toBe('Rất bi quan');
    expect(getSentimentLabel(90, 'en')).toBe('Very Bullish');
  });

  it('defaults sentiment labels to Vietnamese', () => {
    expect(getSentimentLabel(50)).toBe('Trung tính');
  });
});
