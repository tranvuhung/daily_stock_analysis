import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { PortfolioAiReviewCard } from '../PortfolioAiReviewCard';

const { aiReview, listAiReviews, getAiReview, deleteAiReview } = vi.hoisted(() => ({
  aiReview: vi.fn(),
  listAiReviews: vi.fn(),
  getAiReview: vi.fn(),
  deleteAiReview: vi.fn(),
}));

vi.mock('../../../api/portfolio', () => ({
  portfolioApi: { aiReview, listAiReviews, getAiReview, deleteAiReview },
}));

const savedReview = (id: number, summary: string, riskScore: number) => ({
  success: true,
  reviewId: id,
  createdAt: `2026-10-2${id}T09:00:00`,
  asOf: '2026-10-28',
  accountId: 7,
  costMethod: 'fifo',
  timedOut: false,
  durationS: 30,
  tokensUsed: 10,
  holdingCount: 2,
  signalCoverage: { withSignal: 2, withoutSignal: [] },
  riskAvailable: true,
  assessment: {
    portfolioRiskScore: riskScore,
    totalStocks: 2,
    summary,
    positions: [],
    cashRatio: 0.1,
    suggestedCashRatio: null,
    sectorWarnings: [],
    correlationWarnings: [],
    crossMarketNotes: [],
    rebalanceSuggestions: [],
    dataGaps: [],
  },
});

const listItem = (id: number, riskScore: number) => ({
  id,
  createdAt: `2026-10-2${id}T09:00:00`,
  asOf: '2026-10-28',
  accountId: 7,
  costMethod: 'fifo',
  riskScore,
  holdingCount: 2,
});

const renderCard = (props: Partial<React.ComponentProps<typeof PortfolioAiReviewCard>> = {}) =>
  render(
    <UiLanguageProvider>
      <PortfolioAiReviewCard accountId={7} costMethod="fifo" positionCount={2} {...props} />
    </UiLanguageProvider>,
  );

describe('PortfolioAiReviewCard', () => {
  beforeEach(() => {
    aiReview.mockReset();
    listAiReviews.mockReset();
    getAiReview.mockReset();
    deleteAiReview.mockReset();
    listAiReviews.mockResolvedValue({ items: [], total: 0 });
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

  it('loads the latest saved review on mount and switches via history', async () => {
    listAiReviews.mockResolvedValue({ items: [listItem(2, 6), listItem(1, 3)], total: 2 });
    getAiReview.mockImplementation((id: number) =>
      Promise.resolve(id === 2 ? savedReview(2, 'Bản mới nhất', 6) : savedReview(1, 'Bản cũ', 3)),
    );
    renderCard();

    expect(await screen.findByText('Bản mới nhất')).toBeInTheDocument();
    expect(listAiReviews).toHaveBeenCalledWith({ accountId: 7, costMethod: 'fifo', limit: 20 });
    expect(getAiReview).toHaveBeenCalledWith(2);
    expect(screen.getByText(/Lưu lúc/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Lịch sử đánh giá'), { target: { value: '1' } });
    expect(await screen.findByText('Bản cũ')).toBeInTheDocument();
  });

  it('adds a new successful run to the top of the history', async () => {
    aiReview.mockResolvedValue(savedReview(3, 'Vừa chạy', 5));
    renderCard();
    await waitFor(() => expect(listAiReviews).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: 'Đánh giá danh mục bằng AI' }));

    expect(await screen.findByText('Vừa chạy')).toBeInTheDocument();
    const select = screen.getByLabelText('Lịch sử đánh giá') as HTMLSelectElement;
    expect(select.value).toBe('3');
    expect(select.options).toHaveLength(1);
  });

  it('deletes the shown review and falls back to the next one', async () => {
    listAiReviews.mockResolvedValue({ items: [listItem(2, 6), listItem(1, 3)], total: 2 });
    getAiReview.mockImplementation((id: number) =>
      Promise.resolve(id === 2 ? savedReview(2, 'Bản mới nhất', 6) : savedReview(1, 'Bản cũ', 3)),
    );
    deleteAiReview.mockResolvedValue(undefined);
    renderCard();
    await screen.findByText('Bản mới nhất');

    fireEvent.click(screen.getByRole('button', { name: 'Xoá bản này' }));
    // Nút xác nhận trong ConfirmDialog là nút "Xoá bản này" thứ hai. / Second match is the dialog confirm.
    const deleteButtons = await screen.findAllByRole('button', { name: 'Xoá bản này' });
    expect(deleteButtons).toHaveLength(2);
    fireEvent.click(deleteButtons[1]);

    expect(await screen.findByText('Bản cũ')).toBeInTheDocument();
    expect(deleteAiReview).toHaveBeenCalledWith(2);
    expect((screen.getByLabelText('Lịch sử đánh giá') as HTMLSelectElement).options).toHaveLength(1);
  });
});
