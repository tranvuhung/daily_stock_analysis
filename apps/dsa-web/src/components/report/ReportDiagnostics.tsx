import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Check, ChevronDown, Copy, Workflow } from 'lucide-react';
import { historyApi } from '../../api/history';
import { formatUiText, UI_TEXT } from '../../i18n/uiText';
import type {
  ReportLanguage,
  RunDiagnosticComponent,
  RunDiagnosticComponentStatus,
  RunDiagnosticStatus,
  RunDiagnosticSummary,
} from '../../types/analysis';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { normalizeReportLanguage } from '../../utils/reportLanguage';
import { Badge, Button, Card, StatusDot } from '../common';

interface ReportDiagnosticsProps {
  recordId?: number;
  summary?: RunDiagnosticSummary;
  language?: ReportLanguage;
  onOpenRunFlow?: (recordId: number) => void;
}

type BadgeVariant = NonNullable<React.ComponentProps<typeof Badge>['variant']>;
type StatusTone = NonNullable<React.ComponentProps<typeof StatusDot>['tone']>;

const COMPONENT_ORDER = [
  'realtime_quote',
  'daily_data',
  'news',
  'llm',
  'notification',
  'history',
];

const TEXT = {
  vi: {
    eyebrow: 'CHẨN ĐOÁN LƯỢT CHẠY',
    title: 'Trạng thái chạy',
    loading: 'Đang tải chẩn đoán...',
    unavailable: 'Chưa có dữ liệu chẩn đoán',
    noComponents: 'Chưa có chẩn đoán thành phần',
    components: 'Mắt xích chính',
    advanced: 'Trường nâng cao',
    copy: 'Sao chép thông tin gỡ lỗi',
    copied: 'Đã sao chép',
    scope: 'Lấy dữ liệu / LLM / Lưu trữ / Thông báo',
    trace: 'Trace',
    task: 'Task',
    query: 'Query',
    trigger: 'Nguồn kích hoạt',
    overall: {
      normal: 'Bình thường',
      degraded: 'Suy giảm một phần',
      failed: 'Thất bại',
      unknown: 'Không rõ',
    },
    component: {
      ok: 'Bình thường',
      degraded: 'Đã suy giảm sau lần lỗi gần đây',
      failed: 'Thất bại',
      unknown: 'Không rõ',
      not_configured: 'Chưa cấu hình',
      skipped: 'Đã bỏ qua',
    },
  },
  en: {
    eyebrow: 'RUN DIAGNOSTICS',
    title: 'Run Status',
    loading: 'Loading diagnostics...',
    unavailable: 'Diagnostics unavailable',
    noComponents: 'No component diagnostics',
    components: 'Key Path',
    advanced: 'Advanced Fields',
    copy: 'Copy diagnostics',
    copied: 'Copied',
    scope: 'Fetch / LLM / save / notification path',
    trace: 'Trace',
    task: 'Task',
    query: 'Query',
    trigger: 'Trigger',
    overall: {
      normal: 'Normal',
      degraded: 'Degraded',
      failed: 'Failed',
      unknown: 'Unknown',
    },
    component: {
      ok: 'Normal',
      degraded: 'Recent failure',
      failed: 'Failed',
      unknown: 'Unknown',
      not_configured: 'Not configured',
      skipped: 'Skipped',
    },
  },
} as const;

// Bản dịch theo id ổn định từ backend (component.key, messageCode, analysis_input_status).
// Localized text keyed by stable backend ids; unknown ids fall back to the server text.
const COMPONENT_LABELS: Record<ReportLanguage, Record<string, string>> = {
  vi: {
    realtime_quote: 'Giá thời gian thực',
    daily_data: 'Dữ liệu ngày',
    news: 'Tìm kiếm tin tức',
    llm: 'LLM',
    notification: 'Thông báo',
    history: 'Lưu lịch sử',
  },
  en: {
    realtime_quote: 'Realtime quote',
    daily_data: 'Daily data',
    news: 'News search',
    llm: 'LLM',
    notification: 'Notification',
    history: 'History save',
  },
};

