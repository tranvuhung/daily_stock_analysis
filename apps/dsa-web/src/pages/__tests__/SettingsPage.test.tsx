import type React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resolveWebBuildInfo } from '../../utils/constants';
import type { SetupStatusResponse } from '../../types/systemConfig';
import { DesktopUpdateIndicator } from '../../components/layout/DesktopUpdateIndicator';
import { resetSharedDesktopUpdateState } from '../../desktop/updateStore';
import SettingsPage from '../SettingsPage';

const {
  analyzeAsync,
  exportEnv,
  getSchedulerStatus,
  getSetupStatus,
  importEnv,
  runSchedulerNow,
  updateSystemConfig,
  desktopCheckForUpdates,
  desktopGetUpdateState,
  desktopInstallDownloadedUpdate,
  desktopOnUpdateStateChange,
  desktopOpenReleasePage,
  load,
  clearToast,
  setActiveCategory,
  save,
  resetDraft,
  setDraftValue,
  applyPartialUpdate,
  getChangedItems,
  refreshAfterExternalSave,
  refreshStatus,
  settingsPanelErrorBoundary,
  useAuthMock,
  useSystemConfigMock,
  webBuildInfoMock,
} = vi.hoisted(() => ({
  analyzeAsync: vi.fn(),
  exportEnv: vi.fn(),
  getSchedulerStatus: vi.fn(),
  getSetupStatus: vi.fn(),
  importEnv: vi.fn(),
  runSchedulerNow: vi.fn(),
  updateSystemConfig: vi.fn(),
  desktopCheckForUpdates: vi.fn(),
  desktopGetUpdateState: vi.fn(),
  desktopInstallDownloadedUpdate: vi.fn(),
  desktopOnUpdateStateChange: vi.fn(),
  desktopOpenReleasePage: vi.fn(),
  load: vi.fn(),
  clearToast: vi.fn(),
  setActiveCategory: vi.fn(),
  save: vi.fn(),
  resetDraft: vi.fn(),
  setDraftValue: vi.fn(),
  applyPartialUpdate: vi.fn(),
  getChangedItems: vi.fn(),
  refreshAfterExternalSave: vi.fn(),
  refreshStatus: vi.fn(),
  settingsPanelErrorBoundary: vi.fn(),
  useAuthMock: vi.fn(),
  useSystemConfigMock: vi.fn(),
  webBuildInfoMock: {
    version: '3.11.0',
    rawVersion: '3.11.0',
    revision: 'abc123def456',
    buildId: 'build-20260329-021530Z',
    buildTime: '2026-03-29T02:15:30.000Z',
    isFallbackVersion: false,
  },
}));

const mockedAnchorClick = vi.fn();

vi.mock('../../hooks', async () => {
  const actual = await vi.importActual<typeof import('../../hooks')>('../../hooks');
  return {
    ...actual,
    useAuth: () => useAuthMock(),
    useSystemConfig: () => useSystemConfigMock(),
  };
});

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    exportEnv: (...args: unknown[]) => exportEnv(...args),
    getSchedulerStatus: (...args: unknown[]) => getSchedulerStatus(...args),
    getSetupStatus: (...args: unknown[]) => getSetupStatus(...args),
    importEnv: (...args: unknown[]) => importEnv(...args),
    runSchedulerNow: (...args: unknown[]) => runSchedulerNow(...args),
    update: (...args: unknown[]) => updateSystemConfig(...args),
  },
}));

vi.mock('../../api/analysis', () => ({
  analysisApi: {
    analyzeAsync: (...args: unknown[]) => analyzeAsync(...args),
  },
}));

vi.mock('../../utils/constants', async () => {
  const actual = await vi.importActual<typeof import('../../utils/constants')>('../../utils/constants');
  return {
    ...actual,
    WEB_BUILD_INFO: webBuildInfoMock,
  };
});

vi.mock('../../components/settings', () => ({
  AuthSettingsCard: () => <div>Authentication and login protection</div>,
  ChangePasswordCard: () => <div>Change password</div>,
  IntelligentImport: ({ onMerged }: { onMerged: (value: string) => void }) => (
    <button type="button" onClick={() => onMerged('VCB,SZ000002')}>
      merge stock list
    </button>
  ),
  LLMChannelEditor: ({
    items,
    onSaved,
    onDraftItemsChange,
  }: {
    items: Array<{ key: string; value: string }>;
    onSaved: (items: Array<{ key: string; value: string }>) => void;
    onDraftItemsChange?: (items: Array<{ key: string; value: string }>) => void;
  }) => (
    <div>
      <div data-testid="llm-channel-editor-items">{items.map((item) => item.key).join(',')}</div>
      <button
        type="button"
        onClick={() => onDraftItemsChange?.([
          { key: 'LLM_CHANNELS', value: 'draft,backup' },
          { key: 'LLM_DRAFT_API_SURFACE', value: 'responses' },
          { key: 'LITELLM_MODEL', value: 'openai/draft-model' },
          { key: 'GENERATION_BACKEND', value: 'codex_cli' },
        ])}
      >
        emit llm draft
      </button>
      <button
        type="button"
        onClick={() => onSaved([{ key: 'LLM_CHANNELS', value: 'primary,backup' }])}
      >
        save llm channels
      </button>
    </div>
  ),
  GenerationBackendStatusPanel: ({ items }: { items: Array<{ key: string; value: string }> }) => (
    <div data-testid="generation-backend-status-items">
      {items.map((item) => `${item.key}=${item.value}`).join('|')}
    </div>
  ),
  AgentBackendStatusPanel: ({
    items,
    selectedBackend,
    agentArch,
    onUseSingleAgent,
  }: {
    items: Array<{ key: string; value: string }>;
    selectedBackend: string;
    agentArch: string;
    onUseSingleAgent: () => void;
  }) => (
    <div data-testid="agent-backend-status-panel-mock">
      <span data-testid="agent-backend-status-items">
        {items.map((item) => `${item.key}=${item.value}`).join('|')}
      </span>
      <span>{selectedBackend}:{agentArch}</span>
      <button type="button" onClick={onUseSingleAgent}>Switch to Single Agent</button>
    </div>
  ),
  NotificationTestPanel: ({ items }: { items: Array<{ key: string; value: string }> }) => (
    <div>Notification test panel:{items.map((item) => item.key).join(',')}</div>
  ),
  SettingsAlert: ({
    title,
    message,
    actionLabel,
    onAction,
  }: {
    title: string;
    message: string;
    actionLabel?: string;
    onAction?: () => void;
  }) => (
    <div>
      {title}:{message}
      {actionLabel ? (
        <button type="button" onClick={onAction}>
          {actionLabel}
        </button>
      ) : null}
    </div>
  ),
  SettingsCategoryNav: ({
    categories,
    activeCategory,
    onSelect,
  }: {
    categories: Array<{ category: string; title: string }>;
    activeCategory: string;
    onSelect: (value: string) => void;
  }) => (
    <nav>
      {categories.map((category) => (
        <button
          key={category.category}
          type="button"
          aria-pressed={activeCategory === category.category}
          onClick={() => onSelect(category.category)}
        >
          {category.title}
        </button>
      ))}
    </nav>
  ),
  SettingsField: ({
    item,
    disabled,
    issues = [],
  }: {
    item: {
      key: string;
      schema?: {
        description?: string;
        options?: Array<string | { label: string; value: string }>;
      };
      };
    disabled?: boolean;
    issues?: Array<{ code: string; message: string }>;
  }) => (
    <div
      data-testid={`settings-field-${item.key}`}
      data-disabled={disabled ? 'true' : 'false'}
      data-issues={issues.map((issue) => issue.code).join(',')}
    >
      <div>{item.key}</div>
      {item.schema?.description ? <p>{item.schema.description}</p> : null}
      {item.schema?.options?.map((option) => {
        const label = typeof option === 'string' ? option : option.label;
        const value = typeof option === 'string' ? option : option.value;
        return <span key={`${item.key}-${value}`}>{label}</span>;
      })}
    </div>
  ),
  SettingsLoading: () => <div>loading</div>,
  SettingsPanelErrorBoundary: ({
    title,
    diagnosticHint,
    children,
  }: {
    title: string;
    diagnosticHint?: React.ReactNode;
    children: React.ReactNode;
  }) => {
    settingsPanelErrorBoundary(title);
    return (
      <>
        {diagnosticHint ? <div>{diagnosticHint}</div> : null}
        {children}
      </>
    );
  },
  SettingsSectionCard: ({
    title,
    description,
    children,
  }: {
    title: string;
    description?: string;
    children: React.ReactNode;
  }) => (
    <section>
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {children}
    </section>
  ),
}));

