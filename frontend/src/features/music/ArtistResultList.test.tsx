import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { ArtistResultList } from './ArtistResultList';
import { AlbumPlaylistList } from './AlbumPlaylistList';
import type { MusicAlbum, MusicArtist, MusicPlaylist } from '../../types/music';

const ARTISTS: MusicArtist[] = [
  { kind: 'artist', id: 'a1', name: '周杰伦', avatar: null, fans: 8_000_000, songCount: 120 },
];

const ALBUMS: MusicAlbum[] = [
  { kind: 'album', id: 'al1', title: '叶惠美精选', artist: '周杰伦', cover: null, songCount: 12 },
];

const PLAYLISTS: MusicPlaylist[] = [
  { kind: 'playlist', id: 'p1', title: '周杰伦的热门歌单', creator: '网易云音乐', cover: null, songCount: 50 },
];

describe('ArtistResultList (View B)', () => {
  it('renders the artist name and fan/song meta', () => {
    renderWithProviders(<ArtistResultList artists={ARTISTS} onOpenDetail={vi.fn()} />);
    expect(screen.getByText('周杰伦')).toBeInTheDocument();
    expect(screen.getByText(/8,000,000 粉丝/)).toBeInTheDocument();
  });

  it('opens the detail on card click', async () => {
    const user = userEvent.setup();
    const onOpenDetail = vi.fn();
    renderWithProviders(<ArtistResultList artists={ARTISTS} onOpenDetail={onOpenDetail} />);
    await user.click(screen.getByText('周杰伦'));
    expect(onOpenDetail).toHaveBeenCalledWith(ARTISTS[0]);
  });

  it('opens the detail on Enter key', async () => {
    const user = userEvent.setup();
    const onOpenDetail = vi.fn();
    renderWithProviders(<ArtistResultList artists={ARTISTS} onOpenDetail={onOpenDetail} />);
    await user.tab();
    await user.keyboard('{Enter}');
    expect(onOpenDetail).toHaveBeenCalledWith(ARTISTS[0]);
  });
});

describe('AlbumPlaylistList (View C)', () => {
  it('renders album cards with publisher and song count', () => {
    renderWithProviders(<AlbumPlaylistList category="album" albums={ALBUMS} playlists={[]} onOpenDetail={vi.fn()} />);
    expect(screen.getByText('叶惠美精选')).toBeInTheDocument();
    expect(screen.getByText(/周杰伦 发行/)).toBeInTheDocument();
  });

  it('renders playlist cards with creator and song count', () => {
    renderWithProviders(<AlbumPlaylistList category="playlist" albums={[]} playlists={PLAYLISTS} onOpenDetail={vi.fn()} />);
    expect(screen.getByText('周杰伦的热门歌单')).toBeInTheDocument();
    expect(screen.getByText(/网易云音乐 创建/)).toBeInTheDocument();
  });

  it('opens the detail on card click', async () => {
    const user = userEvent.setup();
    const onOpenDetail = vi.fn();
    renderWithProviders(<AlbumPlaylistList category="album" albums={ALBUMS} playlists={[]} onOpenDetail={onOpenDetail} />);
    await user.click(screen.getByText('叶惠美精选'));
    expect(onOpenDetail).toHaveBeenCalledWith(ALBUMS[0]);
  });
});