const MESSAGE_TEMPLATES: Record<ReportLanguage, Record<string, string>> = {
  vi: {
    provider_ok: '{label}: {provider} thành công',
    provider_ok_after_fallback: '{label}: {provider} thành công sau khi các nguồn trước đó lỗi',
    provider_failed: '{label} thất bại: {error}',
    provider_input_degraded: '{label}: {provider} thành công, nhưng dữ liệu {input}',
    not_recorded: '{label}: chưa ghi nhận thông tin chẩn đoán',
    news_results: 'Tìm kiếm tin tức trả về {count} kết quả',
    news_results_input_degraded: 'Tìm kiếm tin tức trả về {count} kết quả, nhưng tin tức {input}; tin liên quan trên trang báo cáo có thể đến từ lần tìm kiếm sau hoặc lịch sử đã lưu',
    news_empty: 'Tìm kiếm tin tức không có kết quả',
    news_input_only: 'Tin tức {input}; tin liên quan trên trang báo cáo có thể đến từ lần tìm kiếm sau hoặc lịch sử đã lưu',
    news_no_raw_evidence: 'Tìm kiếm tin tức không ghi nhận bằng chứng gốc; có thể chưa chạy hoặc chưa bật',
    llm_ok: 'LLM {model} thành công',
    llm_ok_with_fallback: 'LLM {model} thành công, nhưng có lỗi hoặc đổi model trong quá trình chạy',
    llm_ok_no_model: 'LLM thành công, chưa ghi nhận model',
    llm_failed: 'LLM thất bại: {error}',
    notification_not_recorded: 'Chưa ghi nhận kết quả thông báo',
    notification_partial: 'Một số kênh thông báo lỗi, các kênh còn lại đã gửi',
    notification_ok: 'Đã gửi thông báo',
    notification_skipped: 'Thông báo chưa cấu hình hoặc bị bỏ qua ở lượt này',
    notification_failed: 'Gửi thông báo thất bại: {error}',
    history_saved: 'Đã lưu lịch sử báo cáo',
    history_failed: 'Lưu lịch sử báo cáo thất bại: {error}',
  },
  en: {
    provider_ok: '{label}: {provider} succeeded',
    provider_ok_after_fallback: '{label}: {provider} succeeded after earlier sources failed',
    provider_failed: '{label} failed: {error}',
    provider_input_degraded: '{label}: {provider} succeeded, but the data {input}',
    not_recorded: '{label}: no diagnostics recorded',
    news_results: 'News search returned {count} results',
    news_results_input_degraded: 'News search returned {count} results, but the news {input}; related news on the report page may come from a later search or saved history',
    news_empty: 'News search returned no results',
    news_input_only: 'The news {input}; related news on the report page may come from a later search or saved history',
    news_no_raw_evidence: 'News search recorded no raw evidence; it may not have run or may be disabled',
    llm_ok: 'LLM {model} succeeded',
    llm_ok_with_fallback: 'LLM {model} succeeded, with failures or model switches along the way',
    llm_ok_no_model: 'LLM succeeded; model not recorded',
    llm_failed: 'LLM failed: {error}',
    notification_not_recorded: 'Notification result not recorded',
    notification_partial: 'Some notification channels failed; the others were sent',
    notification_ok: 'Notification sent',
    notification_skipped: 'Notification not configured or skipped for this run',
    notification_failed: 'Notification failed: {error}',
    history_saved: 'Report history saved',
    history_failed: 'Saving report history failed: {error}',
  },
};

const INPUT_STATUS_TEXT: Record<ReportLanguage, Record<string, string>> = {
  vi: {
    missing: 'chưa được đưa vào đầu vào phân tích của lượt này',
    partial: 'chỉ có một phần trong đầu vào phân tích của lượt này',
    fallback: 'dùng dữ liệu dự phòng trong đầu vào phân tích của lượt này',
    stale: 'dùng dữ liệu cũ trong đầu vào phân tích của lượt này',
    estimated: 'dùng dữ liệu ước tính trong đầu vào phân tích của lượt này',
    fetch_failed: 'bị đánh dấu lấy dữ liệu thất bại trong đầu vào phân tích',
    not_supported: 'bị đánh dấu không hỗ trợ trong đầu vào phân tích',
  },
  en: {
    missing: "did not reach this run's analysis input",
    partial: "was only partially available to this run's analysis input",
    fallback: "used fallback data in this run's analysis input",
    stale: "used stale data in this run's analysis input",
    estimated: "used estimated data in this run's analysis input",
    fetch_failed: 'was marked as fetch failed in the analysis input',
    not_supported: 'was marked as not supported in the analysis input',
  },
};

