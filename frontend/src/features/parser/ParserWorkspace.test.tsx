import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ParserWorkspace, MAX_TXT_IMPORT_BYTES } from './ParserWorkspace';
import { renderWithProviders } from '../../test/utils';
import { parseApi, downloadApi } from '../../services/api';
import { ApiError } from '../../types/api';
import type { ParseResult } from '../../types/api';
import { useAppStore } from '../../stores/appStore';
import { useDownloadsStore } from '../../stores/downloadsStore';
import { useParserStore, PARSER_EMPTY_INPUT_MESSAGE, PARSER_TOO_MANY_URLS_MESSAGE } from './parserStore';
import { usePreviewStore } from './previewStore';
import { useCookieStore } from '../cookies/cookieStore';

vi.mock('../../services/api', () => ({
  parseApi: { parse: vi.fn() },
  isSafePublicPreviewRoute: (value: unknown, taskId: unknown, kind?: string, suffix?: string) => {
    if (typeof value !== 'string' || typeof taskId !== 'string') return false;
    const prefix = `/api/preview/${taskId}/resources/`;
    const parts = value.startsWith(prefix) ? value.slice(prefix.length).split('/') : [];
    return parts.length === (suffix ? 3 : 2) && (!kind || parts[0] === kind) &&
      ['video', 'image', 'live'].includes(parts[0]) && /^(0|[1-9]\d*)$/.test(parts[1]) &&
      (!suffix || parts[2] === suffix);
  },
  downloadApi: { submit: vi.fn(), getProgress: vi.fn(), getFileUrl: (url: string) => url },
  mediaApi: {
    streamUrl: (taskId: string) => `/api/preview/${taskId}/stream`,
    imageUrl: (taskId: string, index: number) => `/api/preview/${taskId}/images/${index}`,
    albumZipUrl: (taskId: string) => `/api/preview/${taskId}/images.zip`,
  },
  cookieApi: {
    list: vi.fn().mockResolvedValue({ cookies: [] }),
    set: vi.fn(),
    remove: vi.fn(),
  },
}));

// Task 15: downloadsStore now opens a WS client per download; jsdom has no
// WebSocket, so the store would silently fall back to polling instead. Mock
// the client so submit keeps the WS path (and no poll timer interferes).
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

// The card's inline media (xgplayer / swiper) would pull heavy real libs into
// jsdom; mock them to lightweight stubs that expose the props under test.
vi.mock('./VideoPlayer', () => ({
  VideoPlayer: ({ sources, testId }: { sources: { url: string }[]; testId?: string }) => (
    <div data-testid={testId} data-sources={sources.map((s) => s.url).join(',')} />
  ),
}));

vi.mock('./ImageCarousel', () => ({
  ImageCarousel: ({ images, testId }: { images: string[]; testId?: string }) => (
    <div data-testid={testId} data-count={images.length} />
  ),
  COVER_FALLBACK: 'data:image/svg+xml;utf8,fallback',
}));

// --- fixtures (unchanged) ---

const videoResult: ParseResult = {
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
  manifest: {
    kind: 'video',
    videos: [{ url: '/api/preview/t1/resources/video/0', format: 'mp4', quality: '1080p' }],
  },
};

const musicResult: ParseResult = {
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
  manifest: null,
};

const imageResult: ParseResult = {
  task_id: 't3',
  url: 'https://example.com/p/c',
  type: 'image',
  platform: 'xiaohongshu',
  title: 'Post C',
  cover: '/api/preview/t3/resources/image/0',
  duration: null,
  file_size_mb: 0.8,
  format: 'jpg',
  available_qualities: [],
  available_bitrates: [],
  manifest: {
    kind: 'image_album',
    images: [
      { url: '/api/preview/t3/resources/image/0', format: 'jpg' },
      { url: '/api/preview/t3/resources/image/1', format: 'jpg' },
    ],
  },
};

