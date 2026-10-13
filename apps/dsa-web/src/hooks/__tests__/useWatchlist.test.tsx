import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useWatchlist } from '../useWatchlist';

const {
  mockGetWatchlist,
  mockAddToWatchlist,
  mockRemoveFromWatchlist,
} = vi.hoisted(() => ({
  mockGetWatchlist: vi.fn(),
  mockAddToWatchlist: vi.fn(),
  mockRemoveFromWatchlist: vi.fn(),
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getWatchlist: mockGetWatchlist,
    addToWatchlist: mockAddToWatchlist,
    removeFromWatchlist: mockRemoveFromWatchlist,
  },
}));

describe('useWatchlist', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetWatchlist.mockResolvedValue([]);
    mockAddToWatchlist.mockResolvedValue([]);
    mockRemoveFromWatchlist.mockResolvedValue([]);
  });

  it('matches raw VN watchlist entries against prefixed and suffixed variants', async () => {
    mockGetWatchlist.mockResolvedValue(['HPG']);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isInWatchlist('HPG')).toBe(true);
    expect(result.current.isInWatchlist('vnHPG')).toBe(true);
    expect(result.current.isInWatchlist('HPG.VN')).toBe(true);
    expect(result.current.isInWatchlist('FPT')).toBe(false);
  });

  it('removes the matched raw watchlist entry instead of adding a duplicate variant', async () => {
    mockGetWatchlist.mockResolvedValue(['HPG']);
    mockRemoveFromWatchlist.mockResolvedValue([]);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    await act(async () => {
      await result.current.toggleWatchlist('HPG.VN');
    });

    expect(mockRemoveFromWatchlist).toHaveBeenCalledWith('HPG');
    expect(mockAddToWatchlist).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(result.current.watchlistCodes).toEqual([]);
    });
  });

  it('compares submitted and stored VN tickers case-insensitively', async () => {
    mockGetWatchlist.mockResolvedValue(['fpt']);
    mockRemoveFromWatchlist.mockResolvedValue([]);

    const { result } = renderHook(() => useWatchlist());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isInWatchlist('FPT')).toBe(true);

    await act(async () => {
      await result.current.toggleWatchlist('FPT');
    });

    expect(mockRemoveFromWatchlist).toHaveBeenCalledWith('fpt');
    expect(mockAddToWatchlist).not.toHaveBeenCalled();
  });
});
