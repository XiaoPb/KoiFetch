import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DownloadCenterDrawer } from './DownloadCenterDrawer';
import { renderWithProviders } from '../../test/utils';
import { downloadApi } from '../../services/api';
import { ApiError } from '../../types/api';
import { __resetDownloadStreams, useDownloadsStore, type DownloadItem } from '../../stores/downloadsStore';

vi.mock('../../services/api', () => ({
  downloadApi: { submit: vi.fn(), getProgress: vi.fn(), getFileUrl: vi.fn((pathOrUrl: string) => pathOrUrl) },
}));

// Retry re-submits → the store opens a WS client; mock it so no poll timer
// interferes with the assertions.
vi.mock('../../services/wsClient', () => ({
  DownloadWsClient: class {
    url: string;
    status = 'open';
    constructor(options: { url: string }) {
      this.url = options.url;
    }
    subscribe(): () => void {
      return () => undefined;
    }
    connect(): void {
      this.status = 'open';
    }
    close(): void {
      this.status = 'closed';
    }
  },
  buildWsUrl: vi.fn((downloadId: string) => `ws://test/ws/download/${downloadId}`),
}));

function seedItem(partial: Partial<DownloadItem> & Pick<DownloadItem, 'download_id' | 'task_id' | 'status'>): DownloadItem {
  return {
    title: null,
    format: null,
    quality: null,
    created_at: '2026-01-01T00:00:00Z',
    progress: 0,
    speed: null,
    downloaded_bytes: null,
    total_bytes: null,
    remaining_time: null,
    error_code: null,
    error_message: null,
    download_url: null,
    token_expire_at: null,
    ...partial,
  };
}

const FUTURE = '2099-01-01T00:00:00Z';
const PAST = '2020-01-01T00:00:00Z';

function renderDrawer(): void {
  renderWithProviders(<DownloadCenterDrawer open onClose={() => undefined} />);
}

