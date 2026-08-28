import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { AddToPlaylistModal } from './AddToPlaylistModal';
import { usePlaylistsStore, __resetPlaylistsForTests } from './playlistsStore';
import type { MusicSong } from '../../types/music';

const SONG: MusicSong = {
  kind: 'song', id: 's1', title: '晴天', artist: '周杰伦', album: '叶惠美', cover: null, duration: '04:29', play_url: null,
};

describe('AddToPlaylistModal', () => {
  beforeEach(() => {
    __resetPlaylistsForTests();
  });

  it('creates a playlist and adds the song to it', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithProviders(<AddToPlaylistModal open song={SONG} onClose={onClose} />);
    await user.type(screen.getByTestId('new-playlist-name'), '我的最爱');
    await user.click(screen.getByTestId('new-playlist-create'));
    expect(usePlaylistsStore.getState().playlists).toHaveLength(1);
    expect(usePlaylistsStore.getState().playlists[0].songs[0].id).toBe('s1');
    expect(onClose).toHaveBeenCalled();
  });

  it('adds the song to an existing playlist via the pick button', async () => {
    const user = userEvent.setup();
    const created = usePlaylistsStore.getState().create('已有歌单')!;
    const onClose = vi.fn();
    renderWithProviders(<AddToPlaylistModal open song={SONG} onClose={onClose} />);
    await user.click(screen.getByTestId(`pick-playlist-${created.id}`));
    expect(usePlaylistsStore.getState().playlists[0].songs[0].id).toBe('s1');
    expect(onClose).toHaveBeenCalled();
  });

  it('rejects a blank playlist name', async () => {
    const user = userEvent.setup();
    renderWithProviders(<AddToPlaylistModal open song={SONG} onClose={vi.fn()} />);
    await user.click(screen.getByTestId('new-playlist-create'));
    expect(screen.getByText('请输入歌单名称')).toBeInTheDocument();
    expect(usePlaylistsStore.getState().playlists).toHaveLength(0);
  });

  it('renders nothing when closed', () => {
    renderWithProviders(<AddToPlaylistModal open={false} song={SONG} onClose={vi.fn()} />);
    expect(screen.queryByTestId('add-to-playlist-modal')).not.toBeInTheDocument();
  });
});
