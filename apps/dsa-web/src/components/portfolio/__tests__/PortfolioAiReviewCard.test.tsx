import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { PortfolioAiReviewCard } from '../PortfolioAiReviewCard';

const { aiReview } = vi.hoisted(() => ({ aiReview: vi.fn() }));

vi.mock('../../../api/portfolio', () => ({
  portfolioApi: { aiReview },
}));

const renderCard = (props: Partial<React.ComponentProps<typeof PortfolioAiReviewCard>> = {}) =>
  render(
    <UiLanguageProvider>
      <PortfolioAiReviewCard accountId={7} costMethod="fifo" positionCount={2} {...props} />
    </UiLanguageProvider>,
  );

describe('PortfolioAiReviewCard', () => {
  beforeEach(() => {
    aiReview.mockReset();
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'vi');
  });

  it('runs the review and renders the assessment', async () => {
    aiReview.mockResolvedValue({
      success: true,
      asOf: '2026-01-05',
      timedOut: false,
      durationS: 12.3,
      tokensUsed: 100,
      holdingCount: 2,
      signalCoverage: { withSignal: 1, withoutSignal: ['FPT'] },
      riskAvailable: true,
      assessment: {
        portfolioRiskScore: 7,
        totalStocks: 2,
        summary: 'Danh mục tập trung vào HPG',
        positions: [
          { code: 'HPG', currentWeight: 0.6, suggestedWeight: 0.35, signal: 'reduce', priority: 'high', note: 'Giảm dần' },
        ],
        cashRatio: 0.2,
        suggestedCashRatio: 0.3,
        sectorWarnings: [],
        correlationWarnings: [],
        crossMarketNotes: [],
        rebalanceSuggestions: ['Bán bớt 2.000 cp HPG'],
        dataGaps: ['Chưa có tín hiệu AI: FPT'],
      },
    });
    renderCard();

    fireEvent.click(screen.getByRole('button', { name: 'Đánh giá danh mục bằng AI' }));

    expect(await screen.findByText('Danh mục tập trung vào HPG')).toBeInTheDocument();
    expect(aiReview).toHaveBeenCalledWith({ accountId: 7, costMethod: 'fifo' });
    expect(screen.getByText('Điểm rủi ro: 7/10')).toBeInTheDocument();
    expect(screen.getByText('60.0%')).toBeInTheDocument();
    expect(screen.getByText('35.0%')).toBeInTheDocument();
    expect(screen.getByText('Giảm tỷ trọng')).toBeInTheDocument();
    expect(screen.getByText('Bán bớt 2.000 cp HPG')).toBeInTheDocument();
    expect(screen.getByText('Chưa có tín hiệu AI: FPT')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Đánh giá lại' })).toBeEnabled();
  });

  it('shows a timeout warning', async () => {
    aiReview.mockResolvedValue({
      success: false,
      error: 'timeout',
      timedOut: true,
      durationS: 240,
      tokensUsed: 0,
      holdingCount: 2,
      signalCoverage: { withSignal: 0, withoutSignal: [] },
      riskAvailable: true,
      assessment: null,
    });
    renderCard();

    fireEvent.click(screen.getByRole('button', { name: 'Đánh giá danh mục bằng AI' }));

    await waitFor(() => expect(screen.getByText(/vượt quá thời gian cho phép/)).toBeInTheDocument());
  });

  it('disables the button when there are no positions', () => {
    renderCard({ positionCount: 0 });
    expect(screen.getByRole('button', { name: 'Đánh giá danh mục bằng AI' })).toBeDisabled();
  });
});
