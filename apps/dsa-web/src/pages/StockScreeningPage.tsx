import type React from 'react';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Bookmark,
  Building2,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Droplet,
  Factory,
  Flame,
  Gem,
  Landmark,
  Pickaxe,
  Plane,
  Play,
  PlusCircle,
  RefreshCw,
  Search,
  Shield,
  SlidersHorizontal,
  Stethoscope,
  Trees,
  Utensils,
  Wrench,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  screeningApi,
  type ScreeningCandidate,
  type ScreeningHotspotDetail,
  type ScreeningHotspot,
  type ScreeningHotspotsResponse,
  type ScreeningRunSummary,
  type ScreeningScreenResponse,
  type ScreeningScreenTaskStatus,
  type ScreeningStrategy,
} from '../api/screening';
import { formatParsedApiError, getParsedApiError, toApiErrorMessage, type ParsedApiError } from '../api/error';
import { AppPage, Button, InlineAlert, Select } from '../components/common';

const MARKETS = [{ id: 'cn', label: 'A-share' }];
const SCREEN_TASK_STORAGE_KEY = 'dsa.screening.activeScreenTask.v1';
const SCREEN_TASK_POLL_INTERVAL_MS = 2000;
const CUSTOM_STRATEGY_OPTION_VALUE = '__custom_strategy__';
const STRATEGY_CATEGORY_LABELS: Record<string, string> = {
  framework: 'Composite',
  income: 'Income',
  momentum: 'Momentum',
  quality: 'Quality',
  reversal: 'Reversal',
  trend: 'Trend',
  value: 'Value',
};

const formatStrategyCategory = (value?: string) => {
  const normalized = value?.trim();
  if (!normalized) {
    return 'Custom';
  }
  return STRATEGY_CATEGORY_LABELS[normalized.toLowerCase()] || normalized;
};

type PersistedScreenTask = {
  taskId: string;
  runId?: string;
  market: string;
  strategy: string;
  maxResults: number;
};

const formatRunCreatedAt = (value: string | null | undefined) => {
  if (!value) {
    return 'Unknown time';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
};

// Filter label in a history entry: strategy ID → display name (falls back to the raw ID)
const formatHistoryStrategyName = (strategyId: string, strategies: ScreeningStrategy[]): string => {
  const matched = strategies.find((item) => item.id === strategyId);
  return matched?.name || matched?.title || strategyId || 'Unknown strategy';
};

// Filter label in a history entry: market ID → display label
const formatHistoryMarketLabel = (marketId: string | null | undefined): string =>
  MARKETS.find((item) => item.id === marketId)?.label || marketId || 'cn';

const readPersistedScreenTask = (): PersistedScreenTask | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(SCREEN_TASK_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<PersistedScreenTask>;
    if (typeof parsed.taskId !== 'string' || !parsed.taskId.trim()) {
      return null;
    }
    const restoredMaxResults = Number(parsed.maxResults);
    return {
      taskId: parsed.taskId,
      runId: typeof parsed.runId === 'string' && parsed.runId.trim() ? parsed.runId : undefined,
      market: typeof parsed.market === 'string' && parsed.market.trim() ? parsed.market : 'cn',
      strategy: typeof parsed.strategy === 'string' && parsed.strategy.trim() ? parsed.strategy : 'dual_low',
      maxResults: Number.isFinite(restoredMaxResults) ? Math.min(100, Math.max(1, restoredMaxResults)) : 3,
    };
  } catch {
    return null;
  }
};

const persistScreenTask = (task: PersistedScreenTask) => {
  try {
    window.sessionStorage.setItem(SCREEN_TASK_STORAGE_KEY, JSON.stringify(task));
  } catch {
    // Session storage is best-effort; polling still works while the page stays mounted.
  }
};

const clearPersistedScreenTask = () => {
  try {
    window.sessionStorage.removeItem(SCREEN_TASK_STORAGE_KEY);
  } catch {
    // Ignore storage cleanup failures.
  }
};

const isUnrecoverableScreenTaskError = (error: ParsedApiError) =>
  error.title === 'Screening task cannot be restored';

const formatRecoverableScreenTaskPollingError = (error: ParsedApiError) => {
  if (error.category === 'upstream_timeout') {
    return 'The screening task is still running in the background; status polling timed out and will retry automatically.';
  }
  if (error.category === 'upstream_network' || error.category === 'local_connection_failed') {
    return 'The screening task is still running in the background; the local service is temporarily unreachable and polling will retry automatically.';
  }
  return formatParsedApiError(error) || 'Screening task status is temporarily unavailable; it will retry automatically.';
};

const formatScore = (score: ScreeningCandidate['score']) => {
  if (score == null || Number.isNaN(Number(score))) {
    return '-';
  }
  return Number(score).toFixed(2);
};

const formatNumber = (value: unknown, digits = 2) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return Number(value).toFixed(digits);
};

const formatAmount = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  const amount = Number(value);
  if (Math.abs(amount) >= 100_000_000) {
    return `${(amount / 100_000_000).toFixed(2)}00M`;
  }
  if (Math.abs(amount) >= 10_000) {
    return `${(amount / 10_000).toFixed(2)}0K`;
  }
  return amount.toFixed(2);
};

const formatPercent = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return `${(Number(value) * 100).toFixed(0)}%`;
};

const FACTOR_LABELS: Record<string, string> = {
  value: 'Valuation',
  liquidity: 'Liquidity',
  momentum: 'Momentum',
  reversal: 'Reversal',
  activity: 'Activity',
  stability: 'Stability',
  size: 'Size',
  theme_heat: 'Theme heat',
  topic_alignment: 'Theme match',
};

const POST_TAG_LABELS: Record<string, string> = {
  value_quality: 'Value quality',
  controlled_reversal: 'Controlled reversal',
  momentum: 'Trend momentum',
  liquidity: 'Liquidity',
};

const HOTSPOT_QUALITY_LABELS: Record<string, string> = {
  available: 'Available',
  failed: 'Unavailable',
  partial: 'Partially available',
  stale: 'Cached',
};

const HOTSPOT_STAGE_LABELS: Record<string, string> = {
  accelerating: 'Accelerating',
  cooling: 'Cooling off',
  diverging: 'Diverging on volume',
  initial: 'First move',
  persistent_hot: 'Confirmed spread',
  warming: 'Confirmed spread',
  weakening: 'Cooling off',
};

const HOTSPOT_ROLE_LABELS: Record<string, string> = {
  core_leader: 'Core leader',
  follower: 'Follower',
  laggard: 'Laggard',
  leader: 'Core leader',
  secondary: 'Catch-up',
};

const HOTSPOT_MISSING_FIELD_LABELS: Record<string, string> = {
  canonical_topic: 'Canonical topic',
  hotspot_constituents: 'Constituent list',
  leader_stocks: 'Leading stocks',
  live_stocks: 'Live constituent quotes',
  route: 'Momentum path',
  source: 'Data source',
  stocks: 'Constituent list',
  timeline: 'Momentum timeline',
};

const getHotspotStageLabel = (value: unknown) => {
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  return HOTSPOT_STAGE_LABELS[text.toLowerCase()] || text;
};

const getHotspotRoleLabel = (value: unknown) => {
  const text = String(value || '').trim();
  if (!text) {
    return 'Constituent';
  }
  return HOTSPOT_ROLE_LABELS[text.toLowerCase()] || text;
};

const getHotspotQualityLabel = (value: unknown) => {
  const text = String(value || '').trim();
  return HOTSPOT_QUALITY_LABELS[text.toLowerCase()] || 'To be confirmed';
};

const getLocalFactorReason = (item: ScreeningCandidate) => {
  const factors = Object.entries(item.factorScores || {})
    .filter(([, value]) => typeof value === 'number')
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 3)
    .map(([key, value]) => `${FACTOR_LABELS[key] || key} ${Number(value).toFixed(0)}`);
  const tags = (item.postAnalysisTags || [])
    .slice(0, 2)
    .map((tag) => POST_TAG_LABELS[tag] || tag);
  if (factors.length > 0) {
    return `Key strengths: ${factors.join(', ')}${tags.length > 0 ? `; tags: ${tags.join(', ')}` : ''}`;
  }
  return '';
};

const getCandidateReason = (item: ScreeningCandidate) => {
  if (item.llmThesis || item.llmScore != null) {
    return item.reason || item.llmThesis || 'The LLM finished the relative ranking.';
  }
  const localReason = getLocalFactorReason(item);
  if (localReason) {
    return localReason;
  }
  if (item.reason) {
    return item.reason;
  }
  const summaries = item.postAnalysisSummaries || {};
  const summary = Object.values(summaries).find((value) => typeof value === 'string' && value.trim());
  if (typeof summary === 'string') {
    return summary;
  }
  return 'No summary available; check the factor and risk details.';
};

const getSignal = (item: ScreeningCandidate) => {
  const rawSignal = item.raw.action ?? item.raw.signal ?? item.raw.recommendation;
  return typeof rawSignal === 'string' && rawSignal.trim() ? rawSignal : 'Watch';
};

const getFactorEntries = (item: ScreeningCandidate) =>
  Object.entries(item.factorScores || {})
    .filter(([, value]) => typeof value === 'number')
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 6);

const toMessageList = (values: string[] | undefined) =>
  Array.isArray(values) ? values.map((value) => String(value).trim()).filter(Boolean) : [];

const KNOWN_SNAPSHOT_SOURCES = new Set(['tushare', 'sina', 'efinance', 'akshare_em', 'em_datacenter', 'baostock']);
const MAX_MESSAGE_DETAIL_LENGTH = 96;

const truncateMessageDetail = (value: string, maxLength = MAX_MESSAGE_DETAIL_LENGTH) => {
  const text = value.replace(/\s+/g, ' ').trim();
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
};

