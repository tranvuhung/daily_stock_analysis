import type { DecisionAction } from '../types/analysis';

export type DecisionActionTone = 'success' | 'warning' | 'danger' | 'default';
export type DecisionActionLabelMap = Record<DecisionAction, string>;
export type DecisionActionLabelTextKey =
  | 'history.actionBuy'
  | 'history.actionAdd'
  | 'history.actionHold'
  | 'history.actionReduce'
  | 'history.actionSell'
  | 'history.actionWatch'
  | 'history.actionAvoid'
  | 'history.actionAlert';
export type DecisionActionLabelTranslator = (key: DecisionActionLabelTextKey) => string;

// Nhãn dự phòng khi không truyền translator vào buildDecisionActionLabelMap.
// Khuyến nghị cũ (không có action) chỉ được nhận dạng theo cụm từ tiếng Anh.
// Legacy advice without an explicit action is only parsed from English phrases.
export const DEFAULT_DECISION_ACTION_LABELS: DecisionActionLabelMap = {
  buy: 'Mua',
  add: 'Mua thêm',
  hold: 'Nắm giữ',
  reduce: 'Giảm tỷ trọng',
  sell: 'Bán',
  watch: 'Quan sát',
  avoid: 'Tránh',
  alert: 'Cảnh báo',
};

const resolveActionLabels = (labels?: Partial<DecisionActionLabelMap>): DecisionActionLabelMap => ({
  ...DEFAULT_DECISION_ACTION_LABELS,
  ...labels,
});

export const buildDecisionActionLabelMap = (
  t: DecisionActionLabelTranslator,
): DecisionActionLabelMap => ({
  buy: t('history.actionBuy'),
  add: t('history.actionAdd'),
  hold: t('history.actionHold'),
  reduce: t('history.actionReduce'),
  sell: t('history.actionSell'),
  watch: t('history.actionWatch'),
  avoid: t('history.actionAvoid'),
  alert: t('history.actionAlert'),
});

const toneForAction = (action: DecisionAction): DecisionActionTone => {
  if (action === 'buy' || action === 'add' || action === 'hold') return 'success';
  if (action === 'sell' || action === 'reduce') return 'danger';
  return 'warning';
};

const normalizeEnglishAdvice = (value: string): string =>
  value.toLowerCase().replace(/[_-]/g, ' ');

const maskEnglishFinancialCompounds = (value: string): string =>
  value
    .replace(/(^|[^a-z0-9_])buy\s*back(?=$|[^a-z0-9_])/g, '$1financialcompound')
    .replace(/(^|[^a-z0-9_])sell\s*off(?=$|[^a-z0-9_])/g, '$1financialcompound');

const matchesEnglishTerm = (value: string, terms: readonly string[]): boolean =>
  terms.some((term) => new RegExp(`(^|[^a-z0-9_])${term}(?=$|[^a-z0-9_])`).test(value));

const matchesEnglishNegatedAction = (value: string, terms: readonly string[]): boolean => {
  const negationPrefix = String.raw`(?:not\s+(?:a\s+|an\s+|to\s+)?|no\s+(?:need\s+to\s+)?|need\s+not\s+|cannot\s+|can't\s+|cant\s+|do\s+not\s+|don't\s+|dont\s+)`;
  return terms.some((term) =>
    new RegExp(`(^|[^a-z0-9_])${negationPrefix}${term}(?=$|[^a-z0-9_])`).test(value),
  );
};

const hasEnglishAvoidedHoldAction = (value: string): boolean => {
  const terms = String.raw`(?:adding|accumulating|selling|reducing|trimming)`;
  return new RegExp(`(^|[^a-z0-9_])avoid\\s+${terms}(?=$|[^a-z0-9_])`).test(value);
};

