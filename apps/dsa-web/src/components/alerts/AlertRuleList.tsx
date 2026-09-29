import type React from 'react';
import { useState } from 'react';
import { Bell, Trash2 } from 'lucide-react';
import { Badge, Button, Card, ConfirmDialog, EmptyState, Pagination, Select } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { formatUiText, type UiLanguage } from '../../i18n/uiText';
import {
  ALERT_DIRECTION_LABELS,
  ALERT_ENABLED_FILTER_OPTIONS,
  ALERT_LIST_TEXT,
  ALERT_MARKET_LIGHT_STATUS_LABELS,
  ALERT_MARKET_REGION_LABELS,
  ALERT_PRICE_LIMIT_SIDE_OPTIONS,
  ALERT_REALTIME_TEXT,
  ALERT_SCOPE_LABELS,
  ALERT_SEVERITY_LABELS,
  ALERT_TYPE_FILTER_OPTIONS,
  ALERT_TYPE_LABELS,
} from '../../locales/featureText';
import type { AlertFeedStatus, AlertLiveQuote, AlertRuleItem, AlertType, MarketRegion } from '../../types/alerts';
import { liveQuoteKey, useAlertStore } from '../../stores/alertStore';
import { formatDateTime } from '../../utils/format';

export type AlertRuleEnabledFilter = 'all' | 'enabled' | 'disabled';
export type AlertTypeFilter = 'all' | AlertType;
export type AlertRuleBusyAction = 'test' | 'toggle' | 'delete';

export interface AlertRuleBusyState {
  id: number;
  action: AlertRuleBusyAction;
}

function formatParameters(rule: AlertRuleItem, language: UiLanguage): string {
  const directionLabels = ALERT_DIRECTION_LABELS[language];
  if (rule.alertType === 'market_light_status') {
    const statuses = rule.parameters.statuses ?? [];
    return statuses.length > 0
      ? statuses.map((status) => ALERT_MARKET_LIGHT_STATUS_LABELS[language][status] ?? status).join(' / ')
      : '--';
  }
  if (rule.alertType === 'market_light_score_drop') {
    return formatUiText(ALERT_LIST_TEXT[language].scoreDropAtLeast, { value: rule.parameters.minDrop ?? '--' });
  }
  if (rule.alertType === 'price_cross') {
    const base = `${rule.parameters.direction === 'below' ? directionLabels.belowPrice : directionLabels.abovePrice} ${rule.parameters.price ?? '--'}`;
    return rule.parameters.triggerMode === 'cross' ? `${base} (${ALERT_REALTIME_TEXT[language].crossMode})` : base;
  }
  if (rule.alertType === 'price_limit_touch') {
    const side = ALERT_PRICE_LIMIT_SIDE_OPTIONS[language].find((option) => option.value === rule.parameters.side)?.label ?? rule.parameters.side ?? '--';
    return rule.parameters.nearPct ? `${side} ±${rule.parameters.nearPct}%` : side;
  }
  if (rule.alertType === 'price_change_percent') {
    return `${rule.parameters.direction === 'down' ? directionLabels.downChange : directionLabels.upChange} ${rule.parameters.changePct ?? '--'}%`;
  }
  if (rule.alertType === 'volume_spike') {
    return `${rule.parameters.multiplier ?? '--'}x`;
  }
  if (rule.alertType === 'ma_price_cross') {
    return `${rule.parameters.direction === 'below' ? directionLabels.belowThreshold : directionLabels.aboveThreshold} MA${rule.parameters.window ?? '--'}`;
  }
  if (rule.alertType === 'rsi_threshold') {
    return `RSI${rule.parameters.period ?? '--'} ${rule.parameters.direction === 'below' ? directionLabels.belowThreshold : directionLabels.aboveThreshold} ${rule.parameters.threshold ?? '--'}`;
  }
  if (rule.alertType === 'macd_cross' || rule.alertType === 'kdj_cross') {
    const direction = rule.parameters.direction === 'bearish_cross' ? directionLabels.bearishCross : directionLabels.bullishCross;
    if (rule.alertType === 'macd_cross') {
      return `MACD(${rule.parameters.fastPeriod ?? '--'},${rule.parameters.slowPeriod ?? '--'},${rule.parameters.signalPeriod ?? '--'}) ${direction}`;
    }
    return `KDJ(${rule.parameters.period ?? '--'},${rule.parameters.kPeriod ?? '--'},${rule.parameters.dPeriod ?? '--'}) ${direction}`;
  }
  if (rule.alertType === 'portfolio_stop_loss') {
    return rule.parameters.mode === 'breach' ? directionLabels.stopLossBreach : directionLabels.stopLossNear;
  }
  if (rule.alertType === 'portfolio_concentration') return 'top_weight_pct';
  if (rule.alertType === 'portfolio_drawdown') return 'max_drawdown_pct';
  if (rule.alertType === 'portfolio_price_stale') return 'price_stale / price_available';
  return `CCI${rule.parameters.period ?? '--'} ${rule.parameters.direction === 'below' ? directionLabels.belowThreshold : directionLabels.aboveThreshold} ${rule.parameters.threshold ?? '--'}`;
}

