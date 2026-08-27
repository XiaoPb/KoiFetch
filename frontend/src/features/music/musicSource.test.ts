import { describe, expect, it } from 'vitest';
import { createMockMusicSource, PAGE_SIZE, toneWavUri } from './musicSource';

describe('mockMusicSource', () => {
  // Zero latency so tests never wait on the demo delay.
  const source = createMockMusicSource(0);

  it('is deterministic for the same keyword', async () => {
    const a = await source.search({ keyword: '周杰伦', category: 'song', page: 1 });
    const b = await source.search({ keyword: '周杰伦', category: 'song', page: 1 });
    expect(a).toEqual(b);
  });

  it('returns a full first page of songs with hasMore', async () => {
    const result = await source.search({ keyword: '晴天', category: 'song', page: 1 });
    expect(result.songs).toHaveLength(PAGE_SIZE);
    expect(result.hasMore).toBe(true);
    expect(result.totals.song).toBeGreaterThan(PAGE_SIZE);
  });

  it('pages songs and reports hasMore=false past the end', async () => {
    const first = await source.search({ keyword: '晴天', category: 'song', page: 1 });
    const lastPage = Math.ceil(first.totals.song / PAGE_SIZE);
    const last = await source.search({ keyword: '晴天', category: 'song', page: lastPage });
    expect(last.songs.length).toBeGreaterThan(0);
    expect(last.songs.length).toBeLessThanOrEqual(PAGE_SIZE);
    expect(last.hasMore).toBe(false);
  });

  it('filters by category', async () => {
    const artists = await source.search({ keyword: '晴天', category: 'artist', page: 1 });
    expect(artists.artists.length).toBeGreaterThan(0);
    expect(artists.songs).toHaveLength(0);
    const albums = await source.search({ keyword: '晴天', category: 'album', page: 1 });
    expect(albums.albums.length).toBeGreaterThan(0);
    const playlists = await source.search({ keyword: '晴天', category: 'playlist', page: 1 });
    expect(playlists.playlists.length).toBeGreaterThan(0);
  });

  it('returns an empty result for keywords longer than 10 characters', async () => {
    const result = await source.search({ keyword: '一二三四五六七八九十X', category: 'all', page: 1 });
    expect(result.songs).toHaveLength(0);
    expect(result.totals.all).toBe(0);
    expect(result.hasMore).toBe(false);
  });

  it('derives every entity from the search keyword', async () => {
    const songs = await source.search({ keyword: '周杰伦', category: 'song', page: 1 });
    const artists = await source.search({ keyword: '周杰伦', category: 'artist', page: 1 });
    const albums = await source.search({ keyword: '周杰伦', category: 'album', page: 1 });
    const playlists = await source.search({ keyword: '周杰伦', category: 'playlist', page: 1 });

    // The keyword is the primary artist/creator everywhere.
    expect(artists.artists[0].name).toBe('周杰伦');
    expect(songs.songs[0].artist).toMatch(/^周杰伦/);
    expect(albums.albums[0].title).toMatch(/^周杰伦/);
    expect(playlists.playlists[0].title).toMatch(/^周杰伦/);
    expect(playlists.playlists[0].creator).toMatch(/^周杰伦/);
  });

  it('songs carry a real playable audio data URI', async () => {
    const result = await source.search({ keyword: '晴天', category: 'song', page: 1 });
    expect(result.songs.length).toBeGreaterThan(0);
    for (const song of result.songs) {
      expect(song.play_url).toMatch(/^data:audio\/wav;base64,/);
    }
  });

  it('toneWavUri is deterministic and plays a real WAV', () => {
    const a = toneWavUri(3);
    const b = toneWavUri(3);
    expect(a).toBe(b);
    expect(a).toMatch(/^data:audio\/wav;base64,/);
    // 44-byte header + 8000 Hz * 4 s samples → a data URI of meaningful size.
    expect(a.length).toBeGreaterThan(10_000);
    expect(toneWavUri(3)).not.toBe(toneWavUri(4)); // different seeds → different tones
  });
});
