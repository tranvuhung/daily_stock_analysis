import type { UiLanguage } from '../../i18n/uiText';
import type { UiTextKey } from '../../i18n/uiText';
import type {
  RunFlowEdgeKind,
  RunFlowEvent,
  RunFlowEventSeverity,
  RunFlowNode,
  RunFlowNodeKind,
  RunFlowStatus,
} from '../../types/runFlow';

export type RunFlowT = (key: UiTextKey, params?: Record<string, string | number>) => string;

export const RUN_FLOW_STATUS_STYLE: Record<RunFlowStatus, {
  badge: 'default' | 'success' | 'warning' | 'danger' | 'info';
  tone: 'success' | 'warning' | 'danger' | 'info' | 'neutral';
  pulse?: boolean;
}> = {
  pending: { badge: 'default', tone: 'neutral' },
  running: { badge: 'info', tone: 'info', pulse: true },
  success: { badge: 'success', tone: 'success' },
  failed: { badge: 'danger', tone: 'danger' },
  degraded: { badge: 'warning', tone: 'warning' },
  fallback: { badge: 'warning', tone: 'warning' },
  timeout: { badge: 'danger', tone: 'danger' },
  cancel_requested: { badge: 'warning', tone: 'warning', pulse: true },
  cancelled: { badge: 'default', tone: 'neutral' },
  skipped: { badge: 'default', tone: 'neutral' },
  unknown: { badge: 'default', tone: 'neutral' },
};

export const RUN_FLOW_SEVERITY_STYLE: Record<RunFlowEventSeverity, {
  badge: 'default' | 'success' | 'warning' | 'danger' | 'info';
  tone: 'success' | 'warning' | 'danger' | 'info' | 'neutral';
}> = {
  info: { badge: 'info', tone: 'info' },
  success: { badge: 'success', tone: 'success' },
  warning: { badge: 'warning', tone: 'warning' },
  danger: { badge: 'danger', tone: 'danger' },
};

const STATUS_LABEL_KEYS: Record<RunFlowStatus, UiTextKey> = {
  pending: 'runFlow.status.pending',
  running: 'runFlow.status.running',
  success: 'runFlow.status.success',
  failed: 'runFlow.status.failed',
  degraded: 'runFlow.status.degraded',
  fallback: 'runFlow.status.fallback',
  timeout: 'runFlow.status.timeout',
  cancel_requested: 'runFlow.status.cancelRequested',
  cancelled: 'runFlow.status.cancelled',
  skipped: 'runFlow.status.skipped',
  unknown: 'runFlow.status.unknown',
};

const SEVERITY_LABEL_KEYS: Record<RunFlowEventSeverity, UiTextKey> = {
  info: 'runFlow.severity.info',
  success: 'runFlow.severity.success',
  warning: 'runFlow.severity.warning',
  danger: 'runFlow.severity.danger',
};

const EDGE_KIND_LABEL_KEYS: Record<RunFlowEdgeKind, UiTextKey> = {
  data: 'runFlow.edge.data',
  control: 'runFlow.edge.control',
  fallback: 'runFlow.edge.fallback',
  retry: 'runFlow.edge.retry',
};

const NODE_KIND_LABEL_KEYS: Record<RunFlowNodeKind, UiTextKey> = {
  entry: 'runFlow.nodeKind.entry',
  queue: 'runFlow.nodeKind.queue',
  data_source: 'runFlow.nodeKind.dataSource',
  analysis: 'runFlow.nodeKind.analysis',
  model: 'runFlow.nodeKind.model',
  artifact: 'runFlow.nodeKind.artifact',
  notification: 'runFlow.nodeKind.notification',
};

export const getRunFlowStatusLabel = (status: RunFlowStatus, t: RunFlowT): string =>
  t(STATUS_LABEL_KEYS[status] || 'runFlow.status.unknown');

export const getRunFlowSeverityLabel = (severity: RunFlowEventSeverity, t: RunFlowT): string =>
  t(SEVERITY_LABEL_KEYS[severity] || 'runFlow.severity.info');

export const getRunFlowEdgeKindLabel = (kind: RunFlowEdgeKind, t: RunFlowT): string =>
  t(EDGE_KIND_LABEL_KEYS[kind] || 'runFlow.edge.control');

