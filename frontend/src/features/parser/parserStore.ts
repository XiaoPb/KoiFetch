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
 * Split raw textarea content into non-blank, trimmed URL lines (the batch
 * input format: one URL per line). Handles \n, \r\n and lone \r (old-Mac TXT
 * files) line endings.
 */
export function extractUrls(input: string): string[] {
  return input
    .split(/\r\n|\r|\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
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
 * entry point). So: video mode → `video` | `image`; music mode → `music` |
 * `image`.
 */
export function selectVisibleResults(results: ParseResult[], mediaMode: MediaMode): ParseResult[] {
  return results.filter((result) => result.type === mediaMode || result.type === 'image');
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

export const useParserStore = create<ParserState>()((set, get) => ({
  input: '',
  results: [],
  failed: [],
  status: 'idle',
  error: null,

  setInput: (input) => set({ input }),

  parse: async () => {
    const urls = extractUrls(get().input);
    if (urls.length === 0) {
      set({ status: 'error', error: PARSER_EMPTY_INPUT_MESSAGE, results: [], failed: [] });
      return;
    }
    if (urls.length > MAX_PARSE_URLS) {
      set({ status: 'error', error: PARSER_TOO_MANY_URLS_MESSAGE, results: [], failed: [] });
      return;
    }
    // A new attempt clears the previous output so stale cards never linger.
    set({ status: 'loading', error: null, results: [], failed: [] });
    try {
      const data = await parseApi.parse(urls);
      set({ status: 'success', results: data.results, failed: data.failed });
    } catch (err) {
      set({ status: 'error', error: getErrorMessage(err) });
    }
  },

  reset: () => set({ input: '', results: [], failed: [], status: 'idle', error: null }),
}));
