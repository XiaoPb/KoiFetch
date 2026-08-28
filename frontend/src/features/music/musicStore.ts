import { create } from 'zustand';
import { getErrorMessage } from '../../services/apiClient';
import type {
  MusicAlbum,
  MusicArtist,
  MusicCategory,
  MusicEntity,
  MusicPlaylist,
  MusicSong,
} from '../../types/music';
import { type MusicSearchSource } from './musicSource';
import { httpMusicSource } from './httpMusicSource';

export type MusicSearchStatus = 'idle' | 'loading' | 'success' | 'error';

export type LoopMode = 'sequence' | 'loopOne' | 'loopAll';

// Client-side validation message (Chinese-primary bilingual, same pattern as
// the parser store's validation messages).
export const MUSIC_EMPTY_INPUT_MESSAGE = '请输入搜索关键词 / Enter a search keyword';

const ZERO_TOTALS: Record<MusicCategory, number> = { all: 0, song: 0, artist: 0, album: 0, playlist: 0 };

/** Per-category empty semantics: the ACTIVE category's list decides. */
export function isCategoryEmpty(
  category: MusicCategory,
  songs: MusicSong[],
  artists: MusicArtist[],
  albums: MusicAlbum[],
  playlists: MusicPlaylist[],
): boolean {
  switch (category) {
    case 'artist':
      return artists.length === 0;
    case 'album':
      return albums.length === 0;
    case 'playlist':
      return playlists.length === 0;
    default: // 'all' shows songs; an empty song list is empty even if other
      // categories have results (the page shows the others-hint below).
      return songs.length === 0;
  }
}

export interface MusicState {
  /** Current top-bar text. */
  input: string;
  /** Last searched keyword ("" before the first search). */
  keyword: string;
  category: MusicCategory;
  status: MusicSearchStatus;
  error: string | null;
  /** Load-more failure hint (distinct from the initial-search `error`). */
  loadMoreError: string | null;
  totals: Record<MusicCategory, number>;
  songs: MusicSong[];
  artists: MusicArtist[];
  albums: MusicAlbum[];
  playlists: MusicPlaylist[];
  /** Next song page to fetch (only meaningful for all/song categories). */
  page: number;
  hasMore: boolean;
  loadingMore: boolean;

  // Mini player state.
  currentSong: MusicSong | null;
  isPlaying: boolean;
  /** seconds, simulated in v1. */
  currentTime: number;

  // Play queue (P2): upcoming songs after the current one.
  queue: MusicSong[];
  /** Index of `currentSong` in `queue` (-1 when not from the queue). */
  queueIndex: number;
  loopMode: LoopMode;

  // Bottom Action Sheet state.
  actionSheetSong: MusicSong | null;

  // Entity detail drawer state.
  detailEntity: MusicEntity | null;

  setInput: (input: string) => void;
  search: () => Promise<void>;
  setCategory: (category: MusicCategory) => Promise<void>;
  loadMore: () => Promise<void>;
  clearLoadMoreError: () => void;
  reset: () => void;

  playSong: (song: MusicSong) => void;
  togglePlay: () => void;
  pause: () => void;
  updateProgress: (time: number) => void;
  closePlayer: () => void;

  enqueueNext: (song: MusicSong) => void;
  /** Advance to the next queue entry; returns false at the sequence end. */
  playNext: () => boolean;
  cycleLoopMode: () => void;

  openActionSheet: (song: MusicSong) => void;
  closeActionSheet: () => void;
  openDetail: (entity: MusicEntity | null) => void;
  closeDetail: () => void;
}

/**
 * The search-page store. The source is injected so tests can stub it and a
 * follow-up backend plan can swap the default without touching components.
 *
 * Playback (P2): `playSong` starts a fresh one-song queue; 下一首播放
 * (`enqueueNext`) inserts after the current song WITHOUT switching to it;
 * `ended` auto-advances through the queue (sequence mode stops at the end,
 * loopAll wraps); `cycleLoopMode` cycles 顺序 → 单曲循环 → 列表循环.
 */
