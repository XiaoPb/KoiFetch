import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import NasPage from './NasPage';
import { renderWithProviders } from '../test/utils';
import { healthApi, nasApi } from '../services/api';
import { ApiError } from '../types/api';
import { useDownloadsStore, type DownloadItem } from '../stores/downloadsStore';

vi.mock('../services/api', () => ({
  healthApi: { getHealth: vi.fn() },
  nasApi: { save: vi.fn() },
  // downloadsStore imports downloadApi; it is only invoked on actions, which
  // the NAS page never triggers, but keep the module importable.
  downloadApi: { submit: vi.fn(), getProgress: vi.fn(), getFileUrl: vi.fn() },
}));

const healthy = {
  status: 'ok',
  services: { api: 'ok', storage: 'ok' },
  storage_roots: {
    video_storage_path: 'ok',
    image_storage_path: 'ok',
    music_storage_path: 'ok',
    temp_video_path: 'ok',
    temp_image_path: 'ok',
    temp_music_path: 'error',
  },
};

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

describe('NasPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useDownloadsStore.setState({ items: [], submitting: {} });
    (healthApi.getHealth as Mock).mockResolvedValue(healthy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the page title and the storage status panel from /api/health', async () => {
    renderWithProviders(<NasPage />);

    expect(await screen.findByText('NAS 管理')).toBeInTheDocument();
    expect(await screen.findByText('存储状态')).toBeInTheDocument();
    expect(healthApi.getHealth).toHaveBeenCalledTimes(1);

    // Services row.
    expect(screen.getByTestId('storage-service-api')).toHaveTextContent('API 服务');
    expect(screen.getByTestId('storage-service-api')).toHaveTextContent('正常');
    expect(screen.getByTestId('storage-service-storage')).toHaveTextContent('存储服务');

    // All six pond/bubble roots with their statuses.
    expect(screen.getByTestId('storage-root-video_storage_path')).toHaveTextContent('视频池塘');
    expect(screen.getByTestId('storage-root-temp_music_path')).toHaveTextContent('音乐临时区');
    expect(screen.getByTestId('storage-root-temp_music_path')).toHaveTextContent('异常');
  });

  it('shows a loading state while the health check is in flight', () => {
    let resolveHealth: (value: unknown) => void = () => undefined;
    (healthApi.getHealth as Mock).mockReturnValue(new Promise((resolve) => { resolveHealth = resolve; }));
    renderWithProviders(<NasPage />);

    expect(screen.getByTestId('storage-loading')).toBeInTheDocument();
    expect(screen.getByText('正在检查存储状态...')).toBeInTheDocument();

    resolveHealth(healthy);
  });

  it('shows an error state with a retry action when the health check fails', async () => {
    const user = userEvent.setup();
    (healthApi.getHealth as Mock).mockRejectedValueOnce(new Error('network down'));
    renderWithProviders(<NasPage />);

    expect(await screen.findByTestId('storage-error')).toBeInTheDocument();
    expect(screen.getByText('无法获取存储状态')).toBeInTheDocument();

    // Retry re-fetches and renders the panel.
    await user.click(screen.getByTestId('storage-retry'));
    expect(await screen.findByTestId('storage-service-api')).toBeInTheDocument();
    expect(healthApi.getHealth).toHaveBeenCalledTimes(2);
  });

  it('renders completed items with a save action and hides it for other statuses', async () => {
    useDownloadsStore.setState({
      items: [
        seedItem({ download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A' }),
        seedItem({ download_id: 'd2', task_id: 't2', status: 'downloading', title: 'Video B' }),
        seedItem({ download_id: 'd3', task_id: 't3', status: 'failed', title: 'Video C' }),
      ],
    });
    renderWithProviders(<NasPage />);

    expect(await screen.findByText('保存到 NAS')).toBeInTheDocument();
    expect(screen.getByTestId('nas-completed-d1')).toBeInTheDocument();
    expect(screen.getByTestId('nas-save-d1')).toBeInTheDocument();
    expect(screen.queryByTestId('nas-save-d2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('nas-save-d3')).not.toBeInTheDocument();
  });

  it('shows an empty state when no downloads completed in this session', async () => {
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd2', task_id: 't2', status: 'downloading', title: 'Video B' })],
    });
    renderWithProviders(<NasPage />);

    expect(await screen.findByText('暂无已完成的下载')).toBeInTheDocument();
    expect(screen.queryByTestId('nas-save-d2')).not.toBeInTheDocument();
  });

  it('saves a completed item: modal opens with default "/", submits the path, and reports the nas_path', async () => {
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A' })],
    });
    (nasApi.save as Mock).mockResolvedValue({
      nas_path: '/视频/抖音/2026-01-01_x.mp4',
      file_size: 1024,
      saved_at: '2026-01-02T00:00:00Z',
    });
    renderWithProviders(<NasPage />);

    await user.click(await screen.findByTestId('nas-save-d1'));

    const modal = await screen.findByTestId('nas-save-modal');
    expect(modal).toBeInTheDocument();
    // Default target directory is "/" (PRD §3.4.3), leading "/" optional.
    expect(screen.getByTestId('nas-target-input')).toHaveValue('/');

    await user.clear(screen.getByTestId('nas-target-input'));
    await user.type(screen.getByTestId('nas-target-input'), '/视频/抖音');
    await user.click(screen.getByTestId('nas-save-confirm'));

    await waitFor(() => expect(nasApi.save).toHaveBeenCalledWith('d1', '/视频/抖音'));
    expect(await screen.findByText('🎉 锦鲤已游入池塘!')).toBeInTheDocument();
    expect(screen.getByText('已保存到 /视频/抖音/2026-01-01_x.mp4')).toBeInTheDocument();
    // The modal closes after a successful save.
    expect(screen.queryByTestId('nas-save-modal')).not.toBeInTheDocument();
  });

  it('surfaces the backend error message when the save is refused (5002 not completed)', async () => {
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A' })],
    });
    (nasApi.save as Mock).mockRejectedValue(new ApiError('文件未下载完成 / File not fully downloaded', 5002, 400));
    renderWithProviders(<NasPage />);

    await user.click(await screen.findByTestId('nas-save-d1'));
    await user.clear(screen.getByTestId('nas-target-input'));
    await user.type(screen.getByTestId('nas-target-input'), '/视频');
    await user.click(screen.getByTestId('nas-save-confirm'));

    expect(await screen.findByText(/File not fully downloaded/)).toBeInTheDocument();
  });

  it('rejects invalid targets client-side without calling the API', async () => {
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A' })],
    });
    renderWithProviders(<NasPage />);

    await user.click(await screen.findByTestId('nas-save-d1'));

    // Backslash separator.
    await user.clear(screen.getByTestId('nas-target-input'));
    await user.type(screen.getByTestId('nas-target-input'), '视频\\抖音');
    await user.click(screen.getByTestId('nas-save-confirm'));
    expect(screen.getByText('目标路径无效：不能包含 .. 、反斜杠或盘符')).toBeInTheDocument();
    expect(nasApi.save).not.toHaveBeenCalled();

    // Parent-directory traversal.
    await user.clear(screen.getByTestId('nas-target-input'));
    await user.type(screen.getByTestId('nas-target-input'), '/视频/../抖音');
    await user.click(screen.getByTestId('nas-save-confirm'));
    expect(nasApi.save).not.toHaveBeenCalled();

    // Drive-letter prefix.
    await user.clear(screen.getByTestId('nas-target-input'));
    await user.type(screen.getByTestId('nas-target-input'), 'C:\\evil');
    await user.click(screen.getByTestId('nas-save-confirm'));
    expect(nasApi.save).not.toHaveBeenCalled();

    // Root-only "/" is rejected client-side (the backend rejects it too:
    // backend tests treat "/" as an invalid target path).
    await user.clear(screen.getByTestId('nas-target-input'));
    await user.type(screen.getByTestId('nas-target-input'), '/');
    await user.click(screen.getByTestId('nas-save-confirm'));
    expect(screen.getByText('请输入至少一个目录(例如 /视频/抖音)')).toBeInTheDocument();
    expect(nasApi.save).not.toHaveBeenCalled();
  });

  it('accepts a leading-slash-less target path', async () => {
    const user = userEvent.setup();
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A' })],
    });
    (nasApi.save as Mock).mockResolvedValue({
      nas_path: '/视频/x.mp4',
      file_size: 10,
      saved_at: '2026-01-02T00:00:00Z',
    });
    renderWithProviders(<NasPage />);

    await user.click(await screen.findByTestId('nas-save-d1'));
    await user.clear(screen.getByTestId('nas-target-input'));
    await user.type(screen.getByTestId('nas-target-input'), '视频');
    await user.click(screen.getByTestId('nas-save-confirm'));

    await waitFor(() => expect(nasApi.save).toHaveBeenCalledWith('d1', '视频'));
  });
});
