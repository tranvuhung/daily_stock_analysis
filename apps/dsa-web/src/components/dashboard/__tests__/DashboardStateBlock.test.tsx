import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DashboardStateBlock } from '../DashboardStateBlock';

describe('DashboardStateBlock', () => {
  it('renders the title as a paragraph by default', () => {
    const { container } = render(<DashboardStateBlock title="Start analysis" description="Xem nội dung gợi ý" />);

    const title = screen.getByText('Start analysis');
    expect(title.tagName).toBe('P');
    expect(container.querySelector('h3')).toBeNull();
  });

  it('renders the title with the requested heading level', () => {
    render(<DashboardStateBlock title="Start analysis" titleAs="h3" description="Xem nội dung gợi ý" />);

    expect(screen.getByRole('heading', { name: 'Start analysis', level: 3 })).toBeInTheDocument();
  });

  it('keeps icon, description, action, and loading behaviors intact', () => {
    const { rerender } = render(
      <DashboardStateBlock
        title="Start analysis"
        description="Nhập mã cổ phiếu để phân tích"
        icon={<span data-testid="icon">icon</span>}
        action={<button type="button">Bắt đầu ngay</button>}
      />,
    );

    expect(screen.getByTestId('icon')).toBeInTheDocument();
    expect(screen.getByText('Nhập mã cổ phiếu để phân tích')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Bắt đầu ngay' })).toBeInTheDocument();

    rerender(
      <DashboardStateBlock
        title="Start analysis"
        titleAs="h3"
        description="Nhập mã cổ phiếu để phân tích"
        loading
      />,
    );

    expect(screen.getByRole('heading', { name: 'Start analysis', level: 3 })).toBeInTheDocument();
    expect(screen.getByText('Nhập mã cổ phiếu để phân tích')).toBeInTheDocument();
    expect(document.querySelector('.home-spinner')).not.toBeNull();
    expect(screen.queryByTestId('icon')).not.toBeInTheDocument();
  });
});