export function createMusicStore(source: MusicSearchSource) {
  // Monotonic request id: every search/category/loadMore capture the current
  // id and drop their response when a newer request has started, so a slow
  // stale response can never overwrite fresher state (and a search started
  // while loading supersedes the in-flight one instead of being ignored).
  let requestSeq = 0;

  return create<MusicState>()((set, get) => {
    const runSearch = async (term: string, category: MusicCategory): Promise<void> => {
      const seq = ++requestSeq;
      // Clear the previous output immediately: re-search must look like a
      // fresh list from the top (spec), not a diff over stale results.
      set({
        status: 'loading',
        error: null,
        loadMoreError: null,
        totals: ZERO_TOTALS,
        songs: [],
        artists: [],
        albums: [],
        playlists: [],
        hasMore: false,
        page: 1,
        loadingMore: false,
      });
      try {
        const data = await source.search({ keyword: term, category, page: 1 });
        if (seq !== requestSeq) return; // superseded by a newer request
        set({
          status: 'success',
          totals: data.totals,
          songs: data.songs,
          artists: data.artists,
          albums: data.albums,
          playlists: data.playlists,
          hasMore: data.hasMore,
          page: 2,
        });
      } catch (err) {
        if (seq !== requestSeq) return;
        set({ status: 'error', error: getErrorMessage(err) });
      }
    };

    return {
      input: '',
      keyword: '',
      category: 'all',
      status: 'idle',
      error: null,
      loadMoreError: null,
      totals: ZERO_TOTALS,
      songs: [],
      artists: [],
      albums: [],
      playlists: [],
      page: 1,
      hasMore: false,
      loadingMore: false,
      currentSong: null,
      isPlaying: false,
      currentTime: 0,
      queue: [],
      queueIndex: -1,
      loopMode: 'sequence',
      actionSheetSong: null,
      detailEntity: null,

      setInput: (input) => set({ input }),

      search: async () => {
        // A search started while loading supersedes the in-flight one
        // (requestSeq drops the stale response) — Enter is never ignored.
        const term = get().input.trim();
        if (term === '') {
          set({
            status: 'error',
            error: MUSIC_EMPTY_INPUT_MESSAGE,
            keyword: '',
            totals: ZERO_TOTALS,
            songs: [], artists: [], albums: [], playlists: [],
            hasMore: false,
            page: 1,
          });
          return;
        }
        set({ keyword: term });
        await runSearch(term, get().category);
      },

      setCategory: async (category) => {
        set({ category });
        const { keyword } = get();
        if (keyword.trim() === '') return; // nothing searched yet — just switch the tab state
        await runSearch(keyword, category);
      },

      loadMore: async () => {
        const state = get();
        const paginated = state.category === 'all' || state.category === 'song';
        if (!paginated || state.status !== 'success' || state.loadingMore || !state.hasMore) return;
        const seq = ++requestSeq;
        set({ loadingMore: true, loadMoreError: null });
        try {
          const data = await source.search({ keyword: state.keyword, category: state.category, page: state.page });
          if (seq !== requestSeq) return; // a newer search/category reset the list
          set((current) => ({
            songs: [...current.songs, ...data.songs],
            hasMore: data.hasMore,
            page: current.page + 1,
            loadingMore: false,
          }));
        } catch (err) {
          if (seq !== requestSeq) return;
          // Keep the current list; surface the failure so the page can offer
          // a retry (distinct from the initial-search `error`).
          set({ loadingMore: false, loadMoreError: getErrorMessage(err) });
        }
      },

      reset: () =>
        set({
          input: '',
          keyword: '',
          category: 'all',
          status: 'idle',
          error: null,
          loadMoreError: null,
          totals: ZERO_TOTALS,
          songs: [], artists: [], albums: [], playlists: [],
          page: 1,
          hasMore: false,
          loadingMore: false,
          currentSong: null,
          isPlaying: false,
          currentTime: 0,
          queue: [],
          queueIndex: -1,
          loopMode: 'sequence',
          actionSheetSong: null,
          detailEntity: null,
        }),

      clearLoadMoreError: () => set({ loadMoreError: null }),

      playSong: (song) =>
        set({ currentSong: song, isPlaying: true, currentTime: 0, queue: [song], queueIndex: 0 }),
      togglePlay: () => set((state) => ({ isPlaying: !state.isPlaying })),
      pause: () => set({ isPlaying: false }),
      updateProgress: (time) => set({ currentTime: time }),
      closePlayer: () => set({ currentSong: null, isPlaying: false, currentTime: 0, queue: [], queueIndex: -1 }),

      enqueueNext: (song) =>
        set((state) => {
          if (!state.currentSong) {
            // Nothing playing: enqueue-next degrades to immediate play.
            return { currentSong: song, isPlaying: true, currentTime: 0, queue: [song], queueIndex: 0 };
          }
          const queue = [...state.queue];
          queue.splice(state.queueIndex + 1, 0, song);
          return { queue };
        }),

      playNext: () => {
        const { queue, queueIndex, loopMode } = get();
        if (queue.length === 0) return false;
        let index = queueIndex + 1;
        if (index >= queue.length) {
          if (loopMode !== 'loopAll') return false; // sequence stops at the end
          index = 0;
        }
        set({ currentSong: queue[index], queueIndex: index, isPlaying: true, currentTime: 0 });
        return true;
      },

      cycleLoopMode: () =>
        set((state) => ({
          loopMode:
            state.loopMode === 'sequence' ? 'loopOne'
            : state.loopMode === 'loopOne' ? 'loopAll'
            : 'sequence',
        })),

      openActionSheet: (song) => set({ actionSheetSong: song }),
      closeActionSheet: () => set({ actionSheetSong: null }),
      openDetail: (entity) => set({ detailEntity: entity }),
      closeDetail: () => set({ detailEntity: null }),
    };
  });
}

export const useMusicStore = createMusicStore(httpMusicSource);