function isCoolingDown(rule: AlertRuleItem): boolean {
  return rule.cooldownActive === true;
}

function formatTarget(rule: AlertRuleItem, language: UiLanguage): string {
  if (rule.targetScope === 'market') return ALERT_MARKET_REGION_LABELS[language][rule.target as MarketRegion] ?? rule.target;
  if (rule.targetScope === 'watchlist') return 'default';
  if (rule.targetScope === 'portfolio_account' || rule.targetScope === 'portfolio_holdings') {
    const text = ALERT_LIST_TEXT[language];
    return rule.target === 'all'
      ? text.allAccounts
      : formatUiText(text.accountTarget, { target: rule.target });
  }
  return rule.target;
}

const priceFormatter = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 2 });

/** Khoảng cách % từ giá live tới ngưỡng của rule giá cắt ngưỡng. Distance from live price to threshold. */
function thresholdDistance(rule: AlertRuleItem, quote: AlertLiveQuote): string | null {
  if (rule.alertType !== 'price_cross' || !quote.price || !rule.parameters.price) return null;
  const pct = ((rule.parameters.price - quote.price) / quote.price) * 100;
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

const LivePriceCell: React.FC<{ rule: AlertRuleItem; quote?: AlertLiveQuote; language: UiLanguage }> = ({ rule, quote, language }) => {
  if (rule.targetScope !== 'single_symbol' || !quote || quote.price == null) {
    return <span className="text-xs text-muted-text">--</span>;
  }
  const change = quote.changePct ?? null;
  const tone = change == null ? 'text-secondary-text' : change > 0 ? 'text-success' : change < 0 ? 'text-[hsl(var(--color-danger-alert-text))]' : 'text-warning';
  const distance = thresholdDistance(rule, quote);
  return (
    <div data-testid={`live-price-${rule.id}`}>
      <div className="font-mono text-foreground">{priceFormatter.format(quote.price)}</div>
      {change != null ? <div className={`text-xs ${tone}`}>{`${change > 0 ? '+' : ''}${change.toFixed(2)}%`}</div> : null}
      {distance ? <div className="text-xs text-muted-text">{formatUiText(ALERT_REALTIME_TEXT[language].distance, { value: distance })}</div> : null}
    </div>
  );
};

function feedStatusText(feed: AlertFeedStatus | null, language: UiLanguage): { label: string; tone: 'success' | 'warning' | 'default' } {
  const text = ALERT_REALTIME_TEXT[language];
  if (!feed || !feed.enabled) return { label: text.feedDisabled, tone: 'default' };
  if (feed.connected) {
    return { label: feed.inSession === false ? `${text.feedConnected} · ${text.feedOutOfSession}` : text.feedConnected, tone: 'success' };
  }
  return { label: text.feedDisconnected, tone: 'warning' };
}

function hasChildTargetCooldown(rule: AlertRuleItem): boolean {
  return rule.targetScope === 'watchlist' || rule.targetScope === 'portfolio_holdings';
}

interface AlertRuleListProps {
  rules: AlertRuleItem[];
  total: number;
  page: number;
  pageSize: number;
  className?: string;
  isLoading?: boolean;
  enabledFilter: AlertRuleEnabledFilter;
  alertTypeFilter: AlertTypeFilter;
  onEnabledFilterChange: (value: AlertRuleEnabledFilter) => void;
  onAlertTypeFilterChange: (value: AlertTypeFilter) => void;
  onPageChange: (page: number) => void;
  onToggleEnabled: (rule: AlertRuleItem) => void;
  onDelete: (rule: AlertRuleItem) => void;
  onTest: (rule: AlertRuleItem) => void;
  busyRule?: AlertRuleBusyState | null;
}

export const AlertRuleList: React.FC<AlertRuleListProps> = ({
  rules,
  total,
  page,
  pageSize,
  className,
  isLoading = false,
  enabledFilter,
  alertTypeFilter,
  onEnabledFilterChange,
  onAlertTypeFilterChange,
  onPageChange,
  onToggleEnabled,
  onDelete,
  onTest,
  busyRule = null,
}) => {
  const { language } = useUiLanguage();
  const text = ALERT_LIST_TEXT[language];
  const livePrices = useAlertStore((state) => state.livePrices);
  const feedStatus = useAlertStore((state) => state.feedStatus);
  const feed = feedStatusText(feedStatus, language);
  const [pendingDelete, setPendingDelete] = useState<AlertRuleItem | null>(null);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const isRuleBusy = (rule: AlertRuleItem) => busyRule?.id === rule.id;
  const isRuleActionBusy = (rule: AlertRuleItem, action: AlertRuleBusyAction) => (
    busyRule?.id === rule.id && busyRule.action === action
  );

  return (
    <Card
      title={text.title}
      subtitle={formatUiText(text.subtitle, { total })}
      variant="bordered"
      padding="md"
      className={className}
    >
      <div className="mb-3 flex items-center gap-2 text-xs text-secondary-text" data-testid="alert-feed-status">
        <span
          aria-hidden="true"
          className={`inline-block h-2 w-2 rounded-full ${feed.tone === 'success' ? 'bg-success' : feed.tone === 'warning' ? 'bg-warning' : 'bg-muted-text'}`}
        />
        <span>{feed.label}</span>
      </div>
      <div className="mb-4 grid gap-3 md:grid-cols-2">
        <Select
          label={text.enabledFilter}
          value={enabledFilter}
          options={ALERT_ENABLED_FILTER_OPTIONS[language]}
          onChange={(value) => {
            onEnabledFilterChange(value as AlertRuleEnabledFilter);
          }}
        />
        <Select
          label={text.alertTypeFilter}
          value={alertTypeFilter}
          options={ALERT_TYPE_FILTER_OPTIONS[language]}
          onChange={(value) => {
            onAlertTypeFilterChange(value as AlertTypeFilter);
          }}
        />
      </div>

      {rules.length === 0 ? (
        <div className="flex min-h-[220px] flex-1 items-center justify-center">
          <EmptyState
            icon={<Bell className="h-6 w-6" />}
            title={isLoading ? text.loadingRules : text.emptyTitle}
            description={text.emptyDescription}
          />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-x-auto">
          <table className="w-full min-w-[1060px] text-left text-sm">
            <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
              <tr>
                <th className="px-3 py-2 font-medium">{text.rule}</th>
                <th className="px-3 py-2 font-medium">{text.target}</th>
                <th className="px-3 py-2 font-medium">{ALERT_REALTIME_TEXT[language].livePrice}</th>
                <th className="px-3 py-2 font-medium">{text.type}</th>
                <th className="px-3 py-2 font-medium">{text.parameters}</th>
                <th className="px-3 py-2 font-medium">{text.status}</th>
                <th className="px-3 py-2 font-medium">{text.cooldown}</th>
                <th className="px-3 py-2 font-medium">{text.updatedAt}</th>
                <th className="px-3 py-2 text-right font-medium">{text.action}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {rules.map((rule) => (
                <tr key={rule.id} className="align-top">
                  <td className="px-3 py-3">
                    <div className="font-medium text-foreground">{rule.name}</div>
                    <div className="mt-1 text-xs text-muted-text">{formatUiText(text.source, { source: rule.source })}</div>
                  </td>
                  <td className="px-3 py-3 text-secondary-text">
                    <div className="font-mono">{formatTarget(rule, language)}</div>
                    <div className="mt-1 text-xs">{ALERT_SCOPE_LABELS[language][rule.targetScope] ?? rule.targetScope}</div>
                  </td>
                  <td className="px-3 py-3">
                    <LivePriceCell rule={rule} quote={livePrices[liveQuoteKey(rule.target)]} language={language} />
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-col items-start gap-1">
                      <Badge variant="info">{ALERT_TYPE_LABELS[language][rule.alertType]}</Badge>
                      <Badge variant={rule.severity === 'critical' ? 'danger' : rule.severity === 'warning' ? 'warning' : 'default'}>
                        {ALERT_SEVERITY_LABELS[language][rule.severity] ?? rule.severity}
                      </Badge>
                    </div>
                  </td>
                  <td className="px-3 py-3 text-secondary-text">{formatParameters(rule, language)}</td>
                  <td className="px-3 py-3">
                    <Badge variant={rule.enabled ? 'success' : 'default'}>
                      {rule.enabled ? text.enabled : text.disabled}
                    </Badge>
                  </td>
                  <td className="px-3 py-3 text-xs text-secondary-text">
                    <div>{isCoolingDown(rule) ? text.coolingDown : text.notCoolingDown}</div>
                    <div className="mt-1">{formatDateTime(rule.cooldownUntil)}</div>
                    {hasChildTargetCooldown(rule) ? (
                      <div className="mt-1 text-muted-text">{text.childTargetCooldown}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-3 text-xs text-secondary-text">{formatDateTime(rule.updatedAt ?? rule.createdAt)}</td>
                  <td className="px-3 py-3">
                    <div className="flex justify-end gap-2">
                      <Button
                        size="xsm"
                        variant="outline"
                        onClick={() => onTest(rule)}
                        isLoading={isRuleActionBusy(rule, 'test')}
                        loadingText={text.testing}
                        disabled={isRuleBusy(rule) && !isRuleActionBusy(rule, 'test')}
                      >
                        {text.test}
                      </Button>
                      <Button
                        size="xsm"
                        variant={rule.enabled ? 'secondary' : 'primary'}
                        onClick={() => onToggleEnabled(rule)}
                        isLoading={isRuleActionBusy(rule, 'toggle')}
                        loadingText={rule.enabled ? text.disabling : text.enabling}
                        disabled={isRuleBusy(rule) && !isRuleActionBusy(rule, 'toggle')}
                      >
                        {rule.enabled ? text.disable : text.enable}
                      </Button>
                      <Button
                        size="xsm"
                        variant="danger-subtle"
                        aria-label={formatUiText(text.deleteAria, { name: rule.name })}
                        onClick={() => setPendingDelete(rule)}
                        disabled={isRuleBusy(rule)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        {text.delete}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        currentPage={page}
        totalPages={totalPages}
        onPageChange={onPageChange}
        className="mt-5"
      />

      <ConfirmDialog
        isOpen={pendingDelete != null}
        title={text.deleteTitle}
        message={pendingDelete ? formatUiText(text.deleteMessage, { name: pendingDelete.name }) : ''}
        confirmText={text.delete}
        cancelText={text.cancel}
        isDanger
        onConfirm={() => {
          if (pendingDelete) {
            onDelete(pendingDelete);
          }
          setPendingDelete(null);
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </Card>
  );
};
