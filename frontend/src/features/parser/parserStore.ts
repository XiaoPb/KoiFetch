import { create } from 'zustand';
import { parseApi } from '../../services/api';
import { getErrorMessage } from '../../services/apiClient';
import type { MediaMode } from '../../stores/appStore';
import type { ParseFailure, ParseResult } from '../../types/api';

export type ParserStatus = 'idle' | 'loading' | 'success' | 'error';

/**
 * Hard client-side cap mirroring the backend's 50-URL limit: the request is
 * rejected locally with a friendly message instead of being doomed to a 400.
 */
export const MAX_PARSE_URLS = 50;

// Client-side validation messages (Chinese-primary bilingual), following the
// NETWORK_ERROR_MESSAGE precedent in types/api.ts. Kept out of the i18n
// dictionary deliberately: the store is a plain module with no language
// context, and both languages showing side by side matches the app's existing
// network-error pattern. Backend errors surface the API's own bilingual
// message via ApiError.
export const PARSER_EMPTY_INPUT_MESSAGE = '请输入至少一个链接 / Enter at least one URL';
export const PARSER_TOO_MANY_URLS_MESSAGE = `一次最多解析 ${MAX_PARSE_URLS} 个链接 / Up to ${MAX_PARSE_URLS} URLs at a time`;

/**
 * http(s) URLs embedded in arbitrary text. Share-cards (Douyin, WeChat, …)
 * paste as a sentence of prose with the link in the middle, e.g.
 *   "2.84 ... https://v.douyin.com/wLOh31JiznU/ 复制此链接，打开Dou音搜索…"
 * so extraction matches any http(s) URL and ignores everything else.
 * The character class excludes whitespace, quotes, angle brackets and CJK
 * punctuation, so a URL glued to Chinese text ("…/a。") still extracts
 * cleanly; ASCII sentence/bracket punctuation glued after a URL (".", ",", …)
 * is trimmed at the end.
 */
const URL_PATTERN = /https?:\/\/[^\s"'<>，。！？、；：（）【】《》「」『』]+/gi;

/** ASCII punctuation an OS text-selection can glue after a URL. */
const TRAILING_PUNCTUATION = /[.,;:!?)\]}[】]+$/;

/**
 * Extract every http(s) URL from arbitrary input text (share cards, TXT
 * batches, plain URL lists). Non-URL prose is dropped — the user pastes the
 * whole share-card and the workspace parses just the links. Line endings are
 * irrelevant: extraction works across \n, \r\n, lone \r and single-line text.
 */
export function extractUrls(input: string): string[] {
  const matches = input.match(URL_PATTERN) ?? [];
  return matches.map((url) => url.replace(TRAILING_PUNCTUATION, ''));
}

/**
 * mediaMode semantics (documented): the header's video/music switch filters
 * which result CARDS are displayed. The parse request itself is mode-agnostic
 * (the backend resolves whatever the URLs point to), so the filter is pure
 * presentation and switching modes never requires a re-parse.
 *
 * `image` results are shown in BOTH modes: image is a third media type that
 * belongs to neither the video nor the music mode, so hiding it would make
 * those cards unreachable (and the v1 single-image preview would have no
 * entry point). Live-photo cards are video-mode content. So: video mode →
 * `video` | `image` | `live_photo`; music mode → `music` | `image`.
 */
export function selectVisibleResults(results: ParseResult[], mediaMode: MediaMode): ParseResult[] {
  return results.filter(
    (result) =>
      result.type === 'image' ||
      result.type === mediaMode ||
      (mediaMode === 'video' && result.type === 'live_photo'),
  );
}

/**
 * Parser workspace state: raw URL input, parse results, failed URLs, and the
 * submit/loading/error lifecycle. `parse()` validates client-side (empty input,
 * >50 URLs), calls `parseApi.parse` with the trimmed URL list, and stores
 * results + failed URLs for the workspace grid and failed section.
 */
export interface ParserState {
  /** Raw textarea content (batch input, one URL per line). */
  input: string;
  results: ParseResult[];
  failed: ParseFailure[];
  status: ParserStatus;
  /** Client-side validation message or the backend ApiError message. */
  error: string | null;

  setInput: (input: string) => void;
  parse: () => Promise<void>;
  reset: () => void;
}

let parseGeneration = 0;

export const useParserStore = create<ParserState>()((set, get) => ({
  input: '',
  results: [],
  failed: [],
  status: 'idle',
  error: null,

  setInput: (input) => set({ input }),

  parse: async () => {
    const generation = ++parseGeneration;
    const urls = extractUrls(get().input);
    if (urls.length === 0) {
      if (generation === parseGeneration) {
        set({ status: 'error', error: PARSER_EMPTY_INPUT_MESSAGE, results: [], failed: [] });
      }
      return;
    }
    if (urls.length > MAX_PARSE_URLS) {
      if (generation === parseGeneration) {
        set({ status: 'error', error: PARSER_TOO_MANY_URLS_MESSAGE, results: [], failed: [] });
      }
      return;
    }
    // A new attempt clears the previous output so stale cards never linger.
    set({ status: 'loading', error: null, results: [], failed: [] });
    try {
      const data = await parseApi.parse(urls);
      if (generation !== parseGeneration) return;
      set({ status: 'success', results: data.results, failed: data.failed });
    } catch (err) {
      if (generation !== parseGeneration) return;
      set({ status: 'error', error: getErrorMessage(err) });
    }
  },

  reset: () => {
    parseGeneration += 1;
    set({ input: '', results: [], failed: [], status: 'idle', error: null });
  },
}));
