import type React from 'react';
import { ChevronDown, Database } from 'lucide-react';
import type {
  AnalysisContextPackBlockStatus,
  AnalysisContextPackOverview,
  ReportLanguage,
} from '../../types/analysis';
import { normalizeReportLanguage } from '../../utils/reportLanguage';
import { Badge, Card, StatusDot } from '../common';
import { DashboardPanelHeader } from '../dashboard';

interface AnalysisContextSummaryProps {
  overview?: AnalysisContextPackOverview | null;
  language?: ReportLanguage;
}

type BadgeVariant = NonNullable<React.ComponentProps<typeof Badge>['variant']>;
type StatusTone = NonNullable<React.ComponentProps<typeof StatusDot>['tone']>;

const STATUS_STYLE: Record<AnalysisContextPackBlockStatus, { variant: BadgeVariant; tone: StatusTone }> = {
  available: { variant: 'success', tone: 'success' },
  missing: { variant: 'danger', tone: 'danger' },
  not_supported: { variant: 'default', tone: 'neutral' },
  fallback: { variant: 'warning', tone: 'warning' },
  stale: { variant: 'warning', tone: 'warning' },
  estimated: { variant: 'info', tone: 'info' },
  partial: { variant: 'warning', tone: 'warning' },
  fetch_failed: { variant: 'danger', tone: 'danger' },
};

const QUALITY_STYLE = {
  good: { variant: 'success', tone: 'success' },
  usable: { variant: 'info', tone: 'info' },
  limited: { variant: 'warning', tone: 'warning' },
  poor: { variant: 'danger', tone: 'danger' },
} as const satisfies Record<string, { variant: BadgeVariant; tone: StatusTone }>;

const BLOCK_LABELS: Record<ReportLanguage, Record<string, string>> = {
  vi: {
    quote: 'giá',
    daily_bars: 'nến ngày',
    technical: 'kỹ thuật',
    news: 'tin tức',
    fundamentals: 'cơ bản',
    chip: 'phân bổ cổ phiếu',
  },
  en: {
    quote: 'quote',
    daily_bars: 'daily bars',
    technical: 'technical',
    news: 'news',
    fundamentals: 'fundamentals',
    chip: 'chip',
  },
};

const TEXT = {
  vi: {
    eyebrow: 'NGỮ CẢNH DỮ LIỆU',
    title: 'Khối dữ liệu đầu vào',
    counts: 'Thống kê trạng thái',
    source: 'Nguồn',
    sourceUnavailable: 'Không ghi nhận nguồn đầu vào',
    warnings: 'Cảnh báo',
    missingReasons: 'Diễn giải',
    diagnosticCode: 'Mã chẩn đoán',
    inputScope: 'Đầu vào của lượt phân tích này',
    evidenceScope:
      'Chỉ thể hiện phần đã đi vào LLM lần này, không đồng nghĩa với việc nguồn dữ liệu chạy thành công',
    qualityScore: 'Điểm chất lượng',
    limitations: 'Giới hạn dữ liệu',
    newsResultCount: 'Số kết quả tin tức',
    triggerSource: 'Nguồn kích hoạt',
    qualityLevel: {
      good: 'Tốt',
      usable: 'Dùng được',
      limited: 'Hạn chế',
      poor: 'Kém',
    },
    status: {
      available: 'Có sẵn',
      missing: 'Thiếu',
      not_supported: 'Không hỗ trợ',
      fallback: 'Dự phòng',
      stale: 'Quá hạn',
      estimated: 'Ước tính',
      partial: 'Có một phần',
      fetch_failed: 'Lấy dữ liệu thất bại',
    },
  },
  en: {
    eyebrow: 'DATA CONTEXT',
    title: 'Input Blocks',
    counts: 'Status Counts',
    source: 'Source',
    sourceUnavailable: 'Input source not recorded',
    warnings: 'Warnings',
    missingReasons: 'Details',
    diagnosticCode: 'Diagnostic code',
    inputScope: 'Analysis Input',
    evidenceScope: 'Shows inputs included in this LLM run, not provider run success',
    qualityScore: 'Quality',
    limitations: 'Data Limitations',
    newsResultCount: 'News Results',
    triggerSource: 'Trigger',
    qualityLevel: {
      good: 'Good',
      usable: 'Usable',
      limited: 'Limited',
      poor: 'Poor',
    },
    status: {
      available: 'Available',
      missing: 'Missing',
      not_supported: 'Not supported',
      fallback: 'Fallback',
      stale: 'Stale',
      estimated: 'Estimated',
      partial: 'Partial',
      fetch_failed: 'Fetch failed',
    },
  },
} as const;

