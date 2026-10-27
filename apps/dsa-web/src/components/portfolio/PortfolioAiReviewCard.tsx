import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { portfolioApi } from '../../api/portfolio';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { ApiErrorAlert, Badge, Card, InlineAlert } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { formatUiText } from '../../i18n/uiText';
import { PORTFOLIO_TEXT } from '../../locales/featureText';
import type { DecisionAction } from '../../types/analysis';
import type { PortfolioAiReviewResponse, PortfolioCostMethod } from '../../types/portfolio';
import { buildDecisionActionLabelMap } from '../../utils/decisionAction';

interface PortfolioAiReviewCardProps {
  accountId?: number;
  costMethod: PortfolioCostMethod;
  positionCount: number;
}

function formatWeight(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return `${(value * 100).toFixed(1)}%`;
}

// Điểm rủi ro 1-10: <=3 thấp, >=7 cao. / Risk score tone.
function riskScoreVariant(score?: number | null): 'success' | 'warning' | 'danger' | 'default' {
  if (score == null) return 'default';
  if (score >= 7) return 'danger';
  if (score <= 3) return 'success';
  return 'warning';
}

const TextList: React.FC<{ title: string; items: string[] }> = ({ title, items }) => {
  if (items.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold text-foreground mb-1">{title}</h3>
      <ul className="list-disc pl-5 space-y-1 text-xs text-secondary-text">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
};

/**
 * Thẻ "Đánh giá danh mục bằng AI": gọi POST /api/v1/portfolio/ai-review và hiển thị kết quả
 * PortfolioAgent. Kết quả chỉ giữ trong state, reset khi đổi tài khoản hoặc phương pháp giá vốn.
 */
export const PortfolioAiReviewCard: React.FC<PortfolioAiReviewCardProps> = ({
  accountId,
  costMethod,
  positionCount,
}) => {
  const { language, t } = useUiLanguage();
  const text = PORTFOLIO_TEXT[language];
  const actionLabels = useMemo(() => buildDecisionActionLabelMap(t), [t]);
  const priorityLabels: Record<string, string> = {
    high: text.priorityHigh,
    medium: text.priorityMedium,
    low: text.priorityLow,
  };

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PortfolioAiReviewResponse | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    setResult(null);
    setError(null);
  }, [accountId, costMethod]);

  const runReview = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await portfolioApi.aiReview({ accountId, costMethod });
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  };

  const assessment = result?.success ? result.assessment : null;
  let failureMessage: string | null = null;
  if (result && !result.success) {
    if (result.timedOut) failureMessage = text.aiReviewTimeout;
    else if (result.error === 'no_positions') failureMessage = text.aiReviewNoPositions;
    else failureMessage = result.error || text.aiReviewFailed;
  }

  return (
    <Card padding="md">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">{text.aiReviewTitle}</h2>
          <p className="mt-1 text-xs text-secondary-text">{text.aiReviewDescription}</p>
        </div>
        <button
          type="button"
          className="btn-secondary text-sm"
          onClick={() => void runReview()}
          disabled={loading || positionCount === 0}
        >
          {loading ? text.aiReviewRunning : result ? text.aiReviewRerun : text.aiReviewRun}
        </button>
      </div>

      {error ? <ApiErrorAlert error={error} className="mb-3" /> : null}
      {failureMessage ? (
        <InlineAlert variant="warning" title={text.aiReviewFailed} message={failureMessage} className="mb-3" />
      ) : null}

      {assessment ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={riskScoreVariant(assessment.portfolioRiskScore)}>
              {text.aiReviewRiskScore}: {assessment.portfolioRiskScore ?? '--'}/10
            </Badge>
            <Badge variant="info">
              {text.aiReviewCash}: {formatWeight(assessment.cashRatio)}
              {assessment.suggestedCashRatio != null
                ? ` → ${formatWeight(assessment.suggestedCashRatio)} (${text.aiReviewSuggestedCash})`
                : ''}
            </Badge>
            <span className="text-xs text-secondary-text">
              {formatUiText(text.aiReviewMeta, {
                asOf: result?.asOf || '--',
                holdings: result?.holdingCount ?? 0,
                withSignal: result?.signalCoverage.withSignal ?? 0,
                duration: result?.durationS ?? 0,
              })}
            </span>
          </div>

          {assessment.summary ? (
            <p className="text-sm text-foreground whitespace-pre-line">{assessment.summary}</p>
          ) : null}

          {assessment.positions.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-secondary-text">
                    <th className="py-1 pr-3 font-medium">{text.code}</th>
                    <th className="py-1 pr-3 font-medium text-right">{text.aiReviewCurrentWeight}</th>
                    <th className="py-1 pr-3 font-medium text-right">{text.aiReviewSuggestedWeight}</th>
                    <th className="py-1 pr-3 font-medium">{text.aiReviewSignal}</th>
                    <th className="py-1 pr-3 font-medium">{text.aiReviewPriority}</th>
                    <th className="py-1 font-medium">{text.aiReviewNote}</th>
                  </tr>
                </thead>
                <tbody>
                  {assessment.positions.map((position) => (
                    <tr key={position.code} className="border-t border-border/40 align-top">
                      <td className="py-1.5 pr-3 font-mono text-foreground">{position.code}</td>
                      <td className="py-1.5 pr-3 text-right">{formatWeight(position.currentWeight)}</td>
                      <td className="py-1.5 pr-3 text-right">{formatWeight(position.suggestedWeight)}</td>
                      <td className="py-1.5 pr-3">
                        {position.signal ? actionLabels[position.signal as DecisionAction] ?? position.signal : '--'}
                      </td>
                      <td className="py-1.5 pr-3">{position.priority ? priorityLabels[position.priority] : '--'}</td>
                      <td className="py-1.5 text-secondary-text">{position.note || '--'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <TextList title={text.aiReviewRebalance} items={assessment.rebalanceSuggestions} />
            <TextList title={text.aiReviewSectorWarnings} items={assessment.sectorWarnings} />
            <TextList title={text.aiReviewCorrelationWarnings} items={assessment.correlationWarnings} />
            <TextList title={text.aiReviewMarketNotes} items={assessment.crossMarketNotes} />
            <TextList title={text.aiReviewDataGaps} items={assessment.dataGaps} />
          </div>
        </div>
      ) : null}

      <p className="mt-3 text-[11px] text-muted-text">{text.aiReviewDisclaimer}</p>
    </Card>
  );
};