function createDesktopRuntime(overrides: Record<string, unknown> = {}) {
  return {
    version: '3.12.0',
    getUpdateState: desktopGetUpdateState,
    checkForUpdates: desktopCheckForUpdates,
    installDownloadedUpdate: desktopInstallDownloadedUpdate,
    openReleasePage: desktopOpenReleasePage,
    onUpdateStateChange: desktopOnUpdateStateChange,
    ...overrides,
  };
}

const baseCategories = [
  { category: 'system', title: 'System', description: 'System settings', displayOrder: 1, fields: [] },
  { category: 'base', title: 'Base', description: 'Base settings', displayOrder: 2, fields: [] },
  { category: 'ai_model', title: 'AI', description: 'Model settings', displayOrder: 3, fields: [] },
  { category: 'notification', title: 'Notification', description: 'Notification settings', displayOrder: 4, fields: [] },
  { category: 'agent', title: 'Agent', description: 'Agent settings', displayOrder: 5, fields: [] },
];

type ConfigState = {
  categories: Array<{ category: string; title: string; description: string; displayOrder: number; fields: [] }>;
  itemsByCategory: Record<string, Array<Record<string, unknown>>>;
  issueByKey: Record<string, unknown[]>;
  activeCategory: string;
  setActiveCategory: typeof setActiveCategory;
  hasDirty: boolean;
  dirtyCount: number;
  toast: null;
  clearToast: typeof clearToast;
  isLoading: boolean;
  isSaving: boolean;
  loadError: null;
  saveError: null;
  retryAction: null;
  load: typeof load;
  retry: ReturnType<typeof vi.fn>;
  save: typeof save;
  resetDraft: typeof resetDraft;
  setDraftValue: typeof setDraftValue;
  applyPartialUpdate: typeof applyPartialUpdate;
  getChangedItems: () => Array<{ key: string; value: string }>;
  refreshAfterExternalSave: typeof refreshAfterExternalSave;
  configVersion: string;
  maskToken: string;
};

type ConfigOverride = Partial<ConfigState>;

function buildSystemConfigState(overrides: ConfigOverride = {}) {
  return {
    categories: baseCategories,
    itemsByCategory: {
      system: [
        {
          key: 'ADMIN_AUTH_ENABLED',
          value: 'true',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'ADMIN_AUTH_ENABLED',
            category: 'system',
            dataType: 'boolean',
            uiControl: 'switch',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        },
      ],
      base: [
        {
          key: 'STOCK_LIST',
          value: 'SH600000',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'STOCK_LIST',
            category: 'base',
            dataType: 'string',
            uiControl: 'textarea',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        },
      ],
      ai_model: [
        {
          key: 'LLM_CHANNELS',
          value: 'primary',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'LLM_CHANNELS',
            category: 'ai_model',
            dataType: 'string',
            uiControl: 'textarea',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        },
      ],
      agent: [
        {
          key: 'AGENT_ORCHESTRATOR_TIMEOUT_S',
          value: '600',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'AGENT_ORCHESTRATOR_TIMEOUT_S',
            category: 'agent',
            dataType: 'integer',
            uiControl: 'number',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        },
      ],
      notification: [
        {
          key: 'TELEGRAM_BOT_TOKEN',
          value: '123:abc',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'TELEGRAM_BOT_TOKEN',
            category: 'notification',
            dataType: 'string',
            uiControl: 'password',
            isSensitive: true,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 2,
          },
        },
        {
          key: 'DISCORD_WEBHOOK_URL',
          value: 'https://discord.example.com/api/webhooks/hook',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'DISCORD_WEBHOOK_URL',
            category: 'notification',
            dataType: 'string',
            uiControl: 'password',
            isSensitive: true,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        },
      ],
    },
    issueByKey: {},
    activeCategory: 'system',
    setActiveCategory,
    hasDirty: false,
    dirtyCount: 0,
    toast: null,
    clearToast,
    isLoading: false,
    isSaving: false,
    loadError: null,
    saveError: null,
    retryAction: null,
    load,
    retry: vi.fn(),
    save,
    resetDraft,
    setDraftValue,
    applyPartialUpdate,
    getChangedItems: () => [],
    refreshAfterExternalSave,
    configVersion: 'v1',
    maskToken: '******',
    ...overrides,
  };
}

function buildAgentItem(
  key: string,
  value: string,
  displayOrder: number,
  uiControl: 'select' | 'number' | 'text' = 'text',
) {
  return {
    key,
    value,
    rawValueExists: true,
    isMasked: false,
    schema: {
      key,
      category: 'agent',
      dataType: uiControl === 'number' ? 'integer' : 'string',
      uiControl,
      isSensitive: false,
      isRequired: false,
      isEditable: true,
      options: uiControl === 'select' ? [] : [],
      validation: {},
      displayOrder,
    },
  };
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function renderSettingsPage(route = '/settings') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <SettingsPage />
    </MemoryRouter>,
  );
}

function renderDesktopUpdateEntries(route = '/settings') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <>
        <DesktopUpdateIndicator />
        <SettingsPage />
      </>
    </MemoryRouter>,
  );
}

function hangDesktopUpdateCheck() {
  let resolveCheck: ((value: unknown) => void) | undefined;
  desktopCheckForUpdates.mockImplementation(
    () => new Promise((resolve) => {
      resolveCheck = resolve;
    }),
  );
  return {
    async finish(value: unknown) {
      await act(async () => {
        resolveCheck?.(value);
      });
    },
  };
}

