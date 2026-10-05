import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { SidebarNav } from '../SidebarNav';
import { useAlertStore } from '../../../stores/alertStore';

const mockLogout = vi.fn().mockResolvedValue(undefined);
const mockGetScreeningStatus = vi.fn().mockResolvedValue({ enabled: false, available: false });
const mockThemeToggle = vi.fn(({ collapsed }: { collapsed?: boolean }) => (
  <button type="button">{collapsed ? '切换主题(折叠)' : 'Toggle theme'}</button>
));

const completionBadgeState = { value: true };

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: true,
    logout: mockLogout,
  }),
}));

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: completionBadgeState.value }),
}));

vi.mock('../../../api/screening', () => ({
  SCREENING_CONFIG_CHANGED_EVENT: 'screening-config-changed',
  SYSTEM_CONFIG_CHANGED_EVENT: 'dsa-system-config-changed',
  screeningApi: {
    getStatus: () => mockGetScreeningStatus(),
  },
}));

vi.mock('../../theme/ThemeToggle', () => ({
  ThemeToggle: (props: { collapsed?: boolean }) => mockThemeToggle(props),
}));

describe('SidebarNav', () => {
  it('hides the screening navigation item while Screening is disabled', () => {
    mockGetScreeningStatus.mockResolvedValueOnce({ enabled: false, available: true });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: 'Screening' })).not.toBeInTheDocument();
  });

  it('shows screening first when Screening is enabled', async () => {
    mockGetScreeningStatus.mockResolvedValueOnce({ enabled: true, available: true });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: 'Screening' })).toHaveAttribute('href', '/screening');
    const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(hrefs.slice(0, 3)).toEqual(['/screening', '/alerts', '/settings']);
  });

  it('refreshes the controlled screening entry after config changes', async () => {
    mockGetScreeningStatus
      .mockResolvedValueOnce({ enabled: false, available: true })
      .mockResolvedValueOnce({ enabled: true, available: true });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: 'Screening' })).not.toBeInTheDocument();
    window.dispatchEvent(new Event('screening-config-changed'));

    expect(await screen.findByRole('link', { name: 'Screening' })).toHaveAttribute('href', '/screening');
    await waitFor(() => expect(mockGetScreeningStatus.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('does not render the chat completion badge while the chat item is hidden', () => {
    completionBadgeState.value = true;

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId('chat-completion-badge')).not.toBeInTheDocument();
    completionBadgeState.value = false;
  });

  it('renders the collapsed theme toggle variant when the sidebar is collapsed', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav collapsed />
      </MemoryRouter>,
    );

    expect(mockThemeToggle).toHaveBeenCalledWith(
      expect.objectContaining({ variant: 'nav', collapsed: true }),
    );
    expect(screen.getByRole('button', { name: '切换主题(折叠)' })).toBeInTheDocument();
  });

  it('renders the alerts navigation item and marks it active', () => {
    render(
      <MemoryRouter initialEntries={['/alerts']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const alertsLink = screen.getByRole('link', { name: '告警' });
    expect(alertsLink).toHaveAttribute('href', '/alerts');
    expect(alertsLink).toHaveClass('font-medium');
  });

  it('temporarily hides the home, chat, portfolio, AI signals, backtest and usage navigation items', () => {
    render(
      <MemoryRouter initialEntries={['/decision-signals']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(hrefs).not.toContain('/portfolio');
    expect(hrefs).not.toContain('/decision-signals');
    expect(hrefs).not.toContain('/backtest');
    expect(hrefs).not.toContain('/');
    expect(hrefs).not.toContain('/chat');
    expect(hrefs).not.toContain('/usage');
  });

  it('renders the top variant as a horizontal menu bar with extra actions', () => {
    render(
      <MemoryRouter initialEntries={['/alerts']}>
        <SidebarNav variant="top" actions={<button type="button">extra-action</button>} />
      </MemoryRouter>,
    );

    const nav = screen.getByRole('navigation');
    expect(nav).toHaveClass('lg:flex');
    const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(hrefs).toEqual(['/alerts', '/settings']);
    expect(screen.getByRole('button', { name: 'extra-action' })).toBeInTheDocument();
  });

  it('opens the logout confirmation and confirms logout', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));

    expect(await screen.findByRole('heading', { name: 'Log out' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    expect(mockLogout).toHaveBeenCalled();
  });

  it('shows the unread realtime alert badge on the Alerts item', () => {
    useAlertStore.setState({ unreadCount: 3 });
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('alerts-unread-badge')).toHaveTextContent('3');
    useAlertStore.setState({ unreadCount: 0 });
  });
});