const hasEnglishDeferredAction = (value: string): boolean => {
  const terms = String.raw`(?:buy|add|accumulate|sell|reduce|trim)`;
  return (
    new RegExp(`(^|[^a-z0-9_])wait(?:ing)?\\s+to\\s+${terms}(?=$|[^a-z0-9_])`).test(value) ||
    new RegExp(`(^|[^a-z0-9_])waiting\\s+(?:for|until)\\b.*?${terms}(?=$|[^a-z0-9_])`).test(value)
  );
};

export const getLegacyDecisionActionLabel = (
  advice?: string | null,
  labels?: Partial<DecisionActionLabelMap>,
): string | null => {
  const action = getLegacyDecisionAction(advice);
  if (!action) return null;
  return resolveActionLabels(labels)[action];
};

export const getLegacyDecisionAction = (advice?: string | null): DecisionAction | null => {
  const normalized = advice?.trim();
  if (!normalized) return null;
  const lower = maskEnglishFinancialCompounds(normalizeEnglishAdvice(normalized));

  if (hasEnglishDeferredAction(lower)) {
    return null;
  }

  if (
    matchesEnglishNegatedAction(lower, ['buy'])
  ) {
    return 'avoid';
  }
  if (
    hasEnglishAvoidedHoldAction(lower) ||
    matchesEnglishNegatedAction(lower, ['add', 'accumulate', 'sell', 'reduce', 'trim'])
  ) {
    return 'hold';
  }
  const guardMatches = new Set<DecisionAction>();
  if (
    matchesEnglishTerm(lower, ['avoid'])
  ) {
    guardMatches.add('avoid');
  }
  if (
    lower.includes('risk alert') ||
    matchesEnglishTerm(lower, ['alert'])
  ) {
    guardMatches.add('alert');
  }
  if (guardMatches.size === 1) {
    return Array.from(guardMatches)[0];
  }
  if (guardMatches.size > 1) {
    return null;
  }

  const matches = new Set<DecisionAction>();
  if (matchesEnglishTerm(lower, ['add', 'accumulate'])) {
    matches.add('add');
  }
  if (matchesEnglishTerm(lower, ['reduce', 'trim'])) {
    matches.add('reduce');
  }
  if (matchesEnglishTerm(lower, ['sell'])) {
    matches.add('sell');
  }
  if (matchesEnglishTerm(lower, ['hold'])) {
    matches.add('hold');
  }
  if (matchesEnglishTerm(lower, ['watch', 'wait'])) {
    matches.add('watch');
  }
  if (matchesEnglishTerm(lower, ['buy'])) {
    matches.add('buy');
  }

  if (matches.size === 1) {
    return Array.from(matches)[0];
  }
  return null;
};

export const getDecisionActionLabel = (
  action?: DecisionAction | null,
  actionLabel?: string | null,
  legacyAdvice?: string | null,
  emptyLabel: string | null = 'Khuyến nghị',
  labels?: Partial<DecisionActionLabelMap>,
): string | null => {
  const actionLabels = resolveActionLabels(labels);
  if (action) return actionLabels[action];
  const explicitLabel = actionLabel?.trim();
  if (explicitLabel) return explicitLabel;
  return getLegacyDecisionActionLabel(legacyAdvice, actionLabels) || emptyLabel;
};

export const getDecisionActionTone = (
  action?: DecisionAction | null,
  actionLabel?: string | null,
  legacyAdvice?: string | null,
): DecisionActionTone => {
  if (action) return toneForAction(action);

  const label = actionLabel?.trim() || '';
  if (label) {
    const lowerLabel = normalizeEnglishAdvice(label);
    if (matchesEnglishTerm(lowerLabel, ['buy', 'add', 'hold'])) return 'success';
    if (matchesEnglishTerm(lowerLabel, ['sell', 'reduce', 'trim'])) return 'danger';
    if (matchesEnglishTerm(lowerLabel, ['watch', 'wait', 'avoid', 'alert'])) return 'warning';
    return 'default';
  }

  const legacyAction = getLegacyDecisionAction(legacyAdvice);
  if (legacyAction) return toneForAction(legacyAction);

  return 'default';
};