const summarizeScreeningDiagnostic = (detail: string) => {
  if (/no_json_found|invalid_response|coverage below threshold/i.test(detail)) {
    return 'The model returned no usable structured ranking';
  }
  if (/call_failed/i.test(detail)) {
    return 'The model call failed';
  }
  if (/trade_cal returned no open trading days/i.test(detail)) {
    return 'The trading calendar has no open session available';
  }
  if (/too many requests|rate limit|http\s*429/i.test(detail)) {
    return 'Too many requests';
  }
  if (/403 forbidden|forbidden|access denied/i.test(detail)) {
    return 'Access denied';
  }
  if (/timeout|timed out/i.test(detail)) {
    return 'Request timed out';
  }
  if (/RemoteDisconnected|Connection aborted|ProtocolError|ConnectionPool|Max retries exceeded|ProxyError|NameResolutionError/i.test(detail)) {
    return 'Network connection lost';
  }
  if (/missing .*api key|GEMINI_API_KEY|GOOGLE_API_KEY|gemini_api_key/i.test(detail)) {
    return 'No usable LLM API key';
  }
  if (/returned no data|empty/i.test(detail)) {
    return 'No usable data returned';
  }

  const withoutUrl = detail
    .replace(/https?:\/\/\S+/gi, 'URL')
    .replace(/\bwith url:\s*\S+/gi, 'with url: URL')
    .replace(/\burl:\s*\S+/gi, 'url: URL');
  return truncateMessageDetail(withoutUrl);
};

const parseSourceDiagnostic = (value: string) => {
  const match = value.match(/^([a-zA-Z0-9_-]+)\s*[:：]\s*(.+)$/);
  if (!match) {
    return null;
  }
  return {
    source: match[1],
    detail: match[2],
  };
};

const normalizeScreenMessageKey = (value: string) => {
  const formatted = formatScreenMessage(value);
  return formatted ? formatted.trim().toLowerCase() : value.trim().toLowerCase();
};

const formatEnrichmentSummary = (value: string) =>
  value
    .replace(/DSA行情\s*[:：]\s*/gi, 'Quote: ')
    .replace(/DSA新闻\s*[:：]\s*/gi, 'News: ')
    .replace(/DSA事件\s*[:：]\s*/gi, 'Event: ');

const formatScreenMessage = (value: string) => {
  if (/^DSA provider context applied \d+ of \d+ candidates/i.test(value)) {
    return '';
  }
  if (/^LLM ranking skipped:\s*no LLM config/i.test(value)) {
    return 'No smart-rerank model is configured; using deterministic factor ranking.';
  }
  if (/^LLM ranking failed/i.test(value)) {
    return `Smart rerank did not complete: ${summarizeScreeningDiagnostic(value)}; these results still use deterministic factor scoring.`;
  }
  if (/no_json_found|invalid_response|coverage below threshold|call_failed/i.test(value)) {
    return `Smart rerank did not complete: ${summarizeScreeningDiagnostic(value)}; these results still use deterministic factor scoring.`;
  }
  if (/^(?:LLM ranking prompt|LLM context) truncated:/i.test(value)) {
    return '';
  }
  if (/^(?:Remote post-analysis cap|Risk veto excluded|Snapshot hard-filter waterfall|Daily hard-filter waterfall|Daily hard-filter rejections|Candidate context collected rows=)/i.test(value)) {
    return '';
  }
  if (/^Daily K-line (?:enrichment attempted|sources|quality flags|source ordering|source health):?/i.test(value)) {
    return '';
  }
  if (/^Daily K-line enrichment row errors:/i.test(value)) {
    return 'Daily bars were missing for some candidates; results were generated from the available data.';
  }
  if (/^Daily K-line enrichment skipped:/i.test(value)) {
    return 'Optional daily-bar enrichment did not finish; results were generated from snapshot data.';
  }
  if (/^Candidate context row errors:/i.test(value)) {
    return 'Supporting data was missing for some candidates.';
  }
  if (/^Industry\/concepts enrichment:/i.test(value)) {
    return 'Some industry or theme information could not be filled in.';
  }
  if (/^DSA deep analysis failed for /i.test(value)) {
    return 'Deep analysis did not complete for some candidates.';
  }

  const snapshotFallback = value.match(/^Snapshot source fallback:\s*(.+)$/i);
  if (snapshotFallback) {
    const parsed = parseSourceDiagnostic(snapshotFallback[1]);
    if (parsed) {
      return `Data source degraded: ${parsed.source} (${summarizeScreeningDiagnostic(parsed.detail)})`;
    }
    return `Data source degraded: ${summarizeScreeningDiagnostic(snapshotFallback[1])}`;
  }

  const parsed = parseSourceDiagnostic(value);
  if (parsed && KNOWN_SNAPSHOT_SOURCES.has(parsed.source.toLowerCase())) {
    return `Data source degraded: ${parsed.source} (${summarizeScreeningDiagnostic(parsed.detail)})`;
  }
  return truncateMessageDetail(value);
};

const getScreenMessages = (meta: ScreeningScreenResponse | null) => {
  if (!meta) {
    return [];
  }
  const messages: string[] = [];
  const seen = new Set<string>();
  [...toMessageList(meta.warnings), ...toMessageList(meta.sourceErrors), ...toMessageList(meta.llmParseErrors)].forEach(
    (value) => {
      const key = normalizeScreenMessageKey(value);
      if (seen.has(key)) {
        return;
      }
      const message = formatScreenMessage(value);
      if (!message) {
        return;
      }
      seen.add(key);
      messages.push(message);
    },
  );
  return messages;
};

const isRunningScreenTask = (status: string | undefined | null) => status === 'pending' || status === 'processing';

const formatScreenTaskFailure = (value: string | null | undefined) => {
  const text = String(value || '').trim();
  if (!text) {
    return 'The screening task failed, please try again later.';
  }
  return `Screening task failed: ${summarizeScreeningDiagnostic(text)}`;
};

const SCREENING_HOTSPOT_NO_CACHE_HINT = 'No cached Screening hotspot snapshot. Click refresh to fetch live hotspots.';
const SCREENING_HOTSPOT_UNAVAILABLE_CODE = 'eastmoney_hotspot_unavailable';

const formatHotspotEmptyMessage = (result: ScreeningHotspotsResponse) => {
  const message = String(result.message || '').trim();
  const sourceErrors = result.sourceErrors || [];
  if (message && sourceErrors.includes(SCREENING_HOTSPOT_UNAVAILABLE_CODE)) {
    return message;
  }
  if (message === SCREENING_HOTSPOT_NO_CACHE_HINT) {
    return 'No hotspot cache yet';
  }
  const sourceError = sourceErrors[0];
  if (sourceError) {
    return `Hotspot themes returned no data: ${summarizeScreeningDiagnostic(sourceError)}`;
  }
  return 'Hotspot themes returned no data';
};

const ScreenAlertMessage: React.FC<{ messages: string[] }> = ({ messages }) => {
  if (messages.length <= 1) {
    return <span>{messages[0]}</span>;
  }
  return (
    <ul className="list-disc space-y-1 pl-4">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
};

const hasLlmInsight = (item: ScreeningCandidate) =>
  Boolean(
    item.llmThesis ||
      item.llmSector ||
      item.llmTheme ||
      item.llmConfidence != null ||
      item.llmWatchItems?.length ||
      item.llmCatalysts?.length,
  );

const getRiskClassName = (riskLevel: string | undefined) => {
  if (riskLevel === 'high') {
    return 'bg-danger/10 text-danger';
  }
  if (riskLevel === 'medium') {
    return 'bg-warning/10 text-warning';
  }
  if (riskLevel === 'low') {
    return 'bg-success/10 text-success';
  }
  return 'bg-surface text-secondary-text';
};

const getRiskLabel = (riskLevel: string | undefined) => {
  if (riskLevel === 'high') return 'High';
  if (riskLevel === 'medium') return 'Medium';
  if (riskLevel === 'low') return 'Low';
  return 'Not assessed';
};

const getRouteTimeLabel = (item: ScreeningHotspotDetail['route'][number]) => {
  const rawTime = item.publishedAt || item.date || item.time || '';
  if (!rawTime) {
    return 'Time to be confirmed';
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(rawTime)) {
    return rawTime;
  }
  const parsed = new Date(rawTime);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }
  return rawTime;
};

const formatHotspotRouteTitle = (value: string) => {
  const text = String(value || '').trim();
  const normalized = text.toLowerCase();
  if (normalized === 'current fermentation') {
    return 'Current momentum';
  }
  if (normalized === 'news catalyst') {
    return 'News catalyst';
  }
  return text || 'Hotspot change';
};

const formatHotspotRouteDescription = (value: string) => {
  const text = String(value || '').trim();
  if (!text) {
    return 'No further detail.';
  }
  const parts = text.split(/\s*;\s*/).map((part) => part.trim()).filter(Boolean);
  if (parts.some((part) => /\b(?:heat|stage|leaders?)\b/i.test(part))) {
    const localized = parts.map((part) => {
      const heat = part.match(/^(.*?)\s+heat\s+(-?\d+(?:\.\d+)?)$/i);
      if (heat) {
        return `${heat[1]}heat ${formatNumber(heat[2], 1)}`;
      }
      const stage = part.match(/^stage\s+(.+)$/i);
      if (stage) {
        return `stage ${getHotspotStageLabel(stage[1])}`;
      }
      const leaders = part.match(/^leaders?\s+(.+)$/i);
      if (leaders) {
        return `leaders ${leaders[1].split(/\s*,\s*/).filter(Boolean).join(', ')}`;
      }
      return part;
    });
    return `${localized.join(', ')}.`;
  }
  if (/Dsa[A-Z]|Provider\b|stock_board_|concept_constituents|leader_stocks|last_good_cache/i.test(text)) {
    return 'The hotspot data changed.';
  }
  return text;
};

