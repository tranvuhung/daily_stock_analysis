import { useEffect } from 'react';
import { alertsApi } from '../api/alerts';
import { toCamelCase } from '../api/utils';
import { useAlertStore } from '../stores/alertStore';
import type { AlertFeedStatus, AlertLiveQuote, AlertTriggeredEvent } from '../types/alerts';

// Thời gian chờ kết nối lại, tăng dần tới tối đa. Reconnect backoff (ms).
const RECONNECT_BASE_MS = 3000;
const RECONNECT_MAX_MS = 30000;

function parse<T>(event: MessageEvent): T | null {
  try {
    return toCamelCase<T>(JSON.parse(event.data));
  } catch {
    return null;
  }
}

/**
 * Mở một kết nối SSE tới ``/api/v1/alerts/stream`` và ghi sự kiện vào ``useAlertStore``.
 * Gọi một lần ở ``Shell``. Opens the alert SSE stream once and feeds the alert store.
 */
export function useAlertStream(enabled = true): void {
  useEffect(() => {
    if (!enabled || typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
      return undefined;
    }
    const store = useAlertStore.getState();
    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let closed = false;

    const connect = () => {
      if (closed) return;
      source = new window.EventSource(alertsApi.getStreamUrl(), { withCredentials: true });
      source.addEventListener('connected', () => {
        attempts = 0;
        store.setStreamConnected(true);
      });
      source.addEventListener('alert_triggered', (event) => {
        const payload = parse<AlertTriggeredEvent>(event as MessageEvent);
        if (payload) useAlertStore.getState().pushAlert(payload);
      });
      source.addEventListener('price_update', (event) => {
        const payload = parse<AlertLiveQuote>(event as MessageEvent);
        if (payload?.symbol) useAlertStore.getState().upsertPrice(payload);
      });
      source.addEventListener('feed_status', (event) => {
        const payload = parse<AlertFeedStatus>(event as MessageEvent);
        if (payload) useAlertStore.getState().setFeedStatus(payload);
      });
      source.onerror = () => {
        useAlertStore.getState().setStreamConnected(false);
        source?.close();
        source = null;
        if (closed) return;
        const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempts);
        attempts += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
      useAlertStore.getState().setStreamConnected(false);
    };
  }, [enabled]);
}
