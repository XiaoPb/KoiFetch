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
});
