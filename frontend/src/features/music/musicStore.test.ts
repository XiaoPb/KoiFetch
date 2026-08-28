import { describe, expect, it, vi } from 'vitest';
import { createMusicStore, MUSIC_EMPTY_INPUT_MESSAGE } from './musicStore';
import type { MusicSearchSource } from './musicSource';
import type { MusicCategory, MusicSearchResult, MusicSong } from '../../types/music';

function makeSong(id: number): MusicSong {
  return {
    kind: 'song',
    id: `s${id}`,
    title: `Song ${id}`,
    artist: 'Artist A',
    album: 'Album X',
    cover: null,
    duration: '03:00',
    play_url: null,
  };
}

// `_keyword` is unused by design (data is deterministic per the tests); the
// underscore prefix satisfies the repo's `noUnusedParameters` tsconfig rule.
function defaultSearchImpl(_keyword: string, category: MusicCategory, page: number): MusicSearchResult {
  const allSongs = [makeSong(1), makeSong(2), makeSong(3)];
  const start = (page - 1) * 2;
  return {
    totals: { all: 3, song: 3, artist: 0, album: 0, playlist: 0 },
    songs:
      category === 'artist' || category === 'album' || category === 'playlist'
        ? []
        : allSongs.slice(start, start + 2),
    artists:
      category === 'artist'
        ? [{ kind: 'artist', id: 'a1', name: 'Artist A', avatar: null, fans: 100, songCount: 3 }]
        : [],
    albums: [],
    playlists: [],
    hasMore: (category === 'all' || category === 'song') && start + 2 < allSongs.length,
  };
}

function makeSource(): { source: MusicSearchSource; searchMock: ReturnType<typeof vi.fn> } {
  const searchMock = vi.fn(async ({ keyword, category, page }: { keyword: string; category: MusicCategory; page: number }) =>
    defaultSearchImpl(keyword, category, page),
  );
  return { source: { search: searchMock }, searchMock };
}

