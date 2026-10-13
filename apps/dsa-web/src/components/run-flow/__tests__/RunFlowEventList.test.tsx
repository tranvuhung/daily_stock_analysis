import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { RunFlowEvent } from '../../../types/runFlow';
import { RunFlowEventList } from '../RunFlowEventList';

// Fixture bám sát hợp đồng thật của backend (`src/services/run_flow.py`): sự kiện dự phòng
// là `provider_run` kèm `fallbackFrom`/`fallbackTo`, KHÔNG phải một `type` riêng. Bản cũ
// dùng `type: 'provider_fallback'` không tồn tại, và chỉ lọt vì bộ lọc cũ khớp chuỗi "Suy giảm"
// trong tiêu đề — tức là test xanh nhờ so khớp văn bản chứ không nhờ đúng hợp đồng.
// Fixtures follow the real backend contract: a fallback is a `provider_run` carrying
// `fallbackFrom`/`fallbackTo`, not a dedicated event type.
const events: RunFlowEvent[] = [
  {
    id: 'evt-1',
    timestamp: '2026-06-08T08:00:01Z',
    severity: 'info',
    type: 'task_created',
    nodeId: 'request',
    title: 'Task created',
  },
  {
    id: 'evt-2',
    timestamp: '2026-06-08T08:00:02Z',
    severity: 'warning',
    type: 'provider_run',
    nodeId: 'daily_data',
    // Tiêu đề và message cố ý KHÔNG chứa từ khoá "fallback"/"retry" nào, để chứng minh bộ
    // lọc dựa vào metadata chứ không dựa vào văn bản hiển thị.
    // Title and message deliberately contain no fallback keyword.
    title: 'Nến ngày thất bại',
    message: 'Tushare lỗi, đã chuyển sang AkShare',
    metadata: { dataType: 'daily_data', fallbackFrom: 'Tushare', fallbackTo: 'AkShare' },
  },
  {
    id: 'evt-3',
    timestamp: '2026-06-08T08:00:03Z',
    severity: 'danger',
    type: 'task_cancelled',
    nodeId: 'queue',
    title: 'Task cancelled',
  },
];

describe('RunFlowEventList', () => {
  it('localizes event titles from the stable event type', () => {
    render(<RunFlowEventList events={events} />);

    expect(screen.getByText('Task created')).toBeInTheDocument();
    // `provider_run` + severity warning + metadata.dataType=daily_data -> "Daily bars failed"
    expect(screen.getByText('Daily bars failed')).toBeInTheDocument();
    expect(screen.getByText('Task cancelled')).toBeInTheDocument();
  });

  it('filters fallback and cancellation events without matching displayed text', () => {
    render(<RunFlowEventList events={events} />);

    fireEvent.click(screen.getByRole('button', { name: 'Fallback/retry' }));

    expect(screen.getByText('Daily bars failed')).toBeInTheDocument();
    expect(screen.queryByText('Task created')).not.toBeInTheDocument();
    expect(screen.queryByText('Task cancelled')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.getByText('Task cancelled')).toBeInTheDocument();
    expect(screen.queryByText('Daily bars failed')).not.toBeInTheDocument();
    expect(screen.getByText('Danger')).toBeInTheDocument();
  });

  it('keeps the fallback filter working when no title or message is in English', () => {
    // Hồi quy: bộ lọc cũ dùng regex `/fallback|retry|Suy giảm|Thử lại/` trên tiêu đề và message,
    // nên chỉ cần đổi ngôn ngữ nhãn là hỏng âm thầm. Ca này giữ cho lỗi đó không quay lại.
    // Regression guard for the old text-matching filter.
    const vietnameseOnly: RunFlowEvent[] = [
      {
        id: 'evt-vi',
        timestamp: '2026-06-08T08:00:04Z',
        severity: 'warning',
        type: 'provider_run',
        nodeId: 'news',
        title: 'Tin tức thất bại',
        message: 'Đã chuyển nguồn dữ liệu',
        metadata: { dataType: 'news', fallbackFrom: 'Tavily', fallbackTo: 'SearXNG' },
      },
    ];
    render(<RunFlowEventList events={vietnameseOnly} />);

    fireEvent.click(screen.getByRole('button', { name: 'Fallback/retry' }));

    expect(screen.getByText('News failed')).toBeInTheDocument();
  });

  it('does not treat an ordinary failed run as a fallback', () => {
    const plainFailure: RunFlowEvent[] = [
      {
        id: 'evt-plain',
        timestamp: '2026-06-08T08:00:05Z',
        severity: 'danger',
        type: 'provider_run',
        nodeId: 'news',
        title: 'News failed',
        metadata: { dataType: 'news' },
      },
    ];
    render(<RunFlowEventList events={plainFailure} />);

    fireEvent.click(screen.getByRole('button', { name: 'Fallback/retry' }));

    expect(screen.queryByText('News failed')).not.toBeInTheDocument();
  });

  it('selects the event node when an event row is clicked', () => {
    const onSelectNode = vi.fn();
    render(<RunFlowEventList events={events} onSelectNode={onSelectNode} />);

    fireEvent.click(screen.getByRole('button', { name: 'View node linked to event Daily bars failed' }));

    expect(onSelectNode).toHaveBeenCalledWith('daily_data');
  });
});
