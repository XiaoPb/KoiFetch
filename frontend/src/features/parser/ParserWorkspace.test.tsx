import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ParserWorkspace } from './ParserWorkspace';
import { renderWithProviders } from '../../test/utils';
import { parseApi, downloadApi } from '../../services/api';
import { ApiError } from '../../types/api';
import type { ParseResult } from '../../types/api';
import { useAppStore } from '../../stores/appStore';
import { useDownloadsStore } from '../../stores/downloadsStore';
import { useParserStore, PARSER_EMPTY_INPUT_MESSAGE } from './parserStore';
import { usePreviewStore } from './previewStore';

vi.mock('../../services/api', () => ({
  parseApi: { parse: vi.fn() },
  downloadApi: { submit: vi.fn() },
}));

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
};

async function submitUrls(urls: string): Promise<void> {
  const user = userEvent.setup();
  await user.type(screen.getByTestId('url-input'), urls);
  await user.click(screen.getByTestId('parse-button'));
}

describe('ParserWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useParserStore.setState({ input: '', results: [], failed: [], status: 'idle', error: null });
    useDownloadsStore.setState({ items: [], activeCount: 0, submitting: {} });
    usePreviewStore.setState({ activeTask: null });
    useAppStore.setState({ mediaMode: 'video' });
  });

  it('renders the input, mode hint and action buttons', () => {
    renderWithProviders(<ParserWorkspace />);
    expect(screen.getByTestId('url-input')).toBeInTheDocument();
    expect(screen.getByTestId('mode-hint')).toHaveTextContent('当前模式：视频');
    expect(screen.getByTestId('parse-button')).toBeInTheDocument();
    expect(screen.getByTestId('import-txt-button')).toBeInTheDocument();
    expect(screen.getByTestId('clear-button')).toBeInTheDocument();
    expect(screen.getByTestId('parser-empty')).toBeInTheDocument();
  });

  it('blocks an empty submission and shows the friendly error', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await user.click(screen.getByTestId('parse-button'));

    expect(parseApi.parse).not.toHaveBeenCalled();
    expect(await screen.findByTestId('parse-error')).toHaveTextContent(PARSER_EMPTY_INPUT_MESSAGE);
  });

  it('parses typed URLs and renders the result cards plus failed list', async () => {
    (parseApi.parse as Mock).mockResolvedValue({
      results: [videoResult, musicResult],
      failed: [{ url: 'https://example.com/bad', error: '平台不支持 / Unsupported platform' }],
    });
    renderWithProviders(<ParserWorkspace />);
    await submitUrls('https://example.com/v/a\nhttps://example.com/m/b\nhttps://example.com/bad');

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
    expect(within(card).getByTestId('duration-t1')).toHaveTextContent('01:23');
    expect(screen.queryByTestId('result-card-t2')).not.toBeInTheDocument();

    expect(screen.getByTestId('parser-failed')).toHaveTextContent('平台不支持');
    expect(screen.getByTestId('parser-summary')).toHaveTextContent('成功 2 个，失败 1 个');
  });

  it('switching the media mode refilters the displayed cards', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult, musicResult], failed: [] });
    renderWithProviders(<ParserWorkspace />);
    await submitUrls('https://example.com/v/a\nhttps://example.com/m/b');
    await screen.findByTestId('result-card-t1');

    useAppStore.setState({ mediaMode: 'music' });
    expect(await screen.findByTestId('result-card-t2')).toBeInTheDocument();
    expect(screen.queryByTestId('result-card-t1')).not.toBeInTheDocument();
  });

  it('shows the loading state while a parse is in flight', async () => {
    let resolveParse!: (value: unknown) => void;
    (parseApi.parse as Mock).mockReturnValue(new Promise((resolve) => { resolveParse = resolve; }));
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await user.type(screen.getByTestId('url-input'), 'https://example.com/v/a');
    await user.click(screen.getByTestId('parse-button'));

    expect(screen.getByTestId('parser-loading')).toBeInTheDocument();
    resolveParse({ results: [videoResult], failed: [] });
    expect(await screen.findByTestId('result-card-t1')).toBeInTheDocument();
  });

  it('shows the backend error with a working retry', async () => {
    (parseApi.parse as Mock).mockRejectedValueOnce(new ApiError('URL格式无效 / Invalid URL format', 1002, 400));
    (parseApi.parse as Mock).mockResolvedValueOnce({ results: [videoResult], failed: [] });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await user.type(screen.getByTestId('url-input'), 'not-a-url');
    await user.click(screen.getByTestId('parse-button'));

    expect(await screen.findByTestId('parse-error')).toHaveTextContent('URL格式无效');
    expect(parseApi.parse).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId('parse-retry'));
    expect(await screen.findByTestId('result-card-t1')).toBeInTheDocument();
    expect(parseApi.parse).toHaveBeenCalledTimes(2);
  });

  it('records the clicked result in the preview seam on [预览]', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await submitUrls('https://example.com/v/a');

    await user.click(await screen.findByTestId('preview-t1'));
    expect(usePreviewStore.getState().activeTask).toEqual(videoResult);
  });

  it('submits a download through downloadsStore and bumps the badge', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    (downloadApi.submit as Mock).mockResolvedValue({
      download_id: 'd1',
      task_id: 't1',
      status: 'pending',
      created_at: '2026-01-01T00:00:00Z',
    });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await submitUrls('https://example.com/v/a');

    await user.click(await screen.findByTestId('download-t1'));
    await waitFor(() => expect(useDownloadsStore.getState().activeCount).toBe(1));
    expect(downloadApi.submit).toHaveBeenCalledWith('t1', { format: 'mp4', quality: '1080p' });
    expect(useDownloadsStore.getState().items[0]).toMatchObject({
      download_id: 'd1',
      task_id: 't1',
      status: 'pending',
      format: 'mp4',
      quality: '1080p',
    });
  });

  it('toasts the backend message when a download submit fails', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    (downloadApi.submit as Mock).mockRejectedValue(new ApiError('任务不存在 / Task not found', 3001, 400));
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await submitUrls('https://example.com/v/a');

    await user.click(await screen.findByTestId('download-t1'));
    expect(await screen.findByText(/Task not found/)).toBeInTheDocument();
    expect(useDownloadsStore.getState().items).toHaveLength(0);
  });

  it('imports a plain-text TXT file into the input', async () => {
    renderWithProviders(<ParserWorkspace />);
    const file = new File(['https://example.com/v/a\nhttps://example.com/m/b'], 'links.txt', {
      type: 'text/plain',
    });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const user = userEvent.setup();
    await user.upload(input, file);

    await waitFor(() =>
      expect(screen.getByTestId('url-input')).toHaveValue('https://example.com/v/a\nhttps://example.com/m/b'),
    );
  });

  it('rejects a non-TXT import and leaves the input untouched', async () => {
    renderWithProviders(<ParserWorkspace />);
    const file = new File(['nope'], 'links.csv', { type: 'text/csv' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const user = userEvent.setup();
    await user.upload(input, file);

    await waitFor(() => expect(screen.getByTestId('url-input')).toHaveValue(''));
  });

  it('clears the workspace via the clear button', async () => {
    (parseApi.parse as Mock).mockResolvedValue({ results: [videoResult], failed: [] });
    const user = userEvent.setup();
    renderWithProviders(<ParserWorkspace />);
    await submitUrls('https://example.com/v/a');
    await screen.findByTestId('result-card-t1');

    await user.click(screen.getByTestId('clear-button'));
    expect(useParserStore.getState().input).toBe('');
    expect(useParserStore.getState().status).toBe('idle');
    expect(screen.queryByTestId('parser-grid')).not.toBeInTheDocument();
  });
});
