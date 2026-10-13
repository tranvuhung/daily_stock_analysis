import type { MarketReviewRegion } from '../types/analysis';

export type { MarketReviewRegion };

// Chỉ còn thị trường Việt Nam. / Vietnam is the only supported market review region.
export const MARKET_REVIEW_REGION_ORDER: readonly MarketReviewRegion[] = ['vn'];

export function serializeMarketReviewRegions(regions: readonly MarketReviewRegion[]): string {
  return MARKET_REVIEW_REGION_ORDER.filter((region) => regions.includes(region)).join(',') || 'vn';
}
