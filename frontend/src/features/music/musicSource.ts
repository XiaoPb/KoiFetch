import type {
  MusicAlbum,
  MusicArtist,
  MusicCategory,
  MusicPlaylist,
  MusicSearchParams,
  MusicSearchResult,
  MusicSong,
} from '../../types/music';

export interface MusicSearchSource {
  /** Search one category, one page. `page` is 1-based. */
  search(params: MusicSearchParams): Promise<MusicSearchResult>;
}

/** Song-list page size (综合/单曲 views lazy-load in pages of this size). */
export const PAGE_SIZE = 20;

export const HOT_KEYWORDS: string[] = ['周杰伦', '晴天', '热歌榜', '邓紫棋', '许嵩', '民谣'];

const SONG_TITLES = [
  '晴天', '七里香', '稻香', '夜曲', '告白气球', '光年之外', '泡沫', '平凡之路',
  '成都', '消愁', '年少有为', '起风了', '演员', '体面', '说散就散',
] as const;
const ARTIST_NAMES = ['周杰伦', '邓紫棋', '许嵩', '薛之谦', '陈奕迅', '李荣浩', '赵雷', '毛不易', '林俊杰', '王菲'] as const;
const ALBUM_WORDS = ['精选', '合集', '现场', '翻唱', '经典', '原声'] as const;

function hashString(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/** Deterministic PRNG (mulberry32) so the same keyword yields the same data. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function formatDuration(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds < 10 ? `0${seconds}` : seconds}`;
}

function coverUrl(id: string): string {
  return `https://picsum.photos/seed/koi-${encodeURIComponent(id)}/120/120`;
}

function buildMockData(keyword: string): {
  totals: Record<MusicCategory, number>;
  songs: MusicSong[];
  artists: MusicArtist[];
  albums: MusicAlbum[];
  playlists: MusicPlaylist[];
} {
  const rand = mulberry32(hashString(keyword));
  const int = (min: number, max: number): number => min + Math.floor(rand() * (max - min + 1));
  const pick = <T,>(options: readonly T[]): T => options[Math.floor(rand() * options.length)];

  const totals: Record<MusicCategory, number> = {
    all: 200 + int(1, 1800),
    song: 200 + int(1, 1800),
    artist: 20 + int(1, 80),
    album: 10 + int(1, 60),
    playlist: 10 + int(1, 60),
  };

  const artists: MusicArtist[] = Array.from({ length: 10 }, (_, index) => ({
    kind: 'artist',
    id: `artist-${index}`,
    name: index === 0 ? pick(ARTIST_NAMES) : `${pick(ARTIST_NAMES)} ${index + 1}`,
    avatar: coverUrl(`artist-${index}`),
    fans: int(10_000, 9_000_000),
    songCount: int(5, 200),
  }));

  const albums: MusicAlbum[] = Array.from({ length: totals.album }, (_, index) => ({
    kind: 'album',
    id: `album-${index}`,
    title: `${pick(SONG_TITLES)}${pick(ALBUM_WORDS)}`,
    artist: artists[index % artists.length].name,
    cover: coverUrl(`album-${index}`),
    songCount: int(4, 60),
  }));

  const playlists: MusicPlaylist[] = Array.from({ length: totals.playlist }, (_, index) => ({
    kind: 'playlist',
    id: `playlist-${index}`,
    title: `${pick(ARTIST_NAMES)}的热门歌单`,
    creator: artists[index % artists.length].name,
    cover: coverUrl(`playlist-${index}`),
    songCount: int(10, 200),
  }));

  const songs: MusicSong[] = Array.from({ length: totals.song }, (_, index) => ({
    kind: 'song',
    id: `song-${index}`,
    title: index === 0 ? pick(SONG_TITLES) : `${pick(SONG_TITLES)} ${index + 1}`,
    artist: artists[index % artists.length].name,
    album: albums[index % albums.length].title,
    cover: coverUrl(`song-${index}`),
    duration: formatDuration(int(120, 360)),
    play_url: null,
  }));

  return { totals, songs, artists, albums, playlists };
}

/**
 * Deterministic mock behind `MusicSearchSource`, standing in until a backend
 * music-search endpoint exists (follow-up plan). Data is derived entirely from
 * the keyword hash, so repeated searches are stable for tests.
 * Mock rule: keywords longer than 10 characters return no results, so the
 * empty state is reachable deterministically.
 */
export function createMockMusicSource(latencyMs = 250): MusicSearchSource {
  const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
  return {
    async search({ keyword, category, page }) {
      await delay(latencyMs);
      if (keyword.trim().length > 10) {
        return {
          totals: { all: 0, song: 0, artist: 0, album: 0, playlist: 0 },
          songs: [], artists: [], albums: [], playlists: [],
          hasMore: false,
        };
      }
      const data = buildMockData(keyword);
      const start = (page - 1) * PAGE_SIZE;
      const songs =
        category === 'artist' || category === 'album' || category === 'playlist'
          ? []
          : data.songs.slice(start, start + PAGE_SIZE);
      return {
        totals: data.totals,
        songs,
        artists: category === 'artist' ? data.artists : [],
        albums: category === 'album' ? data.albums : [],
        playlists: category === 'playlist' ? data.playlists : [],
        hasMore: (category === 'all' || category === 'song') && start + PAGE_SIZE < data.songs.length,
      };
    },
  };
}

/** The source the app uses today; a follow-up backend plan swaps in an HTTP source. */
export const mockMusicSource = createMockMusicSource(250);

/** Minimal artist entity derived from a name (view-artist fallback). */
export function artistFromName(name: string): MusicArtist {
  return { kind: 'artist', id: `artist:${name}`, name, avatar: null, fans: 0, songCount: 0 };
}
