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

// Client-side validation message (Chinese-primary bilingual, same pattern as
// the parser store's validation messages).
export const MUSIC_EMPTY_INPUT_MESSAGE = '请输入搜索关键词 / Enter a search keyword';

const ZERO_TOTALS: Record<MusicCategory, number> = { all: 0, song: 0, artist: 0, album: 0, playlist: 0 };

export interface MusicState {
  /** Current top-bar text. */
  input: string;
  /** Last searched keyword ("" before the first search). */
  keyword: string;
  category: MusicCategory;
  status: MusicSearchStatus;
  error: string | null;
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

  // Bottom Action Sheet state.
  actionSheetSong: MusicSong | null;

  // Entity detail drawer state.
  detailEntity: MusicEntity | null;

  setInput: (input: string) => void;
  search: () => Promise<void>;
  setCategory: (category: MusicCategory) => Promise<void>;
  loadMore: () => Promise<void>;
  reset: () => void;

  playSong: (song: MusicSong) => void;
  togglePlay: () => void;
  pause: () => void;
  updateProgress: (time: number) => void;
  closePlayer: () => void;

  openActionSheet: (song: MusicSong) => void;
  closeActionSheet: () => void;
  openDetail: (entity: MusicEntity | null) => void;
  closeDetail: () => void;
}

/**
 * The search-page store. The source is injected so tests can stub it and a
 * follow-up backend plan can swap the default without touching components.
 */
export function createMusicStore(source: MusicSearchSource) {
  return create<MusicState>()((set, get) => {
    const runSearch = async (term: string, category: MusicCategory): Promise<void> => {
      // Clear the previous output immediately: re-search must look like a
      // fresh list from the top (spec), not a diff over stale results.
      set({
        status: 'loading',
        error: null,
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
        set({ status: 'error', error: getErrorMessage(err) });
      }
    };

    return {
      input: '',
      keyword: '',
      category: 'all',
      status: 'idle',
      error: null,
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
      actionSheetSong: null,
      detailEntity: null,

      setInput: (input) => set({ input }),

      search: async () => {
        // Double-submit guard: Enter + the submit button can race.
        if (get().status === 'loading') return;
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
        set({ loadingMore: true });
        try {
          const data = await source.search({ keyword: state.keyword, category: state.category, page: state.page });
          set((current) => ({
            songs: [...current.songs, ...data.songs],
            hasMore: data.hasMore,
            page: current.page + 1,
            loadingMore: false,
          }));
        } catch (err) {
          // Keep the current list; surface the error so the page can alert.
          set({ loadingMore: false, error: getErrorMessage(err) });
        }
      },

      reset: () =>
        set({
          input: '',
          keyword: '',
          category: 'all',
          status: 'idle',
          error: null,
          totals: ZERO_TOTALS,
          songs: [], artists: [], albums: [], playlists: [],
          page: 1,
          hasMore: false,
          loadingMore: false,
          currentSong: null,
          isPlaying: false,
          currentTime: 0,
          actionSheetSong: null,
          detailEntity: null,
        }),

      playSong: (song) => set({ currentSong: song, isPlaying: true, currentTime: 0 }),
      togglePlay: () => set((state) => ({ isPlaying: !state.isPlaying })),
      pause: () => set({ isPlaying: false }),
      updateProgress: (time) => set({ currentTime: time }),
      closePlayer: () => set({ currentSong: null, isPlaying: false, currentTime: 0 }),

      openActionSheet: (song) => set({ actionSheetSong: song }),
      closeActionSheet: () => set({ actionSheetSong: null }),
      openDetail: (entity) => set({ detailEntity: entity }),
      closeDetail: () => set({ detailEntity: null }),
    };
  });
}

export const useMusicStore = createMusicStore(httpMusicSource);
