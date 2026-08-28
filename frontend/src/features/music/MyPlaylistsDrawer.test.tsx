import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { MyPlaylistsDrawer } from './MyPlaylistsDrawer';
import { usePlaylistsStore, __resetPlaylistsForTests } from './playlistsStore';
import { useMusicStore } from './musicStore';
import type { MusicSong } from '../../types/music';

function makeSong(id: string, title: string): MusicSong {
  return { kind: 'song', id, title, artist: '周杰伦', album: '叶惠美', cover: null, duration: '04:00', play_url: null };
}

describe('MyPlaylistsDrawer', () => {
  beforeEach(() => {
    __resetPlaylistsForTests();
    const pl = usePlaylistsStore.getState().create('我的最爱')!;
    usePlaylistsStore.getState().addSong(pl.id, makeSong('s1', '晴天'));
    usePlaylistsStore.getState().addSong(pl.id, makeSong('s2', '夜曲'));
  });

  afterEach(() => {
    useMusicStore.setState({ currentSong: null, queue: [], queueIndex: -1, loopMode: 'sequence' });
  });

  it('lists playlists and expands to show songs', async () => {
    const user = userEvent.setup();
    const pl = usePlaylistsStore.getState().playlists[0];
    renderWithProviders(<MyPlaylistsDrawer open onClose={vi.fn()} />);
    expect(screen.getByText('我的最爱')).toBeInTheDocument();
    await user.click(screen.getByText('我的最爱'));
    expect(screen.getByTestId(`playlist-songs-${pl.id}`)).toBeInTheDocument();
  });

  it('plays a song from the expanded playlist', async () => {
    const user = userEvent.setup();
    const pl = usePlaylistsStore.getState().playlists[0];
    renderWithProviders(<MyPlaylistsDrawer open onClose={vi.fn()} />);
    await user.click(screen.getByText('我的最爱'));
    await user.click(screen.getByTestId(`playlist-song-${pl.id}-s1`));
    expect(useMusicStore.getState().currentSong?.id).toBe('s1');
  });

  it('plays the whole playlist from 播放全部', async () => {
    const user = userEvent.setup();
    const pl = usePlaylistsStore.getState().playlists[0];
    const onClose = vi.fn();
    renderWithProviders(<MyPlaylistsDrawer open onClose={onClose} />);
    await user.click(screen.getByTestId(`playall-${pl.id}`));
    expect(useMusicStore.getState().currentSong?.id).toBe('s1');
    expect(useMusicStore.getState().queue.map((s) => s.id)).toEqual(['s1', 's2']);
    expect(onClose).toHaveBeenCalled();
  });

  it('removes a song and deletes the playlist', async () => {
    const user = userEvent.setup();
    const pl = usePlaylistsStore.getState().playlists[0];
    renderWithProviders(<MyPlaylistsDrawer open onClose={vi.fn()} />);
    await user.click(screen.getByText('我的最爱'));
    await user.click(screen.getByTestId(`remove-song-${pl.id}-s1`));
    expect(usePlaylistsStore.getState().playlists[0].songs.map((s) => s.id)).toEqual(['s2']);
    await user.click(screen.getByTestId(`delete-playlist-${pl.id}`));
    // antd Popconfirm confirm button text follows the app's locale (en default).
    await user.click(screen.getByText('OK'));
    expect(usePlaylistsStore.getState().playlists).toHaveLength(0);
  });
});
