import { create } from 'zustand';
import type { AlertFeedStatus, AlertLiveQuote, AlertTriggeredEvent } from '../types/alerts';

// Số toast cảnh báo giữ lại tối đa. Max alert toasts kept on screen.
const MAX_TOASTS = 4;

export interface AlertToast extends AlertTriggeredEvent {
  toastId: string;
}

interface AlertStoreState {
  /** Số cảnh báo realtime chưa xem (badge ở menu Cảnh báo). Unread realtime alerts. */
  unreadCount: number;
  toasts: AlertToast[];
  /** Giá live theo mã trần (HPG). Live prices keyed by bare symbol. */
  livePrices: Record<string, AlertLiveQuote>;
  feedStatus: AlertFeedStatus | null;
  streamConnected: boolean;
  /** Tăng mỗi lần có alert_triggered để trang Cảnh báo tải lại lịch sử. */
  triggerVersion: number;
  pushAlert: (event: AlertTriggeredEvent) => void;
  dismissToast: (toastId: string) => void;
  clearUnread: () => void;
  upsertPrice: (quote: AlertLiveQuote) => void;
  setPrices: (quotes: AlertLiveQuote[]) => void;
  setFeedStatus: (status: AlertFeedStatus | null) => void;
  setStreamConnected: (connected: boolean) => void;
}

let toastSequence = 0;

export function liveQuoteKey(code: string | undefined | null): string {
  const value = String(code ?? '').trim().toUpperCase();
  return value.endsWith('.VN') ? value.slice(0, -3) : value;
}

export const useAlertStore = create<AlertStoreState>((set) => ({
  unreadCount: 0,
  toasts: [],
  livePrices: {},
  feedStatus: null,
  streamConnected: false,
  triggerVersion: 0,
  pushAlert: (event) => set((state) => {
    toastSequence += 1;
    const toast: AlertToast = { ...event, toastId: `${event.triggerId ?? event.ruleId}-${toastSequence}` };
    return {
      unreadCount: state.unreadCount + 1,
      toasts: [toast, ...state.toasts].slice(0, MAX_TOASTS),
      triggerVersion: state.triggerVersion + 1,
    };
  }),
  dismissToast: (toastId) => set((state) => ({ toasts: state.toasts.filter((item) => item.toastId !== toastId) })),
  clearUnread: () => set({ unreadCount: 0 }),
  upsertPrice: (quote) => set((state) => ({
    livePrices: { ...state.livePrices, [liveQuoteKey(quote.symbol)]: { ...state.livePrices[liveQuoteKey(quote.symbol)], ...quote } },
  })),
  setPrices: (quotes) => set((state) => {
    const next = { ...state.livePrices };
    quotes.forEach((quote) => {
      next[liveQuoteKey(quote.target ?? quote.symbol)] = quote;
    });
    return { livePrices: next };
  }),
  setFeedStatus: (status) => set({ feedStatus: status }),
  setStreamConnected: (connected) => set({ streamConnected: connected }),
}));
