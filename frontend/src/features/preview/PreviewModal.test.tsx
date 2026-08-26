import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PreviewModal } from './PreviewModal';
import { renderWithProviders } from '../../test/utils';
import { previewApi, downloadApi } from '../../services/api';
import { ApiError } from '../../types/api';
import type { ParseResult, PreviewData } from '../../types/api';
import { usePreviewStore } from '../parser/previewStore';
import { __resetDownloadStreams, selectActiveCount, useDownloadsStore } from '../../stores/downloadsStore';

vi.mock('../../services/api', () => ({
  previewApi: { getPreview: vi.fn() },
  downloadApi: { submit: vi.fn(), getProgress: vi.fn(), getLatestByTask: vi.fn() },
}));

// downloadsStore opens a WS client per download; the submit path in this test
// is covered by mocking the client so no poll timer interferes.
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

const videoTask: ParseResult = {
  task_id: 't1',
  url: 'https://example.com/v/a',
  type: 'video',
  platform: 'douyin',
  title: 'Video A',
  cover: null,
  duration: '01:23',
  file_size_mb: 12.5,
  format: 'mp4',
  available_qualities: ['1080p', '720p'],
  available_bitrates: [],
};

const musicTask: ParseResult = {
  task_id: 't2',
  url: 'https://example.com/m/b',
  type: 'music',
  platform: 'netease',
  title: 'Song B',
  cover: null,
  duration: '04:00',
  file_size_mb: 8,
  format: 'mp3',
  available_qualities: [],
  available_bitrates: ['320kbps', 'FLAC'],
};

const imageTask: ParseResult = {
  task_id: 't3',
  url: 'https://example.com/p/c',
  type: 'image',
  platform: 'xiaohongshu',
  title: 'Post C',
  cover: 'https://example.com/c.jpg',
  duration: null,
  file_size_mb: 0.8,
  format: 'jpg',
  available_qualities: [],
  available_bitrates: [],
};

const videoPreview: PreviewData = {
  task_id: 't1',
  preview_type: 'video',
  url: 'https://example.com/v/a',
  platform: 'douyin',
  title: 'Video A',
  cover: null,
  duration: '01:23',
  format: 'mp4',
  file_size_mb: 12.5,
  available_qualities: ['1080p', '720p'],
  available_bitrates: [],
  streams: [
    { quality: '1080p', format: 'mp4' },
    { quality: '720p', format: 'mp4' },
  ],
};

const musicPreview: PreviewData = {
  task_id: 't2',
  preview_type: 'music',
  url: 'https://example.com/m/b',
  platform: 'netease',
  title: 'Song B',
  cover: null,
  duration: '04:00',
  format: 'mp3',
  file_size_mb: 8,
  available_qualities: [],
  available_bitrates: ['320kbps', 'FLAC'],
  streams: [{ bitrate: '320kbps', format: 'mp3' }],
};

const imagePreview: PreviewData = {
  task_id: 't3',
  preview_type: 'image',
  url: 'https://example.com/p/c',
  platform: 'xiaohongshu',
  title: 'Post C',
  cover: 'https://example.com/c.jpg',
  duration: null,
  format: 'jpg',
  file_size_mb: 0.8,
  available_qualities: [],
  available_bitrates: [],
  streams: [],
};

