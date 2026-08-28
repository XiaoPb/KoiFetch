import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { App } from 'antd';
import { MusicEntityPage } from './MusicEntityPage';
import type { MusicSong } from '../../types/music';
import { useMusicStore } from '../../features/music/musicStore';
import { musicApi } from '../../services/api';

vi.mock('../../services/api', () => ({
  musicApi: {
    search: vi.fn(),
    importSong: vi.fn(),
    getHotKeywords: vi.fn(),
  },
}));

const ARTIST = { kind: 'artist', id: 'a1', name: '周杰伦', avatar: null, fans: 0, songCount: 2 };

const SONGS: MusicSong[] = [
  { kind: 'song', id: 's1', title: '晴天', artist: '周杰伦', album: '叶惠美', cover: null, duration: '04:30', play_url: null },
  { kind: 'song', id: 's2', title: '七里香', artist: '周杰伦', album: '七里香', cover: null, duration: '04:00', play_url: null },
];

describe('MusicEntityPage', () => {
  beforeEach(() => {
    useMusicStore.getState().reset();
    vi.mocked(musicApi.search).mockReset();
  });

  it('renders an artist passed via location state and plays its songs', async () => {
    vi.mocked(musicApi.search).mockResolvedValue({
      totals: { all: 2, song: 2, artist: 0, album: 0, playlist: 0 },
      songs: SONGS,
      artists: [], albums: [], playlists: [], hasMore: false,
    });
    render(
      <App>
        <MemoryRouter initialEntries={[{ pathname: '/music/artist/a1', state: { entity: ARTIST } }]}>
          <Routes>
            <Route path="/music/artist/:id" element={<MusicEntityPage kind="artist" />} />
          </Routes>
        </MemoryRouter>
      </App>,
    );
    expect(await screen.findByText('周杰伦')).toBeInTheDocument();
    expect(screen.getByText('晴天')).toBeInTheDocument();
    await userEvent.click(screen.getByText('晴天'));
    expect(useMusicStore.getState().currentSong?.id).toBe('s1');
  });

  it('re-searches by the name query when no state is present (direct visit)', async () => {
    vi.mocked(musicApi.search).mockResolvedValue({
      totals: { all: 1, song: 1, artist: 0, album: 0, playlist: 0 },
      songs: [SONGS[0]],
      artists: [], albums: [], playlists: [], hasMore: false,
    });
    render(
      <App>
        <MemoryRouter initialEntries={['/music/artist/a1?name=%E5%91%A8%E6%9D%B0%E4%BC%A6']}>
          <Routes>
            <Route path="/music/artist/:id" element={<MusicEntityPage kind="artist" />} />
          </Routes>
        </MemoryRouter>
      </App>,
    );
    expect(await screen.findByText('晴天')).toBeInTheDocument();
    expect(musicApi.search).toHaveBeenCalledWith(expect.objectContaining({ keyword: '周杰伦' }));
  });
});
