import type { Message } from '../stores/agentChatStore';
import { formatUiText, UI_TEXT, type UiLanguage } from '../i18n/uiText';

/**
 * Format chat messages as Markdown for export.
 */
export function formatSessionAsMarkdown(messages: Message[], language: UiLanguage = 'en'): string {
  const text = UI_TEXT[language];
  const now = new Date();
  const timeStr = now.toLocaleString(language === 'vi' ? 'vi-VN' : 'en-US', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  const lines: string[] = [
    `# ${text['chat.exportTitle']}`,
    '',
    formatUiText(text['chat.exportGeneratedAt'], { time: timeStr }),
    '',
  ];

  for (const msg of messages) {
    const heading = msg.role === 'user' ? `## ${text['chat.exportUser']}` : '## AI';
    if (msg.role === 'assistant' && msg.skillName) {
      lines.push(`${heading} (${msg.skillName})`);
    } else {
      lines.push(heading);
    }
    lines.push('');
    lines.push(msg.content);
    lines.push('');
  }

  return lines.join('\n');
}

/**
 * Trigger browser download of session as .md file.
 * Revokes object URL after download to prevent memory leak.
 */
export function downloadSession(messages: Message[], language: UiLanguage = 'en'): void {
  const content = formatSessionAsMarkdown(messages, language);
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
  const pad = (n: number) => n.toString().padStart(2, '0');
  const timeStr = pad(now.getHours()) + pad(now.getMinutes());
  const filename = `stock-chat_${dateStr}_${timeStr}.md`;

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
