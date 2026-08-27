import { describe, expect, it } from 'vitest';
import { createMockMusicSource, PAGE_SIZE } from './musicSource';

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
});