// Lý do lỗi theo ``error_type`` ổn định của backend (cùng bộ mã với Run Flow,
// ``runFlow.errorReason.*``); code lạ thì dùng ``details.error`` của server.
// Failure reasons keyed by the backend's stable error_type; unknown codes use the server text.
const ERROR_REASON_TEXT: Record<ReportLanguage, Record<string, string>> = {
  vi: {
    NoUsableNews: 'không còn tin hợp lệ sau khi lọc',
  },
  en: {
    NoUsableNews: 'no usable news after filtering',
  },
};

const UNKNOWN_REASON: Record<ReportLanguage, string> = {
  vi: 'Báo cáo cũ hoặc chưa đủ bằng chứng chẩn đoán; không xác định được trạng thái của lượt chạy này',
  en: 'Old report or insufficient diagnostic evidence; the status of this run cannot be determined',
};

const detailText = (details: Record<string, unknown> | undefined, key: string): string | undefined => {
  const value = details?.[key];
  if (value === undefined || value === null || value === '') return undefined;
  return String(value);
};

const getComponentLabel = (component: RunDiagnosticComponent, language: ReportLanguage): string => (
  COMPONENT_LABELS[language][component.key] || component.label
);

/**
 * Trả về message đã bản địa hóa; không có messageCode (bản ghi cũ) thì dùng text server.
 * Returns the localized message; falls back to the server text when there is no messageCode.
 */
const localizeComponentMessage = (component: RunDiagnosticComponent, language: ReportLanguage): string => {
  const template = component.messageCode ? MESSAGE_TEMPLATES[language][component.messageCode] : undefined;
  if (!template) {
    return component.message;
  }
  const details = component.details;
  const inputStatus = detailText(details, 'analysisInputStatus');
  const params = {
    label: getComponentLabel(component, language),
    provider: detailText(details, 'provider') ?? 'unknown',
    count: detailText(details, 'recordCount') ?? '0',
    model: detailText(details, 'model') ?? 'unknown',
    error: ERROR_REASON_TEXT[language][detailText(details, 'errorType') ?? '']
      ?? detailText(details, 'error')
      ?? (language === 'vi' ? 'lỗi không xác định' : 'unknown error'),
    input: inputStatus ? INPUT_STATUS_TEXT[language][inputStatus] ?? inputStatus : '',
  };
  // Mã cần {input} mà không có trạng thái đầu vào thì dùng text server cho an toàn.
  // Codes that need {input} fall back to the server text when the input status is missing.
  if (template.includes('{input}') && !params.input) {
    return component.message;
  }
  return formatUiText(template, params);
};

/**
 * Suy ra lý do tổng quát theo đúng thứ tự backend: failed đầu tiên, rồi degraded đầu tiên.
 * Derives the headline reason in the backend's order: first failed, then first degraded component.
 */
const localizeReason = (
  summary: RunDiagnosticSummary,
  components: RunDiagnosticComponent[],
  language: ReportLanguage,
  overallLabel: string,
): string => {
  if (summary.status === 'unknown') {
    return UNKNOWN_REASON[language];
  }
  const primary = components.find((component) => component.status === 'failed')
    || components.find((component) => component.status === 'degraded');
  if (primary) {
    return primary.messageCode ? localizeComponentMessage(primary, language) : summary.reason;
  }
  return summary.status === 'normal' ? overallLabel : summary.reason;
};

const OVERALL_STATUS_STYLE: Record<RunDiagnosticStatus, { variant: BadgeVariant; tone: StatusTone }> = {
  normal: { variant: 'success', tone: 'success' },
  degraded: { variant: 'warning', tone: 'warning' },
  failed: { variant: 'danger', tone: 'danger' },
  unknown: { variant: 'default', tone: 'neutral' },
};

const COMPONENT_STATUS_STYLE: Record<RunDiagnosticComponentStatus, { variant: BadgeVariant; tone: StatusTone }> = {
  ok: { variant: 'success', tone: 'success' },
  degraded: { variant: 'warning', tone: 'warning' },
  failed: { variant: 'danger', tone: 'danger' },
  unknown: { variant: 'default', tone: 'neutral' },
  not_configured: { variant: 'default', tone: 'neutral' },
  skipped: { variant: 'default', tone: 'neutral' },
};

const compactId = (value?: string): string | null => {
  const text = (value || '').trim();
  if (!text) return null;
  if (text.length <= 28) return text;
  return `${text.slice(0, 10)}...${text.slice(-8)}`;
};

