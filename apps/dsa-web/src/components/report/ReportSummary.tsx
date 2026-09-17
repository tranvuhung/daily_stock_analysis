import React from 'react';
import type { AnalysisResult, AnalysisReport } from '../../types/analysis';
import { ReportOverview } from './ReportOverview';
import { ReportStrategy } from './ReportStrategy';
import { ReportNews } from './ReportNews';
import { ReportDetails } from './ReportDetails';
import { ReportDiagnostics } from './ReportDiagnostics';
import { AnalysisContextSummary } from './AnalysisContextSummary';
import { MarketReviewReportView } from './MarketReviewReportView';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportSummaryProps {
  data: AnalysisResult | AnalysisReport;
  isHistory?: boolean;
  /** Watchlist-related */
  watchlist?: {
    isInWatchlist: (code: string) => boolean;
    onToggle: (code: string) => void;
    isActioning: boolean;
    actionMessage: string | null;
  };
  onOpenRunFlow?: (recordId: number) => void;
}

/**
 * Full report view component
 * Renders the report with the main content first and transparency details last.
 */
export const ReportSummary: React.FC<ReportSummaryProps> = ({
  data,
  isHistory = false,
  watchlist,
  onOpenRunFlow,
}) => {
  // Accepts both the AnalysisResult and AnalysisReport payload shapes
  const report: AnalysisReport = 'report' in data ? data.report : data;
  // Use the report id: queryId can repeat in batch analysis, and the history detail API needs recordId to fetch linked news and details
  const recordId = report.meta.id;
  const diagnosticSummary = 'diagnosticSummary' in data ? data.diagnosticSummary : undefined;

  const { meta, summary, strategy, details } = report;
  const reportLanguage = normalizeReportLanguage(meta.reportLanguage);
  const text = getReportText(reportLanguage);
  const modelUsed = (meta.modelUsed || '').trim();
  const shouldShowModel = Boolean(
    modelUsed && !['unknown', 'error', 'none', 'null', 'n/a'].includes(modelUsed.toLowerCase()),
  );

  if (meta.reportType === 'market_review') {
    return (
      <MarketReviewReportView
        report={report}
        recordId={recordId}
        reportLanguage={reportLanguage}
        onOpenRunFlow={onOpenRunFlow}
      />
    );
  }

  return (
    <div className="space-y-5 pb-8 animate-fade-in">
      {/* Overview section (above the fold) */}
      <ReportOverview
        meta={meta}
        summary={summary}
        details={details}
        isHistory={isHistory}
        watchlist={watchlist}
      />

      {/* Strategy levels section */}
      <ReportStrategy strategy={strategy} language={reportLanguage} />

      {/* News section */}
      <ReportNews recordId={recordId} limit={8} language={reportLanguage} />

      {/* Low-sensitivity summary of the input data blocks */}
      <AnalysisContextSummary
        overview={details?.analysisContextPackOverview}
        language={reportLanguage}
      />

      {/* Run diagnostics summary */}
      <ReportDiagnostics
        recordId={recordId}
        summary={diagnosticSummary}
        language={reportLanguage}
        onOpenRunFlow={onOpenRunFlow}
      />

      {/* Transparency and traceability section */}
      <ReportDetails details={details} recordId={recordId} language={reportLanguage} />

      {/* Analysis model marker (Issue #528) — end of report */}
      {shouldShowModel && (
        <p className="px-1 text-xs text-muted-text">
          {text.analysisModel}: {modelUsed}
        </p>
      )}
    </div>
  );
};
