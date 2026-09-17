import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  HistoryListResponse,
  HistoryItem,
  HistoryFilters,
  AnalysisReport,
  NewsIntelResponse,
  NewsIntelItem,
  RunDiagnosticSummary,
  StockBarResponse,
} from '../types/analysis';
import type { RunFlowSnapshot } from '../types/runFlow';

// ============ API endpoints ============

export interface GetHistoryListParams extends HistoryFilters {
  page?: number;
  limit?: number;
}

export interface GetHistoryListOptions {
  signal?: AbortSignal;
}

export const historyApi = {
  /**
   * Fetch the analysis history list
   * @param params filter and pagination parameters
   */
  getList: async (
    params: GetHistoryListParams = {},
    options: GetHistoryListOptions = {},
  ): Promise<HistoryListResponse> => {
    const { stockCode, reportType, startDate, endDate, page = 1, limit = 20 } = params;

    const queryParams: Record<string, string | number> = { page, limit };
    if (stockCode) queryParams.stock_code = stockCode;
    if (reportType) queryParams.report_type = reportType;
    if (startDate) queryParams.start_date = startDate;
    if (endDate) queryParams.end_date = endDate;

    const response = await apiClient.get<Record<string, unknown>>('/api/v1/history', {
      params: queryParams,
      signal: options.signal,
    });

    const data = toCamelCase<{ total: number; page: number; limit: number; items: HistoryItem[] }>(response.data);
    return {
      total: data.total,
      page: data.page,
      limit: data.limit,
      items: data.items.map(item => toCamelCase<HistoryItem>(item)),
    };
  },

  /**
   * Fetch the detail of a history report
   * @param recordId analysis history primary key ID (uses ID rather than query_id because query_id can repeat in batch analysis)
   */
  getDetail: async (recordId: number): Promise<AnalysisReport> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/history/${recordId}`);
    return toCamelCase<AnalysisReport>(response.data);
  },

  /**
   * Fetch news linked to a history report
   * @param recordId analysis history primary key ID
   * @param limit maximum number of items to return
   */
  getNews: async (recordId: number, limit = 20): Promise<NewsIntelResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/history/${recordId}/news`, {
      params: { limit },
    });

    const data = toCamelCase<NewsIntelResponse>(response.data);
    return {
      total: data.total,
      items: (data.items || []).map(item => toCamelCase<NewsIntelItem>(item)),
    };
  },

  /**
   * Fetch the Markdown content of a history report
   * @param recordId analysis history primary key ID
   * @returns the full report content in Markdown
   */
  getMarkdown: async (recordId: number): Promise<string> => {
    const response = await apiClient.get<{ content: string }>(`/api/v1/history/${recordId}/markdown`);
    return response.data.content;
  },

  /**
   * Generate a share image for a history report
   * @param recordId analysis history primary key ID
   */
  getShareImage: async (recordId: number): Promise<Blob> => {
    const response = await apiClient.get<Blob>(`/api/v1/history/${recordId}/share-image`, {
      responseType: 'blob',
    });
    return response.data;
  },

  /**
   * Fetch the run diagnostic summary of a history report
   * @param recordId analysis history primary key ID
   */
  getDiagnostics: async (recordId: number): Promise<RunDiagnosticSummary> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/history/${recordId}/diagnostics`);
    return toCamelCase<RunDiagnosticSummary>(response.data);
  },

  /**
   * Fetch the run-flow snapshot of a history report
   * @param recordId analysis history primary key ID
   */
  getRecordFlow: async (recordId: number): Promise<RunFlowSnapshot> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/history/${recordId}/flow`);
    return toCamelCase<RunFlowSnapshot>(response.data);
  },

  /**
   * Delete history records in bulk
   * @param recordIds list of analysis history primary key IDs
   */
  deleteRecords: async (recordIds: number[]): Promise<{ deleted: number }> => {
    const response = await apiClient.delete<Record<string, unknown>>('/api/v1/history', {
      data: { record_ids: recordIds },
    });

    return toCamelCase<{ deleted: number }>(response.data);
  },

  /**
   * Delete every history record for a stock code
   * @param stockCode stock code
   */
  deleteByCode: async (stockCode: string): Promise<{ deleted: number }> => {
    const response = await apiClient.delete<Record<string, unknown>>(`/api/v1/history/by-code/${encodeURIComponent(stockCode)}`);
    return toCamelCase<{ deleted: number }>(response.data);
  },

  /**
   * Fetch the stock-bar list (deduplicated stocks, excluding market reviews)
   */
  getStockBarList: async (params: {
    startDate?: string;
    endDate?: string;
    limit?: number;
  } = {}): Promise<StockBarResponse> => {
    const queryParams: Record<string, string | number> = {};
    if (params.startDate) queryParams.start_date = params.startDate;
    if (params.endDate) queryParams.end_date = params.endDate;
    if (params.limit) queryParams.limit = params.limit;

    const response = await apiClient.get<Record<string, unknown>>('/api/v1/history/stocks', {
      params: queryParams,
    });

    const data = toCamelCase<{ total: number; items: unknown[] }>(response.data);
    return {
      total: data.total,
      items: data.items.map(item => toCamelCase<Record<string, unknown>>(item) as unknown as typeof data.items[0]),
    } as StockBarResponse;
  },
};