const getHotspotMissingFieldLabels = (values: string[] | undefined) => {
  const labels = (values || []).map((value) => HOTSPOT_MISSING_FIELD_LABELS[String(value).trim().toLowerCase()] || 'Some details');
  return [...new Set(labels)];
};

const formatHotspotDiagnostic = (value: string) => {
  const text = String(value || '').trim();
  const timeoutSeconds = text.match(/timed out after\s*(\d+(?:\.\d+)?)s/i);
  if (timeoutSeconds) {
    return `Hotspot detail request timed out (${timeoutSeconds[1]}s)`;
  }
  if (/timeout|timed out/i.test(text)) {
    return 'Hotspot detail request timed out';
  }
  if (/RemoteDisconnected|Connection aborted|ProtocolError|ConnectionPool|Max retries exceeded|ProxyError|NameResolutionError/i.test(text)) {
    return 'The hotspot data source connection dropped';
  }
  if (/eastmoney_hotspot_unavailable|returned no data|no live hotspot rows|\bempty\b/i.test(text)) {
    return 'The hotspot data source returned no data';
  }
  if (/rate limit|too many requests|http\s*429/i.test(text)) {
    return 'Too many hotspot data requests';
  }
  if (/Dsa[A-Z]|Provider\b|stock_board_|concept_constituents|leader_stocks|last_good_cache|^[a-z0-9_.:-]+$/i.test(text)) {
    return 'Some hotspot data is temporarily unavailable';
  }
  if (/[\u0080-\uFFFF]/.test(text)) {
    return truncateMessageDetail(text);
  }
  return 'Some hotspot data is temporarily unavailable';
};

const getHotspotDiagnosticMessages = (values: string[] | undefined) =>
  [...new Set((values || []).map(formatHotspotDiagnostic).filter(Boolean))].slice(0, 4);

const hasHotspotDetailDegradation = (detail: ScreeningHotspotDetail) => {
  if ((detail.missingFields || []).length > 0) {
    return true;
  }
  const qualityStatus = String(detail.qualityStatus || '').trim().toLowerCase();
  if (qualityStatus) {
    return qualityStatus !== 'available';
  }
  return (detail.sourceErrors || []).length > 0;
};

const getHotspotFallbackLabel = (detail: ScreeningHotspotDetail) => {
  if (detail.stale || detail.cacheUsed) {
    return detail.staleAgeHours != null
      ? `Cache fallback ${formatNumber(detail.staleAgeHours, 1)}h`
      : 'Cache fallback';
  }
  return 'Fallback data source';
};

const getHotspotSummaryText = (detail: ScreeningHotspotDetail, hotspot?: ScreeningHotspot) => {
  const summaryDetail = detail.summaryDetail || {};
  const heatScore = summaryDetail.heatScore ?? summaryDetail.heat_score ?? hotspot?.heatScore;
  const stage = summaryDetail.stage ?? hotspot?.stage ?? hotspot?.state;
  const rawLeaders = summaryDetail.leaders ?? hotspot?.leaders;
  const leaders = Array.isArray(rawLeaders)
    ? rawLeaders.map((value) => String(value).trim()).filter(Boolean).slice(0, 3)
    : [];
  const parts: string[] = [];
  if (heatScore != null && !Number.isNaN(Number(heatScore))) {
    parts.push(`heat ${formatNumber(heatScore, 1)}`);
  }
  if (stage) {
    parts.push(`stage ${getHotspotStageLabel(stage)}`);
  }
  if (leaders.length > 0) {
    parts.push(`leaders ${leaders.join(', ')}`);
  }
  if (parts.length > 0) {
    return `${detail.name || detail.canonicalTopic || detail.topic}: ${parts.join(', ')}.`;
  }
  const summary = String(detail.summary || '').trim();
  if (summary && !/\b(?:heat|stage|leaders?|quality status|available|partial|stale|failed)\b|Dsa[A-Z]|Provider\b|stock_board_/i.test(summary)) {
    return summary;
  }
  return 'Hotspot detail loaded.';
};

const buildHotspotPreviewDetail = (hotspot: ScreeningHotspot): ScreeningHotspotDetail => {
  const leaders = (hotspot.leaders || []).map((value) => String(value).trim()).filter(Boolean);
  const stage = getHotspotStageLabel(hotspot.stage || hotspot.state);
  const descriptionParts = [`${hotspot.name || hotspot.topic} heat ${formatHotspotMetric(hotspot.heatScore)}`];
  if (stage) {
    descriptionParts.push(`stage ${stage}`);
  }
  if (leaders.length > 0) {
    descriptionParts.push(`leaders ${leaders.slice(0, 3).join(', ')}`);
  }
  const stocks = (hotspot.leaderStocks || []).slice(0, 10);
  return {
    enabled: true,
    provider: 'akshare',
    topic: hotspot.topic,
    name: hotspot.name || hotspot.topic,
    canonicalTopic: hotspot.topic,
    summaryDetail: {
      heatScore: hotspot.heatScore,
      stage: hotspot.stage || hotspot.state,
      leaders,
    },
    route: [{ title: 'Current momentum', description: `${descriptionParts.join(', ')}.` }],
    stocks,
    stockCount: hotspot.sampleStockCount ?? stocks.length,
    sourceErrors: hotspot.sourceErrors,
    qualityStatus: hotspot.qualityStatus,
    missingFields: hotspot.missingFields,
    fallbackUsed: hotspot.fallbackUsed,
    stale: hotspot.stale,
    staleAgeHours: hotspot.staleAgeHours,
    cacheUsed: hotspot.cacheUsed,
    cachedAt: hotspot.cachedAt,
  };
};

const stripHotspotSearchAugmentation = (detail: ScreeningHotspotDetail): ScreeningHotspotDetail => {
  const baseDetail: ScreeningHotspotDetail = {
    ...detail,
    route: (detail.route || []).filter((item) => !item.searchResult),
    ...(detail.timeline
      ? { timeline: detail.timeline.filter((item) => !item.searchResult) }
      : {}),
  };
  delete baseDetail.newsSearchRequested;
  delete baseDetail.newsSearchStatus;
  return baseDetail;
};

const stripHotspotSearchAugmentationByTopic = (
  details: Record<string, ScreeningHotspotDetail>,
) => Object.fromEntries(
  Object.entries(details).map(([topic, detail]) => [topic, stripHotspotSearchAugmentation(detail)]),
) as Record<string, ScreeningHotspotDetail>;

const getHotspotRouteItems = (detail: ScreeningHotspotDetail) => {
  const route = detail.route || [];
  if (route.length > 0) {
    return route;
  }
  return detail.timeline || [];
};

const formatHotspotMetric = (value: unknown, digits = 1) => {
  const formatted = formatNumber(value, digits);
  return formatted === '-' ? 'Watching' : formatted;
};

const getHotspotLeadersText = (item: ScreeningHotspot) => {
  const leaders = (item.leaders || []).map((value) => String(value).trim()).filter(Boolean);
  if (leaders.length > 0) {
    return leaders.slice(0, 2).join(', ');
  }
  return 'Watching';
};

const getHotspotSampleText = (item: ScreeningHotspot) => {
  if (item.sampleStockCount == null || Number.isNaN(Number(item.sampleStockCount))) {
    return 'Active names under watch';
  }
  return `covers ${item.sampleStockCount} stocks`;
};

const formatStockChangeText = (value: unknown) => {
  const formatted = formatNumber(value);
  return formatted === '-' ? 'No quote' : `${formatted}%`;
};

