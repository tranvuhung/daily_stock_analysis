/**
 * StockAutocomplete component tests.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type React from 'react';
import { StockAutocomplete } from '../StockAutocomplete';
import type { StockIndexItem, StockSuggestion } from '../../../types/stockIndex';

let stockIndexHookImpl: () => {
  index: StockIndexItem[];
  loading: boolean;
  fallback: boolean;
  error: Error | null;
  loaded: boolean;
};

let autocompleteHookImpl: () => {
  query: string;
  setQuery: ReturnType<typeof vi.fn>;
  suggestions: StockSuggestion[];
  isOpen: boolean;
  highlightedIndex: number;
  setHighlightedIndex: ReturnType<typeof vi.fn>;
  highlightPrevious: ReturnType<typeof vi.fn>;
  highlightNext: ReturnType<typeof vi.fn>;
  handleSelect: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  reset: ReturnType<typeof vi.fn>;
  isComposing: boolean;
  setIsComposing: ReturnType<typeof vi.fn>;
  runtimeFallback: boolean;
  error: Error | null;
};

// Mock the hooks
vi.mock('../../../hooks/useStockIndex', () => ({
  useStockIndex: () => stockIndexHookImpl(),
}));

vi.mock('../../../hooks/useAutocomplete', () => ({
  useAutocomplete: () => autocompleteHookImpl(),
}));

const mockIndex: StockIndexItem[] = [
  {
    canonicalCode: "FPT",
    displayCode: "FPT",
    nameZh: "FPT Corp",
    pinyinFull: "fptcorp",
    pinyinAbbr: "fc",
    aliases: ["FPT"],
    market: "VN",
    assetType: "stock",
    active: true,
    popularity: 100,
  },
];

const mockSuggestions: StockSuggestion[] = [
  {
    canonicalCode: "FPT",
    displayCode: "FPT",
    nameZh: "FPT Corp",
    market: "VN",
    matchType: "exact" as const,
    matchField: "code" as const,
    score: 100,
  },
];

const vnSuggestion = {
  canonicalCode: "HPG",
  displayCode: "HPG",
  nameZh: "Hòa Phát",
  market: "VN" as const,
  matchType: "exact" as const,
  matchField: "code" as const,
  score: 100,
};

const vnIndexSuggestion = {
  canonicalCode: "VNINDEX",
  displayCode: "VNINDEX",
  nameZh: "Chỉ số VN-Index",
  market: "VN" as const,
  matchType: "exact" as const,
  matchField: "code" as const,
  score: 100,
};

describe('StockAutocomplete', () => {
  const mockOnChange = vi.fn();
  const mockOnSubmit = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    stockIndexHookImpl = () => ({
      index: mockIndex,
      loading: false,
      fallback: false,
      error: null,
      loaded: true,
    });
    autocompleteHookImpl = () => ({
      query: '',
      setQuery: vi.fn(),
      suggestions: mockSuggestions,
      isOpen: false,
      highlightedIndex: -1,
      setHighlightedIndex: vi.fn(),
      highlightPrevious: vi.fn(),
      highlightNext: vi.fn(),
      handleSelect: vi.fn(),
      close: vi.fn(),
      reset: vi.fn(),
      isComposing: false,
      setIsComposing: vi.fn(),
      runtimeFallback: false,
      error: null,
    });
  });

  it('renders the input element', () => {
    render(
      <StockAutocomplete
        value=""
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
      />
    );

    const input = screen.getByPlaceholderText(/Enter a stock code or name/);
    expect(input).toBeInTheDocument();
  });

  it('renders a custom placeholder', () => {
    render(
      <StockAutocomplete
        value=""
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
        placeholder="Nhập mã"
      />
    );

    const input = screen.getByPlaceholderText(/Nhập mã/);
    expect(input).toBeInTheDocument();
  });

  it('renders the current value', () => {
    render(
      <StockAutocomplete
        value="HPG"
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
      />
    );

    const input = screen.getByDisplayValue('HPG');
    expect(input).toBeInTheDocument();
  });

  it('supports the disabled state', () => {
    render(
      <StockAutocomplete
        value=""
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
        disabled={true}
      />
    );

    const input = screen.getByRole('combobox');
    expect(input).toBeDisabled();
  });

  it('calls onChange when the input changes', () => {
    render(
      <StockAutocomplete
        value=""
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
      />
    );

    const input = screen.getByRole('combobox');
    fireEvent.change(input, { target: { value: 'HPG' } });

    expect(mockOnChange).toHaveBeenCalledWith('HPG');
  });

  it('applies a custom class name', () => {
    const { container } = render(
      <StockAutocomplete
        value=""
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
        className="custom-class"
      />
    );

    const input = container.querySelector('.custom-class');
    expect(input).toBeInTheDocument();
  });

  it('exposes the expected accessibility attributes', () => {
    render(
      <StockAutocomplete
        value=""
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
      />
    );

    const input = screen.getByRole('combobox');
    expect(input).toHaveAttribute('aria-autocomplete', 'none');
    expect(input).toHaveAttribute('role', 'combobox');
  });

  it('applies an accessible label to the autocomplete input', () => {
    render(
      <StockAutocomplete
        value=""
        onChange={mockOnChange}
        onSubmit={mockOnSubmit}
        ariaLabel="Current stock"
      />
    );

    expect(screen.getByLabelText('Current stock')).toBeInTheDocument();
  });

  describe('fallback mode', () => {
    it('renders a plain input when index loading fallback is active', () => {
      stockIndexHookImpl = () => ({
        index: [],
        loading: false,
        fallback: true,
        error: new Error('Index load failed'),
        loaded: false,
      });

      render(
        <StockAutocomplete
          value=""
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByPlaceholderText(/Enter a stock code or name/);
      expect(input).toHaveAttribute('data-autocomplete-mode', 'fallback');
    });

    it('renders a plain input when autocomplete runtime fallback is active', () => {
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: [],
        isOpen: false,
        highlightedIndex: -1,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: true,
        error: new Error('Search crashed'),
      });

      render(
        <StockAutocomplete
          value=""
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByPlaceholderText(/Enter a stock code or name/);
      expect(input).toHaveAttribute('data-autocomplete-mode', 'fallback');
    });

    it('submits manually when fallback input receives Enter', () => {
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: [],
        isOpen: false,
        highlightedIndex: -1,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: true,
        error: new Error('Search crashed'),
      });

      render(
        <StockAutocomplete
          value="HPG"
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByDisplayValue('HPG');
      fireEvent.keyDown(input, { key: 'Enter' });

      expect(mockOnSubmit).toHaveBeenCalledWith('HPG');
    });

    it('applies an accessible label to the fallback input', () => {
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: [],
        isOpen: false,
        highlightedIndex: -1,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: true,
        error: new Error('Search crashed'),
      });

      render(
        <StockAutocomplete
          value=""
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
          ariaLabel="Current stock"
        />
      );

      expect(screen.getByLabelText('Current stock')).toHaveAttribute('data-autocomplete-mode', 'fallback');
    });

    it('prevents duplicate form submission when fallback input receives Enter', () => {
      const formSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: [],
        isOpen: false,
        highlightedIndex: -1,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: true,
        error: new Error('Search crashed'),
      });

      render(
        <form onSubmit={formSubmit}>
          <StockAutocomplete
            value="HPG"
            onChange={mockOnChange}
            onSubmit={mockOnSubmit}
          />
        </form>
      );

      fireEvent.keyDown(screen.getByDisplayValue('HPG'), { key: 'Enter' });

      expect(mockOnSubmit).toHaveBeenCalledTimes(1);
      expect(formSubmit).not.toHaveBeenCalled();
    });
  });

  describe('IME support', () => {
    it('handles composition start and end events', () => {
      render(
        <StockAutocomplete
          value=""
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByRole('combobox');

      fireEvent.compositionStart(input);
      fireEvent.compositionEnd(input);

      // The events should be handled without throwing.
      expect(input).toBeInTheDocument();
    });
  });

  describe('keyboard submission', () => {
    it('submits the raw input when suggestions are open but nothing is highlighted', () => {
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: mockSuggestions,
        isOpen: true,
        highlightedIndex: -1,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: false,
        error: null,
      });

      render(
        <StockAutocomplete
          value="FP"
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByDisplayValue('FP');
      fireEvent.keyDown(input, { key: 'Enter' });

      expect(mockOnSubmit).toHaveBeenCalledWith('FP');
    });

    it('submits the highlighted suggestion when one is explicitly selected', () => {
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: mockSuggestions,
        isOpen: true,
        highlightedIndex: 0,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: false,
        error: null,
      });

      render(
        <StockAutocomplete
          value="FP"
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByDisplayValue('FP');
      fireEvent.keyDown(input, { key: 'Enter' });

      expect(mockOnChange).toHaveBeenCalledWith('FPT');
      expect(mockOnSubmit).toHaveBeenCalledWith('FPT', 'FPT Corp', 'autocomplete', {
        market: 'VN',
        displayCode: 'FPT',
      });
    });

  });

  describe('runtime boundary', () => {
    it('renders VN market badges without falling back to plain input', () => {
      // Hồi quy: thiếu VN trong MARKET_BADGE_CONFIG khiến MarketBadge ném lỗi và
      // toàn bộ autocomplete rơi về ô nhập thường.
      // Regression: a missing VN badge config made the autocomplete fall back.
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: [vnSuggestion, vnIndexSuggestion],
        isOpen: true,
        highlightedIndex: 0,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: false,
        error: null,
      });

      render(
        <StockAutocomplete
          value="HPG"
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByDisplayValue('HPG');
      fireEvent.focus(input);

      expect(input).not.toHaveAttribute('data-autocomplete-mode', 'fallback');
      expect(screen.getAllByText('VN')).toHaveLength(2);
      expect(screen.getByText('Hòa Phát')).toBeInTheDocument();
      expect(screen.getByText('Chỉ số VN-Index')).toBeInTheDocument();
    });

    it('falls back to the plain input when the autocomplete tree throws during render', () => {
      autocompleteHookImpl = () => {
        throw new Error('Autocomplete render failed');
      };

      render(
        <StockAutocomplete
          value="VNM"
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByDisplayValue('VNM');
      expect(input).toHaveAttribute('data-autocomplete-mode', 'fallback');
    });

    it('falls back to the plain input when a suggestion contains an unsupported market', () => {
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      autocompleteHookImpl = () => ({
        query: '',
        setQuery: vi.fn(),
        suggestions: [
          {
            canonicalCode: 'TEST.OTC',
            displayCode: 'TEST',
            nameZh: 'Thị trường thử',
            market: 'OTC' as never,
            matchType: 'exact' as const,
            matchField: 'code' as const,
            score: 100,
          },
        ],
        isOpen: true,
        highlightedIndex: 0,
        setHighlightedIndex: vi.fn(),
        highlightPrevious: vi.fn(),
        highlightNext: vi.fn(),
        handleSelect: vi.fn(),
        close: vi.fn(),
        reset: vi.fn(),
        isComposing: false,
        setIsComposing: vi.fn(),
        runtimeFallback: false,
        error: null,
      });

      render(
        <StockAutocomplete
          value="TEST"
          onChange={mockOnChange}
          onSubmit={mockOnSubmit}
        />
      );

      const input = screen.getByDisplayValue('TEST');
      fireEvent.focus(input);

      const fallbackInput = screen.getByDisplayValue('TEST');
      expect(fallbackInput).toHaveAttribute('data-autocomplete-mode', 'fallback');
      expect(consoleErrorSpy).toHaveBeenCalled();
      consoleErrorSpy.mockRestore();
    });
  });
});