// Engine-style video: the backend resolved a direct playable URL, so the card
// plays it inline via the stream proxy without waiting for a download.
const manifestVideoResult: ParseResult = {
  task_id: 't4',
  url: 'https://v.douyin.com/xyz/',
  type: 'video',
  platform: 'douyin',
  title: 'Video D',
  cover: null,
  duration: null,
  file_size_mb: 5.2,
  format: 'mp4',
  available_qualities: [],
  available_bitrates: [],
  manifest: {
    kind: 'video',
    videos: [{ url: '/api/preview/t4/resources/video/0', format: 'mp4', quality: null }],
  },
};

const livePhotoResult: ParseResult = {
  task_id: 't5',
  url: 'https://example.com/live/e',
  type: 'live_photo',
  platform: 'douyin',
  title: 'Live E',
  cover: '/api/preview/t5/resources/live/0/image',
  duration: null,
  file_size_mb: null,
  format: 'heic',
  available_qualities: [],
  available_bitrates: [],
  manifest: {
    kind: 'live_photo',
    live_photos: [
      {
        image_url: '/api/preview/t5/resources/live/0/image',
        motion_url: '/api/preview/t5/resources/live/0/motion',
      },
    ],
    warnings: [],
  },
};

// --- helpers ---

/** Type one URL into the one-line search input and click the search button. */
async function submitUrl(url: string): Promise<void> {
  const user = userEvent.setup();
  await user.type(screen.getByTestId('url-input'), url);
  await user.click(screen.getByRole('button', { name: /解\s*析/ }));
}

/** Seed the store with raw multi-line input (TXT-import equivalent), then search. */
async function parseSeeded(urls: string): Promise<void> {
  useParserStore.setState({ input: urls });
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: /解\s*析/ }));
}

