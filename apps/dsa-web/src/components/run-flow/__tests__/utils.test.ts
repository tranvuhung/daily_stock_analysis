import { describe, expect, it } from 'vitest';
import { formatUiText, UI_TEXT, type UiLanguage, type UiTextKey } from '../../../i18n/uiText';
import type { RunFlowEvent, RunFlowNode } from '../../../types/runFlow';
import {
  getRunFlowContextBlockLabel,
  getRunFlowDataTypeLabel,
  getRunFlowEdgeLabel,
  getRunFlowEventTitle,
  getRunFlowLaneLabel,
  getRunFlowNodeLabel,
  isRunFlowCancelEvent,
  isRunFlowFallbackEvent,
} from '../utils';

// Dựng lại đúng hàm `t` mà `UiLanguageContext` cung cấp, để kiểm chứng nhãn thật sự đổi
// theo ngôn ngữ giao diện chứ không phải theo cấu hình máy chủ.
// Rebuild the same `t` the UI context provides, so we prove labels follow the UI toggle.
const makeT = (language: UiLanguage) => (key: UiTextKey, params?: Record<string, string | number>) =>
  formatUiText(UI_TEXT[language][key], params);

const en = makeT('en');
const vi = makeT('vi');

const providerNode = (overrides: Partial<RunFlowNode> = {}): RunFlowNode => ({
  id: 'provider_daily_data_tushare_1',
  lane: 'data_source',
  kind: 'data_source',
  label: 'Daily bars · Tushare',
  status: 'success',
  provider: 'Tushare',
  metadata: { dataType: 'daily_data' },
  ...overrides,
} as RunFlowNode);

describe('run-flow label localization', () => {
  it('localizes lane labels from the stable lane id', () => {
    expect(getRunFlowLaneLabel('data_source', en)).toBe('Data source');
    expect(getRunFlowLaneLabel('data_source', vi)).toBe('Nguồn dữ liệu');
    expect(getRunFlowLaneLabel('analysis', vi)).toBe('Bộ phân tích');
  });

  it('maps the aliased data types onto a single label', () => {
    // Backend dùng cả `daily_data` lẫn `daily_bars`, cả `news` lẫn `news_search`.
    expect(getRunFlowDataTypeLabel('daily_data', en)).toBe('Daily bars');
    expect(getRunFlowDataTypeLabel('daily_bars', en)).toBe('Daily bars');
    expect(getRunFlowDataTypeLabel('news', vi)).toBe('Tin tức');
    expect(getRunFlowDataTypeLabel('news_search', vi)).toBe('Tin tức');
  });

  it('localizes context block labels from block_key', () => {
    expect(getRunFlowContextBlockLabel('technical', en)).toBe('Technical indicators');
    expect(getRunFlowContextBlockLabel('technical', vi)).toBe('Chỉ báo kỹ thuật');
  });

  it('builds the node label without repeating the provider', () => {
    // Thẻ node đã hiển thị `node.provider` trên dòng riêng, nên nhãn không được ghép lại.
    // The node card renders the provider separately, so the label must not repeat it.
    expect(getRunFlowNodeLabel(providerNode(), en)).toBe('Daily bars');
    expect(getRunFlowNodeLabel(providerNode(), vi)).toBe('Nến ngày');
  });

  it('falls back to the raw backend label when the stable id is missing', () => {
    // Bản ghi lịch sử cũ không có `metadata.data_type` — không được để nhãn trống.
    // Old history records carry no stable id; the raw label must still show.
    const legacy = providerNode({ metadata: {}, label: 'Something the client cannot map' });
    expect(getRunFlowNodeLabel(legacy, en)).toBe('Something the client cannot map');
    expect(getRunFlowDataTypeLabel('an_unknown_type', en, 'Raw label')).toBe('Raw label');
  });

  it('localizes fixed pipeline nodes from the stable node id', () => {
    const node = (id: string, label: string) => ({ id, label, metadata: {} } as RunFlowNode);
    expect(getRunFlowNodeLabel(node('request', 'User request'), vi)).toBe('Yêu cầu người dùng');
    expect(getRunFlowNodeLabel(node('task_queue', 'Task queue'), vi)).toBe('Hàng đợi tác vụ');
    // Hậu tố chỉ số trong id (`llm_analysis_1`, `history_save_2`) vẫn phải khớp.
    expect(getRunFlowNodeLabel(node('llm_analysis_1', 'LLM generation'), vi)).toBe('Sinh nội dung LLM');
    expect(getRunFlowNodeLabel(node('history_save_2', 'Save report'), en)).toBe('Save report');
  });

  it('keeps the channel suffix after a localized node label', () => {
    // `· report` là tên kênh (danh từ riêng), không được dịch.
    const node = { id: 'notification_report_1', label: 'Push notification · report', metadata: {} } as RunFlowNode;
    expect(getRunFlowNodeLabel(node, vi)).toBe('Gửi thông báo · report');
    expect(getRunFlowNodeLabel(node, en)).toBe('Push notification · report');
  });

  it('accepts both camelCase and snake_case metadata keys', () => {
    const snake = providerNode({ metadata: { data_type: 'chip' } });
    expect(getRunFlowNodeLabel(snake, en)).toBe('Chip structure');
  });
});