// Nhãn cạnh do backend gửi là một tập đóng, đã là tiếng Anh và mang ngữ nghĩa từng bước
// (Submit / Input / Generation / Save…), giàu thông tin hơn `edge.kind`. Vì vậy dịch theo
// đúng nhãn đó, chỉ lùi về `edge.kind` khi gặp nhãn lạ.
// The backend edge labels are a closed, meaningful set — richer than `edge.kind` — so they
// are translated as-is, falling back to the kind label only for an unknown value.
const EDGE_LABEL_KEYS: Record<string, UiTextKey> = {
  Submit: 'runFlow.edgeLabel.submit',
  Scheduling: 'runFlow.edgeLabel.scheduling',
  Input: 'runFlow.edgeLabel.input',
  Assembly: 'runFlow.edgeLabel.assembly',
  Generation: 'runFlow.edgeLabel.generation',
  Save: 'runFlow.edgeLabel.save',
  Notification: 'runFlow.edgeLabel.notification',
  Call: 'runFlow.edgeLabel.call',
  Degraded: 'runFlow.edgeLabel.degraded',
  Retry: 'runFlow.edgeLabel.retry',
  // Nhãn tổng hợp do `topologyViewModel` sinh ra, không đến từ backend.
  Invoke: 'runFlow.edgeLabel.invoke',
  Details: 'runFlow.edgeLabel.details',
};

export const getRunFlowEdgeLabel = (
  label: string | null | undefined,
  kind: RunFlowEdgeKind,
  t: RunFlowT,
): string | null => {
  if (!label) return null;
  const key = EDGE_LABEL_KEYS[label.trim()];
  return key ? t(key) : getRunFlowEdgeKindLabel(kind, t);
};

export const getRunFlowNodeKindLabel = (kind: RunFlowNodeKind, t: RunFlowT): string =>
  t(NODE_KIND_LABEL_KEYS[kind] || 'runFlow.nodeKind.analysis');

// Backend gửi kèm định danh ổn định cho mọi chuỗi hiển thị (lane.id, metadata.data_type,
// metadata.block_key, event.type) nên nhãn được dịch ở client theo nút ngôn ngữ, thay vì
// dùng chuỗi đã dựng sẵn từ máy chủ.
// The backend ships a stable id next to every display string, so labels are localized
// client-side from those ids rather than from the server-rendered text.
const LANE_LABEL_KEYS: Record<string, UiTextKey> = {
  entry: 'runFlow.lane.entry',
  data_source: 'runFlow.lane.dataSource',
  analysis: 'runFlow.lane.analysis',
  artifact: 'runFlow.lane.artifact',
};

// Nhiều data_type quy về cùng một nhãn (daily_data/daily_bars, news/news_search,
// fundamental/fundamentals) — giữ đúng bảng của backend.
const DATA_TYPE_LABEL_KEYS: Record<string, UiTextKey> = {
  realtime_quote: 'runFlow.dataType.realtimeQuote',
  daily_data: 'runFlow.dataType.dailyBars',
  daily_bars: 'runFlow.dataType.dailyBars',
  technical: 'runFlow.dataType.technical',
  news: 'runFlow.dataType.news',
  news_search: 'runFlow.dataType.news',
  fundamental: 'runFlow.dataType.fundamentals',
  fundamentals: 'runFlow.dataType.fundamentals',
};

// Node cố định của pipeline: ``node.id`` là định danh ổn định (schema ghi rõ "Stable node
// id"). Một số id mang hậu tố chỉ số hoặc kênh (``llm_analysis_1``, ``history_save_2``,
// ``notification_report_1``) nên khớp theo tiền tố.
// Fixed pipeline nodes keyed by the stable ``node.id``; some ids carry an index or channel
// suffix, so these are matched by prefix.
const NODE_ID_LABEL_KEYS: ReadonlyArray<readonly [string, UiTextKey]> = [
  ['request', 'runFlow.node.request'],
  ['task_queue', 'runFlow.node.taskQueue'],
  ['analysis', 'runFlow.node.analysisPipeline'],
  ['context_pack', 'runFlow.node.contextPack'],
  ['llm', 'runFlow.node.llm'],
  ['history_save', 'runFlow.node.historySave'],
  ['notification', 'runFlow.node.notification'],
];

