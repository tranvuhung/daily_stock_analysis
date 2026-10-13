import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { UiLanguageProvider, useUiLanguage } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { SettingsField } from '../SettingsField';

describe('SettingsField', () => {
  it('renders the backend schema title, which is already localized by the API', () => {
    render(
      <SettingsField
        item={{
          key: 'STOCK_LIST',
          value: 'HPG',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'STOCK_LIST',
            title: 'Stock List',
            category: 'base',
            dataType: 'string',
            uiControl: 'text',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        }}
        value="HPG"
        onChange={vi.fn()}
      />
    );

    expect(screen.getByLabelText('Stock List')).toBeInTheDocument();
  });

  it('prefers the settings-help summary over the raw backend schema description', () => {
    render(
      <SettingsField
        item={{
          key: 'REALTIME_SOURCE_PRIORITY',
          value: 'ssi,vci',
          rawValueExists: false,
          isMasked: false,
          schema: {
            key: 'REALTIME_SOURCE_PRIORITY',
            title: 'Realtime Source Priority',
            description: 'Raw backend description for realtime source priority.',
            category: 'data_source',
            dataType: 'string',
            uiControl: 'text',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 16,
            helpKey: 'settings.data_source.REALTIME_SOURCE_PRIORITY',
          },
        }}
        value="ssi,vci"
        onChange={vi.fn()}
      />
    );

    expect(screen.getByLabelText('Realtime Source Priority')).toBeInTheDocument();
    expect(screen.getByText(/Configures the provider order for realtime quotes/)).toBeInTheDocument();
    expect(screen.queryByText(/Raw backend description for realtime source priority/)).not.toBeInTheDocument();
  });
  it('resolves settings help from the schema key when the runtime item key differs', () => {
    render(
      <SettingsField
        item={{
          key: 'runtime.tickflow.priority',
          value: 'ssi,vci',
          rawValueExists: false,
          isMasked: false,
          schema: {
            key: 'REALTIME_SOURCE_PRIORITY',
            title: 'Realtime Source Priority',
            description: 'Raw backend description for realtime source priority.',
            category: 'data_source',
            dataType: 'string',
            uiControl: 'text',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 16,
            helpKey: 'settings.data_source.REALTIME_SOURCE_PRIORITY',
          },
        }}
        value="ssi,vci"
        onChange={vi.fn()}
      />
    );

    expect(screen.getByLabelText('Realtime Source Priority')).toBeInTheDocument();
    expect(screen.getByText(/Configures the provider order for realtime quotes/)).toBeInTheDocument();
    expect(screen.queryByText(/Raw backend description for realtime source priority/)).not.toBeInTheDocument();
  });
  it('renders sensitive field metadata and validation errors', () => {
    const onChange = vi.fn();

    render(
      <SettingsField
        item={{
          key: 'OPENAI_API_KEY',
          value: 'secret',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'OPENAI_API_KEY',
            title: 'OpenAI API Key',
            category: 'ai_model',
            dataType: 'string',
            uiControl: 'password',
            isSensitive: true,
            isRequired: true,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        }}
        value="secret"
        onChange={onChange}
        issues={[
          {
            key: 'OPENAI_API_KEY',
            code: 'required',
            message: 'API Key is required',
            severity: 'error',
          },
        ]}
      />
    );

    expect(screen.getByText('Sensitive')).toBeInTheDocument();
    expect(screen.getByText('API Key is required')).toBeInTheDocument();

    const input = screen.getByLabelText('OpenAI API Key');
    fireEvent.focus(input);
    fireEvent.change(input, {
      target: { value: 'updated-secret' },
    });

    expect(onChange).toHaveBeenCalledWith('OPENAI_API_KEY', 'updated-secret');
  });

  it('renders multi-value sensitive fields with external delete actions', () => {
    const onChange = vi.fn();

    render(
      <SettingsField
        item={{
          key: 'OPENAI_API_KEYS',
          value: 'secret-a,secret-b',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'OPENAI_API_KEYS',
            category: 'ai_model',
            dataType: 'string',
            uiControl: 'password',
            isSensitive: true,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: { multiValue: true },
            displayOrder: 1,
          },
        }}
        value="secret-a,secret-b"
        onChange={onChange}
      />
    );

    expect(screen.getAllByRole('button', { name: 'Show content' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(2);
  });

  it('allows optional select fields to be cleared when schema provides an empty option', () => {
    const onChange = vi.fn();

    render(
      <SettingsField
        item={{
          key: 'NOTIFICATION_MIN_SEVERITY',
          value: 'warning',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'NOTIFICATION_MIN_SEVERITY',
            title: 'Notification Minimum Severity',
            category: 'notification',
            dataType: 'string',
            uiControl: 'select',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [
              { label: 'Not set', value: '' },
              { label: 'info', value: 'info' },
              { label: 'warning', value: 'warning' },
              { label: 'error', value: 'error' },
              { label: 'critical', value: 'critical' },
            ],
            validation: { enum: ['', 'info', 'warning', 'error', 'critical'] },
            displayOrder: 69,
          },
        }}
        value="warning"
        onChange={onChange}
      />
    );

    const select = screen.getByLabelText('Notification Minimum Severity');
    expect(screen.getByRole('option', { name: 'Not set' })).not.toBeDisabled();
    expect(screen.queryByRole('option', { name: 'Select' })).not.toBeInTheDocument();

    fireEvent.change(select, { target: { value: '' } });

    expect(onChange).toHaveBeenCalledWith('NOTIFICATION_MIN_SEVERITY', '');
  });

  it('shows the schema default for select fields when no explicit env value exists', () => {
    const onChange = vi.fn();

    render(
      <SettingsField
        item={{
          key: 'GENERATION_BACKEND',
          value: '',
          rawValueExists: false,
          isMasked: false,
          schema: {
            key: 'GENERATION_BACKEND',
            title: 'Generation Backend',
            category: 'ai_model',
            dataType: 'string',
            uiControl: 'select',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            defaultValue: 'litellm',
            options: [{ label: 'Default model settings', value: 'litellm' }],
            validation: { enum: ['litellm'] },
            displayOrder: 1,
          },
        }}
        value=""
        onChange={onChange}
      />
    );

    expect(screen.getByLabelText('Generation Backend')).toHaveValue('litellm');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('renders localized labels for real system config select options', () => {
    const selectCases = [
      {
        key: 'NEWS_STRATEGY_PROFILE',
        category: 'data_source',
        options: ['ultra_short', 'short', 'medium', 'long'],
        expectedLabels: ['Ultra short (1 day)', 'Short (3 days)', 'Medium (7 days)', 'Long (30 days)'],
      },
      {
        key: 'REPORT_TYPE',
        category: 'notification',
        options: ['simple', 'full', 'brief'],
        expectedLabels: ['Simple', 'Full', 'Brief'],
      },
      {
        key: 'LOG_LEVEL',
        category: 'system',
        options: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        expectedLabels: ['Debug', 'Info', 'Warning', 'Error', 'Critical'],
      },
    ] as const;

    selectCases.forEach(({ key, category, options, expectedLabels }) => {
      const { unmount } = render(
        <SettingsField
          item={{
            key,
            value: options[0],
            rawValueExists: true,
            isMasked: false,
            schema: {
              key,
              title: key,
              category,
              dataType: 'string',
              uiControl: 'select',
              isSensitive: false,
              isRequired: false,
              isEditable: true,
              options: [...options],
              validation: {},
              displayOrder: 1,
            },
          }}
          value={options[0]}
          onChange={() => undefined}
        />
      );

      expectedLabels.forEach((label) => {
        expect(screen.getByRole('option', { name: label })).toBeInTheDocument();
      });

      options.forEach((rawOption) => {
        expect(screen.queryByRole('option', { name: rawOption })).not.toBeInTheDocument();
      });

      unmount();
    });
  });

  it('renders MARKET_REVIEW_REGION as a free-text field (VN-only value)', () => {
    const onChange = vi.fn();

    render(
      <SettingsField
        item={{
          key: 'MARKET_REVIEW_REGION',
          value: 'vn',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'MARKET_REVIEW_REGION',
            title: 'Market Review Region',
            category: 'system',
            dataType: 'string',
            uiControl: 'text',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
          },
        }}
        value="vn"
        onChange={onChange}
      />
    );

    const input = screen.getByLabelText('Market Review Region') as HTMLInputElement;
    expect(input).toHaveValue('vn');
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    fireEvent.change(input, {
      target: { value: '' },
    });

    expect(onChange).toHaveBeenCalledWith('MARKET_REVIEW_REGION', '');
  });

  it('renders context compression profile options with backend-provided labels', () => {
    const onChange = vi.fn();

    render(
      <SettingsField
        item={{
          key: 'AGENT_CONTEXT_COMPRESSION_PROFILE',
          value: 'balanced',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'AGENT_CONTEXT_COMPRESSION_PROFILE',
            title: 'Context Compression Profile',
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
        }}
        value="balanced"
        onChange={onChange}
      />
    );

    expect(screen.getByLabelText('Context Compression Profile')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Cost first' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Balanced (recommended)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Long-context raw first' })).toBeInTheDocument();
  });

  it('opens detailed field help when help metadata is available', () => {
    render(
      <SettingsField
        item={{
          key: 'STOCK_LIST',
          value: 'HPG,FPT',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'STOCK_LIST',
            category: 'base',
            dataType: 'array',
            uiControl: 'textarea',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [],
            validation: {},
            displayOrder: 1,
            helpKey: 'settings.base.STOCK_LIST',
            examples: ['STOCK_LIST=HPG,FPT,VCB'],
            docs: [
              {
                label: 'Full guide',
                href: 'https://example.com/full-guide',
              },
            ],
            warningCodes: [],
          },
        }}
        value="HPG,FPT"
        onChange={() => undefined}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'View STOCK_LIST configuration help' }));

    expect(screen.getByRole('dialog', { name: 'Watchlist' })).toBeInTheDocument();
    expect(screen.getByText('STOCK_LIST=HPG,FPT,VCB')).toBeInTheDocument();
    // Mục "Tài liệu liên quan" đã bị ẩn: schema có docs nhưng không render link.
    expect(screen.queryByRole('link', { name: /Full guide/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Related docs')).not.toBeInTheDocument();

    const closeButtons = screen.getAllByRole('button', { name: 'Close configuration help' });
    expect(closeButtons[0].tabIndex).toBe(-1);
    const closeButton = closeButtons.find((button) => button.tabIndex !== -1);
    expect(closeButton).toBeDefined();

    closeButton?.focus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(closeButton).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Tab' });
    expect(closeButton).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Watchlist' })).not.toBeInTheDocument();
  });

  it('keeps generation channel help user-facing without env key or examples', () => {
    render(
      <SettingsField
        item={{
          key: 'GENERATION_BACKEND',
          value: 'litellm',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'GENERATION_BACKEND',
            title: 'Generation Backend',
            category: 'ai_model',
            dataType: 'string',
            uiControl: 'select',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [{ label: 'Default model settings', value: 'litellm' }],
            validation: { enum: ['litellm'] },
            displayOrder: 1,
            helpKey: 'settings.ai_model.GENERATION_BACKEND',
            examples: ['GENERATION_BACKEND=litellm'],
            warningCodes: [],
          },
        }}
        value="litellm"
        onChange={() => undefined}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'View Generation Backend configuration help' }));

    const dialog = screen.getByRole('dialog', { name: 'Analysis Generation Method' });
    expect(dialog).toHaveTextContent('Chooses how the system generates stock analysis');
    expect(dialog).not.toHaveTextContent('GENERATION_BACKEND');
    expect(dialog).not.toHaveTextContent('Examples');
    expect(dialog).not.toHaveTextContent('Phase 1');
    expect(dialog).toHaveTextContent('installed and logged in on this machine');
    expect(dialog).toHaveTextContent('Default model settings continue to use your existing API keys');
    expect(dialog).not.toHaveTextContent('LiteLLM');
  });

  it('describes agent auto generation without exposing implementation labels as the primary UI copy', () => {
    render(
      <SettingsField
        item={{
          key: 'AGENT_GENERATION_BACKEND',
          value: 'auto',
          rawValueExists: true,
          isMasked: false,
          schema: {
            key: 'AGENT_GENERATION_BACKEND',
            title: 'Agent Generation Backend',
            category: 'agent',
            dataType: 'string',
            uiControl: 'select',
            isSensitive: false,
            isRequired: false,
            isEditable: true,
            options: [
              { label: 'Auto', value: 'auto' },
              { label: 'Default model settings', value: 'litellm' },
            ],
            validation: { enum: ['auto', 'litellm'] },
            displayOrder: 1,
            helpKey: 'settings.agent.AGENT_GENERATION_BACKEND',
            examples: [],
            warningCodes: [],
          },
        }}
        value="auto"
        onChange={() => undefined}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'View Agent Generation Backend configuration help' }));

    const dialog = screen.getByRole('dialog', { name: 'Ask-Stock Generation Method' });
    expect(dialog).toHaveTextContent('The system chooses the currently available method');
    expect(dialog).toHaveTextContent('If you are unsure, choose Auto.');
    expect(dialog).toHaveTextContent('This setting only affects the ask-stock assistant.');
    expect(dialog).not.toHaveTextContent('LiteLLM');
  });

  it('uses per-field schema titles even when helpKey is shared by multiple fields', () => {
    const restoreLanguage = localStorage.getItem(UI_LANGUAGE_STORAGE_KEY);
    localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');

    try {
      const SchemaTitleSwitcher = ({ children }: { children: ReactNode }) => {
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
          <SchemaTitleSwitcher>
            <SettingsField
              item={{
                key: 'OPENAI_MODEL',
                value: 'gemini/gemini-3.1-pro-preview',
                rawValueExists: true,
                isMasked: false,
                schema: {
                  key: 'OPENAI_MODEL',
                  category: 'ai_model',
                  dataType: 'string',
                  uiControl: 'text',
                  isSensitive: false,
                  isRequired: false,
                  isEditable: true,
                  options: [],
                  validation: {},
                  displayOrder: 10,
                  title: 'Primary model',
                  helpKey: 'settings.llm_channel.primary_model',
                  description: 'Primary model description',
                },
              }}
              value="gemini/gemini-3.1-pro-preview"
              onChange={vi.fn()}
            />
            <SettingsField
              item={{
                key: 'OPENAI_VISION_MODEL',
                value: 'gemini/gemini-2.0-flash',
                rawValueExists: true,
                isMasked: false,
                schema: {
                  key: 'OPENAI_VISION_MODEL',
                  category: 'ai_model',
                  dataType: 'string',
                  uiControl: 'text',
                  isSensitive: false,
                  isRequired: false,
                  isEditable: true,
                  options: [],
                  validation: {},
                  displayOrder: 11,
                  title: 'Vision model',
                  helpKey: 'settings.llm_channel.primary_model',
                  description: 'Vision model description',
                },
              }}
              value="gemini/gemini-2.0-flash"
              onChange={vi.fn()}
            />
          </SchemaTitleSwitcher>
        </UiLanguageProvider>
      );

      fireEvent.click(screen.getByRole('button', { name: 'switch-en' }));

      expect(screen.getByLabelText('Primary model')).toBeInTheDocument();
      expect(screen.getByLabelText('Vision model')).toBeInTheDocument();
    } finally {
      if (restoreLanguage) {
        localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, restoreLanguage);
      } else {
        localStorage.removeItem(UI_LANGUAGE_STORAGE_KEY);
      }
    }
  });
});