const MISSING_REASON_LABELS: Record<ReportLanguage, Record<string, string>> = {
  vi: {
    daily_bars_missing:
      'Dữ liệu nến ngày không đi vào lượt phân tích này nên chỉ báo kỹ thuật có thể thiếu; hãy kiểm tra nguồn nến ngày, mạng hoặc giới hạn tần suất rồi phân tích lại',
    news_context_missing:
      'Tin tức không đi vào lượt chạy LLM này nên kết luận không dùng ngữ cảnh tin tức; mục tin liên quan trên trang báo cáo do một API riêng bổ sung, hiển thị ở đó không đồng nghĩa đã dùng cho phân tích. Hãy kiểm tra cấu hình tìm kiếm, mạng hoặc giới hạn tần suất rồi phân tích lại',
    realtime_quote_missing:
      'Giá thời gian thực không đi vào lượt phân tích này nên các kết luận liên quan tới giá hiện tại có thể bị hạn chế; hãy kiểm tra nguồn giá, mạng hoặc giới hạn tần suất rồi phân tích lại',
    trend_result_missing:
      'Kết quả phân tích kỹ thuật không đi vào lượt này nên đánh giá kỹ thuật có thể thiếu; hãy kiểm tra tính đầy đủ của nến ngày rồi phân tích lại',
    fundamental_context_missing:
      'Dữ liệu cơ bản không đi vào lượt phân tích này nên kết luận không dùng dữ liệu cơ bản; hãy kiểm tra nguồn dữ liệu cơ bản, mạng hoặc giới hạn tần suất rồi phân tích lại',
    fundamental_pipeline_failed:
      'Lấy dữ liệu cơ bản thất bại, lượt phân tích này không dùng dữ liệu cơ bản; hãy kiểm tra cấu hình nguồn, mạng hoặc giới hạn tần suất rồi phân tích lại',
    fundamentals_not_supported:
      'Thị trường hoặc mã hiện tại không hỗ trợ dữ liệu cơ bản nên lượt phân tích này không dùng; hãy kết hợp các chỉ báo khác để đánh giá',
    fundamental_coverage_missing:
      'Dữ liệu độ phủ cơ bản không đi vào lượt phân tích này nên kết luận có thể thiếu một phần thông tin tài chính; hãy kiểm tra phạm vi phủ của nguồn rồi phân tích lại',
    fundamental_source_chain_missing:
      'Không ghi nhận metadata chuỗi nguồn của dữ liệu cơ bản; việc dữ liệu cơ bản có đi vào lượt này hay không lấy theo trạng thái hiện tại, hãy đối chiếu nguồn và cảnh báo để xác minh xuất xứ',
    chip_distribution_missing:
      'Dữ liệu phân bổ cổ phiếu không đi vào lượt phân tích này nên kết luận không dùng phân bổ; hãy xác nhận mức hỗ trợ dữ liệu của thị trường hoặc mã hiện tại',
    chip_not_supported:
      'Thị trường hoặc mã hiện tại không hỗ trợ dữ liệu phân bổ cổ phiếu nên lượt phân tích này không dùng chỉ báo đó; hãy kết hợp các chỉ báo khác',
    today_missing:
      'Dữ liệu hôm nay không đi vào lượt phân tích này nên đánh giá trong phiên có thể bị hạn chế; hãy đối chiếu giá thời gian thực rồi phân tích lại',
    yesterday_missing:
      'Dữ liệu hôm qua không đi vào lượt phân tích này nên so sánh nến ngày có thể thiếu; hãy chờ nguồn cập nhật rồi phân tích lại',
  },
  en: {
    daily_bars_missing: 'Daily bars were not included, so technical indicators may be incomplete; check the daily data source, network, or rate limits and rerun',
    news_context_missing: 'News was not included in this LLM run, so the conclusion did not use news context; related news on the report page is loaded separately and does not indicate that it was used in this analysis. Check search configuration, network, or rate limits and rerun',
    realtime_quote_missing: 'Real-time quotes were not included, so price-related conclusions may be limited; check the quote source, network, or rate limits and rerun',
    trend_result_missing: 'Technical analysis was not included, so the technical view may be incomplete; check daily-bar completeness and rerun',
    fundamental_context_missing: 'Fundamentals were not included, so the conclusion did not use fundamental data; check the data source, network, or rate limits and rerun',
    fundamental_pipeline_failed: 'Fundamental retrieval failed and this analysis did not use fundamental data; check the data-source configuration, network, or rate limits and rerun',
    fundamentals_not_supported: 'Fundamental data is not supported for this market or symbol and was not used; cross-check other indicators',
    fundamental_coverage_missing: 'Fundamental coverage was not included, so some financial context may be missing; check source coverage and rerun',
    fundamental_source_chain_missing: 'Fundamental source-chain metadata was not recorded; use the current status to determine whether fundamentals were included, and review the source and warnings for provenance',
    chip_distribution_missing: 'Chip distribution was not included and was not used in the conclusion; confirm support for this market or symbol',
    chip_not_supported: 'Chip data is not supported for this market or symbol and was not used; cross-check other indicators',
    today_missing: 'Today\'s data was not included, so intraday conclusions may be limited; cross-check real-time quotes and rerun',
    yesterday_missing: 'Yesterday\'s data was not included, so daily comparisons may be incomplete; wait for the source to update and rerun',
  },
};

