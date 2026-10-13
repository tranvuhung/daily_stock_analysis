import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart3, Clipboard, FileText, Gauge, Layers, ShieldAlert, TrendingUp, WalletCards, Workflow } from 'lucide-react';
import { historyApi } from '../../api/history';
import { formatUiText, UI_TEXT } from '../../i18n/uiText';
import type {
  AnalysisReport,
  MarketReviewPayload,
  MarketReviewPayloadSection,
  ReportLanguage,
} from '../../types/analysis';
import { markdownToPlainText } from '../../utils/markdown';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { Card } from '../common';
import { Tooltip } from '../common/Tooltip';
import { ReportMarkdownBody } from './ReportMarkdownBody';
import { ShareImageButton } from './ShareImageButton';

interface MarketReviewReportViewProps {
  report?: AnalysisReport;
  recordId?: number;
  content?: string;
  payload?: MarketReviewPayload | null;
  reportLanguage?: ReportLanguage;
  className?: string;
  onOpenRunFlow?: (recordId: number) => void;
}

type CopyType = 'markdown' | 'text';
type LoadedMarkdown = {
  recordId: number;
  content: string;
};
type LoadError = {
  recordId: number;
  message: string;
};
type MarketReviewSection = {
  id: string;
  title: string;
  content: string;
  icon: typeof FileText;
};
type StructuredMarketData = {
  id: string;
  title?: string;
  breadth?: MarketReviewPayload['breadth'];
  indices: NonNullable<MarketReviewPayload['indices']>;
};

const isMarketReviewPayload = (value: unknown): value is MarketReviewPayload =>
  Boolean(value && typeof value === 'object');

