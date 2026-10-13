import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntelligentImport } from '../IntelligentImport';
import { SystemConfigConflictError } from '../../../api/systemConfig';

const { parseImport, update, onMerged } = vi.hoisted(() => ({
  parseImport: vi.fn(),
  update: vi.fn(),
  onMerged: vi.fn(),
}));

vi.mock('../../../api/stocks', () => ({
  stocksApi: {
    parseImport,
    extractFromImage: vi.fn(),
  },
}));

vi.mock('../../../api/systemConfig', async () => {
  const actual = await vi.importActual<typeof import('../../../api/systemConfig')>('../../../api/systemConfig');
  return {
    ...actual,
    systemConfigApi: {
      ...actual.systemConfigApi,
      update,
    },
  };
});

describe('IntelligentImport', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens the matching hidden file input when the picker buttons are clicked', () => {
    const { container } = render(
      <IntelligentImport
        stockListValue="HPG"
        configVersion="v1"
        maskToken="******"
        onMerged={onMerged}
      />,
    );

    const inputs = container.querySelectorAll('input[type="file"]');
    expect(inputs).toHaveLength(2);

    const imageClick = vi.fn();
    const dataClick = vi.fn();

    Object.defineProperty(inputs[0], 'click', {
      value: imageClick,
      configurable: true,
    });
    Object.defineProperty(inputs[1], 'click', {
      value: dataClick,
      configurable: true,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Choose image' }));
    fireEvent.click(screen.getByRole('button', { name: 'Choose file' }));

    expect(imageClick).toHaveBeenCalledTimes(1);
    expect(dataClick).toHaveBeenCalledTimes(1);
  });

  it('does not open hidden file inputs when the import actions are disabled', () => {
    const { container } = render(
      <IntelligentImport
        stockListValue="HPG"
        configVersion="v1"
        maskToken="******"
        onMerged={onMerged}
        disabled
      />,
    );

    const inputs = container.querySelectorAll('input[type="file"]');
    expect(inputs).toHaveLength(2);

    const imageClick = vi.fn();
    const dataClick = vi.fn();

    Object.defineProperty(inputs[0], 'click', {
      value: imageClick,
      configurable: true,
    });
    Object.defineProperty(inputs[1], 'click', {
      value: dataClick,
      configurable: true,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Choose image' }));
    fireEvent.click(screen.getByRole('button', { name: 'Choose file' }));

    expect(imageClick).not.toHaveBeenCalled();
    expect(dataClick).not.toHaveBeenCalled();
  });

  it('refreshes config state after a config version conflict', async () => {
    parseImport.mockResolvedValue({
      items: [{ code: 'MWG', name: 'Ping An Bank', confidence: 'high' }],
      codes: [],
    });
    update.mockRejectedValue(
      new SystemConfigConflictError('Configuration version conflict', 'v2'),
    );

    render(
      <IntelligentImport
        stockListValue="HPG"
        configVersion="v1"
        maskToken="******"
        onMerged={onMerged}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('Or paste text copied from CSV/Excel...'), {
      target: { value: 'VCB' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }));

    await screen.findByText('MWG');

    fireEvent.click(screen.getByRole('button', { name: 'Merge into watchlist' }));

    await waitFor(() => {
      expect(update).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(onMerged).toHaveBeenCalledWith('HPG,MWG');
    });
    expect(await screen.findByText('Configuration changed. Click "Merge into watchlist" again.')).toBeInTheDocument();
  });

  it('normalizes existing mixed separators when merging into watchlist', async () => {
    parseImport.mockResolvedValue({
      items: [{ code: 'VNM', name: 'Vinamilk', confidence: 'high' }],
      codes: [],
    });
    update.mockResolvedValue({ success: true });

    render(
      <IntelligentImport
        stockListValue="HPG;FPT VCB"
        configVersion="v1"
        maskToken="******"
        onMerged={onMerged}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('Or paste text copied from CSV/Excel...'), {
      target: { value: 'VNM' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Parse' }));

    await screen.findByText('VNM');

    fireEvent.click(screen.getByRole('button', { name: 'Merge into watchlist' }));

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith({
        configVersion: 'v1',
        maskToken: '******',
        reloadNow: true,
        items: [{ key: 'STOCK_LIST', value: 'HPG,FPT,VCB,VNM' }],
      });
    });
    await waitFor(() => {
      expect(onMerged).toHaveBeenCalledWith('HPG,FPT,VCB,VNM');
    });
  });
});