const UNKNOWN_REASON_DETAILS: Record<ReportLanguage, string> = {
  vi: 'Không ghi nhận nguyên nhân cụ thể; hãy đối chiếu trạng thái, nguồn và cảnh báo để truy vết',
  en: 'No specific reason was recorded; review the status, source, and warnings',
};

const STATUS_FALLBACK_GUIDANCE: Record<
  ReportLanguage,
  Partial<Record<AnalysisContextPackBlockStatus, string>>
> = {
  vi: {
    missing:
      'Dữ liệu không đi vào lượt phân tích này nên kết luận liên quan có thể thiếu; hãy kiểm tra nguồn, cấu hình hoặc mạng rồi phân tích lại',
    fetch_failed:
      'Lấy dữ liệu thất bại, lượt phân tích này không dùng dữ liệu đó; hãy kiểm tra nguồn, mạng hoặc giới hạn tần suất rồi phân tích lại',
    not_supported:
      'Thị trường hoặc mã hiện tại không hỗ trợ dữ liệu này nên lượt phân tích không dùng; hãy kết hợp các chỉ báo khác',
    fallback:
      'Lượt phân tích này dùng đường dữ liệu dự phòng; hãy đối chiếu nguồn và cảnh báo để rà soát kết quả',
    stale:
      'Lượt phân tích này dùng dữ liệu có thể không phải mới nhất; hãy kiểm tra thời điểm cập nhật và phân tích lại nếu cần',
    estimated:
      'Lượt phân tích này dùng dữ liệu ước tính; hãy đối chiếu với dữ liệu gốc để rà soát kết quả',
    partial:
      'Chỉ một phần dữ liệu đi vào lượt phân tích này nên kết luận liên quan có thể thiếu; hãy kiểm tra cảnh báo và nguồn rồi phân tích lại',
  },
  en: {
    missing: 'Data was not included, so related conclusions may be incomplete; check the data source, configuration, or network and rerun',
    fetch_failed: 'Data retrieval failed and this analysis did not use the data; check the source, network, or rate limits and rerun',
    not_supported: 'This data is not supported for the current market or symbol and was not used; cross-check other indicators',
    fallback: 'This analysis used a fallback data path; review the result against its source and warnings',
    stale: 'This analysis used data that may not be current; check the timestamp and rerun if needed',
    estimated: 'This analysis used estimated data; cross-check the result against source data',
    partial: 'Only part of the data was included, so related conclusions may be incomplete; check warnings and the data source and rerun',
  },
};

const STATUS_ORDER: AnalysisContextPackBlockStatus[] = [
  'available',
  'missing',
  'fetch_failed',
  'not_supported',
  'fallback',
  'stale',
  'estimated',
  'partial',
];

