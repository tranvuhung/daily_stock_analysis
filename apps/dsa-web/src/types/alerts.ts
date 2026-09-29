import type { AnalysisContextPackOverview, MarketPhaseSummary } from './analysis';
import type { DecisionSignalItem } from './decisionSignals';

export type AlertType =
  | 'price_cross'
  | 'price_change_percent'
  | 'price_limit_touch'
  | 'volume_spike'
  | 'ma_price_cross'
  | 'rsi_threshold'
  | 'macd_cross'
  | 'kdj_cross'
  | 'cci_threshold'
  | 'portfolio_stop_loss'
  | 'portfolio_concentration'
  | 'portfolio_drawdown'
  | 'portfolio_price_stale'
  | 'market_light_status'
  | 'market_light_score_drop';
export type AlertSeverity = 'info' | 'warning' | 'critical';
export type AlertTargetScope = 'single_symbol' | 'watchlist' | 'portfolio_holdings' | 'portfolio_account' | 'market';
export type AlertDirection = 'above' | 'below' | 'up' | 'down' | 'bullish_cross' | 'bearish_cross';
export type PortfolioStopLossMode = 'near' | 'breach';
export type MarketRegion = 'vn' | 'cn' | 'hk' | 'us';
export type MarketLightStatus = 'yellow' | 'red';
export type PriceTriggerMode = 'level' | 'cross';
export type PriceLimitSide = 'ceiling' | 'floor' | 'both';
export type AlertDryRunStatus = 'triggered' | 'not_triggered' | 'evaluation_error';
export type AlertTriggerStatus = 'triggered' | 'skipped' | 'degraded' | 'failed';

export interface AlertRuleParameters {
  direction?: AlertDirection;
  price?: number;
  changePct?: number;
  multiplier?: number;
  window?: number;
  period?: number;
  threshold?: number;
  fastPeriod?: number;
  slowPeriod?: number;
  signalPeriod?: number;
  kPeriod?: number;
  dPeriod?: number;
  mode?: PortfolioStopLossMode;
  statuses?: MarketLightStatus[];
  minDrop?: number;
  triggerMode?: PriceTriggerMode;
  side?: PriceLimitSide;
  nearPct?: number;
}

export interface AlertRuleItem {
  id: number;
  name: string;
  targetScope: AlertTargetScope;
  target: string;
  alertType: AlertType;
  parameters: AlertRuleParameters;
  severity: AlertSeverity;
  enabled: boolean;
  source: string;
  cooldownPolicy?: Record<string, unknown> | null;
  notificationPolicy?: Record<string, unknown> | null;
  lastTriggeredAt?: string | null;
  cooldownUntil?: string | null;
  cooldownActive?: boolean | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AlertRuleListResponse {
  items: AlertRuleItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AlertRuleCreateRequest {
  name?: string;
  targetScope?: AlertTargetScope;
  target: string;
  alertType: AlertType;
  parameters: AlertRuleParameters;
  severity: AlertSeverity;
  enabled?: boolean;
}

export interface AlertDeleteResponse {
  deleted: number;
}

export interface AlertRuleTestResponse {
  ruleId: number;
  targetScope?: AlertTargetScope | string | null;
  status: AlertDryRunStatus;
  triggered: boolean;
  observedValue?: unknown;
  message: string;
  evaluatedCount?: number;
  triggeredCount?: number;
  degradedCount?: number;
  skippedCount?: number;
  targetResults?: AlertRuleTargetResult[];
}

export interface AlertRuleTargetResult {
  target: string;
  displayTarget?: string | null;
  status: AlertDryRunStatus;
  recordStatus?: AlertTriggerStatus | null;
  triggered: boolean;
  observedValue?: unknown;
  threshold?: unknown;
  message: string;
}

export interface AlertTriggerItem {
  id: number;
  ruleId?: number | null;
  target: string;
  observedValue?: number | null;
  threshold?: number | null;
  reason?: string | null;
  dataSource?: string | null;
  dataTimestamp?: string | null;
  triggeredAt?: string | null;
  status: AlertTriggerStatus | string;
  diagnostics?: string | null;
  marketPhaseSummary?: MarketPhaseSummary | null;
  analysisContextPackOverview?: AnalysisContextPackOverview | null;
  analysisVisibilitySource?: string | null;
  decisionSignalSummary?: Partial<DecisionSignalItem> | null;
}

export interface AlertTriggerListResponse {
  items: AlertTriggerItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AlertNotificationItem {
  id: number;
  triggerId?: number | null;
  channel: string;
  attempt: number;
  success: boolean;
  errorCode?: string | null;
  retryable: boolean;
  latencyMs?: number | null;
  diagnostics?: string | null;
  createdAt?: string | null;
}

export interface AlertNotificationListResponse {
  items: AlertNotificationItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AlertRuleListQuery {
  enabled?: boolean;
  alertType?: AlertType;
  targetScope?: AlertTargetScope;
  target?: string;
  source?: string;
  page?: number;
  pageSize?: number;
}

export interface AlertTriggerListQuery {
  ruleId?: number;
  target?: string;
  status?: string;
  page?: number;
  pageSize?: number;
}

export interface AlertNotificationListQuery {
  triggerId?: number;
  channel?: string;
  success?: boolean;
  page?: number;
  pageSize?: number;
}

export interface AlertMarketRegionsResponse {
  regions: MarketRegion[];
  default: MarketRegion | null;
}

export interface AlertLiveQuote {
  /** Mục tiêu của rule (ví dụ HPG hoặc HPG.VN); có trong live-quotes, không có trong price_update. */
  target?: string;
  symbol: string;
  price: number | null;
  change?: number | null;
  changePct?: number | null;
  ref?: number | null;
  ceiling?: number | null;
  floor?: number | null;
  high?: number | null;
  low?: number | null;
  volume?: number | null;
  marketStatus?: string | null;
  ts?: string | null;
  source?: string | null;
}

/** Trạng thái feed giá realtime (MQTT TVSI). Realtime feed status. */
export interface AlertFeedStatus {
  enabled: boolean;
  state?: string;
  connected?: boolean;
  source?: string;
  subscribed?: number;
  rules?: number;
  symbols?: number;
  inSession?: boolean;
  lastMessageAt?: string | null;
  lastMessageAgeSeconds?: number | null;
  lastError?: string | null;
}

export interface AlertLiveQuotesResponse {
  feed: AlertFeedStatus;
  quotes: AlertLiveQuote[];
}

/** Sự kiện ``alert_triggered`` từ SSE. */
export interface AlertTriggeredEvent {
  triggerId?: number | null;
  ruleId: number;
  target: string;
  displayTarget?: string | null;
  alertType: string;
  severity: AlertSeverity;
  observedValue?: number | null;
  threshold?: number | null;
  reason?: string | null;
  dataSource?: string | null;
  triggeredAt: string;
}