describe('DownloadCenterDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useDownloadsStore.setState({ items: [], submitting: {} });
    __resetDownloadStreams();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the empty state when there are no tasks', () => {
    renderDrawer();
    expect(screen.getByTestId('downloads-empty')).toHaveTextContent('暂无下载任务');
  });

  it('renders the task list with status tags and groups by tab', async () => {
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [
        seedItem({ download_id: 'd1', task_id: 't1', status: 'downloading', title: 'Video A', progress: 0.4 }),
        seedItem({ download_id: 'd2', task_id: 't2', status: 'completed', title: 'Song B' }),
        seedItem({ download_id: 'd3', task_id: 't3', status: 'failed', title: 'Post C', error_message: '文件未下载完成 / File not fully downloaded' }),
      ],
    });
    renderDrawer();

    // 全部 tab: every item visible with its status tag.
    expect(screen.getByTestId('download-item-d1')).toBeInTheDocument();
    expect(screen.getByTestId('download-item-d2')).toBeInTheDocument();
    expect(screen.getByTestId('download-item-d3')).toBeInTheDocument();
    expect(screen.getByTestId('download-status-d1')).toHaveTextContent('下载中');
    expect(screen.getByTestId('download-status-d2')).toHaveTextContent('已完成');
    expect(screen.getByTestId('download-status-d3')).toHaveTextContent('失败');

    // 进行中 tab: only the active item.
    await user.click(screen.getByRole('tab', { name: /进行中/ }));
    expect(screen.getByTestId('download-item-d1')).toBeInTheDocument();
    expect(screen.queryByTestId('download-item-d2')).not.toBeInTheDocument();

    // 已完成 tab.
    await user.click(screen.getByRole('tab', { name: /已完成/ }));
    expect(screen.getByTestId('download-item-d2')).toBeInTheDocument();
    expect(screen.queryByTestId('download-item-d1')).not.toBeInTheDocument();
  });

  it('renders a progress bar with bytes/speed/remaining for an active item', () => {
    useDownloadsStore.setState({
      items: [
        seedItem({
          download_id: 'd1', task_id: 't1', status: 'downloading', title: 'Video A',
          progress: 0.5, speed: 1024 * 1024, downloaded_bytes: 50 * 1024 * 1024,
          total_bytes: 100 * 1024 * 1024, remaining_time: 90,
        }),
      ],
    });
    renderDrawer();

    const item = screen.getByTestId('download-item-d1');
    expect(within(item).getByTestId('download-progress-d1')).toBeInTheDocument();
    const text = within(item).getByTestId('progress-text-d1');
    expect(text).toHaveTextContent('50 MB');
    expect(text).toHaveTextContent('100 MB');
    expect(text).toHaveTextContent('1 MB/s');
    expect(text).toHaveTextContent('约 2 分钟');
  });

  it('opens the tokenized file link for a completed item with a valid token', async () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [
        seedItem({
          download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A',
          download_url: '/api/download/file/d1?token=t', token_expire_at: FUTURE,
        }),
      ],
    });
    renderDrawer();

    await user.click(screen.getByTestId('get-file-d1'));
    expect(open).toHaveBeenCalledWith('/api/download/file/d1?token=t', '_blank', 'noopener');
  });

  it('shows an honest missing-link state with refresh for a completed item without a captured URL', async () => {
    useDownloadsStore.setState({
      items: [
        seedItem({ download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A', download_url: null }),
      ],
    });
    renderDrawer();

    const item = screen.getByTestId('download-item-d1');
    expect(within(item).getByText(/未获取到文件链接/)).toBeInTheDocument();
    expect(within(item).getByTestId('refresh-link-d1')).toBeInTheDocument();
    expect(within(item).queryByTestId('get-file-d1')).not.toBeInTheDocument();
  });

  it('shows the expired-link hint with refresh for an expired token', () => {
    useDownloadsStore.setState({
      items: [
        seedItem({
          download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A',
          download_url: '/api/download/file/d1?token=stale', token_expire_at: PAST,
        }),
      ],
    });
    renderDrawer();

    const item = screen.getByTestId('download-item-d1');
    expect(within(item).getByText(/链接已过期/)).toBeInTheDocument();
    expect(within(item).getByTestId('refresh-link-d1')).toBeInTheDocument();
    expect(within(item).queryByTestId('get-file-d1')).not.toBeInTheDocument();
  });

  it('retry re-submits a failed item and moves it back to pending', async () => {
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [
        seedItem({ download_id: 'd1', task_id: 't1', status: 'failed', title: 'Video A', format: 'mp4', quality: '720p', error_message: 'boom' }),
      ],
    });
    (downloadApi.submit as Mock).mockResolvedValue({
      download_id: 'd2', task_id: 't1', status: 'pending', created_at: '2026-02-01T00:00:00Z',
    });
    renderDrawer();

    await user.click(screen.getByTestId('retry-d1'));
    await waitFor(() => expect(useDownloadsStore.getState().items[0].status).toBe('pending'));

    expect(downloadApi.submit).toHaveBeenCalledWith('t1', { format: 'mp4', quality: '720p' });
    expect(useDownloadsStore.getState().items[0].download_id).toBe('d2');
    expect(screen.getByTestId('download-status-d2')).toHaveTextContent('等待中');
  });

  it('toasts the backend error when retry is refused', async () => {
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [
        seedItem({ download_id: 'd1', task_id: 't1', status: 'failed', title: 'Video A' }),
      ],
    });
    (downloadApi.submit as Mock).mockRejectedValue(new ApiError('任务已完成 / Task already completed', 3003, 400));
    renderDrawer();

    await user.click(screen.getByTestId('retry-d1'));
    expect(await screen.findByText(/Task already completed/)).toBeInTheDocument();
    expect(useDownloadsStore.getState().items[0].status).toBe('failed');
  });

  it('renders only a disabled cancel control for active items (v1 has no cancel endpoint)', () => {
    useDownloadsStore.setState({
      items: [
        seedItem({ download_id: 'd1', task_id: 't1', status: 'downloading', title: 'Video A' }),
      ],
    });
    renderDrawer();

    const cancel = screen.getByTestId('cancel-d1');
    expect(cancel).toBeDisabled();
    // No ENABLED cancel affordance anywhere (the disabled control explains
    // itself via its aria-label/tooltip).
    const enabledCancel = screen.queryByRole('button', { name: /取消/i, hidden: false });
    if (enabledCancel) expect(enabledCancel).toBeDisabled();
  });
});