const getCount = (
  overview: AnalysisContextPackOverview,
  status: AnalysisContextPackBlockStatus,
): number => {
  if (status === 'not_supported') {
    return overview.counts.notSupported || 0;
  }
  if (status === 'fetch_failed') {
    return overview.counts.fetchFailed || 0;
  }
  return overview.counts[status] || 0;
};

const formatLimitation = (
  value: string,
  language: ReportLanguage,
  text: (typeof TEXT)[ReportLanguage],
): string => {
  const [rawKey, ...statusParts] = value.split(':');
  if (!rawKey || statusParts.length === 0) {
    return value;
  }

  const key = rawKey.trim();
  const status = statusParts.join(':').trim();
  if (!key || !status) {
    return value;
  }

  const label = BLOCK_LABELS[language][key] || key;
  const statusLabel = (text.status as Record<string, string>)[status] || status;
  return `${label}: ${statusLabel}`;
};

const formatMissingReason = (
  reason: string,
  language: ReportLanguage,
  status: AnalysisContextPackBlockStatus,
): string => {
  const detail = MISSING_REASON_LABELS[language][reason]
    || STATUS_FALLBACK_GUIDANCE[language][status]
    || UNKNOWN_REASON_DETAILS[language];
  return `${detail} (${TEXT[language].diagnosticCode}: ${reason})`;
};

