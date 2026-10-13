import { describe, expect, it } from 'vitest';
import {
  type DecisionActionLabelMap,
  getDecisionActionLabel,
  getLegacyDecisionAction,
  getDecisionActionTone,
  getLegacyDecisionActionLabel,
} from '../decisionAction';

const englishLabels: DecisionActionLabelMap = {
  buy: 'Buy',
  add: 'Add',
  hold: 'Hold',
  reduce: 'Reduce',
  sell: 'Sell',
  watch: 'Watch',
  avoid: 'Avoid',
  alert: 'Alert',
};

describe('decisionAction helpers', () => {
  it('uses structured action taxonomy before server label and legacy advice text', () => {
    expect(getDecisionActionLabel('avoid', 'Tránh', 'Mua', 'Khuyến nghị')).toBe('Tránh');
    expect(getDecisionActionLabel('sell', 'Mua', null, 'Advice', englishLabels)).toBe('Sell');
    expect(getDecisionActionTone('sell', 'Mua', null)).toBe('danger');
    expect(getDecisionActionLabel(null, 'Mua', null, 'Advice', englishLabels)).toBe('Mua');
  });

  it('falls back to the action taxonomy label when actionLabel is absent', () => {
    expect(getDecisionActionLabel('add', null, 'Nắm giữ', 'Khuyến nghị')).toBe('Mua thêm');
    expect(getDecisionActionLabel('watch', null, 'Nắm giữ', 'Advice', englishLabels)).toBe('Watch');
  });

  it('keeps legacy fallback compatible with negated buy advice', () => {
    expect(getDecisionActionLabel(null, null, 'avoid', 'Khuyến nghị')).toBe('Tránh');
    expect(getLegacyDecisionActionLabel('no buy until breakout')).toBe('Tránh');
    expect(getLegacyDecisionActionLabel('no need to buy before confirmation')).toBe('Tránh');
    expect(getLegacyDecisionActionLabel('cannot buy before confirmation')).toBe('Tránh');
    expect(getLegacyDecisionActionLabel("can't buy before confirmation")).toBe('Tránh');
    expect(getLegacyDecisionActionLabel('not a buy yet')).toBe('Tránh');
    expect(getLegacyDecisionActionLabel('not a buy yet', englishLabels)).toBe('Avoid');
    expect(getLegacyDecisionActionLabel('not to buy', englishLabels)).toBe('Avoid');
    expect(getLegacyDecisionActionLabel('avoid buying', englishLabels)).toBe('Avoid');
    expect(getLegacyDecisionActionLabel('avoid buying into weakness', englishLabels)).toBe('Avoid');
    expect(getLegacyDecisionActionLabel('waiting to buy')).toBeNull();
  });

  it('keeps legacy fallback compatible with negated sell and add advice', () => {
    expect(getLegacyDecisionActionLabel('no add before confirmation')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('cannot add before confirmation')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('no need to accumulate here')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel("can't accumulate here")).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('no sell before earnings')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('cannot sell before earnings')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('no need to reduce exposure')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel("can't reduce exposure")).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('no trim while trend holds')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('cannot trim while trend holds')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('not a sell yet')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('not a trim yet')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('not to sell')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('not to trim')).toBe('Nắm giữ');
    expect(getLegacyDecisionActionLabel('not a trim yet', englishLabels)).toBe('Hold');
    expect(getLegacyDecisionActionLabel('avoid selling into weakness', englishLabels)).toBe('Hold');
    expect(getLegacyDecisionActionLabel('avoid trimming before earnings', englishLabels)).toBe('Hold');
    expect(getLegacyDecisionActionLabel('avoid reducing exposure before earnings', englishLabels)).toBe('Hold');
    expect(getDecisionActionTone(null, null, 'no sell before earnings')).toBe('success');
  });

  it('does not turn ambiguous English advice into a badge action', () => {
    expect(getLegacyDecisionActionLabel('buy or sell')).toBeNull();
    expect(getDecisionActionLabel(null, null, 'buy or sell', 'Advice', englishLabels)).toBe('Advice');
  });

  it('does not match financial compound words as legacy actions', () => {
    expect(getLegacyDecisionActionLabel('no buyback announced', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('cannot buyback shares now', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('share buy-back announced', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('share buy back announced', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('no selloff risk', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('not selloff yet', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('sell-off risk remains low', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('sell off risk remains low', englishLabels)).toBeNull();
    expect(getLegacyDecisionActionLabel('no sell-off pressure', englishLabels)).toBeNull();
    expect(getDecisionActionLabel(null, null, 'no buyback announced', 'Advice', englishLabels)).toBe('Advice');
    expect(getDecisionActionLabel(null, null, 'no selloff risk', 'Advice', englishLabels)).toBe('Advice');
    expect(getLegacyDecisionActionLabel('no buy until breakout', englishLabels)).toBe('Avoid');
    expect(getLegacyDecisionActionLabel('cannot buy before confirmation', englishLabels)).toBe('Avoid');
    expect(getLegacyDecisionActionLabel('no sell before earnings', englishLabels)).toBe('Hold');
  });

  it('keeps separate action terms next to financial compounds', () => {
    expect(getLegacyDecisionAction('buy after sell-off')).toBe('buy');
    expect(getLegacyDecisionActionLabel('buy after sell-off', englishLabels)).toBe('Buy');
    expect(getLegacyDecisionAction('sell after buy-back rumor')).toBe('sell');
    expect(getLegacyDecisionActionLabel('sell after buy-back rumor', englishLabels)).toBe('Sell');
  });

  it('ignores legacy advice text that is not English (no Chinese/Vietnamese phrase parsing)', () => {
    expect(getLegacyDecisionActionLabel('买入')).toBeNull();
    expect(getLegacyDecisionActionLabel('Mua vào')).toBeNull();
    expect(getDecisionActionLabel(null, null, 'Mua vào')).toBe('Khuyến nghị');
  });

  it('keeps multi-guard legacy advice empty instead of prioritizing avoid or alert', () => {
    expect(getLegacyDecisionActionLabel('risk alert, avoid buying')).toBeNull();
    expect(getDecisionActionLabel(null, null, 'risk alert, avoid buying', 'Advice', englishLabels)).toBe('Advice');
    expect(getLegacyDecisionActionLabel('avoid buying', englishLabels)).toBe('Avoid');
    expect(getLegacyDecisionActionLabel('risk alert', englishLabels)).toBe('Alert');
  });

  it('maps action tone without reading legacy text when action is present', () => {
    expect(getDecisionActionTone('buy', null, 'sell')).toBe('success');
    expect(getDecisionActionTone('reduce', null, 'buy')).toBe('danger');
    expect(getDecisionActionTone('alert', null, 'buy')).toBe('warning');
    expect(getDecisionActionTone(null, 'Watch', 'buy')).toBe('warning');
    expect(getDecisionActionTone(null, 'Sell', 'buy')).toBe('danger');
    expect(getDecisionActionTone(null, null, 'avoid buying')).toBe('warning');
  });
});
