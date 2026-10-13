import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { ReportNews } from '../ReportNews';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getNews: vi.fn(),
  },
}));

describe('ReportNews', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders news items and refreshes with preserved subpanel styling', async () => {
    vi.mocked(historyApi.getNews).mockResolvedValue({
      total: 1,
      items: [
        {
          title: 'Hoa Phat công bố số liệu kinh doanh mới',
          snippet: 'Công ty công bố kết quả kinh doanh quý, thị trường quan tâm hơn.',
          url: 'https://example.com/news',
        },
      ],
    });

    const { container } = render(<ReportNews recordId={1} />);

    expect(await screen.findByText('Hoa Phat công bố số liệu kinh doanh mới')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Mở liên kết' })).toHaveAttribute('href', 'https://example.com/news');
    expect(screen.getByText('Tin liên quan / tra cứu bổ sung')).toBeVisible();
    expect(screen.getByText('Nguồn: tin bổ sung trên trang báo cáo; phần thực sự dùng cho phân tích xem ở khối dữ liệu đầu vào.')).toBeVisible();
    expect(container.querySelector('.home-panel-card')).toBeTruthy();
    expect(container.querySelector('.home-subpanel')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Làm mới' }));

    await waitFor(() => {
      expect(historyApi.getNews).toHaveBeenCalledTimes(2);
    });
  });

  it('renders the empty state when no news exists', async () => {
    vi.mocked(historyApi.getNews).mockResolvedValue({
      total: 0,
      items: [],
    });

    render(<ReportNews recordId={1} />);

    expect(await screen.findByText('Chưa có tin liên quan')).toBeInTheDocument();
    expect(screen.getByText('Hãy làm mới sau để lấy tin mới nhất.')).toBeInTheDocument();
  });

  it('localizes the empty state description for english reports', async () => {
    vi.mocked(historyApi.getNews).mockResolvedValue({
      total: 0,
      items: [],
    });

    render(<ReportNews recordId={1} language="en" />);

    expect(await screen.findByText('No related news')).toBeInTheDocument();
    expect(screen.getByText('Refresh later to check for the latest updates.')).toBeInTheDocument();
    expect(screen.getByText('Related news / follow-up retrieval')).toBeVisible();
  });

  it('renders the error state and supports retry', async () => {
    vi.mocked(historyApi.getNews)
      .mockRejectedValueOnce(new Error('network failed'))
      .mockResolvedValueOnce({
        total: 1,
        items: [
          {
            title: 'Thử lại thành công',
            snippet: 'Yêu cầu lần hai trả về thành công.',
            url: 'https://example.com/retry',
          },
        ],
      });

    render(<ReportNews recordId={1} />);

    expect(await screen.findByRole('alert')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thử lại' }));

    expect(await screen.findByText('Thử lại thành công')).toBeInTheDocument();
  });
});