export const AnalysisContextSummary: React.FC<AnalysisContextSummaryProps> = ({
  overview,
  language = 'vi',
}) => {
  const reportLanguage = normalizeReportLanguage(language);
  const text = TEXT[reportLanguage];

  if (!overview || !overview.blocks?.length) {
    return null;
  }

  const visibleCounts = STATUS_ORDER
    .map((status) => ({ status, value: getCount(overview, status) }))
    .filter((item) => item.value > 0);
  const summaryCounts = STATUS_ORDER
    .map((status) => ({ status, value: getCount(overview, status) }))
    .filter((item) => item.status === 'available' || item.status === 'missing' || item.value > 0);
  const metadataItems = [
    typeof overview.metadata?.newsResultCount === 'number'
      ? `${text.newsResultCount}: ${overview.metadata.newsResultCount}`
      : null,
  ].filter((item): item is string => Boolean(item));
  const triggerSource = overview.metadata?.triggerSource?.trim();
  const quality = overview.dataQuality;
  const qualityLevel = quality?.level || undefined;
  const qualityStyle = qualityLevel ? QUALITY_STYLE[qualityLevel] : undefined;
  const qualityLabel = qualityLevel ? text.qualityLevel[qualityLevel] : undefined;
  const limitations = quality?.limitations?.map((item) => formatLimitation(item, reportLanguage, text)) || [];

  return (
    <Card variant="bordered" padding="none" className="home-panel-card">
      <details data-testid="analysis-context-summary" className="group">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan/10 text-cyan">
              <Database className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="label-uppercase">{text.eyebrow}</span>
              <span className="mt-0.5 block truncate text-base font-semibold text-foreground">
                {text.title}
              </span>
              <span className="mt-1 block text-xs leading-5 text-muted-text">
                {text.evidenceScope}
              </span>
            </span>
          </div>
          <span className="flex min-w-0 flex-wrap items-center justify-end gap-2">
            {typeof quality?.overallScore === 'number' ? (
              <Badge variant={qualityStyle?.variant || 'default'} className="gap-1.5 shadow-none">
                {qualityStyle ? <StatusDot tone={qualityStyle.tone} className="h-1.5 w-1.5" /> : null}
                {text.qualityScore} {quality.overallScore}/100{qualityLabel ? ` ${qualityLabel}` : ''}
              </Badge>
            ) : null}
            {summaryCounts.map(({ status, value }) => {
              const style = STATUS_STYLE[status];
              return (
                <Badge key={status} variant={style.variant} className="gap-1.5 shadow-none">
                  <StatusDot tone={style.tone} className="h-1.5 w-1.5" />
                  {text.status[status]} {value}
                </Badge>
              );
            })}
            {triggerSource ? (
              <span className="home-accent-chip px-2 py-0.5 text-xs text-muted-text">
                {text.triggerSource}: {triggerSource}
              </span>
            ) : null}
            <span className="home-accent-chip px-2 py-0.5 text-xs text-muted-text">
              {text.inputScope}
            </span>
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-text transition-transform group-open:rotate-180" aria-hidden="true" />
          </span>
        </summary>

        <div className="home-divider border-t px-4 pb-4 pt-3">
          <DashboardPanelHeader
            eyebrow={text.eyebrow}
            title={text.title}
            leading={(
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan/10 text-cyan">
                <Database className="h-4 w-4" aria-hidden="true" />
              </span>
            )}
            actions={metadataItems.length > 0 || typeof quality?.overallScore === 'number' ? (
              <div className="hidden flex-wrap justify-end gap-2 text-xs text-muted-text md:flex">
                {typeof quality?.overallScore === 'number' ? (
                  <span className="home-accent-chip px-2 py-0.5">
                    {text.qualityScore}: {quality.overallScore}/100{qualityLabel ? ` ${qualityLabel}` : ''}
                  </span>
                ) : null}
                {metadataItems.map((item) => (
                  <span key={item} className="home-accent-chip px-2 py-0.5">
                    {item}
                  </span>
                ))}
                <span className="home-accent-chip px-2 py-0.5">
                  {text.inputScope}
                </span>
              </div>
            ) : undefined}
          />

          {visibleCounts.length > 0 ? (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="label-uppercase">{text.counts}</span>
              {visibleCounts.map(({ status, value }) => {
                const style = STATUS_STYLE[status];
                return (
                  <Badge key={status} variant={style.variant} className="gap-1.5 shadow-none">
                    <StatusDot tone={style.tone} className="h-1.5 w-1.5" />
                    {text.status[status]} {value}
                  </Badge>
                );
              })}
            </div>
          ) : null}

          {limitations.length ? (
            <div className="mb-3 home-subpanel p-3 text-xs leading-5 text-muted-text">
              <span className="font-medium text-foreground">{text.limitations}: </span>
              {limitations.join(', ')}
            </div>
          ) : null}

          {overview.warnings?.length ? (
            <div className="mb-3 home-subpanel p-3 text-xs leading-5 text-warning">
              <span className="font-medium">{text.warnings}: </span>
              {overview.warnings.join(', ')}
            </div>
          ) : null}

          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {overview.blocks.map((block) => {
              const style = STATUS_STYLE[block.status] || STATUS_STYLE.missing;
              const hasMissingReasons = Boolean(block.missingReasons?.length);
              const detail = hasMissingReasons
                ? block.missingReasons
                  ?.map((reason) => formatMissingReason(
                    reason,
                    reportLanguage,
                    block.status,
                  ))
                  .join('; ')
                : STATUS_FALLBACK_GUIDANCE[reportLanguage][block.status];
              return (
                <div key={block.key} className="home-subpanel p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{block.label}</p>
                      <p className="mt-1 truncate text-xs text-secondary-text">
                        {text.source}: {block.source || text.sourceUnavailable}
                      </p>
                    </div>
                    <Badge variant={style.variant} className="shrink-0 gap-1.5 shadow-none">
                      <StatusDot tone={style.tone} className="h-1.5 w-1.5" />
                      {text.status[block.status] || block.status}
                    </Badge>
                  </div>

                  {block.warnings?.length ? (
                    <p className="mt-2 text-xs leading-5 text-warning">
                      {text.warnings}: {block.warnings.join(', ')}
                    </p>
                  ) : null}
                  {detail ? (
                    <p className="mt-2 text-xs leading-5 text-muted-text">
                      {text.missingReasons}: {detail}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>

          {metadataItems.length > 0 || typeof quality?.overallScore === 'number' ? (
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-text md:hidden">
              {typeof quality?.overallScore === 'number' ? (
                <span className="home-accent-chip px-2 py-0.5">
                  {text.qualityScore}: {quality.overallScore}/100{qualityLabel ? ` ${qualityLabel}` : ''}
                </span>
              ) : null}
              {metadataItems.map((item) => (
                <span key={item} className="home-accent-chip px-2 py-0.5">
                  {item}
                </span>
              ))}
              <span className="home-accent-chip px-2 py-0.5">
                {text.inputScope}
              </span>
            </div>
          ) : null}
        </div>
      </details>
    </Card>
  );
};
