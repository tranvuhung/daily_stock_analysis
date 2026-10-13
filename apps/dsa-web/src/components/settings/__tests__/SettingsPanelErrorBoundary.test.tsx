import { render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsPanelErrorBoundary } from '../SettingsPanelErrorBoundary';

function ThrowingPanel({ message = 'mock settings panel crash' }: { message?: string }): ReactElement {
  throw new Error(message);
}

describe('SettingsPanelErrorBoundary', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a configurable desktop-log diagnostic fallback when a settings panel throws', () => {
    render(
      <SettingsPanelErrorBoundary
        title="Notification settings"
        resetKey="notification"
        diagnosticHint={(
          <>
            Hãy kiểm tra và cung cấp log desktop
            <code>desktop.log</code>
            , đồng thời bổ sung phiên bản release, phiên bản Windows và lối vào kích hoạt.
          </>
        )}
      >
        <ThrowingPanel />
      </SettingsPanelErrorBoundary>
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Notification settings failed to load')).toBeInTheDocument();
    expect(screen.getByText('desktop.log')).toBeInTheDocument();
    expect(screen.getByText(/phiên bản release, phiên bản Windows và lối vào kích hoạt/)).toBeInTheDocument();
    expect(screen.getByText(/Error summary: mock settings panel crash/)).toBeInTheDocument();
  });

  it('redacts and truncates sensitive error summary text', () => {
    render(
      <SettingsPanelErrorBoundary title="Notification settings" resetKey="notification">
        <ThrowingPanel
          message={`Webhook failed: https://hooks.slack.com/services/T000/B000/path-secret?token=super-secret-token&foo=bar OPENAI_API_KEY=sk-supersecretvalue123456 ${'x'.repeat(220)}`}
        />
      </SettingsPanelErrorBoundary>
    );

    const summary = screen.getByText(/Error summary: /).textContent ?? '';

    expect(summary).toContain('https://hooks.slack.com/[redacted]?[redacted]');
    expect(summary).toContain('?[redacted]');
    expect(summary).toContain('OPENAI_API_KEY=[redacted]');
    expect(summary).not.toContain('/services/T000/B000/path-secret');
    expect(summary).not.toContain('path-secret');
    expect(summary).not.toContain('super-secret-token');
    expect(summary).not.toContain('sk-supersecretvalue123456');
    expect(summary.length).toBeLessThanOrEqual('Error summary: '.length + 183);
  });

  it('resets after resetKey changes so the panel can render again', async () => {
    const { rerender } = render(
      <SettingsPanelErrorBoundary title="Agent settings" resetKey="agent:v1">
        <ThrowingPanel />
      </SettingsPanelErrorBoundary>
    );

    expect(screen.getByText('Agent settings failed to load')).toBeInTheDocument();

    rerender(
      <SettingsPanelErrorBoundary title="Agent settings" resetKey="agent:v2">
        <div>Agent Đã khôi phục cài đặt</div>
      </SettingsPanelErrorBoundary>
    );

    await waitFor(() => {
      expect(screen.getByText('Agent Đã khôi phục cài đặt')).toBeInTheDocument();
    });
    expect(screen.queryByText('Agent settings failed to load')).not.toBeInTheDocument();
  });
});