describe('musicStore', () => {
  it('searches and populates the first page', async () => {
    const { source } = makeSource();
    const store = createMusicStore(source);
    store.setState({ input: '晴天' });
    await store.getState().search();
    const state = store.getState();
    expect(state.status).toBe('success');
    expect(state.keyword).toBe('晴天');
    expect(state.songs).toHaveLength(2);
    expect(state.page).toBe(2);
    expect(state.hasMore).toBe(true);
    expect(state.totals.all).toBe(3);
  });

  it('rejects empty input without calling the source', async () => {
    const { source, searchMock } = makeSource();
    const store = createMusicStore(source);
    store.setState({ input: '   ' });
    await store.getState().search();
    expect(store.getState().status).toBe('error');
    expect(store.getState().error).toBe(MUSIC_EMPTY_INPUT_MESSAGE);
    expect(searchMock).not.toHaveBeenCalled();
  });

  it('re-searches with the same keyword when the category changes', async () => {
    const { source } = makeSource();
    const store = createMusicStore(source);
    store.setState({ input: '晴天' });
    await store.getState().search();
    await store.getState().setCategory('artist');
    const state = store.getState();
    expect(state.category).toBe('artist');
    expect(state.keyword).toBe('晴天');
    expect(state.artists).toHaveLength(1);
    expect(state.songs).toHaveLength(0);
  });

  it('appends the next page via loadMore', async () => {
    const { source } = makeSource();
    const store = createMusicStore(source);
    store.setState({ input: '晴天' });
    await store.getState().search();
    await store.getState().loadMore();
    const state = store.getState();
    expect(state.songs).toHaveLength(3);
    expect(state.page).toBe(3);
    expect(state.hasMore).toBe(false);
  });

  it('ignores loadMore when there is no more data', async () => {
    const { source, searchMock } = makeSource();
    const store = createMusicStore(source);
    store.setState({ input: '晴天' });
    await store.getState().search();
    await store.getState().loadMore(); // drains to hasMore=false
    const callsAfterDrain = searchMock.mock.calls.length;
    await store.getState().loadMore();
    expect(searchMock.mock.calls).toHaveLength(callsAfterDrain);
  });

  it('starts playback and closes the player', () => {
    const { source } = makeSource();
    const store = createMusicStore(source);
    store.getState().playSong(makeSong(1));
    expect(store.getState().currentSong?.id).toBe('s1');
    expect(store.getState().isPlaying).toBe(true);
    store.getState().closePlayer();
    expect(store.getState().currentSong).toBeNull();
  });

  it('resets to the initial state', async () => {
    const { source } = makeSource();
    const store = createMusicStore(source);
    store.setState({ input: '晴天' });
    await store.getState().search();
    store.getState().reset();
    const state = store.getState();
    expect(state.status).toBe('idle');
    expect(state.songs).toHaveLength(0);
    expect(state.input).toBe('');
    expect(state.keyword).toBe('');
  });

  it('supersedes a search started while loading (Enter no longer ignored)', async () => {
    let resolveFirst!: (r: MusicSearchResult) => void;
    let resolveSecond!: (r: MusicSearchResult) => void;
    const searchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<MusicSearchResult>((r) => { resolveFirst = r; }))
      .mockImplementationOnce(() => new Promise<MusicSearchResult>((r) => { resolveSecond = r; }));
    const store = createMusicStore({ search: searchMock });

    store.setState({ input: '旧词' });
    const first = store.getState().search();   // in flight
    expect(store.getState().status).toBe('loading');

    store.setState({ input: '新词' });
    const second = store.getState().search();  // NOT ignored while loading
    expect(searchMock).toHaveBeenCalledTimes(2);

    resolveSecond({
      totals: { all: 1, song: 1, artist: 0, album: 0, playlist: 0 },
      songs: [makeSong(2)], artists: [], albums: [], playlists: [], hasMore: false,
    });
    await second;
    expect(store.getState().songs[0].id).toBe('s2');

    resolveFirst({
      totals: { all: 1, song: 1, artist: 0, album: 0, playlist: 0 },
      songs: [makeSong(1)], artists: [], albums: [], playlists: [], hasMore: false,
    });
    await first;
    // The stale response must NOT overwrite the newer one.
    expect(store.getState().songs[0].id).toBe('s2');
  });

  it('drops a stale loadMore result when a new search starts', async () => {
    let resolveMore!: (r: MusicSearchResult) => void;
    const searchMock = vi.fn(async ({ keyword, category, page }: { keyword: string; category: MusicCategory; page: number }) => {
      if (page > 1) return new Promise<MusicSearchResult>((r) => { resolveMore = r; });
      return defaultSearchImpl(keyword, category, page);
    });
    const store = createMusicStore({ search: searchMock });
    store.setState({ input: '晴天' });
    await store.getState().search();

    const more = store.getState().loadMore();   // in flight
    store.setState({ input: '新词' });
    await store.getState().search();            // supersedes
    resolveMore({
      totals: { all: 1, song: 1, artist: 0, album: 0, playlist: 0 },
      songs: [makeSong(9)], artists: [], albums: [], playlists: [], hasMore: false,
    });
    await more;
    expect(store.getState().songs.every((s) => s.id !== 's9')).toBe(true);
  });

  it('surfaces a loadMore failure in loadMoreError and keeps the list', async () => {
    const searchMock = vi.fn(async ({ keyword, category, page }: { keyword: string; category: MusicCategory; page: number }) => {
      if (page > 1) throw new Error('网络错误');
      return defaultSearchImpl(keyword, category, page);
    });
    const store = createMusicStore({ search: searchMock });
    store.setState({ input: '晴天' });
    await store.getState().search();
    await store.getState().loadMore();
    const state = store.getState();
    expect(state.loadMoreError).toBe('网络错误');
    expect(state.songs).toHaveLength(2); // first page kept
    expect(state.status).toBe('success'); // NOT an error state
  });

  it('clears loadMoreError on the next successful loadMore', async () => {
    let failedOnce = false;
    const searchMock = vi.fn(async ({ keyword, category, page }: { keyword: string; category: MusicCategory; page: number }) => {
      // A failed loadMore does NOT advance `page`, so the retry re-requests
      // the same page — fail exactly once, then succeed.
      if (page > 1 && !failedOnce) {
        failedOnce = true;
        throw new Error('网络错误');
      }
      return defaultSearchImpl(keyword, category, page);
    });
    const store = createMusicStore({ search: searchMock });
    store.setState({ input: '晴天' });
    await store.getState().search();
    await store.getState().loadMore();
    expect(store.getState().loadMoreError).toBe('网络错误');
    await store.getState().loadMore(); // retries the same page, succeeds
    expect(store.getState().loadMoreError).toBeNull();
  });

  it('enqueueNext inserts after the current song without playing it', () => {
    const store = createMusicStore({ search: vi.fn() });
    store.getState().playSong(makeSong(1));
    store.getState().enqueueNext(makeSong(2));
    const state = store.getState();
    expect(state.queue.map((s) => s.id)).toEqual(['s1', 's2']);
    expect(state.currentSong?.id).toBe('s1'); // not switched
    expect(state.isPlaying).toBe(true);
  });

  it('playNext advances and stops at the end in sequence mode', () => {
    const store = createMusicStore({ search: vi.fn() });
    store.getState().playSong(makeSong(1));
    store.getState().enqueueNext(makeSong(2));
    store.getState().enqueueNext(makeSong(3));
    // 下一首播放 inserts at queueIndex+1 each time, so successive enqueues
    // STACK right after the current song: ['s1', 's3', 's2'] — the newest
    // plays next.
    expect(store.getState().queue.map((s) => s.id)).toEqual(['s1', 's3', 's2']);
    expect(store.getState().playNext()).toBe(true);
    expect(store.getState().currentSong?.id).toBe('s3');
    expect(store.getState().playNext()).toBe(true);
    expect(store.getState().currentSong?.id).toBe('s2');
    expect(store.getState().playNext()).toBe(false); // sequence: stop at end
    expect(store.getState().currentSong?.id).toBe('s2');
  });

  it('loopAll wraps to the first song at the end', () => {
    const store = createMusicStore({ search: vi.fn() });
    store.getState().playSong(makeSong(1));
    store.getState().enqueueNext(makeSong(2));
    store.setState({ loopMode: 'loopAll' });
    store.getState().playNext(); // s2
    expect(store.getState().playNext()).toBe(true);
    expect(store.getState().currentSong?.id).toBe('s1');
  });

  it('cycleLoopMode cycles sequence -> loopOne -> loopAll', () => {
    const store = createMusicStore({ search: vi.fn() });
    expect(store.getState().loopMode).toBe('sequence');
    store.getState().cycleLoopMode();
    expect(store.getState().loopMode).toBe('loopOne');
    store.getState().cycleLoopMode();
    expect(store.getState().loopMode).toBe('loopAll');
    store.getState().cycleLoopMode();
    expect(store.getState().loopMode).toBe('sequence');
  });
});
