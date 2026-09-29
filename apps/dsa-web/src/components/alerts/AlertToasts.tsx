import type React from 'react';
import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { InlineAlert, ToastViewport } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { formatUiText } from '../../i18n/uiText';
import { ALERT_REALTIME_TEXT } from '../../locales/featureText';
import { useAlertStore, type AlertToast } from '../../stores/alertStore';

// Toast tự đóng sau khoảng này (ms). Toasts auto-dismiss after this delay.
const AUTO_DISMISS_MS = 12000;

function toastVariant(severity: string): 'info' | 'warning' | 'danger' {
  if (severity === 'critical') return 'danger';
  if (severity === 'info') return 'info';
  return 'warning';
}

const AlertToastItem: React.FC<{ toast: AlertToast }> = ({ toast }) => {
  const { language } = useUiLanguage();
  const text = ALERT_REALTIME_TEXT[language];
  const dismissToast = useAlertStore((state) => state.dismissToast);

  useEffect(() => {
    const timer = setTimeout(() => dismissToast(toast.toastId), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [dismissToast, toast.toastId]);

  return (
    <div className="pointer-events-auto" data-testid="alert-toast">
      <InlineAlert
        variant={toastVariant(toast.severity)}
        title={formatUiText(text.toastTitle, { target: toast.displayTarget || toast.target })}
        message={toast.reason || toast.alertType}
        action={(
          <div className="flex items-center gap-3 text-xs">
            <Link to="/alerts" className="underline" onClick={() => dismissToast(toast.toastId)}>
              {text.viewAlerts}
            </Link>
            <button type="button" className="underline" onClick={() => dismissToast(toast.toastId)}>
              {text.dismiss}
            </button>
          </div>
        )}
      />
    </div>
  );
};

/** Toast toàn cục cho cảnh báo realtime (sự kiện SSE ``alert_triggered``). */
export const AlertToasts: React.FC = () => {
  const toasts = useAlertStore((state) => state.toasts);
  if (toasts.length === 0) return null;
  return (
    <ToastViewport>
      {toasts.map((toast) => <AlertToastItem key={toast.toastId} toast={toast} />)}
    </ToastViewport>
  );
};

export default AlertToasts;
