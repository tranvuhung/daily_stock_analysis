import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { MarketReviewRegionSelector } from '../MarketReviewRegionSelector';
import { serializeMarketReviewRegions } from '../../../utils/marketReviewRegion';

describe('MarketReviewRegionSelector', () => {
  beforeEach(() => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');
  });

  it('serializes canonical UI selections at the HTTP boundary', () => {
    expect(serializeMarketReviewRegions(['kr', 'jp'])).toBe('jp,kr');
    expect(serializeMarketReviewRegions(['cn', 'hk', 'us', 'jp', 'kr', 'vn'])).toBe('both');
    expect(serializeMarketReviewRegions(['cn', 'hk', 'us', 'jp', 'kr'])).toBe('cn,hk,us,jp,kr');
    expect(serializeMarketReviewRegions(['vn'])).toBe('vn');
  });

  it('keeps the runtime-resolved server default opaque and emits a canonical override', () => {
    const onChange = vi.fn();
    render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector onChange={onChange} />
      </UiLanguageProvider>,
    );

    expect(screen.getByRole('button', { name: 'Select market review regions' })).toHaveTextContent(
      'Server default',
    );
    expect(screen.getByRole('button', { name: 'Select market review regions' })).not.toHaveTextContent('A-share');
    fireEvent.click(screen.getByRole('button', { name: 'Select market review regions' }));
    expect(screen.getByText('Resolved by the server when submitted')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /A-share/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /US/ })).not.toBeChecked();

    fireEvent.click(screen.getByRole('checkbox', { name: /JP/ }));
    expect(onChange).toHaveBeenLastCalledWith(['jp']);
  });

  it('supports all markets and restoring the server default', () => {
    const onChange = vi.fn();
    render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector
          value={['us']}
          onChange={onChange}
        />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Select market review regions' }));
    fireEvent.click(screen.getByRole('button', { name: 'All markets' }));
    expect(onChange).toHaveBeenLastCalledWith(['cn', 'hk', 'us', 'jp', 'kr', 'vn']);

    fireEvent.click(screen.getByRole('button', { name: /Server default/ }));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it('keeps at least one market selected in override mode', () => {
    const onChange = vi.fn();
    render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector value={['us']} onChange={onChange} />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Select market review regions' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /US/ }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('closes an open menu and blocks every option when disabled', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <UiLanguageProvider>
        <MarketReviewRegionSelector value={['us']} onChange={onChange} />
      </UiLanguageProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Select market review regions' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    const cnCheckbox = screen.getByRole('checkbox', { name: /A-share/ });

    rerender(
      <UiLanguageProvider>
        <MarketReviewRegionSelector value={['us']} disabled onChange={onChange} />
      </UiLanguageProvider>,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Select market review regions' })).toBeDisabled();
    fireEvent.click(cnCheckbox);
    expect(onChange).not.toHaveBeenCalled();
  });
});