const formatHotspotUpdatedAt = (value: string | null) => {
  if (!value) {
    return 'Needs refresh';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
};

const getHotspotStrength = (item: ScreeningHotspot, index: number) => {
  const heat = Number(item.heatScore ?? 0);
  const changePct = Number(item.changePct ?? 0);
  if (index === 0 || heat >= 90 || changePct >= 8) {
    return { label: 'Strong leader', className: 'bg-red-500/10 text-red-500' };
  }
  if (heat >= 80 || changePct >= 5) {
    return { label: 'Strong', className: 'bg-blue-500/10 text-blue-500' };
  }
  return { label: 'Fairly strong', className: 'bg-cyan/10 text-cyan' };
};

const HOTSPOT_ICON_RULES: Array<{
  pattern: RegExp;
  icon: React.ComponentType<{ className?: string }>;
  className: string;
}> = [
  { pattern: /金|银|铜|铝|铅|锌|钼|钴|镍|贵金属|矿|有色/, icon: Pickaxe, className: 'bg-orange-500/10 text-orange-500' },
  { pattern: /黄金|珠宝/, icon: Gem, className: 'bg-amber-500/10 text-amber-500' },
  { pattern: /油|气|能源|煤/, icon: Droplet, className: 'bg-yellow-700/10 text-yellow-700' },
  { pattern: /金融|券商|银行|保险|资本/, icon: Landmark, className: 'bg-orange-500/10 text-orange-500' },
  { pattern: /航空|机场|航天|运输/, icon: Plane, className: 'bg-blue-500/10 text-blue-500' },
  { pattern: /林业|农业|种植/, icon: Trees, className: 'bg-emerald-500/10 text-emerald-500' },
  { pattern: /医疗|诊断|卫生|医药/, icon: Stethoscope, className: 'bg-teal-500/10 text-teal-500' },
  { pattern: /食品|餐饮|酒/, icon: Utensils, className: 'bg-violet-500/10 text-violet-500' },
  { pattern: /工业|制造|修理|机械|设备/, icon: Wrench, className: 'bg-blue-500/10 text-blue-500' },
  { pattern: /租赁|地产|建筑/, icon: Building2, className: 'bg-emerald-500/10 text-emerald-500' },
  { pattern: /电|芯片|算力|AI|机器人/, icon: Factory, className: 'bg-indigo-500/10 text-indigo-500' },
  { pattern: /保险|安全/, icon: Shield, className: 'bg-blue-500/10 text-blue-500' },
];

const getHotspotIcon = (topic: string) => {
  const match = HOTSPOT_ICON_RULES.find((rule) => rule.pattern.test(topic));
  return match || { icon: Activity, className: 'bg-cyan/10 text-cyan' };
};

const MiniSparkline: React.FC<{ score?: number | null; selected?: boolean }> = ({ score, selected }) => {
  const normalizedScore = Number.isFinite(Number(score)) ? Math.max(0, Math.min(100, Number(score))) : 65;
  const lift = Math.max(0, Math.min(16, normalizedScore / 7));
  const path = `M2 35 C12 ${32 - lift / 4}, 16 ${34 - lift / 2}, 24 ${28 - lift / 3} S38 ${29 - lift}, 46 ${23 - lift / 2} S62 ${24 - lift}, 72 ${16 - lift / 3} S86 ${15 - lift}, 94 ${7}`;
  return (
    <svg className="h-8 w-20" viewBox="0 0 96 40" aria-hidden="true">
      <path d={`${path} L94 40 L2 40 Z`} fill={selected ? 'rgba(249,115,22,0.14)' : 'rgba(59,130,246,0.12)'} />
      <path d={path} fill="none" stroke={selected ? '#f97316' : '#3b82f6'} strokeLinecap="round" strokeWidth="2" />
    </svg>
  );
};

const StockScreeningPage: React.FC = () => {
  const navigate = useNavigate();
  const [restoredTask] = useState<PersistedScreenTask | null>(() => readPersistedScreenTask());
  const [enabled, setEnabled] = useState(false);
  const [available, setAvailable] = useState(false);
  const [market, setMarket] = useState(restoredTask?.market || 'cn');
  const [strategy, setStrategy] = useState(restoredTask?.strategy || 'dual_low');
  const [strategies, setStrategies] = useState<ScreeningStrategy[]>([]);
  const [maxResults, setMaxResults] = useState(restoredTask?.maxResults || 3);
  const [candidates, setCandidates] = useState<ScreeningCandidate[]>([]);
  const [hotspots, setHotspots] = useState<ScreeningHotspot[]>([]);
  const [hotspotsUpdatedAt, setHotspotsUpdatedAt] = useState<string | null>(null);
  const [hotspotsExpanded, setHotspotsExpanded] = useState(false);
  const [selectedHotspotTopic, setSelectedHotspotTopic] = useState<string | null>(null);
  const selectedHotspotTopicRef = useRef<string | null>(null);
  const hotspotDetailRequestIdRef = useRef(0);
  const hotspotDetailsByTopicRef = useRef<Record<string, ScreeningHotspotDetail>>({});
  const historyRunRequestIdRef = useRef(0);
  const [hotspotDetail, setHotspotDetail] = useState<ScreeningHotspotDetail | null>(null);
  const [loadingHotspotDetail, setLoadingHotspotDetail] = useState(false);
  const [searchingHotspotNews, setSearchingHotspotNews] = useState(false);
  const [hotspotDetailError, setHotspotDetailError] = useState('');
  const [loadingHotspots, setLoadingHotspots] = useState(false);
  const [hotspotError, setHotspotError] = useState('');
  const [screenMeta, setScreenMeta] = useState<ScreeningScreenResponse | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(restoredTask?.taskId));
  const [enabling, setEnabling] = useState(false);
  const [historyRuns, setHistoryRuns] = useState<ScreeningRunSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [restoreResolved, setRestoreResolved] = useState(() => !restoredTask?.runId);
  // Marks whether the current strategy came from a history run (auto-restore on refresh, or a manual history pick).
  // When true, loadStrategies skips the "fall back to the first item if absent" normalization,
  // so a late /strategies response cannot rewrite the restored context back to the default strategy.
  const historyContextStrategyRef = useRef(false);
  const [loadingStrategies, setLoadingStrategies] = useState(false);
  const [error, setError] = useState('');
  const [strategyLoadError, setStrategyLoadError] = useState('');
  const [activeTaskId, setActiveTaskId] = useState<string | null>(restoredTask?.taskId ?? null);
  const [taskProgress, setTaskProgress] = useState(restoredTask?.taskId ? 10 : 0);
  const [taskMessage, setTaskMessage] = useState(restoredTask?.taskId ? 'Restoring screening task status...' : '');

  const selectedStrategy = useMemo(() => strategies.find((item) => item.id === strategy), [strategies, strategy]);
  const selectedStrategyTitle = selectedStrategy?.name || selectedStrategy?.title || 'Custom strategy';
  const selectedStrategyTag = formatStrategyCategory(
    selectedStrategy?.category || selectedStrategy?.tag || selectedStrategy?.tags?.[0],
  );
  const displayedStrategy = selectedStrategy ? selectedStrategyTitle : `Custom strategy (${strategy})`;
  const screenMessages = useMemo(() => getScreenMessages(screenMeta), [screenMeta]);
  const selectedHotspot = useMemo(
    () => hotspots.find((item) => item.topic === selectedHotspotTopic),
    [hotspots, selectedHotspotTopic],
  );
  const factorRanking = Boolean(screenMeta && (screenMeta.rankingMode === 'factor' || screenMeta.llmRanked === false));
  const llmFailed = Boolean(factorRanking && screenMeta?.llmFailureReason);
  const alertMessages = llmFailed
    ? screenMessages.length > 0
      ? screenMessages
      : ['Smart rerank did not complete; these candidates still use deterministic factor scoring.']
    : screenMessages;
  const isScreeningEnabled = enabled && available;
  const statusText = isScreeningEnabled ? 'Screening enabled' : 'Screening disabled';

  const applyScreenResult = useCallback((result: ScreeningScreenResponse) => {
    const nextCandidates = result.candidates || [];
    setScreenMeta(result);
    setCandidates(nextCandidates);
    setExpandedCode(nextCandidates[0]?.code ?? null);
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const result = await screeningApi.getHistory({ limit: 10 });
      setHistoryRuns(result.runs || []);
    } catch (err) {
      setHistoryError(toApiErrorMessage(err, 'Failed to load history'));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const handleHistoryRunSelect = useCallback(async (runId: string) => {
    // Race guard: when switching history entries quickly, only apply the newest response
    const requestId = historyRunRequestIdRef.current + 1;
    historyRunRequestIdRef.current = requestId;
    const isCurrentRequest = () => historyRunRequestIdRef.current === requestId;
    // Mutually exclusive with a running screening task: after a manual history pick, pause/cancel background polling
    // so a finishing task cannot write its candidates back over the history context.
    setActiveTaskId(null);
    setHistoryError('');
    setLoading(true);
    try {
      const detail = await screeningApi.getRun(runId);
      if (!isCurrentRequest()) {
        return;
      }
      if (detail?.result) {
        applyScreenResult(detail.result);
        historyContextStrategyRef.current = true;
        // Sync the persisted restore pointer: after a refresh we restore the history run the user just picked,
        // not an older task/run. History details carry no taskId, so runId is used
        // as a placeholder — the normal path restores through getRun(runId) and never polls;
        // if that run fails to restore, the placeholder poll hits an unrecoverable error and clears the stale pointer.
        persistScreenTask({
          taskId: runId,
          runId,
          market: detail.market || market,
          strategy: detail.strategy || strategy,
          maxResults,
        });
        // Sync the strategy and market context of the history run so the result copy and later deep analysis
        // use that run strategy/market rather than the current form selection
        if (detail.strategy) {
          setStrategy(detail.strategy);
        }
        if (detail.market) {
          setMarket(detail.market);
        }
        setError('');
        setTaskProgress(100);
        setTaskMessage('Loaded the historical screening result');
      } else {
        setError('No result for that run was found in the history.');
      }
    } catch (err) {
      if (!isCurrentRequest()) {
        return;
      }
      setError(toApiErrorMessage(err, 'Failed to load the historical result'));
    } finally {
      if (isCurrentRequest()) {
        setLoading(false);
      }
    }
  }, [applyScreenResult, market, maxResults, strategy]);

  const clearScreeningResults = () => {
    setCandidates([]);
    setScreenMeta(null);
    setExpandedCode(null);
  };

  const loadHotspotDetail = useCallback(async (
    topic: string,
    options: { refresh?: boolean; includeSearch?: boolean } = {},
  ) => {
    if (!topic) {
      return;
    }
    const cachedDetail = !options.refresh && !options.includeSearch
      ? hotspotDetailsByTopicRef.current[topic]
      : null;
    if (cachedDetail) {
      setHotspotDetail(cachedDetail);
      setHotspotDetailError('');
      setLoadingHotspotDetail(false);
      return;
    }
    const requestId = hotspotDetailRequestIdRef.current + 1;
    hotspotDetailRequestIdRef.current = requestId;
    const isCurrentRequest = () => hotspotDetailRequestIdRef.current === requestId;
    const canApplyRequest = () => isCurrentRequest() && selectedHotspotTopicRef.current === topic;
    setLoadingHotspotDetail(!options.includeSearch);
    setSearchingHotspotNews(Boolean(options.includeSearch));
    setHotspotDetail((currentDetail) => (currentDetail?.topic === topic ? currentDetail : null));
    setHotspotDetailError('');
    try {
      const detail = await screeningApi.getHotspotDetail({
        topic,
        provider: 'akshare',
        refresh: options.refresh ?? false,
        ...(options.includeSearch ? { includeSearch: true } : {}),
      });
      if (!canApplyRequest()) {
        return;
      }
      const cacheableDetail = options.includeSearch
        ? hotspotDetailsByTopicRef.current[topic] || stripHotspotSearchAugmentation(detail)
        : stripHotspotSearchAugmentation(detail);
      hotspotDetailsByTopicRef.current = {
        ...hotspotDetailsByTopicRef.current,
        [topic]: cacheableDetail,
      };
      setHotspotDetail(options.includeSearch ? detail : cacheableDetail);
      if (options.includeSearch && detail.newsSearchStatus === 'no_results') {
        setHotspotDetailError('No recent usable news was found for this theme.');
      } else if (options.includeSearch && detail.newsSearchStatus !== 'available') {
        setHotspotDetailError('News search failed, please try again later.');
      }
    } catch (err) {
      if (!canApplyRequest()) {
        return;
      }
      if (!options.includeSearch) {
        setHotspotDetail(null);
      }
      setHotspotDetailError(toApiErrorMessage(
        err,
        options.includeSearch ? 'News search failed, please try again later.' : 'Failed to load the hotspot theme detail, please try again later.',
      ));
    } finally {
      if (isCurrentRequest()) {
        setLoadingHotspotDetail(false);
        setSearchingHotspotNews(false);
      }
    }
  }, []);

  const loadStrategies = useCallback(async () => {
    setLoadingStrategies(true);
    try {
      setStrategyLoadError('');
      const result = await screeningApi.getStrategies();
      const loadedStrategies = result.strategies || [];
      setStrategies(loadedStrategies);
      // A custom/retired strategy from a history run being absent from the list is expected; skip the "fall back to the first item" normalization,
      // otherwise a late strategy list would rewrite the just-restored context back to the default strategy.
      if (loadedStrategies.length > 0 && !historyContextStrategyRef.current) {
        setStrategy((currentStrategy) =>
          loadedStrategies.some((item) => item.id === currentStrategy) ? currentStrategy : loadedStrategies[0].id,
        );
      }
    } catch (err) {
      setStrategies([]);
      setStrategyLoadError(err instanceof Error ? err.message : 'Failed to load the strategy list');
    } finally {
      setLoadingStrategies(false);
    }
  }, []);

  const loadHotspots = useCallback(async (refresh = false) => {
    setLoadingHotspots(true);
    setHotspotError('');
    try {
      const result = await screeningApi.getHotspots({ provider: 'akshare', top: 12, refresh });
      const nextHotspots = result.hotspots || [];
      const nextDetails = stripHotspotSearchAugmentationByTopic(result.details || {});
      hotspotDetailsByTopicRef.current = {
        ...hotspotDetailsByTopicRef.current,
        ...nextDetails,
      };
      const currentTopic = selectedHotspotTopicRef.current;
      const retainedTopic = Boolean(currentTopic && nextHotspots.some((item) => item.topic === currentTopic));
      const nextTopic = retainedTopic ? currentTopic : null;
      setHotspots(nextHotspots);
      setHotspotsUpdatedAt(result.cachedAt || (nextHotspots.length > 0 ? new Date().toISOString() : null));
      setSelectedHotspotTopic(nextTopic);
      selectedHotspotTopicRef.current = nextTopic;
      setHotspotDetailError('');
      if (nextTopic && nextDetails[nextTopic]) {
        setHotspotDetail(nextDetails[nextTopic]);
        setLoadingHotspotDetail(false);
      } else if (!retainedTopic) {
        setHotspotDetail(null);
      } else if (refresh && nextTopic) {
        // A refreshed list and a retained detail must describe the same source
        // snapshot. The list endpoint intentionally omits details by default,
        // so explicitly bypass the detail cache for the retained topic.
        await loadHotspotDetail(nextTopic, { refresh: true });
      }
      if (nextHotspots.length === 0) {
        setHotspotError(formatHotspotEmptyMessage(result));
      }
    } catch (err) {
      setHotspotError(toApiErrorMessage(err, 'Failed to load hotspot themes, please try again later.'));
    } finally {
      setLoadingHotspots(false);
    }
  }, [loadHotspotDetail]);

  const handleHotspotSelect = useCallback((topic: string) => {
    selectedHotspotTopicRef.current = topic;
    setSelectedHotspotTopic(topic);
    const cachedDetail = hotspotDetailsByTopicRef.current[topic];
    if (cachedDetail) {
      setHotspotDetail(cachedDetail);
      setHotspotDetailError('');
      setLoadingHotspotDetail(false);
    } else {
      const preview = hotspots.find((item) => item.topic === topic);
      setHotspotDetail((currentDetail) => (
        currentDetail?.topic === topic ? currentDetail : preview ? buildHotspotPreviewDetail(preview) : null
      ));
    }
  }, [hotspots]);

  const toggleHotspotsExpanded = useCallback(() => {
    setHotspotsExpanded((expanded) => {
      const nextExpanded = !expanded;
      if (!nextExpanded) {
        selectedHotspotTopicRef.current = null;
        setSelectedHotspotTopic(null);
        setHotspotDetail(null);
        setHotspotDetailError('');
      }
      return nextExpanded;
    });
  }, []);

  const handleAnalyzeHotspotStock = useCallback((stock: ScreeningHotspotDetail['stocks'][number]) => {
    const stockCode = String(stock.code || '').trim();
    if (!stockCode) {
      return;
    }
    const stockName = String(stock.name || stockCode).trim();
    navigate('/', {
      state: {
        stockCode,
        stockName,
        autoAnalyze: true,
        selectionSource: 'screening_hotspot',
        skills: ['hot_theme'],
      },
    });
  }, [navigate]);

  const handleAnalyzeCandidate = useCallback((candidate: ScreeningCandidate) => {
    const stockCode = String(candidate.code || '').trim();
    if (!stockCode) {
      return;
    }
    const stockName = String(candidate.name || stockCode).trim();
    const analysisSkills = (selectedStrategy?.analysisSkills || []).filter(Boolean);
    navigate('/', {
      state: {
        stockCode,
        stockName,
        autoAnalyze: true,
        selectionSource: 'screening_result',
        ...(analysisSkills.length > 0 ? { skills: analysisSkills } : {}),
      },
    });
  }, [navigate, selectedStrategy]);

  useEffect(() => {
    selectedHotspotTopicRef.current = selectedHotspotTopic;
  }, [selectedHotspotTopic]);

  useEffect(() => {
    if (!selectedHotspotTopic) {
      return;
    }
    void loadHotspotDetail(selectedHotspotTopic);
  }, [loadHotspotDetail, selectedHotspotTopic]);

  useEffect(() => {
    let active = true;
    screeningApi
      .getStatus()
      .then((status) => {
        if (!active) {
          return;
        }
        setEnabled(status.enabled);
        setAvailable(status.available);
        if (status.enabled && status.available) {
          void loadStrategies();
          void loadHotspots(false);
          void loadHistory();
        }
      })
      .catch(() => {
        if (active) {
          setEnabled(false);
          setAvailable(false);
        }
      });
    return () => {
      active = false;
    };
  }, [loadHotspots, loadStrategies]);

  // After a refresh, restore results from the history API by run_id first; fall back to task polling only if that fails
  useEffect(() => {
    const runId = restoredTask?.runId;
    if (!runId) {
      setRestoreResolved(true);
      return undefined;
    }
    let active = true;
    setLoading(true);
    // Record the baseline for the auto-restore request: if the user opens a history entry manually before it returns
    // (handleHistoryRunSelect increments historyRunRequestIdRef), drop this auto-restore response
    // so a late auto-restore cannot switch the page back to the old run and override the user's latest pick.
    const restoreRequestBase = historyRunRequestIdRef.current;
    screeningApi
      .getRun(runId)
      .then((detail) => {
        if (!active || historyRunRequestIdRef.current !== restoreRequestBase) {
          return;
        }
        if (detail?.result) {
          applyScreenResult(detail.result);
          historyContextStrategyRef.current = true;
          // Restore the strategy and market context of that history run so the results view and
          // deep analysis do not reuse the current form strategy (same as handleHistoryRunSelect)
          if (detail.strategy) {
            setStrategy(detail.strategy);
          }
          if (detail.market) {
            setMarket(detail.market);
          }
          setError('');
          setTaskProgress(100);
          setTaskMessage('Restored the previous screening result from history');
          setActiveTaskId(null);
        }
      })
      .catch(() => {
        // History restore failed (the run is gone or the service restarted): fall back to task polling;
        // if the user already picked a history entry manually, keep their choice instead.
        if (active && historyRunRequestIdRef.current === restoreRequestBase) {
          setActiveTaskId(restoredTask?.taskId ?? null);
        }
      })
      .finally(() => {
        // Always release the auto-restore latch, stale or not, otherwise polling for a new task stays blocked until the old request times out.
        if (active) {
          setRestoreResolved(true);
        }
        // A stale auto-restore must not touch the shared loading flag: a manually requested history detail may still be in flight,
        // and clearing it early would re-enable "Run screening" before a late history response overwrites the new task state.
        if (active && historyRunRequestIdRef.current === restoreRequestBase) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [applyScreenResult, restoredTask]);

  useEffect(() => {
    if (!activeTaskId || !restoreResolved) {
      return undefined;
    }

    const pollingTaskId = activeTaskId;
    let active = true;
    let timer: ReturnType<typeof window.setTimeout> | undefined;

    function finishTask() {
      setActiveTaskId(null);
      setLoading(false);
    }

    function applyTaskStatus(task: ScreeningScreenTaskStatus) {
      const nextProgress = Number(task.progress ?? 0);
      setTaskProgress(Number.isFinite(nextProgress) ? nextProgress : 0);
      setTaskMessage(task.message || '');

      if (task.status === 'completed') {
        if (task.result) {
          applyScreenResult(task.result);
          setError('');
          // Persist runId: after a refresh, restore from the history API rather than relying on the in-memory task
          const completedRunId = task.result.runId || screenMeta?.runId;
          if (completedRunId) {
            persistScreenTask({
              taskId: pollingTaskId,
              runId: completedRunId,
              market,
              strategy,
              maxResults,
            });
          }
        } else {
          setError('The screening task finished, but the server returned no candidates.');
          setCandidates([]);
          setScreenMeta(null);
        }
        finishTask();
        return;
      }

      if (task.status === 'failed') {
        setCandidates([]);
        setScreenMeta(null);
        setExpandedCode(null);
        setError(formatScreenTaskFailure(task.error || task.message));
        clearPersistedScreenTask();
        finishTask();
        return;
      }

      if (isRunningScreenTask(task.status)) {
        setLoading(true);
        timer = window.setTimeout(pollTask, SCREEN_TASK_POLL_INTERVAL_MS);
        return;
      }

      setError(`The screening task returned an unknown status: ${task.status || 'unknown'}`);
      clearPersistedScreenTask();
      finishTask();
    }

    async function pollTask() {
      try {
        const task = await screeningApi.getScreenTask(pollingTaskId);
        if (!active) {
          return;
        }
        applyTaskStatus(task);
      } catch (err) {
        if (!active) {
          return;
        }
        const parsedError = getParsedApiError(err);
        if (isUnrecoverableScreenTaskError(parsedError)) {
          setError(formatParsedApiError(parsedError) || 'The screening task cannot be restored; please submit it again.');
          setCandidates([]);
          setScreenMeta(null);
          clearPersistedScreenTask();
          finishTask();
          return;
        }
        setError(formatRecoverableScreenTaskPollingError(parsedError));
        setLoading(true);
        timer = window.setTimeout(pollTask, SCREEN_TASK_POLL_INTERVAL_MS);
      }
    }

    void pollTask();

    return () => {
      active = false;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [activeTaskId, applyScreenResult, restoreResolved]);

  const handleEnable = async () => {
    setEnabling(true);
    setError('');
    try {
      await screeningApi.enable();
      setEnabled(true);
      setAvailable(true);
      await loadStrategies();
    } catch (err) {
      try {
        const status = await screeningApi.getStatus();
        setEnabled(status.enabled);
        setAvailable(status.available);
      } catch {
        setEnabled(false);
        setAvailable(false);
      }
      setError(err instanceof Error ? err.message : 'Failed to enable screening');
    } finally {
      setEnabling(false);
    }
  };

  const handleStrategyChange = (nextStrategy: string) => {
    if (nextStrategy !== strategy) {
      clearScreeningResults();
    }
    setStrategy(nextStrategy);
  };

  const handleMarketChange = (nextMarket: string) => {
    if (nextMarket !== market) {
      clearScreeningResults();
    }
    setMarket(nextMarket);
  };

  const handleMaxResultsChange = (nextMaxResults: number) => {
    if (nextMaxResults !== maxResults) {
      clearScreeningResults();
    }
    setMaxResults(nextMaxResults);
  };

  const handleSubmit = async () => {
    // Submitting a new task means the user abandons the current history/restore context:
    // bump the request token to invalidate in-flight history detail and auto-restore responses,
    // and release the auto-restore latch so polling for the new task can start immediately.
    historyRunRequestIdRef.current += 1;
    setRestoreResolved(true);
    setLoading(true);
    setError('');
    setScreenMeta(null);
    setTaskProgress(0);
    setTaskMessage('Submitting the screening task...');
    try {
      const task = await screeningApi.startScreen({ market, strategy, maxResults });
      persistScreenTask({
        taskId: task.taskId,
        market,
        strategy,
        maxResults,
      });
      setActiveTaskId(task.taskId);
      setTaskProgress(0);
      setTaskMessage(task.message || 'Screening task submitted');
    } catch (err) {
      setCandidates([]);
      setLoading(false);
      setError(toApiErrorMessage(err, 'Failed to submit the screening task, please try again later.'));
    }
  };

  return (
    <AppPage className="max-w-6xl space-y-6 pb-12 pt-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="grid h-7 w-7 place-items-center rounded-full border-2 border-cyan text-cyan shadow-[0_0_24px_hsl(var(--primary)/0.18)]">
            <PlusCircle className="h-4 w-4" />
          </span>
          <h1 className="text-2xl font-bold tracking-normal text-foreground">Screening</h1>
        </div>

        <div className="inline-flex w-fit items-center gap-2 rounded-2xl border border-border/70 bg-card/80 px-4 py-2 text-sm shadow-soft-card">
          <span className={`h-2.5 w-2.5 rounded-full ${isScreeningEnabled ? 'bg-success' : 'bg-warning'}`} />
          <span className="font-medium text-secondary-text">{statusText}</span>
        </div>
      </div>

      {!enabled ? (
        <InlineAlert
          variant="info"
          title="Screening is off"
          message="Turn it on to run screening strategies."
          action={
            <Button size="sm" isLoading={enabling} loadingText="Enabling..." onClick={() => void handleEnable()}>
              Enable screening
            </Button>
          }
        />
      ) : null}

      {enabled && !available ? (
        <InlineAlert
          variant="warning"
          title="Screening is unavailable"
          message="Check the backend logs, strategy files and base data dependencies, then restart the service."
        />
      ) : null}

      {error ? <InlineAlert variant="danger" title="Request failed" message={error} /> : null}

      <section className="rounded-2xl border border-border/80 bg-card/95 p-4 shadow-soft-card">
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-orange-500/10 text-orange-500 shadow-[0_10px_30px_rgba(249,115,22,0.16)]">
              <Flame className="h-5 w-5" />
            </span>
            <h2 className="text-lg font-bold tracking-normal text-foreground">Hotspot themes</h2>
          </div>
          <div className="flex flex-col items-start gap-2 lg:items-end">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={!isScreeningEnabled}
                onClick={toggleHotspotsExpanded}
              >
                <Bookmark className="h-4 w-4" />
                {hotspotsExpanded ? 'Collapse hotspot themes' : `Expand hotspot themes${hotspots.length ? ` (${hotspots.length})` : ''}`}
                <ChevronDown className={`h-4 w-4 transition-transform ${hotspotsExpanded ? 'rotate-180' : ''}`} />
              </Button>
              {hotspotsExpanded ? (
              <Button
                size="sm"
                variant="secondary"
                isLoading={loadingHotspots}
                loadingText="Refreshing..."
                disabled={!isScreeningEnabled || loadingHotspots}
                onClick={() => void loadHotspots(true)}
              >
                <RefreshCw className="h-4 w-4" />
                Refresh hotspot themes
              </Button>
              ) : null}
            </div>
            {hotspotsUpdatedAt ? (
              <p className="text-xs text-secondary-text">Updated {formatHotspotUpdatedAt(hotspotsUpdatedAt)}</p>
            ) : null}
          </div>
        </div>

        {hotspotsExpanded && hotspotError ? (
          <p className="mb-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {hotspotError}
          </p>
        ) : null}

        {!hotspotsExpanded ? null : hotspots.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface/70 px-4 py-6 text-sm text-secondary-text">
            No hotspot data yet — click "Refresh hotspot themes" to fetch it.
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {hotspots.map((item, index) => {
              const selected = selectedHotspotTopic === item.topic;
              const strength = getHotspotStrength(item, index);
              const iconMeta = getHotspotIcon(item.name || item.topic);
              const Icon = iconMeta.icon;
              return (
              <button
                key={`${item.topic}-${item.rank ?? ''}`}
                className={`group relative min-h-[116px] overflow-hidden rounded-xl border px-3 py-3 text-left transition-all ${
                  selected
                    ? 'border-orange-400 bg-gradient-to-br from-orange-500/10 via-card to-card shadow-[0_0_0_1px_rgba(249,115,22,0.16),0_18px_44px_rgba(249,115,22,0.14)]'
                    : 'border-border/80 bg-card hover:-translate-y-0.5 hover:border-orange-300/70 hover:shadow-soft-card'
                }`}
                type="button"
                onClick={() => handleHotspotSelect(item.topic)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <span
                      className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-bold ${
                        index < 3 ? 'bg-orange-500 text-white shadow-[0_8px_24px_rgba(249,115,22,0.24)]' : 'bg-surface text-secondary-text'
                      }`}
                    >
                      {index + 1}
                    </span>
                    <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${iconMeta.className}`}>
                      <Icon className="h-5 w-5" />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-bold text-foreground">{item.name || item.topic}</p>
                      <span className={`mt-1 inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${strength.className}`}>
                        {strength.label}
                      </span>
                    </div>
                  </div>
                  <span className="shrink-0 text-2xl font-black leading-none text-orange-500">
                    {formatNumber(item.heatScore, 0)}
                  </span>
                </div>
                <div className="mt-4 grid max-w-[72%] gap-1 text-[11px] text-secondary-text">
                  <span>Change <strong className="font-semibold text-foreground">{formatHotspotMetric(item.changePct)}%</strong></span>
                  <span>Trend <strong className="font-semibold text-foreground">{formatHotspotMetric(item.trendScore)}</strong> · persistence <strong className="font-semibold text-foreground">{formatHotspotMetric(item.persistenceScore)}</strong></span>
                  <span>{getHotspotSampleText(item)} · leaders {getHotspotLeadersText(item)}</span>
                </div>
                <div className="absolute bottom-3 right-3 opacity-95 transition-transform group-hover:scale-105">
                  <MiniSparkline score={item.heatScore} selected={selected} />
                </div>
              </button>
              );
            })}
          </div>
        )}

        {hotspotsExpanded && selectedHotspotTopic ? (
          <div className="mt-4 rounded-xl border border-border/80 bg-surface/80 p-4">
            <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-foreground">
                  {hotspotDetail?.name || selectedHotspotTopic}
                </h3>
                <p className="mt-1 text-xs leading-5 text-secondary-text">
                  {hotspotDetail
                    ? getHotspotSummaryText(hotspotDetail, selectedHotspot)
                    : loadingHotspotDetail
                      ? 'Loading the momentum path and constituents...'
                      : 'Click a theme to see its momentum path and constituents.'}
                </p>
                {hotspotDetail?.canonicalTopic && hotspotDetail.canonicalTopic !== selectedHotspotTopic ? (
                  <p className="mt-1 text-[11px] text-secondary-text">Canonical topic: {hotspotDetail.canonicalTopic}</p>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  isLoading={searchingHotspotNews}
                  loadingText="Searching..."
                  disabled={loadingHotspotDetail || searchingHotspotNews}
                  onClick={() => void loadHotspotDetail(selectedHotspotTopic, { includeSearch: true })}
                >
                  <Search className="h-3.5 w-3.5" />
                  Search latest news
                </Button>
                {loadingHotspotDetail ? (
                  <span className="w-fit rounded-full bg-cyan/10 px-3 py-1 text-xs font-semibold text-cyan">
                    Filling in details
                  </span>
                ) : null}
                {hotspotDetail?.qualityStatus ? (
                  <span className="w-fit rounded-full bg-warning/10 px-3 py-1 text-xs font-semibold text-warning">
                    Quality {getHotspotQualityLabel(hotspotDetail.qualityStatus)}
                  </span>
                ) : null}
                {hotspotDetail?.fallbackUsed || hotspotDetail?.stale ? (
                  <span className="w-fit rounded-full bg-warning/10 px-3 py-1 text-xs font-semibold text-warning">
                    {getHotspotFallbackLabel(hotspotDetail)}
                  </span>
                ) : null}
                {hotspotDetail?.stockCount != null ? (
                  <span className="w-fit rounded-full bg-orange-500/10 px-3 py-1 text-xs font-semibold text-orange-500">
                    Constituents {hotspotDetail.stockCount}
                  </span>
                ) : null}
              </div>
            </div>

            {hotspotDetailError ? (
              <p className="mb-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                {hotspotDetailError}
              </p>
            ) : null}

            {hotspotDetail && hasHotspotDetailDegradation(hotspotDetail) ? (
              <details className="mb-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                <summary className="cursor-pointer font-semibold">Detail data is degraded — expand to see why</summary>
                <div className="mt-2 space-y-1 leading-5">
                  {(hotspotDetail.missingFields || []).length > 0 ? (
                    <p>Missing: {getHotspotMissingFieldLabels(hotspotDetail.missingFields).join(', ')}</p>
                  ) : null}
                  {getHotspotDiagnosticMessages(hotspotDetail.sourceErrors).map((message, index) => (
                    <p key={`${message}-${index}`}>{message}</p>
                  ))}
                </div>
              </details>
            ) : null}

            {hotspotDetail ? (
              <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
                <div>
                  <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-secondary-text">
                    <Clock3 className="h-3.5 w-3.5 text-orange-500" />
                    Momentum timeline
                  </p>
                  <div className="relative space-y-0 pl-4 before:absolute before:bottom-3 before:left-[5px] before:top-2 before:w-px before:bg-border">
                    {getHotspotRouteItems(hotspotDetail).map((item, index) => (
                      <div key={`${item.title}-${index}`} className="relative pb-4 last:pb-0">
                        <span className="absolute -left-4 top-1 h-2.5 w-2.5 rounded-full border border-orange-400 bg-card" />
                        <div className="rounded-lg border border-border/70 bg-card/80 p-3">
                          <p className="text-[11px] font-semibold text-orange-500">{getRouteTimeLabel(item)}</p>
                          <p className="mt-1 text-xs font-semibold text-foreground">{formatHotspotRouteTitle(item.title)}</p>
                          <p className="mt-1 text-xs leading-5 text-secondary-text">{formatHotspotRouteDescription(item.description)}</p>
                          {item.url ? (
                            <a
                              className="mt-2 inline-flex text-[11px] font-semibold text-cyan hover:text-foreground"
                              href={item.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              View news
                            </a>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-xs font-semibold text-secondary-text">Constituents</p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {(hotspotDetail.stocks || []).slice(0, 10).map((stock) => (
                      <div key={`${stock.code || stock.name}`} className="rounded-lg border border-border/70 bg-card/80 p-3">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-xs font-semibold text-foreground">{stock.name || stock.code || '-'}</p>
                            <p className="mt-1 text-[11px] text-secondary-text">{stock.code || '-'}</p>
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            <span className="rounded-full bg-cyan/10 px-2 py-1 text-[11px] font-semibold text-cyan">
                              {getHotspotRoleLabel(stock.role)}
                            </span>
                            {stock.code ? (
                              <button
                                type="button"
                                aria-label={`Analyze ${stock.name || stock.code}`}
                                className="inline-flex h-7 items-center gap-1 rounded-full border border-cyan/30 bg-cyan/10 px-2 text-[11px] font-semibold text-cyan transition-colors hover:border-cyan hover:bg-cyan/15 hover:text-foreground"
                                onClick={() => handleAnalyzeHotspotStock(stock)}
                              >
                                <Play className="h-3 w-3" />
                                Analyze
                              </button>
                            ) : null}
                          </div>
                        </div>
                        <p className="mt-2 text-[11px] text-secondary-text">
                          Change {formatStockChangeText(stock.changePct)} · heat {formatNumber(stock.hotStockScore, 0)}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="rounded-2xl border border-cyan/35 bg-card/95 p-4 shadow-soft-card">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <SlidersHorizontal className="h-4 w-4 text-cyan" />
            Run screening
          </div>
          <span className="rounded-full border border-cyan/30 bg-cyan/10 px-3 py-1 text-xs font-semibold text-cyan">
            {selectedStrategyTag}
          </span>
        </div>

        <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr_180px_auto] lg:items-end">
          <label className="space-y-2 text-xs font-medium text-secondary-text">
            Market
            <select
              className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
              value={market}
              disabled={loading}
              onChange={(event) => handleMarketChange(event.target.value)}
            >
              {MARKETS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <div className="space-y-2 text-xs font-medium text-secondary-text">
            <label htmlFor="screening-strategy">Strategy</label>
            <Select
              id="screening-strategy"
              value={selectedStrategy ? strategy : CUSTOM_STRATEGY_OPTION_VALUE}
              disabled={loading || loadingStrategies}
              placeholder=""
              options={[
                ...strategies.map((item) => ({
                  value: item.id,
                  label: item.name || item.title || item.id,
                })),
                { value: CUSTOM_STRATEGY_OPTION_VALUE, label: 'Custom strategy…' },
              ]}
              onChange={(value) =>
                handleStrategyChange(
                  value === CUSTOM_STRATEGY_OPTION_VALUE ? '' : value,
                )
              }
            />
          </div>

          {!selectedStrategy && !loadingStrategies ? (
            <label className="space-y-2 text-xs font-medium text-secondary-text lg:col-start-2">
              Custom strategy ID
              <input
                aria-label="Custom strategy ID"
                className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
                value={strategy}
                disabled={loading}
                placeholder="Enter a strategy ID"
                onChange={(event) => handleStrategyChange(event.target.value)}
              />
            </label>
          ) : null}

          <label className="space-y-2 text-xs font-medium text-secondary-text">
            Result count
            <input
              className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
              type="number"
              min={1}
              max={100}
              value={maxResults}
              disabled={loading}
              onChange={(event) => handleMaxResultsChange(Number(event.target.value))}
            />
          </label>

          <Button
            className="h-11 min-w-40"
            isLoading={loading}
            loadingText="Screening..."
            disabled={!isScreeningEnabled || loading || !strategy.trim()}
            onClick={() => void handleSubmit()}
          >
            <Play className="h-4 w-4" />
            Run screening
          </Button>
        </div>

        <div className="mt-3 rounded-xl border border-border/75 bg-surface/55 px-3 py-2 text-xs leading-5 text-secondary-text">
          {strategyLoadError
            ? strategyLoadError
            : selectedStrategy?.description || 'The strategy applies hard filters and factor scoring first, then risk and portfolio constraints.'}
        </div>
      </section>

      {loading || screenMeta ? (
        <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-3">
              <span className={`grid h-7 w-7 place-items-center rounded-full ${loading ? 'text-cyan' : 'text-success'}`}>
                {loading ? <CircleAlert className="h-5 w-5" /> : <CheckCircle2 className="h-5 w-5" />}
              </span>
              <div>
                <h2 className="text-sm font-semibold text-foreground">{loading ? 'Screening running' : 'Screening complete'}</h2>
                <p className="mt-1 text-xs text-secondary-text">
                  {loading
                    ? `${taskMessage || 'Running screening'} · ${taskProgress}%`
                    : `${displayedStrategy} · ${MARKETS.find((item) => item.id === market)?.label}`}
                </p>
              </div>
            </div>
          </div>

          <details className="mt-3 border-t border-border/70 pt-3 text-xs text-secondary-text">
            <summary className="w-fit cursor-pointer font-medium text-secondary-text">Run details</summary>
            <div className="mt-2 grid gap-1">
              <span>Task: {activeTaskId ? activeTaskId.slice(0, 12) : '-'}</span>
              <span>Run ID: {screenMeta?.runId || '-'}</span>
              <span>
                Snapshot {screenMeta?.snapshotCount ?? '-'} · after filters {screenMeta?.afterFilterCount ?? '-'} · candidates {screenMeta?.candidateCount ?? candidates.length}
              </span>
              <span>
                Ranking: {screenMeta?.llmRanked ? 'smart rerank' : screenMeta ? 'deterministic factors' : '-'}
                {screenMeta?.llmModelUsed ? ` · ${screenMeta.llmModelUsed}` : ''}
                {screenMeta?.llmCoverage != null ? ` · coverage ${formatPercent(screenMeta.llmCoverage)}` : ''}
              </span>
              {screenMeta?.resultVariantPoolSize ? (
                <span>
                  Candidate variance: near-score pool {screenMeta.resultVariantPoolSize} ·{' '}
                  {screenMeta.resultVariantApplied
                    ? `${screenMeta.resultVariantRotatedSlots ?? 0} slot(s) rotated this run`
                    : 'baseline kept this run'}
                </span>
              ) : null}
              <span>
                Deep enrichment: {screenMeta?.dsaEnrichment?.enrichedCount ?? '-'} / {screenMeta?.dsaEnrichment?.requestedCount ?? '-'}
              </span>
            </div>
          </details>
        </section>
      ) : null}

      {screenMeta && alertMessages.length > 0 ? (
        <InlineAlert
          variant={llmFailed ? 'warning' : 'info'}
          title={llmFailed ? 'Using factor ranking' : 'Screening notice'}
          message={<ScreenAlertMessage messages={alertMessages} />}
        />
      ) : null}

      {screenMeta ? (
        <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
          <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <h2 className="text-base font-semibold text-foreground">Screening results</h2>
          <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-2 text-xs text-secondary-text">
            <Search className="h-4 w-4 text-cyan" />
            {candidates.length} candidate(s)
          </div>
          </div>

        {candidates.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface/70 px-5 py-10 text-center">
            <p className="text-sm font-medium text-foreground">No candidate matches the criteria</p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border">
            <table className="w-full min-w-[860px] border-collapse text-sm">
              <thead className="bg-surface text-left text-xs text-secondary-text">
                <tr>
                  <th className="w-14 px-4 py-3 font-semibold">#</th>
                  <th className="px-4 py-3 font-semibold">Code</th>
                  <th className="px-4 py-3 font-semibold">Name</th>
                  <th className="px-4 py-3 font-semibold">Industry</th>
                  <th className="px-4 py-3 font-semibold">Price</th>
                  <th className="px-4 py-3 font-semibold">Change</th>
                  <th className="px-4 py-3 font-semibold">Score</th>
                  <th className="px-4 py-3 font-semibold">Ranking basis</th>
                  <th className="px-4 py-3 font-semibold">Risk</th>
                  <th className="px-4 py-3 font-semibold">Details</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((item) => {
                  const expanded = expandedCode === item.code;
                  const factors = getFactorEntries(item);
                  const llmInsightAvailable = hasLlmInsight(item);
                  const dsaWarnings = item.dsaContext?.warnings || [];
                  const dsaNews = item.dsaNews || [];
                  const dsaEvents = item.dsaEvents || [];
                  return (
                    <Fragment key={`${item.rank}-${item.code}`}>
                      <tr className="border-t border-border align-top transition-colors hover:bg-hover/50">
                        <td className="px-4 py-3 text-secondary-text">{item.rank}</td>
                        <td className="px-4 py-3 font-mono font-semibold text-foreground">{item.code}</td>
                        <td className="px-4 py-3 font-semibold text-foreground">{item.name || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{item.industry || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{formatNumber(item.price)}</td>
                        <td className="px-4 py-3 text-secondary-text">{formatNumber(item.changePct)}%</td>
                        <td className="px-4 py-3 font-bold text-cyan">{formatScore(item.score)}</td>
                        <td className="px-4 py-3 text-secondary-text">{factorRanking ? 'Factor ranking' : formatScore(item.llmScore)}</td>
                        <td className="px-4 py-3">
                          <span className={`rounded-lg px-2.5 py-1 text-xs font-semibold ${getRiskClassName(item.riskLevel)}`}>
                            {getRiskLabel(item.riskLevel)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <button
                            className="text-sm font-semibold text-cyan transition-colors hover:text-foreground"
                            type="button"
                            onClick={() => setExpandedCode(expanded ? null : item.code)}
                          >
                            {expanded ? 'Collapse' : 'Expand'}
                          </button>
                        </td>
                      </tr>
                      {expanded ? (
                        <tr className="border-t border-border bg-surface/45">
                          <td colSpan={10} className="px-4 py-4">
                            <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
                              <div className="space-y-3">
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">Summary</p>
                                  <p className="mt-1 text-sm leading-6 text-foreground">{getCandidateReason(item)}</p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">Action signal</p>
                                  <p className="mt-1 text-sm text-foreground">{getSignal(item)}</p>
                                  <button
                                    className="mt-2 rounded-lg border border-cyan/40 px-3 py-1.5 text-xs font-semibold text-cyan transition-colors hover:bg-cyan/10"
                                    type="button"
                                    onClick={() => handleAnalyzeCandidate(item)}
                                  >
                                    Run deeper analysis
                                  </button>
                                </div>
                                {item.dsaAnalysisSummary ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">Enhanced summary</p>
                                    <p className="mt-1 text-sm leading-6 text-foreground">
                                      {formatEnrichmentSummary(item.dsaAnalysisSummary)}
                                    </p>
                                  </div>
                                ) : null}
                                {llmInsightAvailable ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">Model assessment</p>
                                    <p className="mt-1 text-sm leading-6 text-foreground">{item.llmThesis || item.reason}</p>
                                    <p className="mt-1 text-xs text-secondary-text">
                                      Sector {item.llmSector || '-'} · theme {item.llmTheme || '-'} · confidence {formatPercent(item.llmConfidence)}
                                    </p>
                                  </div>
                                ) : null}
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">Risk tags</p>
                                  <p className="mt-1 text-sm text-foreground">
                                    {[...(item.riskFlags || []), ...(item.llmRisks || [])].length
                                      ? [...(item.riskFlags || []), ...(item.llmRisks || [])].join(', ')
                                      : 'None'}
                                  </p>
                                </div>
                              </div>
                              <div className="space-y-3">
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">Key factors</p>
                                  <div className="mt-2 grid grid-cols-2 gap-2">
                                    {factors.length > 0 ? (
                                      factors.map(([key, value]) => (
                                        <div key={key} className="rounded-lg border border-border bg-card px-3 py-2">
                                          <span className="block text-xs text-secondary-text">{FACTOR_LABELS[key] || key}</span>
                                          <span className="text-sm font-semibold text-foreground">{formatNumber(value)}</span>
                                        </div>
                                      ))
                                    ) : (
                                      <span className="text-sm text-secondary-text">No factor detail</span>
                                    )}
                                  </div>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">Turnover</p>
                                  <p className="mt-1 text-sm text-foreground">{formatAmount(item.amount)}</p>
                                </div>
                                {item.llmWatchItems?.length ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">Model watch items</p>
                                    <p className="mt-1 text-sm text-foreground">{item.llmWatchItems.join(', ')}</p>
                                  </div>
                                ) : null}
                                {item.llmCatalysts?.length ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">Catalysts</p>
                                    <p className="mt-1 text-sm text-foreground">{item.llmCatalysts.join(', ')}</p>
                                  </div>
                                ) : null}
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">Related news</p>
                                  {dsaNews.length > 0 ? (
                                    <ul className="mt-1 space-y-1 text-sm text-foreground">
                                      {dsaNews.slice(0, 3).map((newsItem, newsIndex) => (
                                        <li key={`${item.code}-dsa-news-${newsIndex}`}>
                                          {newsItem.title || newsItem.snippet || '-'}
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="mt-1 text-sm text-secondary-text">None</p>
                                  )}
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">Announcements and events</p>
                                  {dsaEvents.length > 0 ? (
                                    <ul className="mt-1 space-y-1 text-sm text-foreground">
                                      {dsaEvents.slice(0, 3).map((eventItem, eventIndex) => (
                                        <li key={`${item.code}-dsa-event-${eventIndex}`}>
                                          {eventItem.title || eventItem.snippet || '-'}
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="mt-1 text-sm text-secondary-text">None</p>
                                  )}
                                </div>
                                {dsaWarnings.length > 0 ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">Data enrichment notes</p>
                                    <p className="mt-1 text-sm text-secondary-text">{dsaWarnings.join(', ')}</p>
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        </section>
      ) : null}

      <section className="rounded-2xl border border-border/80 bg-card/95 p-4 shadow-soft-card">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Clock3 className="h-4 w-4 text-cyan" />
            History
          </div>
          <button
            type="button"
            className="text-xs font-medium text-cyan transition-colors hover:text-foreground"
            onClick={() => void loadHistory()}
            disabled={historyLoading}
          >
            {historyLoading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
        {historyError ? (
          <p className="mb-3 text-xs text-danger">{historyError}</p>
        ) : null}
        {historyRuns.length === 0 ? (
          <p className="py-3 text-center text-xs text-secondary-text">
            {historyLoading ? 'Loading history...' : 'No screening history yet'}
          </p>
        ) : (
          <div className="divide-y divide-border/70">
            {historyRuns.map((run) => (
              <button
                key={run.runId}
                type="button"
                className="flex w-full items-center justify-between gap-3 py-2.5 text-left transition-colors hover:bg-hover/50"
                onClick={() => void handleHistoryRunSelect(run.runId)}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {formatHistoryStrategyName(run.strategy, strategies)}
                    <span className="ml-2 text-xs font-normal text-secondary-text">
                      {formatHistoryMarketLabel(run.market)}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-xs text-secondary-text">
                    {run.candidateCount ?? 0} returned
                    {run.snapshotCount != null ? ` · snapshot ${run.snapshotCount}` : ''}
                    {run.llmRanked ? ' · smart rerank' : ''}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-secondary-text">
                  {formatRunCreatedAt(run.createdAt)}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
    </AppPage>
  );
};

export default StockScreeningPage;