describe('SettingsPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    Object.assign(webBuildInfoMock, {
      version: '3.11.0',
      rawVersion: '3.11.0',
      revision: 'abc123def456',
      buildId: 'build-20260329-021530Z',
      buildTime: '2026-03-29T02:15:30.000Z',
      isFallbackVersion: false,
    });
    load.mockResolvedValue(true);
    exportEnv.mockResolvedValue({
      content: 'STOCK_LIST=HPG\n',
      configVersion: 'v1',
      updatedAt: '2026-03-21T00:00:00Z',
    });
    getSchedulerStatus.mockResolvedValue({
      enabled: true,
      running: false,
      scheduleTimes: ['09:20', '15:10'],
      nextRunAt: '2026-06-21T09:20:00+08:00',
      lastRunAt: null,
      lastSuccessAt: null,
      lastError: null,
    });
    getSetupStatus.mockResolvedValue({
      isComplete: true,
      readyForSmoke: true,
      requiredMissingKeys: [],
      nextStepKey: null,
      checks: [
        {
          key: 'stock_list',
          title: 'Watchlist',
          category: 'base',
          required: true,
          status: 'configured',
          message: 'Watchlist configured.',
          nextStep: null,
        },
        {
          key: 'llm_channels',
          title: 'Model channel',
          category: 'ai_model',
          required: true,
          status: 'configured',
          message: 'Model channel configured.',
          nextStep: null,
        },
        {
          key: 'notification',
          title: 'Notify',
          category: 'notification',
          required: false,
          status: 'optional',
          message: 'Notifications are optional.',
          nextStep: null,
        },
      ],
    });
    analyzeAsync.mockResolvedValue({
      taskId: 'task-setup-smoke',
      status: 'pending',
      message: 'accepted',
    });
    runSchedulerNow.mockResolvedValue({
      accepted: true,
      running: true,
    });
    importEnv.mockResolvedValue({
      success: true,
      configVersion: 'v2',
      appliedCount: 1,
      skippedMaskedCount: 0,
      reloadTriggered: true,
      updatedKeys: ['STOCK_LIST'],
      warnings: [],
    });
    updateSystemConfig.mockResolvedValue({
      success: true,
      configVersion: 'v2',
      updatedKeys: ['STOCK_LIST'],
      reloadTriggered: true,
    });
    desktopGetUpdateState.mockResolvedValue({
      status: 'idle',
      currentVersion: '3.12.0',
      latestVersion: '',
      message: '',
    });
    desktopCheckForUpdates.mockResolvedValue({
      status: 'up-to-date',
      currentVersion: '3.12.0',
      latestVersion: '3.12.0',
      message: 'The desktop app is already up to date.',
    });
    desktopInstallDownloadedUpdate.mockResolvedValue(true);
    desktopOpenReleasePage.mockResolvedValue(true);
    desktopOnUpdateStateChange.mockImplementation(() => () => undefined);
    useAuthMock.mockReturnValue({
      authEnabled: true,
      passwordChangeable: true,
      refreshStatus,
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState());
    delete (window as { dsaDesktop?: unknown }).dsaDesktop;
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(mockedAnchorClick);
    resetSharedDesktopUpdateState();
  });

  afterEach(() => {
    resetSharedDesktopUpdateState();
  });

  it('renders category navigation and auth settings modules', async () => {
    renderSettingsPage();

    expect(await screen.findByRole('heading', { name: 'System settings' })).toBeInTheDocument();
    expect(screen.getByText('Authentication and login protection')).toBeInTheDocument();
    expect(screen.getByText('Change password')).toBeInTheDocument();
    expect(load).toHaveBeenCalled();
  });

  it('renders first-run setup checks and routes setup actions', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));

    renderSettingsPage();

    expect(await screen.findByTestId('first-run-setup-card')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'First-run setup check' })).toBeInTheDocument();
    expect(screen.getByText('Watchlist')).toBeInTheDocument();
    expect(screen.getAllByText('Configured')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Configure models' }));
    fireEvent.click(screen.getByRole('button', { name: 'Manage watchlist' }));
    fireEvent.click(screen.getByRole('button', { name: 'Configure notifications' }));

    expect(setActiveCategory).toHaveBeenNthCalledWith(1, 'ai_model');
    expect(setActiveCategory).toHaveBeenNthCalledWith(2, 'base');
    expect(setActiveCategory).toHaveBeenNthCalledWith(3, 'notification');
  });

  it('applies category from the search string and scrolls to desktop version info', async () => {
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    (window as { dsaDesktop?: unknown }).dsaDesktop = {
      version: '3.12.0',
      getUpdateState: desktopGetUpdateState,
      checkForUpdates: desktopCheckForUpdates,
      installDownloadedUpdate: desktopInstallDownloadedUpdate,
      openReleasePage: desktopOpenReleasePage,
      onUpdateStateChange: desktopOnUpdateStateChange,
    };

    renderSettingsPage('/settings?category=system#desktop-version-info');

    await waitFor(() => expect(setActiveCategory).toHaveBeenCalledWith('system'));
    expect(await screen.findByText('Desktop update')).toBeInTheDocument();
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });

  it('keeps first-run setup summary neutral while setup status is loading', async () => {
    getSetupStatus.mockImplementation(() => new Promise(() => undefined));
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));

    renderSettingsPage();

    expect(await screen.findByText('Checking first-run setup')).toBeInTheDocument();
    expect(screen.getByText('Reading configuration status. Missing items and the smoke-run entry appear after the check completes.')).toBeInTheDocument();
    expect(screen.queryByText('Base setup is ready for minimal analysis')).not.toBeInTheDocument();
    expect(screen.queryByText('Some base setup still needs attention')).not.toBeInTheDocument();
    expect(screen.queryByText('All required items are ready. Run a brief analysis to verify the flow.')).not.toBeInTheDocument();
  });

  it('keeps first-run setup summary neutral when setup status fails', async () => {
    getSetupStatus.mockRejectedValue(new Error('setup status unavailable'));
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));

    renderSettingsPage();

    expect(await screen.findByText('Setup status is unavailable')).toBeInTheDocument();
    expect(screen.getByText('The setup status check failed. You can still review settings and refresh the check later.')).toBeInTheDocument();
    expect(screen.queryByText('Base setup is ready for minimal analysis')).not.toBeInTheDocument();
    expect(screen.queryByText('Some base setup still needs attention')).not.toBeInTheDocument();
    expect(screen.queryByText('All required items are ready. Run a brief analysis to verify the flow.')).not.toBeInTheDocument();
  });

  it('keeps the latest first-run setup status when refresh responses resolve out of order', async () => {
    const staleRefresh = createDeferred<SetupStatusResponse>();
    const latestRefresh = createDeferred<SetupStatusResponse>();
    const initialStatus: SetupStatusResponse = {
      isComplete: true,
      readyForSmoke: true,
      requiredMissingKeys: [],
      nextStepKey: null,
      checks: [
        {
          key: 'initial-status',
          title: 'Initial status',
          category: 'base',
          required: true,
          status: 'configured',
          message: 'Initial config status.',
          nextStep: null,
        },
      ],
    };
    const staleStatus: SetupStatusResponse = {
      isComplete: false,
      readyForSmoke: false,
      requiredMissingKeys: ['LLM_CHANNELS'],
      nextStepKey: 'LLM_CHANNELS',
      checks: [
        {
          key: 'stale-status',
          title: 'Stale status',
          category: 'ai_model',
          required: true,
          status: 'needs_action',
          message: 'Stale config status.',
          nextStep: 'This stale response must not override the latest status.',
        },
      ],
    };
    const latestStatus: SetupStatusResponse = {
      isComplete: true,
      readyForSmoke: true,
      requiredMissingKeys: [],
      nextStepKey: null,
      checks: [
        {
          key: 'latest-status',
          title: 'Latest status',
          category: 'base',
          required: true,
          status: 'configured',
          message: 'Latest config status.',
          nextStep: null,
        },
      ],
    };

    getSetupStatus
      .mockResolvedValueOnce(initialStatus)
      .mockImplementationOnce(() => staleRefresh.promise)
      .mockImplementationOnce(() => latestRefresh.promise);
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));

    renderSettingsPage();

    expect(await screen.findByText('Initial status')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Refresh check' }));
    fireEvent.click(screen.getByRole('button', { name: 'merge stock list' }));

    await waitFor(() => expect(getSetupStatus).toHaveBeenCalledTimes(3));

    await act(async () => {
      latestRefresh.resolve(latestStatus);
      await latestRefresh.promise;
    });

    expect(await screen.findByText('Latest status')).toBeInTheDocument();
    expect(screen.queryByText('Stale status')).not.toBeInTheDocument();

    await act(async () => {
      staleRefresh.resolve(staleStatus);
      await staleRefresh.promise;
    });

    await waitFor(() => expect(screen.getByText('Latest status')).toBeInTheDocument());
    expect(screen.queryByText('Stale status')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Brief smoke run' })).toBeEnabled();
  });

  it('runs a brief setup smoke analysis with the first watchlist stock', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));

    renderSettingsPage();

    await screen.findByText('Base setup is ready for minimal analysis');
    fireEvent.click(screen.getByRole('button', { name: 'Brief smoke run' }));

    await waitFor(() => expect(analyzeAsync).toHaveBeenCalledWith({
      stockCode: 'SH600000',
      reportType: 'brief',
      asyncMode: true,
      notify: false,
      originalQuery: 'SH600000',
      selectionSource: 'manual',
    }));
    expect(await screen.findByText(/task-setup-smoke/)).toBeInTheDocument();
  });

  it('allows brief setup smoke when only the Agent channel is incomplete', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));
    getSetupStatus.mockResolvedValue({
      isComplete: false,
      readyForSmoke: true,
      requiredMissingKeys: ['llm_agent'],
      nextStepKey: 'llm_agent',
      checks: [
        {
          key: 'llm_primary',
          title: 'Primary LLM channel',
          category: 'ai_model',
          required: true,
          status: 'configured',
          message: 'Claude Code CLI local generation backend enabled (experimental/limited).',
          nextStep: null,
        },
        {
          key: 'llm_agent',
          title: 'Agent channel',
          category: 'agent',
          required: true,
          status: 'needs_action',
          message: 'Agent tool calls require a LiteLLM model config; the local CLI generation mode is not inherited.',
          nextStep: 'Configure a LiteLLM model to use the Ask-Stock Agent.',
        },
        {
          key: 'stock_list',
          title: 'Watchlist',
          category: 'base',
          required: true,
          status: 'configured',
          message: '1 stock configured.',
          nextStep: null,
        },
      ],
    });

    renderSettingsPage();

    await screen.findByText('1 item(s) still need action: Agent channel');
    expect(screen.getByRole('button', { name: 'Brief smoke run' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Brief smoke run' }));

    await waitFor(() => expect(analyzeAsync).toHaveBeenCalledWith({
      stockCode: 'SH600000',
      reportType: 'brief',
      asyncMode: true,
      notify: false,
      originalQuery: 'SH600000',
      selectionSource: 'manual',
    }));
  });

  it('shows missing setup items and lets the user reopen the setup check', async () => {
    getSetupStatus.mockResolvedValue({
      isComplete: false,
      readyForSmoke: false,
      requiredMissingKeys: ['LLM_CHANNELS'],
      nextStepKey: 'LLM_CHANNELS',
      checks: [
        {
          key: 'llm_channels',
          title: 'Model channel',
          category: 'ai_model',
          required: true,
          status: 'needs_action',
          message: 'No model channel configured yet.',
          nextStep: 'Configure a model channel first.',
        },
      ],
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));

    renderSettingsPage();

    expect(await screen.findByText('Some base setup still needs attention')).toBeInTheDocument();
    expect(screen.getByText('1 item(s) still need action: Model channel')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Brief smoke run' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Hide for now' }));
    expect(screen.getByText('First-run setup check is hidden')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open check' }));
    expect(screen.getByText('First-run setup check')).toBeInTheDocument();
  });

  it('renders web build info in system settings', async () => {
    renderSettingsPage();

    expect(await screen.findByRole('heading', { name: 'Version info' })).toBeInTheDocument();
    expect(screen.getByText('3.11.0')).toBeInTheDocument();
    expect(screen.getByText('abc123def456')).toBeInTheDocument();
    expect(screen.getByText('2026-03-29T02:15:30.000Z')).toBeInTheDocument();
  });

  it('renders desktop app version in system settings during desktop runtime', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: '3.12.0' };

    renderSettingsPage();

    expect(await screen.findByRole('heading', { name: 'Version info' })).toBeInTheDocument();
    expect(screen.getByText('Desktop version')).toBeInTheDocument();
    expect(screen.getByText('3.12.0')).toBeInTheDocument();
  });

  it('keeps version grid at three columns when desktop runtime has no usable version', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: '   ' };

    renderSettingsPage();

    const section = (await screen.findByRole('heading', { name: 'Version info' })).closest('section');
    const versionGrid = section?.querySelector('div.grid.grid-cols-1.gap-3');

    expect(screen.queryByText('Desktop version')).not.toBeInTheDocument();
    expect(versionGrid).toHaveClass('md:grid-cols-3');
    expect(versionGrid).not.toHaveClass('md:grid-cols-4');
  });

  it('ignores non-string desktop runtime version values without breaking render', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: 3120 };

    renderSettingsPage();

    const section = (await screen.findByRole('heading', { name: 'Version info' })).closest('section');
    const versionGrid = section?.querySelector('div.grid.grid-cols-1.gap-3');

    expect(screen.queryByText('Desktop version')).not.toBeInTheDocument();
    expect(versionGrid).toHaveClass('md:grid-cols-3');
  });

  it('normalizes malformed desktop update payloads instead of throwing', async () => {
    desktopGetUpdateState.mockResolvedValue({
      status: 123,
      currentVersion: 3120,
      latestVersion: null,
      releaseUrl: { href: 'https://example.com' },
      checkedAt: ['2026-04-25T01:02:00Z'],
      message: false,
      releaseName: { text: 'v3.13.0' },
      tagName: undefined,
    });
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderSettingsPage();

    await waitFor(() => {
      expect(desktopGetUpdateState).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole('button', { name: 'Check for updates' })).toBeInTheDocument();
    expect(screen.queryByText('Update check failed')).not.toBeInTheDocument();
    expect(screen.queryByText('New version available')).not.toBeInTheDocument();
  });

  it('uses an explicit development label instead of presenting a build ID as the version', () => {
    expect(resolveWebBuildInfo({
      packageVersion: '0.0.0',
      revision: 'abc123def456',
      buildTimestamp: '2026-03-29T02:15:30.000Z',
    })).toEqual({
      version: 'development',
      rawVersion: '0.0.0',
      revision: 'abc123def456',
      buildId: 'build-20260329-021530Z',
      buildTime: '2026-03-29T02:15:30.000Z',
      isFallbackVersion: true,
    });
  });

  it('renders fallback version hint when package version is placeholder', async () => {
    Object.assign(webBuildInfoMock, {
      version: 'development',
      rawVersion: '0.0.0',
      revision: 'abc123def456',
      buildId: 'build-20260329-021530Z',
      buildTime: '2026-03-29T02:15:30.000Z',
      isFallbackVersion: true,
    });

    renderSettingsPage();

    expect(await screen.findByRole('heading', { name: 'Version info' })).toBeInTheDocument();
    expect(screen.getByText(/This build has no release version/)).toBeInTheDocument();
    expect(screen.getByText('development')).toBeInTheDocument();
    expect(screen.getByText('abc123def456')).toBeInTheDocument();
  });

  it('resets local drafts from the page header button', () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ hasDirty: true, dirtyCount: 2 }));

    renderSettingsPage();

    // Clear the initial load call from useEffect
    vi.clearAllMocks();

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));

    // Reset should call resetDraft and NOT call load
    expect(resetDraft).toHaveBeenCalledTimes(1);
    expect(load).not.toHaveBeenCalled();
  });

  it('shows deep research and event monitor fields in the agent category when available', () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'agent',
      itemsByCategory: {
        ...buildSystemConfigState().itemsByCategory,
        agent: [
          {
            key: 'AGENT_ORCHESTRATOR_TIMEOUT_S',
            value: '600',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'AGENT_ORCHESTRATOR_TIMEOUT_S',
              category: 'agent',
              dataType: 'integer',
              uiControl: 'number',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 1,
            },
          },
          {
            key: 'AGENT_DEEP_RESEARCH_BUDGET',
            value: '30000',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'AGENT_DEEP_RESEARCH_BUDGET',
              category: 'agent',
              dataType: 'integer',
              uiControl: 'number',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 2,
            },
          },
          {
            key: 'AGENT_EVENT_MONITOR_ENABLED',
            value: 'false',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'AGENT_EVENT_MONITOR_ENABLED',
              category: 'agent',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 3,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    expect(screen.getByText('AGENT_ORCHESTRATOR_TIMEOUT_S')).toBeInTheDocument();
    expect(screen.getByText('AGENT_DEEP_RESEARCH_BUDGET')).toBeInTheDocument();
    expect(screen.getByText('AGENT_EVENT_MONITOR_ENABLED')).toBeInTheDocument();
    expect(settingsPanelErrorBoundary).toHaveBeenCalledWith('Agent settings');
  });

  it('integrates one Agent backend selector and keeps Codex limits editable as unsaved draft actions', () => {
    const configState = buildSystemConfigState();
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'agent',
      hasDirty: true,
      dirtyCount: 2,
      getChangedItems: () => [
        { key: 'AGENT_BACKEND', value: 'codex_app_server' },
        { key: 'AGENT_ARCH', value: 'multi' },
      ],
      itemsByCategory: {
        ...configState.itemsByCategory,
        agent: [
          buildAgentItem('AGENT_BACKEND', 'codex_app_server', 1, 'select'),
          buildAgentItem('AGENT_GENERATION_BACKEND', 'auto', 2, 'select'),
          buildAgentItem('AGENT_LITELLM_MODEL', 'openai/gpt-4o-mini', 3),
          buildAgentItem('AGENT_MAX_STEPS', '10', 4, 'number'),
          buildAgentItem('AGENT_ARCH', 'multi', 5, 'select'),
          buildAgentItem('AGENT_ORCHESTRATOR_TIMEOUT_S', '600', 6, 'number'),
        ],
      },
    }));

    renderSettingsPage();

    expect(screen.getByTestId('settings-field-AGENT_BACKEND')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-AGENT_GENERATION_BACKEND')).not.toBeInTheDocument();
    expect(screen.getByTestId('settings-field-AGENT_MAX_STEPS')).toHaveAttribute('data-disabled', 'false');
    expect(screen.getByTestId('settings-field-AGENT_ARCH')).toHaveAttribute(
      'data-issues',
      'unsupported_agent_arch',
    );
    expect(screen.getByTestId('agent-backend-status-items')).toHaveTextContent(
      'AGENT_BACKEND=codex_app_server|AGENT_ARCH=multi',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Switch to Single Agent' }));
    expect(setDraftValue).toHaveBeenCalledWith('AGENT_ARCH', 'single');
    expect(save).not.toHaveBeenCalled();
  });

  it('renders context compression profile labels and blank preset guidance in agent settings', () => {
    const configState = buildSystemConfigState();
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'agent',
      itemsByCategory: {
        ...configState.itemsByCategory,
        agent: [
          {
            key: 'AGENT_CONTEXT_COMPRESSION_PROFILE',
            value: 'balanced',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'AGENT_CONTEXT_COMPRESSION_PROFILE',
              category: 'agent',
              dataType: 'string',
              uiControl: 'select',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [
                { label: 'Cost first', value: 'cost' },
                { label: 'Balanced (recommended)', value: 'balanced' },
                { label: 'Long-context raw first', value: 'long_context_raw_first' },
              ],
              validation: {
                enum: ['cost', 'balanced', 'long_context_raw_first'],
              },
              displayOrder: 72,
            },
          },
          {
            key: 'AGENT_CONTEXT_COMPRESSION_TRIGGER_TOKENS',
            value: '',
            rawValueExists: false,
            isMasked: false,
            schema: {
              key: 'AGENT_CONTEXT_COMPRESSION_TRIGGER_TOKENS',
              category: 'agent',
              dataType: 'integer',
              uiControl: 'number',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: { min: 1000 },
              displayOrder: 73,
              description: 'Summarize when estimated history tokens exceed this value; leave empty to follow the compression profile default.',
            },
          },
          {
            key: 'AGENT_CONTEXT_PROTECTED_TURNS',
            value: '',
            rawValueExists: false,
            isMasked: false,
            schema: {
              key: 'AGENT_CONTEXT_PROTECTED_TURNS',
              category: 'agent',
              dataType: 'integer',
              uiControl: 'number',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: { min: 1 },
              displayOrder: 74,
              description: 'Keep the latest N user turns and replies verbatim when compressing; leave empty to follow the compression profile default.',
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    expect(screen.getByText('AGENT_CONTEXT_COMPRESSION_PROFILE')).toBeInTheDocument();
    expect(screen.getByText('Cost first')).toBeInTheDocument();
    expect(screen.getByText('Balanced (recommended)')).toBeInTheDocument();
    expect(screen.getByText('Long-context raw first')).toBeInTheDocument();
    expect(screen.getByText(/Summarize when estimated history tokens exceed this value/)).toHaveTextContent('leave empty to follow the compression profile default');
    expect(screen.getByText(/Keep the latest N user turns and replies verbatim/)).toHaveTextContent('leave empty to follow the compression profile default');
  });

  it('reset button semantic: discards local changes without network request', () => {
    // Simulate user has unsaved drafts
    const dirtyState = buildSystemConfigState({
      hasDirty: true,
      dirtyCount: 2,
    });

    useSystemConfigMock.mockReturnValue(dirtyState);

    renderSettingsPage();

    // Clear initial useEffect load call
    vi.clearAllMocks();

    // Click reset button
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));

    // Verify semantic: reset should only discard local changes
    // It should NOT trigger a network load
    expect(resetDraft).toHaveBeenCalledTimes(1);
    expect(load).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
  });

  it('refreshes server state after intelligent import merges stock list', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'base' }));

    renderSettingsPage();

    fireEvent.click(screen.getByRole('button', { name: 'merge stock list' }));

    expect(refreshAfterExternalSave).toHaveBeenCalledWith(['STOCK_LIST']);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('refreshes server state after llm channel editor saves', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'ai_model' }));

    renderSettingsPage();

    fireEvent.click(screen.getByRole('button', { name: 'save llm channels' }));

    expect(refreshAfterExternalSave).toHaveBeenCalledWith(['LLM_CHANNELS']);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('passes merged generation backend draft items to the backend status panel', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'ai_model',
      getChangedItems: () => [
        { key: 'GENERATION_BACKEND', value: 'litellm' },
        { key: 'LLM_CHANNELS', value: 'saved' },
        { key: 'OPENAI_MODEL', value: 'gpt-draft' },
        { key: 'GEMINI_MODEL', value: 'gemini-draft' },
        { key: 'OLLAMA_API_BASE', value: 'http://localhost:11434' },
        { key: 'DISCORD_WEBHOOK_URL', value: 'not-a-url' },
      ],
    }));

    renderSettingsPage();

    fireEvent.click(screen.getByRole('button', { name: 'emit llm draft' }));

    const statusItems = await screen.findByTestId('generation-backend-status-items');
    await waitFor(() => {
      expect(statusItems).toHaveTextContent('GENERATION_BACKEND=litellm');
      expect(statusItems).toHaveTextContent('LLM_CHANNELS=draft,backup');
      expect(statusItems).toHaveTextContent('LLM_DRAFT_API_SURFACE=responses');
      expect(statusItems).toHaveTextContent('LITELLM_MODEL=openai/draft-model');
      expect(statusItems).toHaveTextContent('OPENAI_MODEL=gpt-draft');
      expect(statusItems).toHaveTextContent('GEMINI_MODEL=gemini-draft');
      expect(statusItems).toHaveTextContent('OLLAMA_API_BASE=http://localhost:11434');
      expect(statusItems).not.toHaveTextContent('GENERATION_BACKEND=codex_cli');
      expect(statusItems).not.toHaveTextContent('DISCORD_WEBHOOK_URL=not-a-url');
    });
  });

  it('clears llm channel draft items after llm channel editor saves', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'ai_model' }));

    renderSettingsPage();

    fireEvent.click(screen.getByRole('button', { name: 'emit llm draft' }));
    expect(await screen.findByTestId('generation-backend-status-items')).toHaveTextContent('LLM_CHANNELS=draft,backup');

    fireEvent.click(screen.getByRole('button', { name: 'save llm channels' }));

    await waitFor(() => {
      expect(screen.getByTestId('generation-backend-status-items')).not.toHaveTextContent('LLM_CHANNELS=draft,backup');
    });
    expect(refreshAfterExternalSave).toHaveBeenCalledWith(['LLM_CHANNELS']);
  });

  it('keeps prompt cache settings collapsed and expandable at the bottom of AI model settings', () => {
    const aiField = (key: string, displayOrder: number, value = '') => ({
      key,
      value,
      rawValueExists: Boolean(value),
      isMasked: false,
      schema: {
        key,
        category: 'ai_model',
        dataType: 'string',
        uiControl: key === 'LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL' ? 'select' : 'text',
        isSensitive: false,
        isRequired: false,
        isEditable: true,
        options: key === 'LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL' ? ['off', 'basic', 'debug'] : [],
        validation: {},
        displayOrder,
      },
    });
    const configState = buildSystemConfigState();
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'ai_model',
      itemsByCategory: {
        ...configState.itemsByCategory,
        ai_model: [
          aiField('LITELLM_CONFIG', 10, './litellm.yaml'),
          aiField('LLM_PROMPT_CACHE_TELEMETRY_ENABLED', 20, 'true'),
          aiField('LLM_PROMPT_CACHE_HINTS_ENABLED', 21, 'false'),
          aiField('LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL', 22, 'off'),
        ],
      },
    }));

    const { container } = renderSettingsPage();

    const promptCacheSummary = screen.getByText('Provider Prompt Cache advanced settings').closest('summary');
    const promptCacheDetails = promptCacheSummary?.closest('details');
    const telemetryField = screen.getByTestId('settings-field-LLM_PROMPT_CACHE_TELEMETRY_ENABLED');
    const hintsField = screen.getByTestId('settings-field-LLM_PROMPT_CACHE_HINTS_ENABLED');
    const diagnosticsField = screen.getByTestId('settings-field-LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL');

    expect(promptCacheSummary).toBeInTheDocument();
    expect(promptCacheDetails).toBeInTheDocument();
    expect(promptCacheDetails).not.toHaveAttribute('open');
    expect(promptCacheDetails).toContainElement(telemetryField);
    expect(promptCacheDetails).toContainElement(hintsField);
    expect(promptCacheDetails).toContainElement(diagnosticsField);
    expect(telemetryField).not.toBeVisible();
    expect(hintsField).not.toBeVisible();
    expect(diagnosticsField).not.toBeVisible();

    fireEvent.click(promptCacheSummary as HTMLElement);

    expect(promptCacheDetails).toHaveAttribute('open');
    expect(telemetryField).toBeVisible();
    expect(hintsField).toBeVisible();
    expect(diagnosticsField).toBeVisible();

    expect(Array.from(container.querySelectorAll('[data-testid^="settings-field-"]')).map((node) => node.getAttribute('data-testid'))).toEqual([
      'settings-field-LITELLM_CONFIG',
      'settings-field-LLM_PROMPT_CACHE_TELEMETRY_ENABLED',
      'settings-field-LLM_PROMPT_CACHE_HINTS_ENABLED',
      'settings-field-LLM_PROMPT_CACHE_DIAGNOSTICS_LEVEL',
    ]);
  });

  it('maps schedule settings to the scheduler card instead of generic raw fields', async () => {
    const configState = buildSystemConfigState();
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'true',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIME',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIME',
              category: 'system',
              dataType: 'time',
              uiControl: 'time',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 10,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '09:20,15:10',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
          {
            key: 'SCHEDULE_RUN_IMMEDIATELY',
            value: 'false',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_RUN_IMMEDIATELY',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 12,
            },
          },
          {
            key: 'LOG_LEVEL',
            value: 'INFO',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'LOG_LEVEL',
              category: 'system',
              dataType: 'string',
              uiControl: 'select',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: ['INFO', 'DEBUG'],
              validation: {},
              displayOrder: 50,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    expect(await screen.findByTestId('scheduler-settings-card')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-SCHEDULE_ENABLED')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-SCHEDULE_TIME')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-SCHEDULE_TIMES')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-SCHEDULE_RUN_IMMEDIATELY')).not.toBeInTheDocument();
    expect(screen.getByTestId('settings-field-LOG_LEVEL')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('scheduler-time-input-0'), {
      target: { value: '10:30' },
    });

    expect(setDraftValue).toHaveBeenCalledWith('SCHEDULE_TIMES', '10:30,15:10');

    fireEvent.click(screen.getByTestId('scheduler-run-now-button'));

    await waitFor(() => expect(runSchedulerNow).toHaveBeenCalledTimes(1));
  });

  it('shows an error when run-now is rejected because analysis is already running', async () => {
    runSchedulerNow.mockRejectedValueOnce(new Error('A scheduled analysis is already running'));
    const configState = buildSystemConfigState();
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'true',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    fireEvent.click(await screen.findByTestId('scheduler-run-now-button'));

    await waitFor(() => expect(runSchedulerNow).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/A scheduled analysis is already running/)).toBeInTheDocument();
  });

  it('does not show a failed run as the last successful scheduler run', async () => {
    const configState = buildSystemConfigState();
    getSchedulerStatus.mockResolvedValueOnce({
      enabled: true,
      running: false,
      scheduleTimes: ['18:00'],
      nextRunAt: null,
      lastRunAt: '2026-06-21T17:00:00+08:00',
      lastSuccessAt: null,
      lastError: 'analysis failed',
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'true',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    expect(await screen.findByTestId('scheduler-last-success')).toHaveTextContent('-');
    expect(screen.getByTestId('scheduler-last-error')).toHaveTextContent('analysis failed');
  });

  it('shows active runtime scheduler state even when saved schedule flag is false', async () => {
    const configState = buildSystemConfigState();
    getSchedulerStatus.mockResolvedValueOnce({
      enabled: true,
      running: false,
      scheduleTimes: ['18:00'],
      nextRunAt: null,
      lastRunAt: null,
      lastSuccessAt: null,
      lastError: null,
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'false',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    const enabledCheckbox = await screen.findByTestId('scheduler-enabled-checkbox');
    expect(enabledCheckbox).toBeChecked();

    fireEvent.click(enabledCheckbox);

    expect(setDraftValue).toHaveBeenCalledWith('SCHEDULE_ENABLED', 'false');
    await waitFor(() => expect(enabledCheckbox).not.toBeChecked());
  });

  it('keeps local scheduler toggle edits when runtime and saved states are initially consistent', async () => {
    const configState = buildSystemConfigState();
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'true',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));
    renderSettingsPage();

    const enabledCheckbox = await screen.findByTestId('scheduler-enabled-checkbox');
    expect(enabledCheckbox).toBeChecked();

    fireEvent.click(enabledCheckbox);

    expect(setDraftValue).toHaveBeenCalledWith('SCHEDULE_ENABLED', 'false');
    await waitFor(() => expect(screen.getByTestId('scheduler-enabled-checkbox')).not.toBeChecked());

    const refreshButton = screen.getByTestId('scheduler-refresh-status-button');
    fireEvent.click(refreshButton);
    await waitFor(() => expect(screen.getByTestId('scheduler-enabled-checkbox')).not.toBeChecked());
  });

  it('can reconcile runtime scheduler state when runtime is enabled but saved value is disabled', async () => {
    save.mockResolvedValue({ success: true });
    getChangedItems.mockReturnValue([]);
    const configState = buildSystemConfigState();
    getSchedulerStatus.mockResolvedValueOnce({
      enabled: true,
      running: false,
      scheduleTimes: ['18:00'],
      nextRunAt: null,
      lastRunAt: null,
      lastSuccessAt: null,
      lastError: null,
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      hasDirty: false,
      dirtyCount: 0,
      getChangedItems: () => [],
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'false',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    const saveButton = screen.getByRole('button', { name: /Save configuration/ });
    expect(saveButton).toBeDisabled();

    const enabledCheckbox = await screen.findByTestId('scheduler-enabled-checkbox');
    expect(enabledCheckbox).toBeChecked();
    fireEvent.click(enabledCheckbox);

    await waitFor(() => expect(enabledCheckbox).not.toBeChecked());
    await waitFor(() => expect(saveButton).toBeEnabled());
    await waitFor(() => expect(saveButton).toHaveTextContent('Save configuration (1)'));

    fireEvent.click(saveButton);
    await waitFor(() => expect(save).toHaveBeenCalledWith([{ key: 'SCHEDULE_ENABLED', value: 'false' }]));
  });

  it('can reconcile runtime scheduler state when runtime is disabled but saved value is enabled', async () => {
    save.mockResolvedValue({ success: true });
    getChangedItems.mockReturnValue([]);
    const configState = buildSystemConfigState();
    getSchedulerStatus.mockResolvedValueOnce({
      enabled: false,
      running: false,
      scheduleTimes: ['18:00'],
      nextRunAt: null,
      lastRunAt: null,
      lastSuccessAt: null,
      lastError: null,
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      hasDirty: false,
      dirtyCount: 0,
      getChangedItems: () => [],
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'true',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    const saveButton = screen.getByRole('button', { name: /Save configuration/ });
    expect(saveButton).toBeDisabled();

    const enabledCheckbox = await screen.findByTestId('scheduler-enabled-checkbox');
    expect(enabledCheckbox).not.toBeChecked();
    fireEvent.click(enabledCheckbox);

    await waitFor(() => expect(enabledCheckbox).toBeChecked());
    await waitFor(() => expect(saveButton).toBeEnabled());
    await waitFor(() => expect(saveButton).toHaveTextContent('Save configuration (1)'));

    fireEvent.click(saveButton);
    await waitFor(() => expect(save).toHaveBeenCalledWith([{ key: 'SCHEDULE_ENABLED', value: 'true' }]));
  });

  it('refreshes scheduler status after saving scheduler settings', async () => {
    const configState = buildSystemConfigState();
    getSchedulerStatus
      .mockResolvedValueOnce({
        enabled: false,
        running: false,
        scheduleTimes: [],
        nextRunAt: null,
        lastRunAt: null,
        lastSuccessAt: null,
        lastError: null,
      })
      .mockResolvedValueOnce({
        enabled: true,
        running: false,
        scheduleTimes: ['09:20', '15:10'],
        nextRunAt: '2026-06-21T09:20:00+08:00',
        lastRunAt: null,
        lastSuccessAt: null,
        lastError: null,
      });
    save.mockResolvedValue({ success: true });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      hasDirty: true,
      dirtyCount: 1,
      getChangedItems: () => [{ key: 'SCHEDULE_ENABLED', value: 'true' }],
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'false',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '09:20,15:10',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    expect(await screen.findByText('Disabled')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Save configuration (1)' }));

    await waitFor(() => expect(getSchedulerStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Enabled')).toBeInTheDocument();
  });

  it('passes LLM channel support keys to the channel editor without rendering them as generic fields', async () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'ai_model',
      itemsByCategory: {
        ...buildSystemConfigState().itemsByCategory,
        ai_model: [
          {
            key: 'LLM_CHANNELS',
            value: 'my_proxy',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'LLM_CHANNELS',
              category: 'ai_model',
              dataType: 'string',
              uiControl: 'textarea',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 1,
            },
          },
          {
            key: 'LITELLM_MODEL',
            value: 'gpt-5.0',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'LITELLM_MODEL',
              category: 'ai_model',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 2,
            },
          },
          {
            key: 'OPENAI_BASE_URL',
            value: 'https://api.openai.com/v1',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'OPENAI_BASE_URL',
              category: 'ai_model',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 3,
            },
          },
          {
            key: 'OPENAI_MODEL',
            value: 'gpt-5.0',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'OPENAI_MODEL',
              category: 'ai_model',
              isMasked: false,
              dataType: 'string',
              uiControl: 'text',
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 4,
            },
          },
          {
            key: 'LLM_MY_PROXY_API_KEY',
            value: 'sk-test',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'LLM_MY_PROXY_API_KEY',
              category: 'ai_model',
              dataType: 'string',
              uiControl: 'password',
              isSensitive: true,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 9000,
            },
          },
          {
            key: 'LLM_MY_PROXY_MODELS',
            value: 'gpt-5.5',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'LLM_MY_PROXY_MODELS',
              category: 'ai_model',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 9000,
            },
          },
        ],
      },
    }));

    renderSettingsPage();

    const llmEditorItems = await screen.findByTestId('llm-channel-editor-items');
    expect(llmEditorItems).toHaveTextContent('LLM_CHANNELS');
    expect(llmEditorItems).toHaveTextContent('LITELLM_MODEL');
    expect(llmEditorItems).toHaveTextContent('OPENAI_BASE_URL');
    expect(llmEditorItems).toHaveTextContent('OPENAI_MODEL');
    expect(llmEditorItems).toHaveTextContent('LLM_MY_PROXY_API_KEY');
    expect(llmEditorItems).toHaveTextContent('LLM_MY_PROXY_MODELS');
    expect(screen.queryByTestId('settings-field-LITELLM_MODEL')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-OPENAI_BASE_URL')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-OPENAI_MODEL')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-LLM_MY_PROXY_API_KEY')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-field-LLM_MY_PROXY_MODELS')).not.toBeInTheDocument();
  });

  it('renders notification test panel before notification fields', () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'notification' }));

    renderSettingsPage();

    expect(screen.getByText('Notification test panel:TELEGRAM_BOT_TOKEN,DISCORD_WEBHOOK_URL')).toBeInTheDocument();
    expect(screen.getByTestId('settings-field-TELEGRAM_BOT_TOKEN')).toBeInTheDocument();
    expect(settingsPanelErrorBoundary).toHaveBeenCalledWith('Notification test');
    expect(settingsPanelErrorBoundary).toHaveBeenCalledWith('Notification settings');
  });

  it('hides unused notification channel fields and routing options', () => {
    const buildItem = (key: string, value: string, options: string[] = []) => ({
      key,
      value,
      rawValueExists: true,
      isMasked: false,
      schema: {
        key,
        category: 'notification',
        dataType: options.length ? 'array' : 'string',
        uiControl: options.length ? 'multiselect' : 'text',
        isSensitive: false,
        isRequired: false,
        isEditable: true,
        options,
        validation: {},
        displayOrder: 1,
      },
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'notification',
      itemsByCategory: {
        notification: [
          buildItem('PUSHOVER_USER_KEY', 'po'),
          buildItem('NTFY_URL', 'https://ntfy.sh/topic'),
          buildItem('GOTIFY_URL', 'https://gotify.example.com'),
          buildItem('TELEGRAM_BOT_TOKEN', '123:abc'),
          buildItem('NOTIFICATION_REPORT_CHANNELS', 'telegram', ['pushover', 'ntfy', 'gotify', 'telegram', 'email']),
        ],
      },
    }));

    renderSettingsPage();

    for (const key of ['PUSHOVER_USER_KEY', 'NTFY_URL', 'GOTIFY_URL']) {
      expect(screen.queryByTestId(`settings-field-${key}`)).not.toBeInTheDocument();
    }
    expect(screen.getByTestId('settings-field-TELEGRAM_BOT_TOKEN')).toBeInTheDocument();
    const routingField = screen.getByTestId('settings-field-NOTIFICATION_REPORT_CHANNELS');
    expect(routingField).toHaveTextContent('telegram');
    expect(routingField).toHaveTextContent('email');
    for (const channel of ['pushover', 'ntfy', 'gotify']) {
      expect(routingField).not.toHaveTextContent(channel);
    }
  });

  it('uses browser and backend logs in settings panel diagnostic hints outside desktop runtime', () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'notification' }));

    renderSettingsPage();

    expect(screen.getAllByText(/browser developer console and backend logs/)).toHaveLength(2);
    expect(screen.queryByText('desktop.log')).not.toBeInTheDocument();
  });

  it('uses desktop log in settings panel diagnostic hints during desktop runtime', () => {
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ activeCategory: 'notification' }));
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderSettingsPage();

    expect(screen.getAllByText('desktop.log')).toHaveLength(2);
    expect(screen.queryByText(/browser developer console and backend logs/)).not.toBeInTheDocument();
  });

  it('renders env backup actions outside desktop runtime', () => {
    renderSettingsPage();

    expect(screen.getByRole('heading', { name: 'Configuration backup' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Export .env' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import .env' })).toBeInTheDocument();
    expect(screen.getByText(/In Docker deployments/)).toHaveTextContent('ENV_FILE');
  });

  it('disables env backup actions when web auth is not enabled', () => {
    useAuthMock.mockReturnValue({
      authEnabled: false,
      passwordChangeable: false,
      refreshStatus,
    });

    renderSettingsPage();

    expect(screen.getByText(/not protected by admin authentication/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Export .env' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Import .env' })).toBeDisabled();
  });

  it('uses live auth state for env backup availability instead of loaded config items', () => {
    const configState = buildSystemConfigState();
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: configState.itemsByCategory.system.map((item) => (
          item.key === 'ADMIN_AUTH_ENABLED' ? { ...item, value: 'false' } : item
        )),
      },
    }));
    useAuthMock.mockReturnValue({
      authEnabled: true,
      passwordChangeable: true,
      refreshStatus,
    });

    renderSettingsPage();

    expect(screen.queryByText(/not protected by admin authentication/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Export .env' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Import .env' })).not.toBeDisabled();
  });

  it('exports saved env from config backup actions', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: '3.12.0' };

    renderSettingsPage();

    vi.clearAllMocks();

    fireEvent.click(screen.getByRole('button', { name: 'Export .env' }));

    await waitFor(() => expect(exportEnv).toHaveBeenCalledTimes(1));
    expect(mockedAnchorClick).toHaveBeenCalledTimes(1);
    expect(load).not.toHaveBeenCalled();
  });

  it('asks for confirmation before importing when local drafts exist', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: '3.12.0' };
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({ hasDirty: true, dirtyCount: 2 }));

    renderSettingsPage();

    vi.clearAllMocks();

    fireEvent.click(screen.getByRole('button', { name: 'Import .env' }));

    expect(await screen.findByText('Import overwrites current drafts')).toBeInTheDocument();
    expect(importEnv).not.toHaveBeenCalled();
  });

  it('reloads config after successful env import', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: '3.12.0' };

    const { container } = renderSettingsPage();

    vi.clearAllMocks();

    const input = container.querySelector('input[type="file"]');
    expect(input).not.toBeNull();

    fireEvent.change(input as HTMLInputElement, {
      target: {
        files: [new File(['STOCK_LIST=300750\n'], 'desktop-backup.env', { type: 'text/plain' })],
      },
    });

    await waitFor(() => expect(importEnv).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  });

  it('refreshes scheduler status after successful env import updates scheduler settings', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: '3.12.0' };
    const configState = buildSystemConfigState();
    getSchedulerStatus
      .mockResolvedValueOnce({
        enabled: false,
        running: false,
        scheduleTimes: ['18:00'],
        nextRunAt: null,
        lastRunAt: null,
        lastSuccessAt: null,
        lastError: null,
      })
      .mockResolvedValueOnce({
        enabled: true,
        running: false,
        scheduleTimes: ['09:20', '15:10'],
        nextRunAt: '2026-06-21T09:20:00+08:00',
        lastRunAt: null,
        lastSuccessAt: null,
        lastError: null,
      });
    importEnv.mockResolvedValueOnce({
      success: true,
      configVersion: 'v2',
      appliedCount: 2,
      skippedMaskedCount: 0,
      reloadTriggered: true,
      updatedKeys: ['SCHEDULE_ENABLED', 'SCHEDULE_TIMES'],
      warnings: [],
    });
    useSystemConfigMock.mockReturnValue(buildSystemConfigState({
      activeCategory: 'system',
      itemsByCategory: {
        ...configState.itemsByCategory,
        system: [
          ...configState.itemsByCategory.system,
          {
            key: 'SCHEDULE_ENABLED',
            value: 'false',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_ENABLED',
              category: 'system',
              dataType: 'boolean',
              uiControl: 'switch',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 8,
            },
          },
          {
            key: 'SCHEDULE_TIMES',
            value: '18:00',
            rawValueExists: true,
            isMasked: false,
            schema: {
              key: 'SCHEDULE_TIMES',
              category: 'system',
              dataType: 'string',
              uiControl: 'text',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [],
              validation: {},
              displayOrder: 11,
            },
          },
        ],
      },
    }));

    const { container } = renderSettingsPage();

    await waitFor(() => expect(getSchedulerStatus).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Disabled')).toBeInTheDocument();

    vi.clearAllMocks();

    const input = container.querySelector('input[type="file"]');
    expect(input).not.toBeNull();

    fireEvent.change(input as HTMLInputElement, {
      target: {
        files: [new File(['SCHEDULE_ENABLED=true\nSCHEDULE_TIMES=09:20,15:10\n'], 'desktop-backup.env', { type: 'text/plain' })],
      },
    });

    await waitFor(() => expect(importEnv).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getSchedulerStatus).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Enabled')).toBeInTheDocument();
  });

  it('shows an error when env import succeeds but reload fails', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = { version: '3.12.0' };
    load.mockResolvedValue(false);

    const { container } = renderSettingsPage();

    vi.clearAllMocks();
    load.mockResolvedValue(false);

    const input = container.querySelector('input[type="file"]');
    expect(input).not.toBeNull();

    fireEvent.change(input as HTMLInputElement, {
      target: {
        files: [new File(['STOCK_LIST=300750\n'], 'desktop-backup.env', { type: 'text/plain' })],
      },
    });

    await waitFor(() => expect(importEnv).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    expect(screen.getByText('Config imported but refresh failed')).toBeInTheDocument();
    expect(screen.getByText('The backup was imported, but configuration reload failed. Reload the page manually.')).toBeInTheDocument();
    expect(screen.queryByText('.env backup imported and configuration reloaded.')).not.toBeInTheDocument();
  });

  it('renders desktop update notice when a newer release is available', async () => {
    desktopGetUpdateState.mockResolvedValue({
      status: 'update-available',
      currentVersion: '3.12.0',
      latestVersion: '3.13.0',
      releaseUrl: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0',
      message: 'Version 3.13.0 is available on GitHub Releases.',
    });
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderSettingsPage();

    expect(await screen.findByText(/New version available:Current 3\.12\.0, latest 3\.13\.0/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open release' })).toBeInTheDocument();
  });

  it('checks desktop updates on demand and renders the latest-version state', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderSettingsPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Check for updates' }));

    await waitFor(() => expect(desktopCheckForUpdates).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Up to date:The desktop app is already up to date.')).toBeInTheDocument();
  });

  it('opens GitHub release page from desktop update notice', async () => {
    desktopGetUpdateState.mockResolvedValue({
      status: 'update-available',
      currentVersion: '3.12.0',
      latestVersion: '3.13.0',
      releaseUrl: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0',
      message: 'Version 3.13.0 is available on GitHub Releases.',
    });
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderSettingsPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Open release' }));

    await waitFor(() => {
      expect(desktopOpenReleasePage).toHaveBeenCalledWith(
        'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0'
      );
    });
  });

  it('renders downloaded desktop update and starts install on demand', async () => {
    desktopGetUpdateState.mockResolvedValue({
      status: 'update-downloaded',
      updateMode: 'auto',
      currentVersion: '3.12.0',
      latestVersion: '3.13.0',
      releaseUrl: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0',
      message: 'Version 3.13.0 has been downloaded. Restart the app to install it.',
      downloadPercent: 100,
    });
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderSettingsPage();

    expect(await screen.findByText('Update downloaded:Version 3.13.0 has been downloaded. Restart the app to install it.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Restart to install' }));

    await waitFor(() => expect(desktopInstallDownloadedUpdate).toHaveBeenCalledTimes(1));
  });

  it('disables the settings check button while the header entry is already checking', async () => {
    const pendingCheck = hangDesktopUpdateCheck();
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderDesktopUpdateEntries();

    fireEvent.click(await screen.findByRole('button', { name: 'Desktop updates' }));
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Desktop updates' })).getByRole('button', { name: 'Check for updates' }),
    );

    await waitFor(() => expect(desktopCheckForUpdates).toHaveBeenCalledTimes(1));

    const settingsCard = await waitFor(() => {
      const card = document.querySelector('#desktop-version-info');
      expect(card).not.toBeNull();
      expect(within(card as HTMLElement).getByRole('button', { name: 'Checking...' })).toBeDisabled();
      return card as HTMLElement;
    });
    const settingsButton = within(settingsCard).getByRole('button', { name: 'Checking...' });

    fireEvent.click(settingsButton);
    expect(desktopCheckForUpdates).toHaveBeenCalledTimes(1);

    await pendingCheck.finish({
      status: 'up-to-date',
      currentVersion: '3.12.0',
      latestVersion: '3.12.0',
      message: 'The desktop app is already up to date.',
    });
  });

  it('does not start a second check from the header while settings is already checking', async () => {
    const pendingCheck = hangDesktopUpdateCheck();
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    renderDesktopUpdateEntries();

    const settingsCard = await waitFor(() => {
      const card = document.querySelector('#desktop-version-info');
      expect(card).not.toBeNull();
      return card as HTMLElement;
    });
    fireEvent.click(within(settingsCard).getByRole('button', { name: 'Check for updates' }));

    await waitFor(() => expect(desktopCheckForUpdates).toHaveBeenCalledTimes(1));
    expect(within(settingsCard).getByRole('button', { name: 'Checking...' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Desktop updates' }));
    expect(screen.queryByRole('button', { name: 'Check for updates' })).not.toBeInTheDocument();
    fireEvent.click(within(settingsCard).getByRole('button', { name: 'Checking...' }));
    expect(desktopCheckForUpdates).toHaveBeenCalledTimes(1);

    await pendingCheck.finish({
      status: 'up-to-date',
      currentVersion: '3.12.0',
      latestVersion: '3.12.0',
      message: 'The desktop app is already up to date.',
    });
  });

  it('keeps both entries busy when a late settings mount receives a stale idle snapshot', async () => {
    const pendingCheck = hangDesktopUpdateCheck();
    (window as { dsaDesktop?: unknown }).dsaDesktop = createDesktopRuntime();

    const view = render(
      <MemoryRouter initialEntries={['/settings']}>
        <DesktopUpdateIndicator />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Desktop updates' }));
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Desktop updates' })).getByRole('button', { name: 'Check for updates' }),
    );
    await waitFor(() => expect(desktopCheckForUpdates).toHaveBeenCalledTimes(1));

    view.rerender(
      <MemoryRouter initialEntries={['/settings']}>
        <>
          <DesktopUpdateIndicator />
          <SettingsPage />
        </>
      </MemoryRouter>,
    );

    const settingsCard = await waitFor(() => {
      const card = document.querySelector('#desktop-version-info');
      expect(card).not.toBeNull();
      return card as HTMLElement;
    });
    expect(within(settingsCard).getByRole('button', { name: 'Checking...' })).toBeDisabled();
    expect(desktopCheckForUpdates).toHaveBeenCalledTimes(1);

    await pendingCheck.finish({
      status: 'up-to-date',
      currentVersion: '3.12.0',
      latestVersion: '3.12.0',
      message: 'The desktop app is already up to date.',
    });
  });
});