const getOrderedComponents = (
  components?: Record<string, RunDiagnosticComponent>,
): RunDiagnosticComponent[] => {
  const items = Object.values(components || {});
  const ordered = COMPONENT_ORDER
    .map((key) => items.find((component) => component.key === key))
    .filter((component): component is RunDiagnosticComponent => Boolean(component));
  const remaining = items.filter((component) => !COMPONENT_ORDER.includes(component.key));
  return [...ordered, ...remaining];
};

/**
 * Collapsed report diagnostics for self-hosted troubleshooting.
 */
export const ReportDiagnostics: React.FC<ReportDiagnosticsProps> = ({
  recordId,
  summary,
  language = 'vi',
  onOpenRunFlow,
}) => {
  const { language: uiLanguage } = useUiLanguage();
  const reportLanguage = normalizeReportLanguage(language);
  const text = TEXT[reportLanguage];
  const runFlowText = UI_TEXT[uiLanguage];
  const [fetchState, setFetchState] = useState<{
    recordId?: number;
    summary: RunDiagnosticSummary | null;
    failed: boolean;
  }>({
    summary: null,
    failed: false,
  });
  const [copied, setCopied] = useState(false);
  const resetCopiedTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (summary || !recordId) {
      return undefined;
    }

    let active = true;
    void historyApi.getDiagnostics(recordId)
      .then((result) => {
        if (active) {
          setFetchState({
            recordId,
            summary: result,
            failed: false,
          });
        }
      })
      .catch(() => {
        if (active) {
          setFetchState({
            recordId,
            summary: null,
            failed: true,
          });
        }
      });

    return () => {
      active = false;
    };
  }, [recordId, summary]);

  useEffect(() => () => {
    if (resetCopiedTimerRef.current !== null) {
      window.clearTimeout(resetCopiedTimerRef.current);
    }
  }, []);

  const fetchedForRecord = recordId !== undefined && fetchState.recordId === recordId
    ? fetchState
    : null;
  const loadedSummary = summary ?? fetchedForRecord?.summary ?? null;
  const loadFailed = !summary && Boolean(fetchedForRecord?.failed);
  const isLoading = Boolean(recordId && !summary && !fetchedForRecord);

  const visibleSummary = useMemo<RunDiagnosticSummary | null>(() => {
    if (loadedSummary) {
      return loadedSummary;
    }
    if (!recordId && !summary) {
      return null;
    }
    if (!isLoading && !loadFailed) {
      return null;
    }
    return {
      status: 'unknown',
      statusLabel: text.overall.unknown,
      reason: loadFailed ? text.unavailable : text.loading,
      components: {},
      copyText: '',
    };
  }, [isLoading, loadFailed, loadedSummary, recordId, summary, text]);

  if (!visibleSummary) {
    return null;
  }

  const statusStyle = OVERALL_STATUS_STYLE[visibleSummary.status] || OVERALL_STATUS_STYLE.unknown;
  const statusLabel = text.overall[visibleSummary.status] || visibleSummary.statusLabel;
  const components = getOrderedComponents(visibleSummary.components);
  const reasonText = loadedSummary
    ? localizeReason(loadedSummary, components, reportLanguage, statusLabel)
    : visibleSummary.reason;
  const traceId = compactId(visibleSummary.traceId);
  const taskId = compactId(visibleSummary.taskId);
  const queryId = compactId(visibleSummary.queryId);
  const hasCopyText = Boolean(visibleSummary.copyText && !isLoading);
  const advancedPayload = {
    traceId: visibleSummary.traceId,
    taskId: visibleSummary.taskId,
    queryId: visibleSummary.queryId,
    stockCode: visibleSummary.stockCode,
    triggerSource: visibleSummary.triggerSource,
    components: components.reduce<Record<string, Record<string, unknown>>>((payload, component) => {
      payload[component.key] = {
        status: component.status,
        message: component.message,
        details: component.details || {},
      };
      return payload;
    }, {}),
  };
  const hasAdvancedPayload = Boolean(
    visibleSummary.traceId
    || visibleSummary.taskId
    || visibleSummary.queryId
    || visibleSummary.stockCode
    || visibleSummary.triggerSource
    || components.some((component) => component.details && Object.keys(component.details).length > 0),
  );

  const copyDiagnostics = async () => {
    if (!hasCopyText || !navigator.clipboard?.writeText) {
      return;
    }

    try {
      await navigator.clipboard.writeText(visibleSummary.copyText);
      setCopied(true);
      if (resetCopiedTimerRef.current !== null) {
        window.clearTimeout(resetCopiedTimerRef.current);
      }
      resetCopiedTimerRef.current = window.setTimeout(() => {
        setCopied(false);
        resetCopiedTimerRef.current = null;
      }, 2000);
    } catch (err) {
      console.error('Copy diagnostics failed:', err);
    }
  };

  return (
    <Card variant="bordered" padding="none" className="home-panel-card text-left">
      <details data-testid="run-diagnostics" className="group">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan/10 text-cyan">
              <Activity className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="label-uppercase">{text.eyebrow}</span>
              <span className="mt-0.5 block truncate text-base font-semibold text-foreground">
                {text.title}
              </span>
            </span>
          </div>
          <span className="flex shrink-0 items-center gap-2">
            {isLoading ? (
              <span className="home-spinner h-3.5 w-3.5 animate-spin border-2" aria-hidden="true" />
            ) : null}
            <Badge variant={statusStyle.variant} className="gap-1.5 shadow-none">
              <StatusDot tone={statusStyle.tone} className="h-1.5 w-1.5" />
              {statusLabel}
            </Badge>
            <span className="hidden home-accent-chip px-2 py-0.5 text-xs text-muted-text md:inline-flex">
              {text.scope}
            </span>
            <ChevronDown className="h-4 w-4 text-muted-text transition-transform group-open:rotate-180" aria-hidden="true" />
          </span>
        </summary>

        <div className="home-divider space-y-4 border-t px-4 pb-4 pt-3">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0 space-y-2">
              <p className="text-sm leading-6 text-foreground">
                {reasonText}
              </p>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-text">
                {traceId ? (
                  <span className="home-accent-chip px-2 py-0.5 font-mono">
                    {text.trace}: {traceId}
                  </span>
                ) : null}
                {taskId ? (
                  <span className="home-accent-chip px-2 py-0.5 font-mono">
                    {text.task}: {taskId}
                  </span>
                ) : null}
                {queryId ? (
                  <span className="home-accent-chip px-2 py-0.5 font-mono">
                    {text.query}: {queryId}
                  </span>
                ) : null}
                {visibleSummary.triggerSource ? (
                  <span className="home-accent-chip px-2 py-0.5">
                    {text.trigger}: {visibleSummary.triggerSource}
                  </span>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {recordId !== undefined && onOpenRunFlow ? (
                <Button
                  variant="ghost"
                  size="xsm"
                  onClick={() => onOpenRunFlow(recordId)}
                  aria-label={formatUiText(runFlowText['runFlow.openHistoryAria'], { recordId })}
                >
                  <Workflow className="h-3.5 w-3.5" aria-hidden="true" />
                  {runFlowText['runFlow.open']}
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="xsm"
                disabled={!hasCopyText}
                onClick={() => void copyDiagnostics()}
                aria-label={copied ? text.copied : text.copy}
                className="shrink-0"
              >
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? text.copied : text.copy}
              </Button>
            </div>
          </div>

          <div>
            <span className="label-uppercase">{text.components}</span>
            <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
              {components.length > 0 ? components.map((component) => {
                const componentStyle = COMPONENT_STATUS_STYLE[component.status] || COMPONENT_STATUS_STYLE.unknown;
                const componentLabel = text.component[component.status] || component.status;
                return (
                  <div key={component.key} className="home-subpanel p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-foreground">
                          {getComponentLabel(component, reportLanguage)}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-secondary-text">
                          {localizeComponentMessage(component, reportLanguage)}
                        </p>
                      </div>
                      <Badge variant={componentStyle.variant} className="shrink-0 gap-1.5 shadow-none">
                        <StatusDot tone={componentStyle.tone} className="h-1.5 w-1.5" />
                        {componentLabel}
                      </Badge>
                    </div>
                  </div>
                );
              }) : (
                <p className="home-subpanel p-3 text-sm text-secondary-text">
                  {text.noComponents}
                </p>
              )}
            </div>
          </div>

          {hasAdvancedPayload ? (
            <details className="home-subpanel group/advanced p-3">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                <span className="text-sm font-medium text-foreground">{text.advanced}</span>
                <ChevronDown className="h-4 w-4 text-muted-text transition-transform group-open/advanced:rotate-180" aria-hidden="true" />
              </summary>
              <pre className="home-trace-pre home-trace-pre-content mt-3 max-h-80 overflow-auto rounded-lg bg-base p-3 text-left font-mono text-xs text-foreground">
                {JSON.stringify(advancedPayload, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>
      </details>
    </Card>
  );
};
