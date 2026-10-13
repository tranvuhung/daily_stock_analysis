import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StockBarItemComponent } from '../StockBarItem';
import type { StockBarItem } from '../../../types/analysis';

const issue1600Item: StockBarItem = {
  id: 1,
  stockCode: 'VNM',
  stockName: 'Vietnam Dairy Products JSC',
  sentimentScore: 62,
  operationAdvice: 'Watch',
  analysisCount: 2,
  lastAnalysisTime: '2026-05-31T04:52:00Z',
  marketPhaseSummary: {
    market: 'VN',
    phase: 'non_trading',
    warnings: [],
  },
};

describe('StockBarItemComponent', () => {
  it('keeps market phase in the meta row instead of the action row', () => {
    render(
      <StockBarItemComponent
        item={issue1600Item}
        isViewing={false}
        onClick={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    const meta = screen.getByTestId('history-card-meta');

    expect(within(actions).getByText('Watch 62')).toBeInTheDocument();
    expect(within(actions).getByRole('button', { name: /Delete Vietnam Dairy Products JSC history record/ })).toBeInTheDocument();
    expect(within(actions).queryByText('VN · Non-trading day')).not.toBeInTheDocument();
    expect(within(meta).getByText('VN · Non-trading day')).toBeVisible();

    expect(screen.getByText('Vietnam Dairy P.')).toBeVisible();
    expect(
      screen.getByRole('button', {
        name: /^Vietnam Dairy Products JSC VNM history record$/,
      }),
    ).toBeInTheDocument();
  });

  it('uses structured action before legacy operation advice', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: 'avoid',
          actionLabel: 'Avoid',
          operationAdvice: 'Buy',
          sentimentScore: 35,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).getByText('Avoid 35')).toBeInTheDocument();
    expect(within(actions).queryByText('Buy 35')).not.toBeInTheDocument();
  });

  it('uses the unified legacy fallback for negated buy advice without structured action', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'Do not buy yet',
          sentimentScore: 28,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).getByText('Avoid 28')).toBeInTheDocument();
    expect(within(actions).queryByText('Buy 28')).not.toBeInTheDocument();
  });

  it('uses the unified legacy fallback for backend-aligned hold advice without structured action', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'No need to sell, keep the position',
          sentimentScore: 48,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).getByText('Hold 48')).toBeInTheDocument();
  });

  it('does not render ambiguous English legacy advice as a buy action', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'buy or sell',
          sentimentScore: 28,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).queryByText('buy 28')).not.toBeInTheDocument();
    expect(within(actions).getByText(/28/)).toBeInTheDocument();
  });

  it('does not render financial compound English advice as an action badge', () => {
    const { rerender } = render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'no selloff risk',
          sentimentScore: 28,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    let actions = screen.getByTestId('history-card-actions');
    expect(within(actions).queryByText('Hold 28')).not.toBeInTheDocument();
    expect(within(actions).getByText(/28/)).toBeInTheDocument();

    rerender(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'sell-off risk remains low',
          sentimentScore: 31,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    actions = screen.getByTestId('history-card-actions');
    expect(within(actions).queryByText('Sell 31')).not.toBeInTheDocument();
    expect(within(actions).getByText(/31/)).toBeInTheDocument();
  });

  it('does not render multi-guard legacy advice as an action badge', () => {
    render(
      <StockBarItemComponent
        item={{
          ...issue1600Item,
          action: null,
          actionLabel: null,
          operationAdvice: 'risk alert, avoid buying',
          sentimentScore: 28,
        }}
        isViewing={false}
        onClick={vi.fn()}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    expect(within(actions).queryByText('Avoid 28')).not.toBeInTheDocument();
    expect(within(actions).queryByText('Alert 28')).not.toBeInTheDocument();
    expect(within(actions).getByText(/28/)).toBeInTheDocument();
  });
});
