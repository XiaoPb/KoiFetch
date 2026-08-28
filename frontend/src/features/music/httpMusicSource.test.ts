import { beforeEach, describe, expect, it, vi } from 'vitest';
import { musicApi } from '../../services/api';
import { createHttpMusicSource } from './httpMusicSource';
import type { MusicSearchResult } from '../../types/music';

vi.mock('../../services/api', () => ({
  musicApi: {
    search: vi.fn(),
    importSong: vi.fn(),
  },
}));

const RESULT: MusicSearchResult = {
  totals: { all: 1, song: 1, artist: 0, album: 0, playlist: 0 },
  songs: [
    {
      kind: 'song',
      id: 'song-1',
      title: '晴天',
      artist: '周杰伦',
      album: '叶惠美',
      cover: null,
      duration: '04:30',
      play_url: '/api/music/stream?src=http%3A%2F%2Fcdn%2F1.mp3',
      bitrate: 320,
    },
  ],
  artists: [],
  albums: [],
  playlists: [],
  hasMore: false,
};

describe('httpMusicSource', () => {
  beforeEach(() => {
    vi.mocked(musicApi.search).mockReset();
  });

  it('delegates search params and returns the API payload as-is', async () => {
    vi.mocked(musicApi.search).mockResolvedValue(RESULT);
    const source = createHttpMusicSource();
    const result = await source.search({ keyword: '晴天', category: 'song', page: 1 });
    expect(musicApi.search).toHaveBeenCalledWith({ keyword: '晴天', category: 'song', page: 1 });
    expect(result).toBe(RESULT);
    expect(result.songs[0].play_url).toContain('/api/music/stream?src=');
  });
});