describe('run-flow edge labels', () => {
  it('translates the specific backend step label rather than collapsing to the kind', () => {
    // Nhãn backend ('Submit', 'Generation'...) mang ngữ nghĩa từng bước, giàu thông tin hơn
    // `edge.kind` ('data'/'control'), nên phải giữ nguyên ngữ nghĩa khi dịch.
    expect(getRunFlowEdgeLabel('Submit', 'control', vi)).toBe('Gửi yêu cầu');
    expect(getRunFlowEdgeLabel('Generation', 'data', vi)).toBe('Sinh nội dung');
    expect(getRunFlowEdgeLabel('Save', 'data', en)).toBe('Save');
    expect(getRunFlowEdgeLabel('Degraded', 'fallback', vi)).toBe('Suy giảm');
  });

  it('translates the synthetic labels produced by the view model', () => {
    expect(getRunFlowEdgeLabel('Invoke', 'control', vi)).toBe('Gọi');
    expect(getRunFlowEdgeLabel('Details', 'control', vi)).toBe('Chi tiết');
  });

  it('falls back to the edge kind for an unknown label, and renders nothing when empty', () => {
    expect(getRunFlowEdgeLabel('Something new', 'fallback', en)).toBe('Fallback');
    expect(getRunFlowEdgeLabel(null, 'data', en)).toBeNull();
    expect(getRunFlowEdgeLabel('', 'data', en)).toBeNull();
  });
});

const event = (overrides: Partial<RunFlowEvent> = {}): RunFlowEvent => ({
  id: 'evt-1',
  timestamp: '2026-06-08T08:00:00Z',
  severity: 'success',
  type: 'provider_run',
  nodeId: 'provider_news_tavily_1',
  title: 'raw backend title',
  metadata: { dataType: 'news' },
  ...overrides,
} as RunFlowEvent);

describe('run-flow event titles', () => {
  it('localizes provider runs with the data type interpolated', () => {
    expect(getRunFlowEventTitle(event(), en)).toBe('News succeeded');
    expect(getRunFlowEventTitle(event(), vi)).toBe('Tin tức thành công');
    expect(getRunFlowEventTitle(event({ severity: 'danger' }), vi)).toBe('Tin tức thất bại');
  });

  it('localizes task lifecycle events', () => {
    expect(getRunFlowEventTitle(event({ type: 'task_created' }), vi)).toBe('Đã tạo tác vụ');
    expect(getRunFlowEventTitle(event({ type: 'task_cancelled' }), en)).toBe('Task cancelled');
  });

  it('distinguishes the three notification outcomes by severity', () => {
    expect(getRunFlowEventTitle(event({ type: 'notification_run', severity: 'success' }), en)).toBe('Notification sent successfully');
    expect(getRunFlowEventTitle(event({ type: 'notification_run', severity: 'warning' }), en)).toBe('Notification skipped');
    expect(getRunFlowEventTitle(event({ type: 'notification_run', severity: 'danger' }), en)).toBe('Notification failed');
  });

  it('falls back to the backend title for an unknown event type', () => {
    expect(getRunFlowEventTitle(event({ type: 'something_new' }), en)).toBe('raw backend title');
  });
});

describe('run-flow event filters', () => {
  it('detects a provider fallback from metadata, not from text', () => {
    expect(isRunFlowFallbackEvent(event({ metadata: { fallbackFrom: 'Tushare' } }))).toBe(true);
    expect(isRunFlowFallbackEvent(event({ metadata: { fallback_to: 'AkShare' } }))).toBe(true);
    expect(isRunFlowFallbackEvent(event({ metadata: { dataType: 'news' } }))).toBe(false);
  });

  it('detects an LLM model switch', () => {
    expect(isRunFlowFallbackEvent(event({ type: 'llm_run', metadata: { fallbackModel: 'gpt-4o' } }))).toBe(true);
    expect(isRunFlowFallbackEvent(event({ type: 'llm_run', metadata: {} }))).toBe(false);
  });

  it('detects cancellation from the stable event type', () => {
    expect(isRunFlowCancelEvent(event({ type: 'task_cancelled' }))).toBe(true);
    expect(isRunFlowCancelEvent(event({ type: 'task_cancel_requested' }))).toBe(true);
    expect(isRunFlowCancelEvent(event({ type: 'task_completed' }))).toBe(false);
  });
});