const TOP_HEADING_PATTERN = /^\s*#\s+(.+?)\s*(?:\n+|$)/;
const SECTION_HEADING_PATTERN = /^(#{2,3})\s+(.+?)\s*$/gm;

const normalizeHeading = (value: string): string =>
  value.trim().replace(/\s+/g, ' ').toLowerCase();

const stripTopHeading = (markdown: string, title?: string): string => {
  const match = markdown.match(TOP_HEADING_PATTERN);
  if (!match) {
    return markdown.trim();
  }

  const heading = normalizeHeading(match[1]);
  const reportTitle = normalizeHeading(title || '');
  // Các chuỗi để NHẬN DẠNG tiêu đề do backend sinh ra (chỉ vi/en), không phải nhãn giao diện.
  // Strings used to RECOGNISE backend-generated titles (vi/en only), not UI labels.
  const genericTitles = new Set([
    'market review',
    'điểm lại thị trường',
    'điểm lại thị trường chi tiết',
  ]);

  if (heading === reportTitle || genericTitles.has(heading)) {
    return markdown.slice(match[0].length).trim();
  }

  return markdown.trim();
};

const getSectionIcon = (title: string): typeof FileText => {
  // Các biểu thức dưới đây khớp TIÊU ĐỀ MỤC do backend sinh ra (vi/en) để chọn icon — là
  // logic nhận dạng dữ liệu, không phải nhãn hiển thị.
  // Patterns below match backend-generated section titles (vi/en) to pick an icon.
  const normalized = normalizeHeading(title);
  if (/chỉ số|toàn thị trường|index|overview/.test(normalized)) {
    return BarChart3;
  }
  if (/tâm lý|độ rộng|sentiment|breadth|temperature/.test(normalized)) {
    return Gauge;
  }
  if (/ngành|nhóm ngành|chủ đề|luân chuyển|sector|theme|rotation/.test(normalized)) {
    return TrendingUp;
  }
  if (/dòng tiền|thanh khoản|giá trị giao dịch|flow|turnover|volume|capital/.test(normalized)) {
    return WalletCards;
  }
  if (/rủi ro|cơ hội|theo dõi|risk|watch|next/.test(normalized)) {
    return ShieldAlert;
  }
  return FileText;
};

const splitMarketReviewSections = (markdown: string): MarketReviewSection[] => {
  const matches = Array.from(markdown.matchAll(SECTION_HEADING_PATTERN));
  if (matches.length === 0) {
    return [{
      id: 'full-review',
      title: 'Review body',
      content: markdown,
      icon: FileText,
    }];
  }

  const intro = markdown.slice(0, matches[0].index).trim();
  const sections: MarketReviewSection[] = intro
    ? [{
        id: 'overview',
        title: 'Review overview',
        content: intro,
        icon: FileText,
      }]
    : [];

  matches.forEach((match, index) => {
    const start = (match.index ?? 0) + match[0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index ?? markdown.length : markdown.length;
    const title = match[2].trim();
    const content = markdown.slice(start, end).trim();
    if (!content) {
      return;
    }
    sections.push({
      id: `${index}-${normalizeHeading(title).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'section'}`,
      title,
      content,
      icon: getSectionIcon(title),
    });
  });

  return sections;
};

const getPayloadSections = (payload?: MarketReviewPayload | null): MarketReviewSection[] => {
  if (!payload) {
    return [];
  }

  const payloadTitle = normalizeHeading(payload.title || '');
  return (payload.sections || [])
    .filter((section: MarketReviewPayloadSection) => section.markdown?.trim())
    .filter((section: MarketReviewPayloadSection) => normalizeHeading(section.title || '') !== payloadTitle)
    .map((section, index) => ({
      id: `${section.key || index}-${normalizeHeading(section.title).replace(/[^a-z0-9]+/g, '-') || 'section'}`,
      title: section.title || 'Review',
      content: section.markdown,
      icon: getSectionIcon(section.title || ''),
    }));
};

const hasStructuredMarketData = (payload?: MarketReviewPayload | null): boolean =>
  Boolean(payload?.breadth || payload?.indices?.length);

const getStructuredMarketData = (payload?: MarketReviewPayload | null): StructuredMarketData[] => {
  if (!payload) {
    return [];
  }

  if (!hasStructuredMarketData(payload)) {
    return [];
  }

  return [{
    id: payload.region || 'market',
    title: payload.title,
    breadth: payload.breadth,
    indices: payload.indices || [],
  }];
};

const coerceFiniteNumber = (value: unknown): number | null => {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }

  if (typeof value === 'string' && value.trim()) {
    const normalizedValue = value.trim().replace(/,/g, '');
    const numericText = normalizedValue.endsWith('%')
      ? normalizedValue.slice(0, -1).trim()
      : normalizedValue;
    const parsed = Number(numericText);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
};

const formatMarketNumber = (value: unknown, options?: { zeroAsMissing?: boolean }): string => {
  const numericValue = coerceFiniteNumber(value);
  if (numericValue === null || (options?.zeroAsMissing && numericValue === 0)) {
    return '-';
  }
  return numericValue.toFixed(2);
};

const formatMarketCount = (value: unknown): string => {
  const numericValue = coerceFiniteNumber(value);
  return numericValue === null ? '-' : numericValue.toFixed(0);
};

const formatMarketAmount = (value: unknown, unit?: string): string => {
  const formattedValue = formatMarketNumber(value);
  if (formattedValue === '-') {
    return '-';
  }
  return unit ? `${formattedValue} ${unit}` : formattedValue;
};

const formatMarketPercent = (value: unknown): string => {
  const formattedValue = formatMarketNumber(value);
  return formattedValue === '-' ? '-' : `${formattedValue}%`;
};

const formatMarketHighLow = (high: unknown, low: unknown): string => {
  const highText = formatMarketNumber(high, { zeroAsMissing: true });
  const lowText = formatMarketNumber(low, { zeroAsMissing: true });
  return highText === '-' && lowText === '-' ? '-' : `${highText} / ${lowText}`;
};

const MARKET_REVIEW_TEXT: Record<ReportLanguage, {
  reviewSummary: string;
  noReviewSummary: string;
  noSentimentScore: string;
  rotationAndFunds: string;
  noRotationView: string;
  riskAndWatch: string;
  noRiskWatch: string;
  structuredMarketData: string;
  noBreadthData: string;
  advancers: string;
  decliners: string;
  limitUpDown: string;
  turnover: string;
  index: string;
  last: string;
  change: string;
  highLow: string;
}> = {
  vi: {
    reviewSummary: 'Tóm tắt điểm lại',
    noReviewSummary: 'Chưa có tóm tắt',
    noSentimentScore: 'Chưa có điểm',
    rotationAndFunds: 'Luân chuyển và dòng tiền',
    noRotationView: 'Chưa có nhận định luân chuyển',
    riskAndWatch: 'Rủi ro và theo dõi',
    noRiskWatch: 'Chưa có trọng tâm theo dõi',
    structuredMarketData: 'Dữ liệu thị trường có cấu trúc',
    noBreadthData: 'Chưa có dữ liệu',
    advancers: 'Số mã tăng',
    decliners: 'Số mã giảm',
    limitUpDown: 'Trần/Sàn',
    turnover: 'Giá trị giao dịch',
    index: 'Chỉ số',
    last: 'Mới nhất',
    change: 'Biến động',
    highLow: 'Cao/Thấp',
  },
  en: {
    reviewSummary: 'Review Summary',
    noReviewSummary: 'No review summary yet',
    noSentimentScore: 'No score yet',
    rotationAndFunds: 'Rotation & Funds',
    noRotationView: 'No rotation view yet',
    riskAndWatch: 'Risks & Watchlist',
    noRiskWatch: 'No key observations yet',
    structuredMarketData: 'Structured Market Data',
    noBreadthData: 'No data',
    advancers: 'Advancers',
    decliners: 'Decliners',
    limitUpDown: 'Limit Up/Down',
    turnover: 'Turnover',
    index: 'Index',
    last: 'Last',
    change: 'Change',
    highLow: 'High/Low',
  },
};

export const MarketReviewReportView: React.FC<MarketReviewReportViewProps> = ({
  report,
  recordId,
  content: providedContent,
  payload: providedPayload,
  reportLanguage = 'vi',
  className = '',
  onOpenRunFlow,
}) => {
  const { language: uiLanguage } = useUiLanguage();
  const normalizedReportLanguage = normalizeReportLanguage(reportLanguage);
  const text = getReportText(normalizedReportLanguage);
  const runFlowText = UI_TEXT[uiLanguage];
  const marketReviewText = MARKET_REVIEW_TEXT[normalizedReportLanguage];
  const [loadedMarkdown, setLoadedMarkdown] = useState<LoadedMarkdown | null>(null);
  const [loadError, setLoadError] = useState<LoadError | null>(null);
  const [copiedType, setCopiedType] = useState<CopyType | null>(null);
  const summary = report?.summary;
  const meta = report?.meta;
  const contextPayload = report?.details?.contextSnapshot?.marketReviewPayload;
  const marketReviewPayload = providedPayload ?? (isMarketReviewPayload(contextPayload) ? contextPayload : null);
  const loadedContent = loadedMarkdown && loadedMarkdown.recordId === recordId ? loadedMarkdown.content : '';
  const content = providedContent ?? marketReviewPayload?.markdownReport ?? loadedContent;
  const error = loadError && loadError.recordId === recordId ? loadError.message : null;
  const hasStructuredContent = Boolean(marketReviewPayload?.sections?.length);
  const isLoading = Boolean(recordId && !providedContent && !hasStructuredContent && loadedMarkdown?.recordId !== recordId && !error);
  const displayTitle = marketReviewPayload?.rootTitle || marketReviewPayload?.title || meta?.stockName || 'Market Review';
  const structuredContent = useMemo(
    () => stripTopHeading(content, displayTitle),
    [content, displayTitle],
  );
  const sections = useMemo(
    () => {
      const payloadSections = getPayloadSections(marketReviewPayload);
      return payloadSections.length > 0 ? payloadSections : splitMarketReviewSections(structuredContent);
    },
    [marketReviewPayload, structuredContent],
  );
  const structuredMarketData = useMemo(
    () => getStructuredMarketData(marketReviewPayload),
    [marketReviewPayload],
  );
  const canOpenRunFlow = recordId !== undefined && onOpenRunFlow;

  useEffect(() => {
    if (!recordId || providedContent || hasStructuredContent) {
      return undefined;
    }

    let isMounted = true;

    historyApi.getMarkdown(recordId)
      .then((markdownContent) => {
        if (isMounted) {
          setLoadedMarkdown({ recordId, content: markdownContent });
          setLoadError(null);
        }
      })
      .catch((err: unknown) => {
        if (isMounted) {
          setLoadError({
            recordId,
            message: err instanceof Error ? err.message : text.loadReportFailed,
          });
        }
      });

    return () => {
      isMounted = false;
    };
  }, [hasStructuredContent, providedContent, recordId, text.loadReportFailed]);

  const handleCopy = useCallback(async (type: CopyType) => {
    if (!content) {
      return;
    }
    try {
      const value = type === 'markdown' ? content : markdownToPlainText(content);
      await navigator.clipboard.writeText(value);
      setCopiedType(type);
      window.setTimeout(() => setCopiedType(null), 2000);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  }, [content]);

  const insightCards = useMemo(() => [
    {
      icon: FileText,
      label: marketReviewText.reviewSummary,
      value: summary?.analysisSummary || marketReviewText.noReviewSummary,
    },
    {
      icon: Gauge,
      label: text.marketSentiment,
      value: summary?.sentimentScore !== undefined
        ? `${summary.sentimentScore} / 100`
        : marketReviewText.noSentimentScore,
    },
    {
      icon: Layers,
      label: marketReviewText.rotationAndFunds,
      value: summary?.operationAdvice || marketReviewText.noRotationView,
    },
    {
      icon: ShieldAlert,
      label: marketReviewText.riskAndWatch,
      value: summary?.trendPrediction || marketReviewText.noRiskWatch,
    },
  ], [marketReviewText, summary, text.marketSentiment]);

  return (
    <div className={`animate-fade-in space-y-4 pb-8 ${className}`}>
      <Card variant="gradient" padding="md" className="home-report-hero text-left">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 inline-flex items-center gap-2 text-xs font-semibold text-secondary-text">
              <BarChart3 className="h-4 w-4" aria-hidden="true" />
              <span>MARKET REVIEW</span>
            </div>
            <h2 className="text-[26px] font-bold leading-tight text-foreground sm:text-[30px]">
              {displayTitle}
            </h2>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-text">
              {meta?.stockCode ? (
                <span className="home-accent-chip px-2 py-0.5 font-mono">{meta.stockCode}</span>
              ) : null}
              {meta?.createdAt ? <span>{new Date(meta.createdAt).toLocaleString()}</span> : null}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <ShareImageButton
              recordId={recordId}
              reportTitle={displayTitle}
              reportLanguage={reportLanguage}
            />
            {canOpenRunFlow ? (
              <Tooltip content={runFlowText['runFlow.open']}>
                <span className="inline-flex">
                  <button
                    type="button"
                    onClick={() => onOpenRunFlow(recordId)}
                    className="home-surface-button flex h-10 w-10 items-center justify-center rounded-lg text-secondary-text hover:text-foreground"
                    aria-label={formatUiText(runFlowText['runFlow.openHistoryAria'], { recordId })}
                  >
                    <Workflow className="h-5 w-5" aria-hidden="true" />
                  </button>
                </span>
              </Tooltip>
            ) : null}
            <Tooltip content={text.copyMarkdownSource}>
              <span className="inline-flex">
                <button
                  type="button"
                  onClick={() => void handleCopy('markdown')}
                  disabled={isLoading || !content || copiedType !== null}
                  className="home-surface-button flex h-10 w-10 items-center justify-center rounded-lg text-secondary-text hover:text-foreground disabled:opacity-50"
                  aria-label={text.copyMarkdownSource}
                >
                  {copiedType === 'markdown' ? (
                    <svg className="h-5 w-5 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <Clipboard className="h-5 w-5" aria-hidden="true" />
                  )}
                </button>
              </span>
            </Tooltip>
            <Tooltip content={text.copyPlainText}>
              <span className="inline-flex">
                <button
                  type="button"
                  onClick={() => void handleCopy('text')}
                  disabled={isLoading || !content || copiedType !== null}
                  className="home-surface-button flex h-10 w-10 items-center justify-center rounded-lg text-secondary-text hover:text-foreground disabled:opacity-50"
                  aria-label={text.copyPlainText}
                >
                  {copiedType === 'text' ? (
                    <svg className="h-5 w-5 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <FileText className="h-5 w-5" aria-hidden="true" />
                  )}
                </button>
              </span>
            </Tooltip>
          </div>
        </div>
      </Card>

      {summary ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {insightCards.map(({ icon: Icon, label, value }) => (
            <Card key={label} variant="bordered" padding="sm" className="home-panel-card text-left">
              <div className="flex items-start gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </div>
                <div className="min-w-0">
                  <p className="label-uppercase">{label}</p>
                  <p className="mt-2 line-clamp-4 text-sm leading-6 text-foreground">{value}</p>
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : null}

      {structuredMarketData.length > 0 ? (
        <Card variant="bordered" padding="md" className="home-panel-card text-left">
          <div className="mb-3 flex items-center gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <BarChart3 className="h-4 w-4" aria-hidden="true" />
            </span>
            <h3 className="text-base font-semibold text-foreground">{marketReviewText.structuredMarketData}</h3>
          </div>
          <div className="space-y-5">
            {structuredMarketData.map((marketData) => (
              <div key={marketData.id} className="space-y-3">
                {marketData.breadth ? (
                  <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
                    <div className="rounded-lg border border-subtle p-3">
                      <p className="label-uppercase">{marketReviewText.advancers}</p>
                      <p className="mt-1 font-semibold text-foreground">
                        {formatMarketCount(marketData.breadth.upCount)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-subtle p-3">
                      <p className="label-uppercase">{marketReviewText.decliners}</p>
                      <p className="mt-1 font-semibold text-foreground">
                        {formatMarketCount(marketData.breadth.downCount)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-subtle p-3">
                      <p className="label-uppercase">{marketReviewText.limitUpDown}</p>
                      <p className="mt-1 font-semibold text-foreground">
                        {formatMarketCount(marketData.breadth.limitUpCount)} /{' '}
                        {formatMarketCount(marketData.breadth.limitDownCount)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-subtle p-3">
                      <p className="label-uppercase">{marketReviewText.turnover}</p>
                      <p className="mt-1 font-semibold text-foreground">
                        {formatMarketAmount(marketData.breadth.totalAmount, marketData.breadth.turnoverUnit)}
                      </p>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-secondary-text">{marketReviewText.noBreadthData}</p>
                )}
                {marketData.indices.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-sm">
                      <thead className="text-left text-xs uppercase text-muted-text">
                        <tr>
                          <th className="px-2 py-2">{marketReviewText.index}</th>
                          <th className="px-2 py-2">{marketReviewText.last}</th>
                          <th className="px-2 py-2">{marketReviewText.change}</th>
                          <th className="px-2 py-2">{marketReviewText.highLow}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-subtle">
                        {marketData.indices.map((index) => (
                          <tr key={index.code || index.name}>
                            <td className="px-2 py-2 font-medium text-foreground">{index.name}</td>
                            <td className="px-2 py-2 text-secondary-text">{formatMarketNumber(index.current)}</td>
                            <td className="px-2 py-2 text-secondary-text">{formatMarketPercent(index.changePct)}</td>
                            <td className="px-2 py-2 text-secondary-text">{formatMarketHighLow(index.high, index.low)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {isLoading ? (
        <Card variant="bordered" padding="md" className="home-panel-card text-left">
          <div className="flex h-64 flex-col items-center justify-center">
            <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
            <p className="mt-4 text-sm text-secondary-text">{text.loadingReport}</p>
          </div>
        </Card>
      ) : error ? (
        <Card variant="bordered" padding="md" className="home-panel-card text-left">
          <div className="flex h-64 flex-col items-center justify-center">
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-danger/10">
              <ShieldAlert className="h-6 w-6 text-danger" aria-hidden="true" />
            </div>
            <p className="text-sm text-danger">{error}</p>
          </div>
        </Card>
      ) : (
        <div data-testid="market-review-report" className="space-y-4">
          {sections.map(({ id, title, content: sectionContent, icon: Icon }) => (
            <Card key={id} variant="bordered" padding="md" className="home-panel-card text-left">
              <div className="mb-3 flex items-center gap-2">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
                <h3 className="text-base font-semibold text-foreground">{title}</h3>
              </div>
              <ReportMarkdownBody
                content={sectionContent}
                className="market-review-markdown"
              />
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};