const nodeIdLabelKey = (nodeId: string): UiTextKey | undefined => {
  for (const [prefix, key] of NODE_ID_LABEL_KEYS) {
    if (nodeId === prefix || nodeId.startsWith(`${prefix}_`)) {
      return key;
    }
  }
  return undefined;
};

const CONTEXT_BLOCK_LABEL_KEYS: Record<string, UiTextKey> = {
  quote: 'runFlow.contextBlock.quote',
  daily_bars: 'runFlow.contextBlock.dailyBars',
  technical: 'runFlow.contextBlock.technical',
  news: 'runFlow.contextBlock.news',
  fundamentals: 'runFlow.contextBlock.fundamentals',
};

/**
 * Đọc một khoá metadata, chấp nhận cả camelCase lẫn snake_case vì payload đi qua
 * ``toCamelCase`` ở tầng API nhưng bản ghi lịch sử cũ vẫn có thể ở dạng snake_case.
 * Read a metadata key accepting both camelCase and snake_case.
 */
export const runFlowMetadataString = (
  metadata: Record<string, unknown> | null | undefined,
  ...keys: string[]
): string | null => {
  if (!metadata) return null;
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return null;
};

/**
 * ``fallback`` là nhãn thô do backend gửi. Bản ghi lịch sử cũ có thể thiếu định danh ổn
 * định, khi đó phải hiển thị nhãn thô chứ không được để trống.
 * ``fallback`` is the raw backend label, used when the stable id is absent (old records).
 */
const localizedOrFallback = (
  key: UiTextKey | undefined,
  t: RunFlowT,
  fallback?: string | null,
): string => (key ? t(key) : (fallback || '').trim());

export const getRunFlowLaneLabel = (
  laneId: string,
  t: RunFlowT,
  fallback?: string | null,
): string => localizedOrFallback(LANE_LABEL_KEYS[laneId], t, fallback) || laneId;

export const getRunFlowDataTypeLabel = (
  dataType: string | null | undefined,
  t: RunFlowT,
  fallback?: string | null,
): string => {
  const key = dataType ? DATA_TYPE_LABEL_KEYS[dataType] : undefined;
  return localizedOrFallback(key, t, fallback) || dataType || '';
};

export const getRunFlowContextBlockLabel = (
  blockKey: string | null | undefined,
  t: RunFlowT,
  fallback?: string | null,
): string => {
  const key = blockKey ? CONTEXT_BLOCK_LABEL_KEYS[blockKey] : undefined;
  return localizedOrFallback(key, t, fallback) || blockKey || '';
};

/**
 * Nhãn node dịch từ ``metadata.data_type`` / ``metadata.block_key``.
 *
 * KHÔNG ghép ``· provider`` vào nhãn: thẻ node đã hiển thị ``node.provider`` trên một dòng
 * riêng, nên ghép vào sẽ lặp tên provider hai lần. Backend vẫn gửi ``label`` dạng
 * ``"<nhãn> · <provider>"`` và chuỗi đó chỉ còn dùng làm đường lùi.
 * Node label localized from the stable data_type / block_key. The provider is deliberately
 * NOT appended: the node card already renders ``node.provider`` on its own line.
 * Node không phải nguồn dữ liệu (entry/queue/llm/artifact…) không có hai khoá này nên giữ
 * nguyên nhãn backend, vốn đã là tiếng Anh.
 */
export const getRunFlowNodeLabel = (node: RunFlowNode, t: RunFlowT): string => {
  const blockKey = runFlowMetadataString(node.metadata, 'blockKey', 'block_key');
  if (blockKey) {
    return getRunFlowContextBlockLabel(blockKey, t, node.label);
  }
  const dataType = runFlowMetadataString(node.metadata, 'dataType', 'data_type');
  if (dataType) {
    return getRunFlowDataTypeLabel(dataType, t, node.label);
  }
  const nodeKey = nodeIdLabelKey(node.id);
  if (nodeKey) {
    // Backend ghép hậu tố kênh vào nhãn (``Push notification · report``); giữ lại hậu tố đó
    // vì nó là tên kênh, không phải chữ cần dịch.
    // Keep the backend's channel suffix (a proper noun) after the localized base label.
    const [, suffix] = node.label.split(' · ');
    const base = t(nodeKey);
    return suffix ? `${base} · ${suffix}` : base;
  }
  return node.label;
};