describe('PreviewModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePreviewStore.setState({ activeTask: null });
    useDownloadsStore.setState({ items: [], submitting: {} });
    __resetDownloadStreams();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders nothing while no task is active', () => {
    renderWithProviders(<PreviewModal />);
    expect(screen.queryByTestId('preview-modal')).not.toBeInTheDocument();
  });

  it('shows a loading state while the preview is fetched', () => {
    let resolvePreview!: (value: unknown) => void;
    (previewApi.getPreview as Mock).mockReturnValue(new Promise((resolve) => { resolvePreview = resolve; }));
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    expect(screen.getByTestId('preview-loading')).toBeInTheDocument();
    resolvePreview(videoPreview);
  });

  it('renders video metadata, the streams ladder and the metadata-only note', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(videoPreview);
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    const content = await screen.findByTestId('preview-content');
    expect(content).toHaveTextContent('douyin');
    expect(content).toHaveTextContent('01:23');
    expect(content).toHaveTextContent('mp4');
    expect(content).toHaveTextContent('12.5 MB');
    expect(screen.getByTestId('preview-metadata-note')).toBeInTheDocument();
    // Streams ladder: both qualities as rows (scoped — the quality picker
    // also shows its selected value).
    const streams = await screen.findByTestId('preview-streams');
    expect(within(streams).getByText('1080p')).toBeInTheDocument();
    expect(within(streams).getByText('720p')).toBeInTheDocument();
    // Quality picker present, bitrate picker absent.
    expect(screen.getByTestId('preview-quality-select')).toBeInTheDocument();
    expect(screen.queryByTestId('preview-bitrate-select')).not.toBeInTheDocument();
  });

  it('plays a completed download inline for a video preview', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(videoPreview);
    useDownloadsStore.setState({
      items: [
        {
          download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A',
          format: 'mp4', quality: '1080p', created_at: '2026-01-01T00:00:00Z',
          progress: 1, speed: null, downloaded_bytes: null, total_bytes: null,
          remaining_time: null, error_code: null, error_message: null,
          download_url: '/api/download/file/d1?token=t',
          token_expire_at: '2099-01-01T00:00:00Z',
        },
      ],
    });
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    expect(await screen.findByTestId('preview-content')).toBeInTheDocument();
    // react-player wrapper renders (it manages the inner <video> itself).
    expect(screen.getByTestId('preview-video-player')).toBeInTheDocument();
    // Real playback replaces the metadata-only note.
    expect(screen.queryByTestId('preview-metadata-note')).not.toBeInTheDocument();
  });

  it('shows the play-after-download hint for a video preview without a completed download', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(videoPreview);
    (downloadApi.getLatestByTask as Mock).mockRejectedValue(new ApiError('任务不存在 / Task not found', 3001, 400));
    useDownloadsStore.setState({ items: [] });
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    expect(await screen.findByTestId('preview-content')).toBeInTheDocument();
    expect(screen.getByTestId('preview-metadata-note')).toHaveTextContent('下载完成后可在此播放');
  });

  it('re-attaches a completed download after a reload and shows the player', async () => {
    // Simulates the recovery path: the session-local store is empty (F5),
    // but the backend has the task's completed download.
    (previewApi.getPreview as Mock).mockResolvedValue(videoPreview);
    (downloadApi.getLatestByTask as Mock).mockResolvedValue({
      download_id: 'd1', task_id: 't1', status: 'completed', progress: 100,
      speed: null, downloaded_bytes: 100, total_bytes: 100,
      remaining_time: null, error_message: null,
    });
    useDownloadsStore.setState({ items: [] });
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    // The completed download is upserted into the store (recovery wiring).
    await waitFor(() => {
      const item = useDownloadsStore.getState().items.find((i) => i.download_id === 'd1');
      expect(item?.status).toBe('completed');
    });
    expect(downloadApi.getLatestByTask).toHaveBeenCalledWith('t1');
  });

  it('renders a music preview with the bitrate ladder', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(musicPreview);
    usePreviewStore.setState({ activeTask: musicTask });
    renderWithProviders(<PreviewModal />);

    expect(await screen.findByTestId('preview-content')).toBeInTheDocument();
    expect(screen.getByTestId('preview-bitrate-select')).toBeInTheDocument();
    expect(screen.queryByTestId('preview-quality-select')).not.toBeInTheDocument();
    expect(within(await screen.findByTestId('preview-streams')).getByText('320kbps')).toBeInTheDocument();
  });

  it('renders an image preview with the cover image and no metadata note', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(imagePreview);
    usePreviewStore.setState({ activeTask: imageTask });
    renderWithProviders(<PreviewModal />);

    expect(await screen.findByTestId('preview-cover')).toBeInTheDocument();
    expect(screen.queryByTestId('preview-metadata-note')).not.toBeInTheDocument();
  });

  it('shows the backend error with a working retry', async () => {
    (previewApi.getPreview as Mock)
      .mockRejectedValueOnce(new ApiError('任务不存在 / Task not found', 3001, 400))
      .mockResolvedValueOnce(videoPreview);
    const user = userEvent.setup();
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    expect(await screen.findByTestId('preview-error')).toHaveTextContent(/Task not found/);
    expect(previewApi.getPreview).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId('preview-retry'));
    expect(await screen.findByTestId('preview-content')).toBeInTheDocument();
    expect(previewApi.getPreview).toHaveBeenCalledTimes(2);
  });

  it('submits a download through downloadsStore from the shortcut button', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(videoPreview);
    (downloadApi.submit as Mock).mockResolvedValue({
      download_id: 'd1', task_id: 't1', status: 'pending', created_at: '2026-01-01T00:00:00Z',
    });
    const user = userEvent.setup();
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    await screen.findByTestId('preview-content');
    await user.click(screen.getByTestId('preview-download'));

    await waitFor(() => expect(selectActiveCount(useDownloadsStore.getState())).toBe(1));
    // Video: format from the preview, quality from the picker default. The
    // store passes only format/quality to the API (title is store-local).
    expect(downloadApi.submit).toHaveBeenCalledWith('t1', { format: 'mp4', quality: '1080p' });
    expect(useDownloadsStore.getState().items[0].title).toBe('Video A');
  });

  it('toasts the backend message when the download submit fails', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(videoPreview);
    (downloadApi.submit as Mock).mockRejectedValue(new ApiError('任务已在下载 / Task already downloading', 3002, 409));
    const user = userEvent.setup();
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    await screen.findByTestId('preview-content');
    await user.click(screen.getByTestId('preview-download'));

    expect(await screen.findByText(/Task already downloading/)).toBeInTheDocument();
  });

  it('ignores a stale preview response when the task changed mid-flight', async () => {
    let resolveA!: (value: unknown) => void;
    let resolveB!: (value: unknown) => void;
    (previewApi.getPreview as Mock)
      .mockReturnValueOnce(new Promise((resolve) => { resolveA = resolve; }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveB = resolve; }));

    usePreviewStore.setState({ activeTask: videoTask }); // t1 → slow request A
    renderWithProviders(<PreviewModal />);

    // Switch to t2 while A is still in flight → fast request B.
    usePreviewStore.setState({ activeTask: musicTask });
    resolveB(musicPreview);
    expect(await screen.findByText('Song B')).toBeInTheDocument();

    // A resolves LATE — must not overwrite t2's metadata.
    resolveA(videoPreview);
    await waitFor(() => expect(screen.getByTestId('preview-content')).toHaveTextContent('netease'));
    expect(screen.queryByText('douyin')).not.toBeInTheDocument();
    expect(screen.queryByTestId('preview-quality-select')).not.toBeInTheDocument();
    expect(screen.getByTestId('preview-bitrate-select')).toBeInTheDocument();
  });

  it('closes via the modal close button and clears the preview store', async () => {
    (previewApi.getPreview as Mock).mockResolvedValue(videoPreview);
    const user = userEvent.setup();
    usePreviewStore.setState({ activeTask: videoTask });
    renderWithProviders(<PreviewModal />);

    await screen.findByTestId('preview-content');
    await user.click(document.querySelector('.ant-modal-close') as HTMLElement);

    expect(usePreviewStore.getState().activeTask).toBeNull();
    // destroyOnClose unmounts the dialog after the leave motion; jsdom runs
    // rc-motion's no-transition fallback immediately.
    await waitFor(() => expect(screen.queryByTestId('preview-modal')).not.toBeInTheDocument());
  });
});
