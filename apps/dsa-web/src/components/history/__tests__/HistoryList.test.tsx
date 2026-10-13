import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { HistoryList } from '../HistoryList';
import type { HistoryItem } from '../../../types/analysis';

const baseProps = {
  isLoading: false,
  isLoadingMore: false,
  hasMore: false,
  selectedIds: new Set<number>(),
  onItemClick: vi.fn(),
  onLoadMore: vi.fn(),
  onToggleItemSelection: vi.fn(),
  onToggleSelectAll: vi.fn(),
  onDeleteSelected: vi.fn(),
};

const items: HistoryItem[] = [
  {
    id: 1,
    queryId: 'q-1',
    stockCode: 'FPT',
    stockName: 'FPT Corp',
    sentimentScore: 82,
    operationAdvice: 'Buy',
    createdAt: '2026-03-15T08:00:00Z',
  },
];

const longNameItem: HistoryItem = {
  id: 2,
  queryId: 'q-2',
  stockCode: 'VNM',
  stockName: 'Vietnam Dairy Products JSC',
  sentimentScore: 75,
  operationAdvice: 'Hold',
  createdAt: '2026-03-16T08:00:00Z',
  marketPhaseSummary: {
    market: 'VN',
    phase: 'non_trading',
    warnings: [],
  },
};

describe('HistoryList', () => {
  it('shows the empty state copy when no history exists', () => {
    const { container } = render(<HistoryList {...baseProps} items={[]} />);

    expect(screen.getByText('No analysis history')).toBeInTheDocument();
    expect(screen.getByText('Recent results will be saved here after the first analysis.')).toBeInTheDocument();
    expect(screen.getByText('Analysis history')).toBeInTheDocument();
    expect(container.querySelector('.glass-card')).toBeTruthy();
  });

  it('renders selected count and forwards item interactions', () => {
    const onItemClick = vi.fn();
    const onToggleItemSelection = vi.fn();

    render(
      <HistoryList
        {...baseProps}
        items={items}
        selectedIds={new Set([1])}
        selectedId={1}
        onItemClick={onItemClick}
        onToggleItemSelection={onToggleItemSelection}
      />,
    );

    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(screen.getByText('Buy 82')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /FPT Corp/i }));
    expect(onItemClick).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    expect(onToggleItemSelection).toHaveBeenCalledWith(1);
  });

  it('uses structured action before legacy operation advice', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[
          {
            ...items[0],
            action: 'avoid',
            actionLabel: 'Avoid',
            operationAdvice: 'Buy',
            sentimentScore: 35,
          },
        ]}
      />,
    );

    expect(screen.getByText('Avoid 35')).toBeInTheDocument();
    expect(screen.queryByText('Buy 35')).not.toBeInTheDocument();
  });

  it('uses the unified legacy fallback for negated buy advice without structured action', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[
          {
            ...items[0],
            action: null,
            actionLabel: null,
            operationAdvice: 'Do not buy yet',
            sentimentScore: 28,
          },
        ]}
      />,
    );

    expect(screen.getByText('Avoid 28')).toBeInTheDocument();
    expect(screen.queryByText('Buy 28')).not.toBeInTheDocument();
  });

  it('uses the unified legacy fallback for backend-aligned hold advice without structured action', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[
          {
            ...items[0],
            action: null,
            actionLabel: null,
            operationAdvice: 'No need to sell, keep the position',
            sentimentScore: 48,
          },
        ]}
      />,
    );

    expect(screen.getByText('Hold 48')).toBeInTheDocument();
    expect(screen.queryByText('Sentiment 48')).not.toBeInTheDocument();
  });

  it('does not render ambiguous English legacy advice as a buy action', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[
          {
            ...items[0],
            action: null,
            actionLabel: null,
            operationAdvice: 'buy or sell',
            sentimentScore: 28,
          },
        ]}
      />,
    );

    expect(screen.getByText('Sentiment 28')).toBeInTheDocument();
    expect(screen.queryByText('buy 28')).not.toBeInTheDocument();
  });

  it('does not render financial compound English advice as an action badge', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[
          {
            ...items[0],
            action: null,
            actionLabel: null,
            operationAdvice: 'no buyback announced',
            sentimentScore: 28,
          },
          {
            ...items[0],
            id: 2,
            queryId: 'q-2',
            action: null,
            actionLabel: null,
            operationAdvice: 'no selloff risk',
            sentimentScore: 31,
          },
          {
            ...items[0],
            id: 3,
            queryId: 'q-3',
            action: null,
            actionLabel: null,
            operationAdvice: 'sell-off risk remains low',
            sentimentScore: 33,
          },
        ]}
      />,
    );

    expect(screen.getByText('Sentiment 28')).toBeInTheDocument();
    expect(screen.getByText('Sentiment 31')).toBeInTheDocument();
    expect(screen.getByText('Sentiment 33')).toBeInTheDocument();
    expect(screen.queryByText('Avoid 28')).not.toBeInTheDocument();
    expect(screen.queryByText('Hold 31')).not.toBeInTheDocument();
    expect(screen.queryByText('Sell 33')).not.toBeInTheDocument();
  });

  it('does not render multi-guard legacy advice as an avoid or alert action', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[
          {
            ...items[0],
            action: null,
            actionLabel: null,
            operationAdvice: 'risk alert, avoid buying',
            sentimentScore: 28,
          },
        ]}
      />,
    );

    expect(screen.getByText('Sentiment 28')).toBeInTheDocument();
    expect(screen.queryByText('Avoid 28')).not.toBeInTheDocument();
    expect(screen.queryByText('Alert 28')).not.toBeInTheDocument();
  });

  it('toggles select-all when clicking the label text', () => {
    const onToggleSelectAll = vi.fn();

    render(
      <HistoryList
        {...baseProps}
        items={items}
        onToggleSelectAll={onToggleSelectAll}
      />,
    );

    fireEvent.click(screen.getByText('Select current'));

    expect(onToggleSelectAll).toHaveBeenCalledTimes(1);
  });

  it('disables delete when nothing is selected', () => {
    render(<HistoryList {...baseProps} items={items} />);

    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
  });

  it('truncates long stock names with trailing dot', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[longNameItem]}
      />,
    );

    // 'Vietnam Dairy Products JSC' (26 chars) is truncated to 15 chars + dot.
    expect(screen.getByText('Vietnam Dairy P.')).toBeInTheDocument();
    expect(screen.queryByText('Vietnam Dairy Products JSC')).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', {
        name: /^Vietnam Dairy Products JSC VNM history record$/,
      }),
    ).toBeInTheDocument();

    const actions = screen.getByTestId('history-card-actions');
    const meta = screen.getByTestId('history-card-meta');
    expect(within(actions).queryByText('VN · Non-trading day')).not.toBeInTheDocument();
    expect(within(meta).getByText('VN · Non-trading day')).toBeVisible();
  });

  it('generates unique select-all ids across multiple instances', () => {
    const { container } = render(
      <>
        <HistoryList {...baseProps} items={items} />
        <HistoryList {...baseProps} items={items} />
      </>,
    );

    const labels = container.querySelectorAll('label[for]');
    const ids = Array.from(labels).map((label) => label.getAttribute('for'));

    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
