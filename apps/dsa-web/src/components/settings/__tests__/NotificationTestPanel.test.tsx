import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { useUiLanguage, UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { NotificationTestPanel } from '../NotificationTestPanel';

const testNotificationChannel = vi.hoisted(() => vi.fn());

vi.mock('../../../api/systemConfig', () => ({
  systemConfigApi: {
    testNotificationChannel,
  },
}));

describe('NotificationTestPanel', () => {
  beforeEach(() => {
    testNotificationChannel.mockReset();
    testNotificationChannel.mockResolvedValue({
      success: true,
      message: 'ok',
      errorCode: null,
      stage: 'notification_send',
      retryable: false,
      latencyMs: 12,
      attempts: [
        {
          channel: 'custom',
          success: true,
          message: 'sent',
          target: 'https://example.com/hook?token=***',
          errorCode: null,
          stage: 'notification_send',
          retryable: false,
          latencyMs: 12,
          httpStatus: 200,
        },
      ],
    });
  });

  it('submits draft notification items and renders attempt details', async () => {
    render(
      <NotificationTestPanel
        items={[{ key: 'CUSTOM_WEBHOOK_URLS', value: 'https://example.com/hook?token=secret' }]}
        maskToken="******"
      />,
    );

    expect(screen.getByRole('option', { name: 'Telegram' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'WeCom' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Feishu Webhook' })).not.toBeInTheDocument();
    for (const hidden of ['DingTalk', 'Pushover', 'ntfy', 'Gotify', 'PushPlus', 'ServerChan3', 'AstrBot']) {
      expect(screen.queryByRole('option', { name: hidden })).not.toBeInTheDocument();
    }
    fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'custom' } });
    fireEvent.click(screen.getByRole('button', { name: /Send test/ }));

    await waitFor(() => expect(testNotificationChannel).toHaveBeenCalledWith(expect.objectContaining({
      channel: 'custom',
      items: [{ key: 'CUSTOM_WEBHOOK_URLS', value: 'https://example.com/hook?token=secret' }],
      maskToken: '******',
      timeoutSeconds: 20,
    })));
    expect(await screen.findByText('Test succeeded')).toBeInTheDocument();
    expect(screen.getByText('HTTP 200')).toBeInTheDocument();
    expect(screen.getByText('https://example.com/hook?token=***')).toBeInTheDocument();
  });

  it('uses translated defaults when UI language changes and user has not edited fields', async () => {
    localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');

    const SwitchHarness = ({ children }: { children: ReactNode }) => {
      const { setLanguage } = useUiLanguage();
      return (
        <div>
          <button type="button" onClick={() => setLanguage('en')}>
            switch-en
          </button>
          {children}
        </div>
      );
    };

    render(
      <UiLanguageProvider>
        <SwitchHarness>
          <NotificationTestPanel
            items={[{ key: 'CUSTOM_WEBHOOK_URLS', value: 'https://example.com/hook?token=secret' }]}
            maskToken="******"
          />
        </SwitchHarness>
      </UiLanguageProvider>
    );

    const titleInput = screen.getByLabelText('Title');
    const contentInput = screen.getByLabelText('Body');

    expect(titleInput).toHaveValue('DSA notification test');
    expect(contentInput).toHaveValue('This is a test notification from the DSA Web settings page.');

    fireEvent.click(screen.getByRole('button', { name: 'switch-en' }));

    await waitFor(() => {
      expect(titleInput).toHaveValue('DSA notification test');
      expect(contentInput).toHaveValue('This is a test notification from the DSA Web settings page.');
    });

    fireEvent.click(screen.getByRole('button', { name: /Gửi thử|Send test/ }));
    await waitFor(() => expect(testNotificationChannel).toHaveBeenCalledWith(expect.objectContaining({
      title: 'DSA notification test',
      content: 'This is a test notification from the DSA Web settings page.',
      timeoutSeconds: 20,
    })));
  });

  it('preserves user-edited notification defaults when language switches', async () => {
    localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');

    const SwitchHarness = ({ children }: { children: ReactNode }) => {
      const { setLanguage } = useUiLanguage();
      return (
        <div>
          <button type="button" onClick={() => setLanguage('en')}>
            switch-en
          </button>
          {children}
        </div>
      );
    };

    render(
      <UiLanguageProvider>
        <SwitchHarness>
          <NotificationTestPanel
            items={[{ key: 'CUSTOM_WEBHOOK_URLS', value: 'https://example.com/hook?token=secret' }]}
            maskToken="******"
          />
        </SwitchHarness>
      </UiLanguageProvider>
    );

    const titleInput = screen.getByLabelText('Title');
    const contentInput = screen.getByLabelText('Body');

    fireEvent.change(titleInput, { target: { value: 'Tiêu đề tuỳ chỉnh' } });
    fireEvent.change(contentInput, { target: { value: 'Nội dung tuỳ chỉnh' } });

    fireEvent.click(screen.getByRole('button', { name: 'switch-en' }));
    expect(titleInput).toHaveValue('Tiêu đề tuỳ chỉnh');
    expect(contentInput).toHaveValue('Nội dung tuỳ chỉnh');
  });

  it('renders custom webhook partial failure attempts', async () => {
    testNotificationChannel.mockResolvedValueOnce({
      success: true,
      message: 'Kiểm thử thông báo Webhook tuỳ chỉnh thành công một phần (1/2)',
      errorCode: null,
      stage: 'notification_send',
      retryable: true,
      latencyMs: 35,
      attempts: [
        {
          channel: 'custom',
          success: false,
          message: 'HTTP 500',
          target: 'https://example.com/hook?token=***',
          errorCode: 'http_500',
          stage: 'notification_send',
          retryable: true,
          latencyMs: 12,
          httpStatus: 500,
        },
        {
          channel: 'custom',
          success: true,
          message: 'sent',
          target: 'https://example.com/second/***',
          errorCode: null,
          stage: 'notification_send',
          retryable: false,
          latencyMs: 23,
          httpStatus: 200,
        },
      ],
    });

    render(
      <NotificationTestPanel
        items={[{ key: 'CUSTOM_WEBHOOK_URLS', value: 'https://example.com/hook?token=secret' }]}
        maskToken="******"
      />,
    );

    fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'custom' } });
    fireEvent.click(screen.getByRole('button', { name: /Send test/ }));

    expect(await screen.findByText('Test succeeded')).toBeInTheDocument();
    expect(screen.getByText(/thành công một phần/)).toBeInTheDocument();
    expect(screen.getAllByText('HTTP 500').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('HTTP 200')).toBeInTheDocument();
    expect(screen.getByText('http_500')).toHaveClass('text-warning');
    expect(screen.getByText('https://example.com/hook?token=***')).toBeInTheDocument();
  });

  it('renders retryable timeout diagnostics', async () => {
    testNotificationChannel.mockResolvedValueOnce({
      success: false,
      message: 'Kiểm thử thông báo lỗi: timeout',
      errorCode: 'timeout',
      stage: 'notification_send',
      retryable: true,
      latencyMs: null,
      attempts: [
        {
          channel: 'discord',
          success: false,
          message: 'timeout',
          target: 'https://discord.example.com/api/webhooks/send?key=***',
          errorCode: 'timeout',
          stage: 'notification_send',
          retryable: true,
          latencyMs: null,
          httpStatus: null,
        },
      ],
    });

    render(
      <NotificationTestPanel
        items={[{ key: 'DISCORD_WEBHOOK_URL', value: 'https://discord.example.com/api/webhooks/send?key=secret' }]}
        maskToken="******"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Send test/ }));

    expect(await screen.findByText('Test failed')).toBeInTheDocument();
    const timeoutEntries = screen.getAllByText('timeout');
    expect(timeoutEntries[0]).toBeInTheDocument();
    expect(screen.getByText('https://discord.example.com/api/webhooks/send?key=***')).toBeInTheDocument();
    expect(timeoutEntries[0]).toHaveClass('text-warning');
  });
});
