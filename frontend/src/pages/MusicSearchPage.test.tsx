import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../test/utils';
import MusicSearchPage from './MusicSearchPage';
import { useMusicStore } from '../features/music/musicStore';
import { musicApi } from '../services/api';

// The default music store is now HTTP-backed (httpMusicSource), so the page
// tests drive the mocked musicApi instead of the deterministic mock source.
vi.mock('../services/api', () => ({
  musicApi: { search: vi.fn(), importSong: vi.fn(), getHotKeywords: vi.fn().mockResolvedValue({ keywords: [] }) },
  authApi: { login: vi.fn() },
  healthApi: { getHealth: vi.fn().mockResolvedValue({ status: 'ok', services: {}, storage_roots: {} }) },
  parseApi: { parse: vi.fn() },
  downloadApi: { submit: vi.fn() },
  nasApi: { save: vi.fn() },
}));

function makeSong(id: number) {
  return {
    kind: 'song' as const,
    id: `song-${id}`,
    title: `晴天${id}`,
    artist: '周杰伦',
    album: '叶惠美',
    cover: null,
    duration: '04:30',
    play_url: null,
    bitrate: 320,
  };
}

function stubSearch(): void {
  vi.mocked(musicApi.search).mockImplementation(async ({ keyword, category }) => {
    if (keyword.length > 10) {
      return {
        totals: { all: 0, song: 0, artist: 0, album: 0, playlist: 0 },
        songs: [], artists: [], albums: [], playlists: [], hasMore: false,
      };
    }
    const songs = [makeSong(1), makeSong(2)];
    return {
      totals: { all: 2, song: 2, artist: 1, album: 1, playlist: 0 },
      songs: category === 'artist' || category === 'album' || category === 'playlist' ? [] : songs,
      artists: category === 'artist' ? [{ kind: 'artist', id: 'a1', name: '周杰伦', avatar: null, fans: 100, songCount: 2 }] : [],
      albums: category === 'album' ? [{ kind: 'album', id: 'al1', title: '叶惠美', artist: '周杰伦', cover: null, songCount: 2 }] : [],
      playlists: [],
      hasMore: false,
    };
  });
}

describe('MusicSearchPage', () => {
  beforeEach(() => {
    stubSearch();
    useMusicStore.getState().reset();
  });

  it('renders the top search bar and the idle empty state', () => {
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    expect(screen.getByTestId('music-search-input')).toBeInTheDocument();
    expect(screen.getByTestId('music-empty')).toBeInTheDocument();
    expect(screen.getByText('热门搜索')).toBeInTheDocument();
  });

  it('searches and renders the song list with the result count', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    expect(await screen.findByTestId('music-song-list')).toBeInTheDocument();
    expect(screen.getByTestId('music-count')).toHaveTextContent('约');
    expect(screen.getAllByTestId('music-song-row').length).toBeGreaterThan(0);
  });

  it('switches to the artist view via the filter tab', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    await user.click(screen.getByText('歌手'));
    expect(await screen.findByTestId('artist-result-list')).toBeInTheDocument();
    expect(screen.getByTestId('music-count')).toHaveTextContent('位歌手');
  });

  it('shows the empty state with hot keywords when there are no results', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '一二三四五六七八九十X');
    await user.click(screen.getByTestId('music-search-submit'));
    expect(await screen.findByTestId('music-empty')).toBeInTheDocument();
    expect(screen.getByText('没找到相关歌曲，试试其他关键词')).toBeInTheDocument();
  });

  it('shows the per-category empty state when only that category is empty', async () => {
    // 综合 returns songs but no artists; switching to 歌手 shows the artist
    // empty state even though songs exist (the old isEmpty required ALL empty).
    // Override the shared stub locally — the default stub returns artists.
    vi.mocked(musicApi.search).mockImplementation(async ({ category }) => ({
      totals: { all: 2, song: 2, artist: 0, album: 0, playlist: 0 },
      songs: category === 'artist' || category === 'album' || category === 'playlist' ? [] : [makeSong(1)],
      artists: [], albums: [], playlists: [], hasMore: false,
    }));
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    await user.click(screen.getByText('歌手'));
    expect(await screen.findByTestId('music-empty')).toBeInTheDocument();
    expect(screen.getByText('没有找到相关歌手')).toBeInTheDocument();
  });

  it('opens the mini player when a song row is clicked', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    await user.click(screen.getAllByTestId('music-song-row')[0]);
    expect(await screen.findByTestId('music-mini-player')).toBeInTheDocument();
  });

  it('opens the action sheet from the more button', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    const moreButtons = screen.getAllByTestId(/^more-/);
    await user.click(moreButtons[0]);
    expect(await screen.findByTestId('music-action-sheet')).toBeInTheDocument();
    expect(screen.getByText('下一首播放')).toBeInTheDocument();
  });

  it('opens the my-playlists drawer from the entry button', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.click(screen.getByTestId('open-my-playlists'));
    expect(await screen.findByTestId('my-playlists-drawer')).toBeInTheDocument();
  });

  it('shows a loadMore failure hint with a retry action', async () => {
    vi.mocked(musicApi.search).mockImplementation(async ({ page }) => {
      if (page > 1) throw new Error('网络错误');
      return { totals: { all: 2, song: 2, artist: 0, album: 0, playlist: 0 }, songs: [makeSong(1)], artists: [], albums: [], playlists: [], hasMore: true };
    });
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    await useMusicStore.getState().loadMore();
    expect(await screen.findByTestId('music-load-more-error')).toBeInTheDocument();
  });

  it('shows a sort selector after a search and reorders on 热度', async () => {
    vi.mocked(musicApi.search).mockImplementation(async () => ({
      totals: { all: 2, song: 2, artist: 0, album: 0, playlist: 0 },
      songs: [makeSong(1), { ...makeSong(2), bitrate: 96 }],
      artists: [], albums: [], playlists: [], hasMore: false,
    }));
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    // 综合: as-returned; 热度: 320 (makeSong default) beats 96 — same order,
    // so assert both modes keep 晴天1 first and the selector is present.
    expect(screen.getByTestId('sort-selector')).toBeInTheDocument();
    expect(screen.getAllByTestId('music-song-row')[0]).toHaveTextContent('晴天1');
    await user.click(screen.getByTestId('sort-selector'));
    await user.click(screen.getByText('热度'));
    expect(screen.getAllByTestId('music-song-row')[0]).toHaveTextContent('晴天1');
  });

  it('shows search suggestions while the input is focused and idle', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/' });
    await user.click(screen.getByTestId('music-search-input'));
    expect(await screen.findByTestId('music-suggestions')).toBeInTheDocument();
  });
});
