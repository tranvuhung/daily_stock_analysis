import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { HomeStockWorkspace } from '../HomeStockWorkspace';
import type { HomeWatchlistRow, HomeWorkspaceTab } from '../HomeStockWorkspace';

function renderWorkspace({
  watchlistRows,
  selectedRecordId,
  selectedStockCode,
  selectedAssetType,
  activeTab = 'watchlist',
}: {
  watchlistRows: HomeWatchlistRow[];
  selectedRecordId?: number;
  selectedStockCode?: string;
  selectedAssetType?: 'stock' | 'index' | null;
  activeTab?: HomeWorkspaceTab;
}) {
  const onHistoryItemClick = vi.fn();
  const onRemoveFromWatchlist = vi.fn().mockResolvedValue(undefined);
  window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');

  const renderView = (rows: HomeWatchlistRow[]) => (
    <UiLanguageProvider>
      <HomeStockWorkspace
        activeTab={activeTab}
        onTabChange={vi.fn()}
        watchlistRows={rows}
        watchlistLoading={false}
        watchlistActioning={false}
        watchlistMessage={null}
        onAddToWatchlist={vi.fn().mockResolvedValue(undefined)}
        onRemoveFromWatchlist={onRemoveFromWatchlist}
        onRefreshWatchlist={vi.fn().mockResolvedValue(undefined)}
        onAnalyzeWatchlist={vi.fn().mockResolvedValue(undefined)}
        isBatchAnalyzing={false}
        batchStatus={null}
        todayItems={[]}
        isLoadingTodayItems={false}
        todayLoadError={false}
        watchlistAnalyzedTodayCount={rows.filter((row) => row.analyzedToday).length}
        historyItems={[]}
        isLoadingHistory={false}
        selectedStockCode={selectedStockCode}
        selectedAssetType={selectedAssetType ?? null}
        selectedRecordId={selectedRecordId}
        onHistoryItemClick={onHistoryItemClick}
      />
    </UiLanguageProvider>
  );
  const view = render(renderView(watchlistRows));

  return {
    onHistoryItemClick,
    onRemoveFromWatchlist,
    rerenderWatchlistRows: (rows: HomeWatchlistRow[]) => view.rerender(renderView(rows)),
  };
}