// A matchMedia that reports no breakpoints — i.e. a small/mobile viewport.
function mobileMatchMedia(): () => {
  matches: boolean;
  media: string;
  onchange: null;
  addListener: ReturnType<typeof vi.fn>;
  removeListener: ReturnType<typeof vi.fn>;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
  dispatchEvent: ReturnType<typeof vi.fn>;
} {
  return () => ({
    matches: false,
    media: '',
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
}

describe('ParserWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useParserStore.setState({ input: '', results: [], failed: [], status: 'idle', error: null });
    useDownloadsStore.setState({ items: [], submitting: {} });
    usePreviewStore.setState({ activeTask: null });
    useAppStore.setState({ mediaMode: 'video' });
    useCookieStore.setState({ drawerOpen: false, entries: [], loading: false, error: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the search input, mode hint and action buttons', () => {
    renderWithProviders(<ParserWorkspace />);
    expect(screen.getByTestId('url-input')).toBeInTheDocument();
    expect(screen.getByTestId('mode-hint')).toHaveTextContent('当前模式：视频');
    expect(screen.getByRole('button', { name: /解\s*析/ })).toBeInTheDocument();
    expect(screen.getByTestId('import-txt-button')).toBeInTheDocument();
    expect(screen.getByTestId('clear-button')).toBeInTheDocument();
    expect(screen.getByTestId('parser-empty')).toBeInTheDocument();
  });

  it('keeps the compact search + TXT group on mobile', () => {
    vi.stubGlobal('matchMedia', mobileMatchMedia());
    renderWithProviders(<ParserWorkspace />);
    expect(screen.getByTestId('url-input')).toBeInTheDocument();
    // Icon-only search button on mobile (the icon's accessible name).
    expect(screen.getByRole('button', { name: /scan/i })).toBeInTheDocument();
    expect(screen.getByTestId('import-txt-button')).toBeInTheDocument();
  });

  it('blocks an empty submission and shows the friendly error', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await user.click(screen.getByRole('button', { name: /解\s*析/ }));

    expect(parseApi.parse).not.toHaveBeenCalled();
    expect(await screen.findByTestId('parse-error')).toHaveTextContent(PARSER_EMPTY_INPUT_MESSAGE);
  });

  it('parses the input URLs and renders the result cards plus failed list', async () => {
    (parseApi.parse as Mock).mockResolvedValue({
      results: [videoResult, musicResult],
      failed: [{ url: 'https://example.com/bad', error: '平台不支持 / Unsupported platform' }],
    });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/v/a\nhttps://example.com/m/b\nhttps://example.com/bad');

    expect(parseApi.parse).toHaveBeenCalledWith([
      'https://example.com/v/a',
      'https://example.com/m/b',
      'https://example.com/bad',
    ]);

    // Video mode filters the grid to video results; the music card is hidden.
    const card = await screen.findByTestId('result-card-t1');
    expect(within(card).getByTestId('title-t1')).toHaveTextContent('Video A');
    expect(within(card).getByTestId('platform-t1')).toHaveTextContent('douyin');
    expect(within(card).getByText('mp4')).toBeInTheDocument();
    expect(within(card).getByText('12.5 MB')).toBeInTheDocument();
    expect(within(card).getByTestId('card-player-t1')).toBeInTheDocument();
    expect(screen.queryByTestId('result-card-t2')).not.toBeInTheDocument();

    expect(screen.getByTestId('parser-failed')).toHaveTextContent('平台不支持');
    // Summary counts the mode-visible cards (video mode → the video card only).
    expect(screen.getByTestId('parser-summary')).toHaveTextContent('成功 1 个，失败 1 个');
  });

  it('renders image results in both media modes', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult, imageResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/v/a\nhttps://example.com/p/c');

    // Video mode: the video card and the image card.
    expect(await screen.findByTestId('result-card-t1')).toBeInTheDocument();
    expect(screen.getByTestId('result-card-t3')).toBeInTheDocument();

    // Music mode: the image card stays visible, the video card hides.
    // act() flushes the re-keyed grid synchronously (the grid remounts on
    // mediaMode change, so a stale pre-flush element would be detached).
    act(() => useAppStore.setState({ mediaMode: 'music' }));
    expect(await screen.findByTestId('result-card-t3')).toBeInTheDocument();
    expect(screen.queryByTestId('result-card-t1')).not.toBeInTheDocument();
  });

  it('switching the media mode refilters the displayed cards', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult, musicResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/v/a\nhttps://example.com/m/b');
    await screen.findByTestId('result-card-t1');

    useAppStore.setState({ mediaMode: 'music' });
    expect(await screen.findByTestId('result-card-t2')).toBeInTheDocument();
    expect(screen.queryByTestId('result-card-t1')).not.toBeInTheDocument();
  });

  it('re-keys the grid with the media mode so cards replay the animation', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult, musicResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/v/a\nhttps://example.com/m/b');

    const grid = await screen.findByTestId('parser-grid');
    expect(grid).toHaveAttribute('data-mode', 'video');
    expect(grid.querySelector('.parser-grid-item')).toBeInTheDocument();

    // act() flushes the re-keyed grid synchronously: React 18 defers renders
    // scheduled outside React events, so without it the synchronous
    // getByTestId below would read the pre-flush grid (data-mode="video").
    act(() => useAppStore.setState({ mediaMode: 'music' }));
    // The grid remounts (key={mediaMode}) — DOM node identity changes — which
    // is what replays the staggered animation. Pin the re-key explicitly.
    expect(screen.getByTestId('parser-grid')).not.toBe(grid);
    expect(screen.getByTestId('parser-grid')).toHaveAttribute('data-mode', 'music');
    expect(await screen.findByTestId('result-card-t2')).toBeInTheDocument();
    expect(screen.queryByTestId('result-card-t1')).not.toBeInTheDocument();
  });

  it('shows a media-type badge on each card cover', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult, musicResult, imageResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/v/a\nhttps://example.com/m/b\nhttps://example.com/p/c');

    // Video mode renders the video and image cards, each with a type badge;
    // the music card is filtered out of this mode (selectVisibleResults).
    expect(await screen.findByTestId('type-badge-t1')).toBeInTheDocument();
    expect(screen.getByTestId('type-badge-t3')).toBeInTheDocument();

    // Music mode swaps in the music card, which carries its own badge.
    act(() => useAppStore.setState({ mediaMode: 'music' }));
    expect(await screen.findByTestId('type-badge-t2')).toBeInTheDocument();
  });

  it('shows loading on the search button while a parse is in flight', async () => {
    let resolveParse!: (value: unknown) => void;
    (parseApi.parse as Mock).mockReturnValue(new Promise((resolve) => { resolveParse = resolve; }));
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await user.type(screen.getByTestId('url-input'), 'https://example.com/v/a');
    await user.click(screen.getByRole('button', { name: /解\s*析/ }));

    expect(screen.getByRole('button', { name: /解\s*析/ })).toHaveClass('ant-btn-loading');
    expect(screen.getByTestId('import-txt-button')).toBeDisabled();

    resolveParse({ results: [videoResult], failed: [] });
    expect(await screen.findByTestId('result-card-t1')).toBeInTheDocument();
  });

  it('parses when the user presses Enter in the search input', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await user.type(screen.getByTestId('url-input'), 'https://example.com/v/a');
    await user.keyboard('{Enter}');

    expect(await screen.findByTestId('result-card-t1')).toBeInTheDocument();
    expect(parseApi.parse).toHaveBeenCalledWith(['https://example.com/v/a']);
  });

  it('clearing the input with the allowClear icon does not trigger a parse', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await user.type(screen.getByTestId('url-input'), 'https://example.com/v/a');

    // antd renders the allowClear ✖ as role="button" once the input has a value.
    await user.click(screen.getByRole('button', { name: /close/i }));

    expect(parseApi.parse).not.toHaveBeenCalled();
    expect(screen.queryByTestId('parse-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('url-input')).toHaveValue('');
  });

  it('shows the backend error with a working retry', async () => {
    (parseApi.parse as Mock).mockRejectedValueOnce(new ApiError('URL格式无效 / Invalid URL format', 1002, 400));
    (parseApi.parse as Mock).mockResolvedValueOnce({ results: [videoResult], failed: [] });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    // A scheme-qualified URL passes client-side extraction so the backend's
    // rejection surfaces (prose without a URL is filtered client-side).
    await user.type(screen.getByTestId('url-input'), 'https://not-a-real-host/x');
    await user.click(screen.getByRole('button', { name: /解\s*析/ }));

    expect(await screen.findByTestId('parse-error')).toHaveTextContent('URL格式无效');
    expect(parseApi.parse).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId('parse-retry'));
    expect(await screen.findByTestId('result-card-t1')).toBeInTheDocument();
    expect(parseApi.parse).toHaveBeenCalledTimes(2);
  });

  it('prompts to re-configure cookies when a failure carries code 1006', async () => {
    (parseApi.parse as Mock).mockResolvedValue({
      results: [],
      failed: [
        {
          url: 'https://v.douyin.com/abc/',
          error: 'Cookie 无效或已过期，请重新设置 / Cookie invalid or expired — please update it',
          code: 1006,
        },
      ],
    });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://v.douyin.com/abc/');

    const alert = await screen.findByTestId('cookie-alert');
    expect(alert).toHaveTextContent(/Cookie 缺失或无效/);

    await user.click(screen.getByTestId('cookie-settings-link'));
    expect(useCookieStore.getState().drawerOpen).toBe(true);
    useCookieStore.setState({ drawerOpen: false });
  });

  it('does not show the cookie alert when failures are unrelated', async () => {
    (parseApi.parse as Mock).mockResolvedValue({
      results: [],
      failed: [{ url: 'https://example.com/bad', error: '平台不支持 / Unsupported platform' }],
    });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/bad');
    expect(await screen.findByTestId('parser-failed')).toBeInTheDocument();
    expect(screen.queryByTestId('cookie-alert')).not.toBeInTheDocument();
  });

  it('shows the cookie alert alongside other failures in a mixed batch', async () => {
    (parseApi.parse as Mock).mockResolvedValue({
      results: [],
      failed: [
        {
          url: 'https://v.douyin.com/abc/',
          error: '该平台需要 Cookie，请先在设置中配置 / This platform requires a cookie — configure it in Settings',
          code: 1006,
        },
        { url: 'https://example.com/bad', error: '平台不支持 / Unsupported platform' },
      ],
    });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://v.douyin.com/abc/\nhttps://example.com/bad');

    expect(await screen.findByTestId('cookie-alert')).toBeInTheDocument();
    expect(screen.getByTestId('parser-failed')).toBeInTheDocument();
  });

  it('treats a code-null failure as unrelated to cookies', async () => {
    (parseApi.parse as Mock).mockResolvedValue({
      results: [],
      failed: [{ url: 'https://example.com/bad', error: '解析失败 / Parse failed', code: null }],
    });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/bad');
    expect(await screen.findByTestId('parser-failed')).toBeInTheDocument();
    expect(screen.queryByTestId('cookie-alert')).not.toBeInTheDocument();
  });

  it('records the clicked result in the preview seam on [预览] (music only)', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [musicResult], failed: [] });
    // Music cards only render in music mode (selectVisibleResults filters the
    // grid), so flip the header mode before submitting.
    act(() => useAppStore.setState({ mediaMode: 'music' }));
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await submitUrl('https://example.com/m/b');

    await user.click(await screen.findByTestId('preview-t2'));
    expect(usePreviewStore.getState().activeTask).toEqual(musicResult);
  });

  it('renders manifest-backed video and live-photo previews without downloading on parse', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult, livePhotoResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/v/a\nhttps://example.com/live/e');

    expect(await screen.findByTestId('card-player-t1')).toBeInTheDocument();
    expect(await screen.findByTestId('live-photo-t5')).toBeInTheDocument();
    expect(downloadApi.submit).not.toHaveBeenCalled();
    expect(useDownloadsStore.getState().items).toHaveLength(0);
  });

  it('does not submit downloads for music results while parsing', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [musicResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await submitUrl('https://example.com/m/b');

    expect(downloadApi.submit).not.toHaveBeenCalled();
  });

  it('plays a completed video download inline on the result card', async () => {
    // 主页面直接播放: a completed download with a valid link swaps the cover
    // for an inline react-player, no modal needed.
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    useDownloadsStore.setState({
      items: [
        {
          download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A',
          format: 'mp4', quality: '1080p', created_at: '2026-01-01T00:00:00Z',
          progress: 1, speed: null, downloaded_bytes: 100, total_bytes: 100,
          remaining_time: null, error_code: null, error_message: null,
          download_url: '/api/download/file/d1?token=t',
          token_expire_at: '2099-01-01T00:00:00Z',
        },
      ],
    });
    renderWithProviders(<ParserWorkspace />);
    await submitUrl('https://example.com/v/a');

    expect(await screen.findByTestId('card-player-t1')).toBeInTheDocument();
    // The cover/duration badge are replaced while the player is active.
    expect(screen.queryByTestId('duration-t1')).not.toBeInTheDocument();
  });

  it('reuses a completed download only when its selected variant matches', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    useDownloadsStore.setState({
      items: [
        {
          download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A',
          format: 'mp4', quality: '1080p', created_at: '2026-01-01T00:00:00Z',
          progress: 1, speed: null, downloaded_bytes: 100, total_bytes: 100,
          remaining_time: null, error_code: null, error_message: null,
          download_url: '/api/download/file/d1?token=t',
          token_expire_at: '2099-01-01T00:00:00Z',
        },
      ],
    });
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const user = userEvent.setup();
    try {
      renderWithProviders(<ParserWorkspace />);
      await submitUrl('https://example.com/v/a');
      await user.click(await screen.findByTestId('download-t1'));

      expect(open).toHaveBeenCalledWith('/api/download/file/d1?token=t', '_blank', 'noopener');
      expect(downloadApi.submit).not.toHaveBeenCalled();
    } finally {
      open.mockRestore();
    }
  });

  it('submits a new download when the selected variant differs from a completed one', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    (downloadApi.submit as Mock).mockResolvedValue({
      download_id: 'd2', task_id: 't1', status: 'pending', created_at: '2026-01-01T00:00:00Z',
    });
    useDownloadsStore.setState({
      items: [
        {
          download_id: 'd1', task_id: 't1', status: 'completed', title: 'Video A',
          format: 'mp4', quality: '720p', created_at: '2026-01-01T00:00:00Z',
          progress: 1, speed: null, downloaded_bytes: 100, total_bytes: 100,
          remaining_time: null, error_code: null, error_message: null,
          download_url: '/api/download/file/d1?token=t',
          token_expire_at: '2099-01-01T00:00:00Z',
        },
      ],
    });
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const user = userEvent.setup();
    try {
      renderWithProviders(<ParserWorkspace />);
      await submitUrl('https://example.com/v/a');
      await user.click(screen.getByTestId('download-t1'));

      expect(open).not.toHaveBeenCalled();
      expect(downloadApi.submit).toHaveBeenCalledWith('t1', {
        format: 'mp4', quality: '1080p',
      });
    } finally {
      open.mockRestore();
    }
  });

  it('plays a manifest video inline through its same-origin resource route', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [manifestVideoResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await submitUrl('https://v.douyin.com/xyz/');

    const player = await screen.findByTestId('card-player-t4');
    expect(player).toHaveAttribute('data-sources', '/api/preview/t4/resources/video/0');
    // [预览] is gone; [下载] remains.
    expect(screen.queryByTestId('preview-t4')).not.toBeInTheDocument();
    expect(screen.getByTestId('download-t4')).toBeInTheDocument();
  });

  it('renders the image carousel with download-current and download-all', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [imageResult], failed: [] });
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const user = userEvent.setup();
    try {
      renderWithProviders(<ParserWorkspace />);
      await submitUrl('https://example.com/p/c');

      const carousel = await screen.findByTestId('carousel-t3');
      expect(carousel).toHaveAttribute('data-count', '2');

      await user.click(screen.getByTestId('download-current-t3'));
      expect(open).toHaveBeenCalledWith('/api/preview/t3/images/0', '_blank', 'noopener');

      await user.click(screen.getByTestId('download-all-t3'));
      expect(open).toHaveBeenCalledWith('/api/preview/t3/images.zip', '_blank', 'noopener');

      expect(screen.queryByTestId('preview-t3')).not.toBeInTheDocument();
    } finally {
      // clearAllMocks() in beforeEach only clears call history — restore the
      // spy's real implementation so no later test inherits the stub.
      open.mockRestore();
    }
  });

  it('toasts the backend message when a download submit fails', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    (downloadApi.submit as Mock).mockRejectedValue(new ApiError('任务不存在 / Task not found', 3001, 400));
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await submitUrl('https://example.com/v/a');

    await user.click(await screen.findByTestId('download-t1'));
    expect(await screen.findByText(/Task not found/)).toBeInTheDocument();
    expect(useDownloadsStore.getState().items).toHaveLength(0);
  });

  it('imports a plain-text TXT file into the input and reports the batch size', async () => {
    renderWithProviders(<ParserWorkspace />);
    const file = new File(['https://example.com/v/a\nhttps://example.com/m/b'], 'links.txt', {
      type: 'text/plain',
    });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const user = userEvent.setup();
    await user.upload(input, file);

    // The store keeps the raw multi-line text after import. (The one-line
    // search input itself sanitizes line breaks out of its DOM value — HTML
    // value sanitization strips \n/\r from single-line <input> values — so
    // the multi-line content is verified at the store, per the design note
    // "a single-line input cannot contain \n".)
    await waitFor(() =>
      expect(useParserStore.getState().input).toBe('https://example.com/v/a\nhttps://example.com/m/b'),
    );
    expect(screen.getByTestId('extracted-count')).toHaveTextContent('已提取 2 条链接');
  });

  it('extracts the link from pasted share text before parsing', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    const share =
      '2.84 10/22 A@G.vF seB:/ :8pm 小麦和威龙偶遇小博博会发生什么？ # 三角洲行动 ' +
      'https://v.douyin.com/wLOh31JiznU/ 复制此链接，打开Dou音搜索，直接观看视频！';
    await user.type(screen.getByTestId('url-input'), share);
    await user.click(screen.getByRole('button', { name: /解\s*析/ }));

    expect(await screen.findByTestId('result-card-t1')).toBeInTheDocument();
    expect(parseApi.parse).toHaveBeenCalledWith(['https://v.douyin.com/wLOh31JiznU/']);
  });

  it('rejects a non-TXT file via the workspace guard with a toast', async () => {
    // The OS file dialog treats `accept` as advisory, so a non-TXT file CAN be
    // picked in a real browser. userEvent pre-filters by accept by default
    // (applyAccept), which would bypass the app entirely — disable that so the
    // .csv reaches beforeUpload exactly as it would from a real picker.
    const user = userEvent.setup({ applyAccept: false });
    renderWithProviders(<ParserWorkspace />);
    const file = new File(['nope'], 'links.csv', { type: 'text/csv' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    // rc-upload's input-change handler passes every file to beforeUpload when
    // `directory` is unset (`!directory || attrAccept(...)`), so the
    // workspace's isTxt guard is load-bearing here: it toasts and returns
    // Upload.LIST_IGNORE, leaving the input untouched.
    expect(await screen.findByText('仅支持 TXT 文本文件')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('url-input')).toHaveValue(''));
  });

  it('rejects an oversized TXT import with a toast', async () => {
    renderWithProviders(<ParserWorkspace />);
    // A file just over the 1 MB guard — decoding it would freeze the tab.
    const big = new Array(MAX_TXT_IMPORT_BYTES + 16).fill('a').join('');
    const file = new File([big], 'big.txt', { type: 'text/plain' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const user = userEvent.setup();
    await user.upload(input, file);

    expect(await screen.findByText('TXT 文件过大(最大 1 MB)')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('url-input')).toHaveValue(''));
  });

  it('toasts when the TXT file cannot be read', async () => {
    // Simulate a FileReader that fails (e.g. a locked file).
    class FailingFileReader {
      onload: ((event: unknown) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      error: Error | null = new Error('boom');
      readAsText(): void {
        setTimeout(() => this.onerror?.({}), 0);
      }
    }
    vi.stubGlobal('FileReader', FailingFileReader);

    renderWithProviders(<ParserWorkspace />);
    const file = new File(['content'], 'links.txt', { type: 'text/plain' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const user = userEvent.setup();
    await user.upload(input, file);

    expect(await screen.findByText('TXT 文件读取失败')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('url-input')).toHaveValue(''));
  });

  it('blocks a parse with more than 50 URLs at the component level', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    const urls = Array.from({ length: 51 }, (_, i) => `https://example.com/x/${i}`);
    useParserStore.setState({ input: urls.join('\n') });
    await user.click(screen.getByRole('button', { name: /解\s*析/ }));

    expect(await screen.findByTestId('parse-error')).toHaveTextContent(PARSER_TOO_MANY_URLS_MESSAGE);
    expect(parseApi.parse).not.toHaveBeenCalled();
  });

  it('updates the mode hint when the header mode changes', async () => {
    renderWithProviders(<ParserWorkspace />);
    expect(screen.getByTestId('mode-hint')).toHaveTextContent('当前模式：视频');

    useAppStore.setState({ mediaMode: 'music' });
    expect(await screen.findByText('当前模式：音乐')).toBeInTheDocument();
  });

  it('renders duplicate failed URLs without key collisions', async () => {
    (parseApi.parse as Mock).mockResolvedValue({
      results: [videoResult],
      failed: [
        { url: 'https://example.com/dup', error: '第一次失败 / first failure' },
        { url: 'https://example.com/dup', error: '第二次失败 / second failure' },
      ],
    });
    renderWithProviders(<ParserWorkspace />);
    await parseSeeded('https://example.com/v/a\nhttps://example.com/dup\nhttps://example.com/dup');

    const failed = await screen.findByTestId('parser-failed');
    expect(within(failed).getAllByText('https://example.com/dup')).toHaveLength(2);
    expect(within(failed).getByText(/second failure/)).toBeInTheDocument();
  });

  it('clears the workspace via the clear button', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await submitUrl('https://example.com/v/a');
    await screen.findByTestId('result-card-t1');

    await user.click(screen.getByTestId('clear-button'));
    expect(useParserStore.getState().input).toBe('');
    expect(useParserStore.getState().status).toBe('idle');
    expect(screen.queryByTestId('parser-grid')).not.toBeInTheDocument();
  });
});
