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

  it('extracts http(s) URLs embedded in share text and ignores prose', () => {
    const share =
      '2.84 10/22 A@G.vF seB:/ :8pm 小麦和威龙偶遇小博博会发生什么？ # 三角洲行动 # 原创动画 https://v.douyin.com/wLOh31JiznU/ 复制此链接，打开Dou音搜索，直接观看视频！';
    expect(extractUrls(share)).toEqual(['https://v.douyin.com/wLOh31JiznU/']);
  });

  it('extracts multiple URLs from one line and works across line endings', () => {
    expect(extractUrls('看看这个 https://a.com/x 和这个 https://b.com/y，还有文字')).toEqual([
      'https://a.com/x',
      'https://b.com/y',
    ]);
    // Old-Mac TXT (\r), CRLF and blank lines are all irrelevant to extraction.
    expect(extractUrls('https://a\rhttps://b\r\n\nhttps://c')).toEqual([
      'https://a',
      'https://b',
      'https://c',
    ]);
    expect(extractUrls('   \n\n  ')).toEqual([]);
    expect(extractUrls('只有文字没有链接')).toEqual([]);
  });

  it('strips trailing punctuation glued to URLs and is case-insensitive', () => {
    expect(extractUrls('(https://a.com/x)。，https://b.com/y!')).toEqual([
      'https://a.com/x',
      'https://b.com/y',
    ]);
    // Matching is case-insensitive; the extracted URL keeps its original case.
    expect(extractUrls('HTTPS://A.COM/X http://B.com/y')).toEqual(['HTTPS://A.COM/X', 'http://B.com/y']);
    // Query strings are preserved (only the trailing-punctuation trim applies).
    expect(extractUrls('https://a.com/p?a=1&b=2')).toEqual(['https://a.com/p?a=1&b=2']);
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
    // A scheme-qualified URL passes client-side extraction, so the backend's
    // own rejection surfaces (prose without a URL is filtered client-side
    // into the empty-input error instead — covered by the empty test above).
    useParserStore.setState({ input: 'https://not-a-real-host/x' });
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
