import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { parseApi } from '../../services/api';
import { ApiError } from '../../types/api';
import type { ParseResult } from '../../types/api';
import {
  MAX_PARSE_URLS,
  PARSER_EMPTY_INPUT_MESSAGE,
  PARSER_TOO_MANY_URLS_MESSAGE,
  extractUrls,
  selectVisibleResults,
  useParserStore,
} from './parserStore';

vi.mock('../../services/api', () => ({
  parseApi: { parse: vi.fn() },
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

const imageResult: ParseResult = {
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

const okParse = (overrides: Partial<{ results: ParseResult[]; failed: { url: string; error: string }[] }> = {}) => ({
  results: overrides.results ?? [videoResult],
  failed: overrides.failed ?? [],
});

describe('parserStore', () => {
  beforeEach(() => {
    useParserStore.setState({ input: '', results: [], failed: [], status: 'idle', error: null });
    vi.clearAllMocks();
  });

  it('splits raw input into trimmed non-blank URL lines', () => {
    expect(extractUrls('  https://a  \n\n  \nhttps://b\r\nhttps://c ')).toEqual([
      'https://a',
      'https://b',
      'https://c',
    ]);
    // Lone \r line endings (old-Mac TXT files) split too.
    expect(extractUrls('https://a\rhttps://b')).toEqual(['https://a', 'https://b']);
    expect(extractUrls('   \n\n  ')).toEqual([]);
  });

  it('populates results and failed on a successful parse', async () => {
    (parseApi.parse as Mock).mockResolvedValue(
      okParse({ results: [videoResult], failed: [{ url: 'https://bad', error: '平台不支持 / Unsupported platform' }] }),
    );
    useParserStore.setState({ input: 'https://example.com/v/a\nhttps://bad' });
    await useParserStore.getState().parse();

    const state = useParserStore.getState();
    expect(state.status).toBe('success');
    expect(state.error).toBeNull();
    expect(state.results).toEqual([videoResult]);
    expect(state.failed).toEqual([{ url: 'https://bad', error: '平台不支持 / Unsupported platform' }]);
    expect(parseApi.parse).toHaveBeenCalledWith(['https://example.com/v/a', 'https://bad']);
  });

  it('flips to loading while a parse is in flight', async () => {
    let resolveParse!: (value: unknown) => void;
    (parseApi.parse as Mock).mockReturnValue(new Promise((resolve) => { resolveParse = resolve; }));
    useParserStore.setState({ input: 'https://example.com/v/a' });

    const pending = useParserStore.getState().parse();
    expect(useParserStore.getState().status).toBe('loading');

    resolveParse(okParse());
    await pending;
    expect(useParserStore.getState().status).toBe('success');
  });

  it('clears stale results when a new parse starts', async () => {
    (parseApi.parse as Mock).mockResolvedValue(okParse());
    useParserStore.setState({ input: 'https://example.com/v/a' });
    await useParserStore.getState().parse();
    expect(useParserStore.getState().results).toHaveLength(1);

    let resolveParse!: (value: unknown) => void;
    (parseApi.parse as Mock).mockReturnValue(new Promise((resolve) => { resolveParse = resolve; }));
    useParserStore.setState({ input: 'https://example.com/m/b' });

    const pending = useParserStore.getState().parse();
    expect(useParserStore.getState().results).toEqual([]);

    resolveParse(okParse({ results: [musicResult] }));
    await pending;
    expect(useParserStore.getState().results).toEqual([musicResult]);
  });

  it('rejects an empty input without calling the API', async () => {
    useParserStore.setState({ input: '   \n  \n' });
    await useParserStore.getState().parse();

    const state = useParserStore.getState();
    expect(state.status).toBe('error');
    expect(state.error).toBe(PARSER_EMPTY_INPUT_MESSAGE);
    expect(state.results).toEqual([]);
    expect(parseApi.parse).not.toHaveBeenCalled();
  });

  it('rejects more than the 50-URL cap without calling the API', async () => {
    const urls = Array.from({ length: MAX_PARSE_URLS + 1 }, (_, i) => `https://example.com/x/${i}`);
    useParserStore.setState({ input: urls.join('\n') });
    await useParserStore.getState().parse();

    const state = useParserStore.getState();
    expect(state.status).toBe('error');
    expect(state.error).toBe(PARSER_TOO_MANY_URLS_MESSAGE);
    expect(parseApi.parse).not.toHaveBeenCalled();
  });

  it('stores the API error message when the backend rejects the parse', async () => {
    (parseApi.parse as Mock).mockRejectedValue(new ApiError('URL格式无效 / Invalid URL format', 1002, 400));
    useParserStore.setState({ input: 'not-a-url' });
    await useParserStore.getState().parse();

    const state = useParserStore.getState();
    expect(state.status).toBe('error');
    expect(state.error).toBe('URL格式无效 / Invalid URL format');
    expect(state.results).toEqual([]);
    expect(state.failed).toEqual([]);
  });

  it('reset clears input, results, failed, status and error', async () => {
    (parseApi.parse as Mock).mockResolvedValue(okParse());
    useParserStore.setState({ input: 'https://example.com/v/a' });
    await useParserStore.getState().parse();

    useParserStore.getState().reset();
    expect(useParserStore.getState()).toMatchObject({
      input: '',
      results: [],
      failed: [],
      status: 'idle',
      error: null,
    });
  });

  it('selectVisibleResults shows video/music per mode and image in both modes', () => {
    const all = [videoResult, musicResult, imageResult];
    expect(selectVisibleResults(all, 'video')).toEqual([videoResult, imageResult]);
    expect(selectVisibleResults(all, 'music')).toEqual([musicResult, imageResult]);
    // image belongs to neither mode, so it must be reachable in both.
    expect(selectVisibleResults([imageResult], 'video')).toEqual([imageResult]);
    expect(selectVisibleResults([imageResult], 'music')).toEqual([imageResult]);
    expect(selectVisibleResults([], 'video')).toEqual([]);
  });
});