export const formatDuration = (value: number | null | undefined, t: RunFlowT): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return t('runFlow.valueUnavailable');
  }
  if (value < 1000) {
    return t('runFlow.durationMs', { value });
  }
  if (value < 60000) {
    return t('runFlow.durationSeconds', { value: (value / 1000).toFixed(1) });
  }
  return t('runFlow.durationMinutes', { value: (value / 60000).toFixed(1) });
};

export const formatDateTime = (
  value: string | null | undefined,
  language: UiLanguage,
  t: RunFlowT,
): string => {
  if (!value) {
    return t('runFlow.valueUnavailable');
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString(language === 'vi' ? 'vi-VN' : 'en-US');
};

export const compactText = (value: string | null | undefined, maxLength = 64): string => {
  const text = (value || '').trim();
  if (!text || text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, Math.max(8, maxLength - 12))}...${text.slice(-8)}`;
};

export const getNodeDisplayOrder = (node: RunFlowNode, index: number): number => {
  const explicitOrder = node.metadata?.order;
  if (typeof explicitOrder === 'number' && Number.isFinite(explicitOrder)) {
    return explicitOrder;
  }
  return index;
};

export const formatMetadataValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  if (typeof value === 'string') {
    return compactText(value, 120);
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return compactText(JSON.stringify(value), 120);
  } catch {
    return compactText(String(value), 120);
  }
};


/**
 * Tiêu đề sự kiện dịch theo ``event.type`` (trường được schema mô tả là "Stable event type")
 * cộng với ``event.severity``. Loại nào không nhận ra thì lùi về tiêu đề backend gửi.
 * Localize the event title from the stable ``event.type`` plus ``event.severity``.
 */
export const getRunFlowEventTitle = (event: RunFlowEvent, t: RunFlowT): string => {
  const severity = event.severity;
  switch (event.type) {
    case 'task_created':
      return t('runFlow.event.taskCreated');
    case 'task_started':
      return t('runFlow.event.taskStarted');
    case 'task_completed':
      return t('runFlow.event.taskCompleted');
    case 'task_failed':
      return t('runFlow.event.taskFailed');
    case 'task_cancelled':
      return t('runFlow.event.taskCancelled');
    case 'task_cancel_requested':
      return t('runFlow.event.taskCancelRequested');
    case 'provider_run': {
      const dataType = runFlowMetadataString(event.metadata, 'dataType', 'data_type');
      const label = getRunFlowDataTypeLabel(dataType, t, null) || t('runFlow.nodeKind.dataSource');
      return t(
        severity === 'success' ? 'runFlow.event.providerRun.succeeded' : 'runFlow.event.providerRun.failed',
        { label },
      );
    }
    case 'llm_run':
      return t(severity === 'success' ? 'runFlow.event.llmRun.succeeded' : 'runFlow.event.llmRun.failed');
    case 'history_run':
      return t(severity === 'success' ? 'runFlow.event.historyRun.succeeded' : 'runFlow.event.historyRun.failed');
    case 'notification_run':
      if (severity === 'success') return t('runFlow.event.notificationRun.succeeded');
      if (severity === 'warning') return t('runFlow.event.notificationRun.skipped');
      return t('runFlow.event.notificationRun.failed');
    case 'context_block_status': {
      const blockKey = runFlowMetadataString(event.metadata, 'blockKey', 'block_key');
      const label = getRunFlowContextBlockLabel(blockKey, t, null);
      return label ? t('runFlow.event.contextBlockStatus', { label }) : event.title;
    }
    default:
      return event.title;
  }
};

/**
 * Bộ lọc sự kiện khớp trên TRƯỜNG ỔN ĐỊNH, không khớp trên văn bản hiển thị.
 * Trước đây dùng regex ``/fallback|retry|<CJK keywords>/`` trên ``type + title + message``, nên
 * mỗi lần đổi ngôn ngữ nhãn là bộ lọc hỏng âm thầm.
 * Event filters match on stable fields, never on displayed text: the previous regex over
 * the rendered title/message broke silently whenever the label language changed.
 */
export const isRunFlowFallbackEvent = (event: RunFlowEvent): boolean => {
  if (event.type === 'provider_run') {
    return Boolean(
      runFlowMetadataString(event.metadata, 'fallbackFrom', 'fallback_from')
      || runFlowMetadataString(event.metadata, 'fallbackTo', 'fallback_to'),
    );
  }
  if (event.type === 'llm_run') {
    return Boolean(runFlowMetadataString(event.metadata, 'fallbackModel', 'fallback_model'));
  }
  return false;
};

// Lý do lỗi theo ``error_type`` ổn định của backend; code lạ thì dùng text server.
// Localized failure reasons keyed by the backend's stable ``error_type``.
const ERROR_REASON_KEYS: Record<string, UiTextKey> = {
  NoUsableNews: 'runFlow.errorReason.noUsableNews',
};

/**
 * Lý do lỗi đã bản địa hoá: ưu tiên ``error_type`` đã biết, sau đó text server
 * (``error_message``), cuối cùng là "lỗi không xác định".
 */
export const getRunFlowErrorReason = (
  errorType: string | null | undefined,
  fallback: string | null | undefined,
  t: RunFlowT,
): string => {
  const key = errorType ? ERROR_REASON_KEYS[errorType] : undefined;
  if (key) return t(key);
  return (fallback || '').trim() || t('runFlow.errorReason.unknown');
};

const runFlowMetadataNumber = (
  metadata: Record<string, unknown> | null | undefined,
  ...keys: string[]
): number | null => {
  if (!metadata) return null;
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
};

const providerRunMessage = (
  metadata: Record<string, unknown> | null | undefined,
  success: boolean,
  t: RunFlowT,
  recordCount?: number | null,
): string | null => {
  const dataType = runFlowMetadataString(metadata, 'dataType', 'data_type');
  const provider = runFlowMetadataString(metadata, 'provider');
  if (!dataType || !provider) return null;
  const label = getRunFlowDataTypeLabel(dataType, t, dataType);
  if (success) {
    const count = recordCount ?? runFlowMetadataNumber(metadata, 'recordCount', 'record_count');
    return count === null || count === undefined
      ? t('runFlow.eventMessage.providerRun.succeeded', { label, provider })
      : t('runFlow.eventMessage.providerRun.succeededWithCount', { label, provider, count });
  }
  const reason = getRunFlowErrorReason(
    runFlowMetadataString(metadata, 'errorType', 'error_type'),
    runFlowMetadataString(metadata, 'errorMessage', 'error_message'),
    t,
  );
  return t('runFlow.eventMessage.providerRun.failed', { label, provider, reason });
};

/**
 * Message của event đã bản địa hoá. Hiện chỉ ``provider_run`` được dựng lại từ
 * metadata (provider, data_type, record_count, error_type, error_message); event
 * khác, hoặc payload cũ thiếu metadata, giữ nguyên ``event.message`` của server.
 * Localized event message; only provider_run is rebuilt from metadata, the rest
 * keep the server text.
 */
export const getRunFlowEventMessage = (event: RunFlowEvent, t: RunFlowT): string | null => {
  if (event.type === 'provider_run') {
    const localized = providerRunMessage(event.metadata, event.severity === 'success', t);
    if (localized) return localized;
  }
  return event.message ?? null;
};

/**
 * Message của node nguồn dữ liệu đã bản địa hoá (cùng quy tắc với event). Node block
 * đầu vào (``block_key``) hoặc node đang chạy giữ nguyên text server.
 */
export const getRunFlowNodeMessage = (node: RunFlowNode, t: RunFlowT): string | null => {
  const isProviderAttempt = node.kind === 'data_source'
    && !runFlowMetadataString(node.metadata, 'blockKey', 'block_key');
  if (isProviderAttempt) {
    if (node.status === 'success' || node.status === 'fallback') {
      const localized = providerRunMessage(node.metadata, true, t, node.recordCount);
      if (localized) return localized;
    } else if (node.status === 'failed' || node.status === 'timeout') {
      const localized = providerRunMessage(node.metadata, false, t);
      if (localized) return localized;
    }
  }
  return node.message ?? null;
};

export const isRunFlowCancelEvent = (event: RunFlowEvent): boolean =>
  event.type === 'task_cancelled' || event.type === 'task_cancel_requested';