describe('HomeStockWorkspace', () => {
  it('uses the mobile scroll-shell contract for watchlist and today content', () => {
    renderWorkspace({ watchlistRows: [] });

    const workspace = screen.getByTestId('home-stock-workspace');
    expect(workspace).toHaveClass('glass-card', 'home-stock-scroll-shell');
    expect(workspace).not.toHaveClass('overflow-hidden');
    expect(screen.getByTestId('home-stock-workspace-scroll')).toHaveClass('overscroll-y-contain', 'touch-pan-y');
  });

  it('uses the same mobile scroll-shell contract around history and StockBar', () => {
    renderWorkspace({ watchlistRows: [], activeTab: 'history' });

    const workspace = screen.getByTestId('home-stock-workspace');
    const stockBar = screen.getByTestId('home-stock-bar');
    expect(workspace).toHaveClass('home-stock-scroll-shell');
    expect(workspace).not.toHaveClass('overflow-hidden');
    expect(stockBar).toHaveClass('glass-card', 'home-stock-scroll-shell', 'min-h-0');
    expect(stockBar).not.toHaveClass('overflow-hidden');
    expect(screen.getByTestId('home-stock-bar-scroll')).toHaveClass('touch-pan-y');
  });

  it('opens the latest watchlist detail from a native button and keeps the row selected', () => {
    const { onHistoryItemClick } = renderWorkspace({
      watchlistRows: [{
        code: 'FPT',
        analyzedToday: true,
        latestItem: {
          id: 21,
          stockCode: 'FPT',
          stockName: 'FPT Corp',
          sentimentScore: 88,
          operationAdvice: 'Buy',
          analysisCount: 1,
          lastAnalysisTime: '2026-03-19T09:00:00+07:00',
        },
      }],
      selectedRecordId: 21,
    });

    const row = screen.getByRole('button', { name: 'Open the latest analysis details for FPT' });
    fireEvent.click(row);

    expect(onHistoryItemClick).toHaveBeenCalledWith(21);
    expect(row.tagName).toBe('BUTTON');
    expect(row).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows an explicit notice when a watchlist row has no detail yet', async () => {
    const { onHistoryItemClick } = renderWorkspace({
      watchlistRows: [{
        code: 'VCB',
        analyzedToday: false,
      }],
    });

    const row = screen.getByRole('button', { name: 'No analysis details for VCB yet. Run an analysis first.' });
    fireEvent.click(row);

    expect(await screen.findByRole('alert')).toHaveTextContent('No analysis details yet. Run an analysis first.');
    expect(onHistoryItemClick).not.toHaveBeenCalled();
  });

  it('shows loading feedback instead of no-detail copy while latest detail lookup is still pending', async () => {
    const { onHistoryItemClick } = renderWorkspace({
      watchlistRows: [{
        code: 'VCB',
        analyzedToday: false,
        isTodayStatusLoading: true,
      }],
    });

    const row = screen.getByRole('button', { name: 'Looking up the latest analysis details for VCB' });
    fireEvent.click(row);

    expect(await screen.findByRole('alert')).toHaveTextContent('Looking up the latest analysis details. Please wait.');
    expect(onHistoryItemClick).not.toHaveBeenCalled();
    expect(screen.getByText('Looking up details...')).toBeInTheDocument();
  });

  it('shows retry feedback instead of no-detail copy when the latest detail lookup failed', async () => {
    const { onHistoryItemClick } = renderWorkspace({
      watchlistRows: [{
        code: 'VCB',
        analyzedToday: false,
        isTodayStatusUnknown: true,
      }],
    });

    const row = screen.getByRole('button', { name: 'The latest analysis details for VCB could not be confirmed. Please try again later.' });
    fireEvent.click(row);

    expect(await screen.findByRole('alert')).toHaveTextContent('The latest analysis details could not be confirmed. Please try again later.');
    expect(screen.queryByText('No analysis details yet. Run an analysis first.')).not.toBeInTheDocument();
    expect(screen.getByText('Details unavailable')).toBeInTheDocument();
    expect(onHistoryItemClick).not.toHaveBeenCalled();
  });

  it('does not expose a cached detail while the current row status is unsettled', async () => {
    const cachedItem = {
      id: 24,
      stockCode: 'VCB',
      stockName: 'Vietcombank',
      sentimentScore: 68,
      operationAdvice: 'neutral',
      analysisCount: 1,
      lastAnalysisTime: '2026-03-18T09:20:00+07:00',
    };
    const { onHistoryItemClick, rerenderWatchlistRows } = renderWorkspace({
      watchlistRows: [{
        code: 'VCB',
        analyzedToday: false,
        latestItem: cachedItem,
        isTodayStatusLoading: true,
      }],
    });

    fireEvent.click(screen.getByRole('button', { name: 'Looking up the latest analysis details for VCB' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Looking up the latest analysis details. Please wait.');
    expect(onHistoryItemClick).not.toHaveBeenCalled();

    rerenderWatchlistRows([{
      code: 'VCB',
      analyzedToday: false,
      latestItem: cachedItem,
      isTodayStatusUnknown: true,
    }]);
    fireEvent.click(screen.getByRole('button', { name: 'The latest analysis details for VCB could not be confirmed. Please try again later.' }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('The latest analysis details could not be confirmed. Please try again later.');
    });
    expect(onHistoryItemClick).not.toHaveBeenCalled();
  });

  it('clears a loading notice when the same row detail lookup settles', async () => {
    const { rerenderWatchlistRows } = renderWorkspace({
      watchlistRows: [{
        code: 'VCB',
        analyzedToday: false,
        isTodayStatusLoading: true,
      }],
    });

    const row = screen.getByTestId('watchlist-row-VCB');
    fireEvent.click(row.querySelector('button[aria-pressed]') as HTMLButtonElement);
    expect(await screen.findByRole('alert')).toBeInTheDocument();

    rerenderWatchlistRows([{
      code: 'VCB',
      analyzedToday: false,
      latestItem: {
        id: 22,
        stockCode: 'VCB',
        stockName: 'Vietcombank',
        sentimentScore: 72,
        operationAdvice: 'neutral',
        analysisCount: 1,
        lastAnalysisTime: '2026-03-19T09:00:00+07:00',
      },
    }]);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(row.querySelector('button[aria-pressed]')).toBeInTheDocument();
  });

  it('clears a no-detail notice when the matching row receives a detail', async () => {
    const { rerenderWatchlistRows } = renderWorkspace({
      watchlistRows: [{
        code: 'VCB',
        analyzedToday: false,
      }],
    });

    fireEvent.click(screen.getByRole('button', { name: 'No analysis details for VCB yet. Run an analysis first.' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('No analysis details yet. Run an analysis first.');

    rerenderWatchlistRows([{
      code: 'VCB',
      analyzedToday: true,
      latestItem: {
        id: 23,
        stockCode: 'VCB',
        stockName: 'Vietcombank',
        sentimentScore: 80,
        operationAdvice: 'buy',
        analysisCount: 1,
        lastAnalysisTime: '2026-03-19T10:00:00+07:00',
      },
    }]);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Open the latest analysis details for VCB' })).toBeInTheDocument();
  });

  it('derives an opened notice from the latest row state instead of retaining stale copy', async () => {
    const { rerenderWatchlistRows } = renderWorkspace({
      watchlistRows: [{
        code: 'VCB',
        analyzedToday: false,
      }],
    });

    const row = screen.getByTestId('watchlist-row-VCB');
    fireEvent.click(row.querySelector('button[aria-pressed]') as HTMLButtonElement);
    expect(await screen.findByRole('alert')).toHaveTextContent('No analysis details yet. Run an analysis first.');

    rerenderWatchlistRows([{
      code: 'VCB',
      analyzedToday: false,
      isTodayStatusLoading: true,
    }]);
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Looking up the latest analysis details. Please wait.');
    });

    rerenderWatchlistRows([{
      code: 'VCB',
      analyzedToday: false,
      isTodayStatusUnknown: true,
    }]);
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('The latest analysis details could not be confirmed. Please try again later.');
    });
  });

  it('does not bubble delete clicks into detail opening', async () => {
    const { onHistoryItemClick, onRemoveFromWatchlist } = renderWorkspace({
      watchlistRows: [{
        code: 'FPT',
        analyzedToday: true,
        latestItem: {
          id: 21,
          stockCode: 'FPT',
          stockName: 'FPT Corp',
          sentimentScore: 88,
          operationAdvice: 'Buy',
          analysisCount: 1,
          lastAnalysisTime: '2026-03-19T09:00:00+07:00',
        },
      }],
    });

    fireEvent.click(screen.getByRole('button', { name: 'Remove FPT from watchlist' }));

    expect(onRemoveFromWatchlist).toHaveBeenCalledWith('FPT');
    expect(onHistoryItemClick).not.toHaveBeenCalled();
  });

  it('keeps the watchlist row selected for equivalent stock-code formats', () => {
    renderWorkspace({
      watchlistRows: [{
        code: 'HPG.VN',
        analyzedToday: true,
        latestItem: {
          id: 88,
          stockCode: 'HPG',
          stockName: 'Hoa Phat Group',
          sentimentScore: 91,
          operationAdvice: 'Buy',
          analysisCount: 1,
          lastAnalysisTime: '2026-03-19T09:00:00+07:00',
        },
      }],
      selectedStockCode: 'vnHPG',
    });

    expect(screen.getByRole('button', { name: 'Open the latest analysis details for HPG.VN' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps a same-code stock row selected without selecting the index row', () => {
    renderWorkspace({
      watchlistRows: [
        {
          code: 'vnindex',
          assetType: 'index',
          analyzedToday: true,
          latestItem: {
            id: 31,
            stockCode: 'vnindex',
            stockName: 'VN-Index',
            sentimentScore: 66,
            operationAdvice: 'Watch',
            analysisCount: 1,
            lastAnalysisTime: '2026-03-19T09:00:00+07:00',
            assetType: 'index',
          },
        },
        {
          code: 'VNINDEX',
          assetType: 'stock',
          analyzedToday: true,
          latestItem: {
            id: 32,
            stockCode: 'VNINDEX',
            stockName: 'Same-code stock',
            sentimentScore: 71,
            operationAdvice: 'Buy',
            analysisCount: 1,
            lastAnalysisTime: '2026-03-19T09:00:00+07:00',
            assetType: 'stock',
          },
        },
      ],
      selectedStockCode: 'VNINDEX',
      selectedAssetType: 'stock',
      selectedRecordId: 32,
    });

    const stockRowButton = screen.getByTestId('watchlist-row-VNINDEX').querySelector('button[aria-pressed]');
    const indexRowButton = screen.getByTestId('watchlist-row-vnindex').querySelector('button[aria-pressed]');
    expect(stockRowButton).toHaveAttribute('aria-pressed', 'true');
    expect(indexRowButton).toHaveAttribute('aria-pressed', 'false');
  });

  it('selects the index row when the index report is active', () => {
    renderWorkspace({
      watchlistRows: [
        {
          code: 'vnindex',
          assetType: 'index',
          analyzedToday: true,
          latestItem: {
            id: 41,
            stockCode: 'VNINDEX',
            stockName: 'VN-Index',
            sentimentScore: 66,
            operationAdvice: 'Watch',
            analysisCount: 2,
            lastAnalysisTime: '2026-03-19T09:00:00+07:00',
            assetType: 'index',
          },
        },
        {
          code: 'VNINDEX',
          assetType: 'stock',
          analyzedToday: true,
          latestItem: {
            id: 42,
            stockCode: 'VNINDEX',
            stockName: 'Same-code stock',
            sentimentScore: 71,
            operationAdvice: 'Buy',
            analysisCount: 1,
            lastAnalysisTime: '2026-03-19T09:00:00+07:00',
            assetType: 'stock',
          },
        },
      ],
      selectedStockCode: 'vnindex',
      selectedAssetType: 'index',
      selectedRecordId: 41,
    });

    const indexRowButton = screen.getByTestId('watchlist-row-vnindex').querySelector('button[aria-pressed]');
    const stockRowButton = screen.getByTestId('watchlist-row-VNINDEX').querySelector('button[aria-pressed]');
    expect(indexRowButton).toHaveAttribute('aria-pressed', 'true');
    expect(stockRowButton).toHaveAttribute('aria-pressed', 'false');
  });

  it('selects an alias-form index row via its registry identity even without a latest detail (PR #2312)', () => {
    renderWorkspace({
      watchlistRows: [
        {
          code: 'VNINDEX.VN',
          assetType: 'index',
          identityKey: 'vnindex',
          analyzedToday: true,
        },
        {
          code: 'VNINDEX',
          assetType: 'stock',
          analyzedToday: true,
        },
      ],
      selectedStockCode: 'vnindex',
      selectedAssetType: 'index',
    });

    // No `selectedRecordId` and no latest detail on either row, so selection
    // must come from the identity comparison: the canonical report pairs with
    // the alias row through `identityKey` (HomePage's registry resolution) and
    // never with the same-code `VNINDEX` stock row.
    const aliasRowButton = screen.getByTestId('watchlist-row-VNINDEX.VN').querySelector('button[aria-pressed]');
    const stockRowButton = screen.getByTestId('watchlist-row-VNINDEX').querySelector('button[aria-pressed]');
    expect(aliasRowButton).toHaveAttribute('aria-pressed', 'true');
    expect(stockRowButton).toHaveAttribute('aria-pressed', 'false');
  });

  it('keeps the no-detail notice on the triggering index row instead of reusing the same-code stock row detail (PR #2312)', async () => {
    const { onHistoryItemClick } = renderWorkspace({
      watchlistRows: [
        {
          code: 'VNINDEX',
          assetType: 'stock',
          analyzedToday: true,
          latestItem: {
            id: 99,
            stockCode: 'VNINDEX',
            stockName: 'Same-code stock',
            sentimentScore: 70,
            operationAdvice: 'Buy',
            analysisCount: 1,
            lastAnalysisTime: '2026-03-19T09:00:00+07:00',
            assetType: 'stock',
          },
        },
        {
          code: 'VNINDEX.VN',
          assetType: 'index',
          analyzedToday: false,
        },
      ],
    });

    fireEvent.click(screen.getByTestId('watchlist-row-VNINDEX.VN').querySelector('button[aria-pressed]') as HTMLButtonElement);

    // The index row has no detail yet. The notice carries the triggering row's
    // own code+assetType (VNINDEX.VN / index); without that, the notice could
    // be swallowed by the same-code stock row (which HAS a detail).
    expect(await screen.findByRole('alert')).toHaveTextContent('No analysis details yet. Run an analysis first.');
    expect(onHistoryItemClick).not.toHaveBeenCalled();
  });
});
