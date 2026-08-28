import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { App } from 'antd';
import { MyPlaylistPage } from './MyPlaylistPage';
import { useMusicStore } from '../../features/music/musicStore';
import { usePlaylistsStore, __resetPlaylistsForTests } from '../../features/music/playlistsStore';
import type { MusicSong } from '../../types/music';

function makeSong(id: string, title: string): MusicSong {
  return { kind: 'song', id, title, artist: '周杰伦', album: '叶惠美', cover: null, duration: '04:00', play_url: null };
}

function renderAt(id: string): void {
  render(
    <App>
      <MemoryRouter initialEntries={[`/music/my-playlist/${id}`]}>
        <Routes>
          <Route path="/music/my-playlist/:id" element={<MyPlaylistPage />} />
        </Routes>
      </MemoryRouter>
    </App>,
  );
}

describe('MyPlaylistPage', () => {
  beforeEach(() => {
    __resetPlaylistsForTests();
    useMusicStore.setState({ currentSong: null });
  });

  it('shows the local playlist and plays its songs', async () => {
    const pl = usePlaylistsStore.getState().create('我的最爱')!;
    usePlaylistsStore.getState().addSong(pl.id, makeSong('s1', '晴天'));
    const user = userEvent.setup();
    renderAt(pl.id);
    expect(await screen.findByText('我的最爱')).toBeInTheDocument();
    await user.click(screen.getByText('晴天'));
    expect(useMusicStore.getState().currentSong?.id).toBe('s1');
  });

  it('removes a song from the playlist', async () => {
    const pl = usePlaylistsStore.getState().create('我的最爱')!;
    usePlaylistsStore.getState().addSong(pl.id, makeSong('s1', '晴天'));
    const user = userEvent.setup();
    renderAt(pl.id);
    await screen.findByText('晴天');
    await user.click(screen.getByTestId('remove-s1'));
    expect(usePlaylistsStore.getState().playlists[0].songs).toHaveLength(0);
    expect(screen.getByText('歌单暂无歌曲')).toBeInTheDocument();
  });

  it('shows an honest empty state for an unknown playlist', async () => {
    renderAt('missing');
    expect(screen.getByText('歌单暂无歌曲')).toBeInTheDocument();
  });
});
