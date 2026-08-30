// Music search domain types (music search page, Task 1).
// The search page is frontend-only for now: `musicSource.ts` serves a
// deterministic mock behind the `MusicSearchSource` interface, so these types
// double as the API contract a future backend music-search plan must satisfy.

export type MusicCategory = 'all' | 'song' | 'artist' | 'album' | 'playlist';

export interface MusicSong {
  kind: 'song';
  id: string;
  title: string;
  artist: string;
  album: string;
  cover: string | null;
  /** "MM:SS" */
  duration: string;
  /** Real playable source; null in mock mode (the mini player simulates). */
  play_url: string | null;
  /** Raw LRC or plain lyrics; timestamps are parsed client-side. */
  lyric?: string | null;
  /** kbps (real backend); used as the 热度 sort proxy. Mock songs omit it. */
  bitrate?: number | null;
}

export interface MusicArtist {
  kind: 'artist';
  id: string;
  name: string;
  avatar: string | null;
  fans: number;
  songCount: number;
}

export interface MusicAlbum {
  kind: 'album';
  id: string;
  title: string;
  artist: string;
  cover: string | null;
  songCount: number;
}

export interface MusicPlaylist {
  kind: 'playlist';
  id: string;
  title: string;
  creator: string;
  cover: string | null;
  songCount: number;
}

export type MusicEntity = MusicArtist | MusicAlbum | MusicPlaylist;

export interface MusicSearchParams {
  keyword: string;
  category: MusicCategory;
  /** 1-based page number (only song lists paginate). */
  page: number;
}

export interface MusicSearchResult {
  /** Per-category totals so the filter bar can show "约 X 首单曲" etc. */
  totals: Record<MusicCategory, number>;
  songs: MusicSong[];
  artists: MusicArtist[];
  albums: MusicAlbum[];
  playlists: MusicPlaylist[];
  /** True when the song list has another page (all/song categories only). */
  hasMore: boolean;
}
