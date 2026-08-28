import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/utils';
import { EntityDetailView } from './entityViews';
import type { MusicAlbum, MusicArtist, MusicPlaylist, MusicSong } from '../../types/music';

const ARTIST: MusicArtist = { kind: 'artist', id: 'a1', name: '周杰伦', avatar: null, fans: 100, songCount: 3 };
const ALBUM: MusicAlbum = { kind: 'album', id: 'al1', title: '叶惠美精选', artist: '周杰伦', cover: null, songCount: 12 };
const PLAYLIST: MusicPlaylist = { kind: 'playlist', id: 'p1', title: '周杰伦的热门歌单', creator: '网易云音乐', cover: null, songCount: 50 };
const SONGS: MusicSong[] = [
  { kind: 'song', id: 's1', title: '晴天', artist: '周杰伦', album: '叶惠美精选', cover: null, duration: '04:29', play_url: null },
  { kind: 'song', id: 's2', title: '夜曲', artist: '周杰伦', album: '叶惠美精选', cover: null, duration: '03:45', play_url: null },
];

describe('EntityDetailView', () => {
  it('shows artist details and plays its hot songs', async () => {
    const user = userEvent.setup();
    const onPlay = vi.fn();
    renderWithProviders(<EntityDetailView entity={ARTIST} songs={SONGS} onPlay={onPlay} onMore={vi.fn()} />);
    expect(screen.getByText('周杰伦')).toBeInTheDocument();
    expect(screen.getByText('热门歌曲')).toBeInTheDocument();
    await user.click(screen.getByText('晴天'));
    expect(onPlay).toHaveBeenCalledWith(SONGS[0]);
  });

  it('shows album details', () => {
    renderWithProviders(<EntityDetailView entity={ALBUM} songs={SONGS} onPlay={vi.fn()} onMore={vi.fn()} />);
    expect(screen.getByText('叶惠美精选')).toBeInTheDocument();
    expect(screen.getByText(/周杰伦 发行/)).toBeInTheDocument();
  });

  it('shows playlist details without a hot-songs section', () => {
    renderWithProviders(<EntityDetailView entity={PLAYLIST} songs={[]} onPlay={vi.fn()} onMore={vi.fn()} />);
    expect(screen.getByText('周杰伦的热门歌单')).toBeInTheDocument();
    expect(screen.queryByText('热门歌曲')).not.toBeInTheDocument();
  });

  it('shows the no-songs hint when no songs match the entity', () => {
    renderWithProviders(<EntityDetailView entity={ARTIST} songs={[]} onPlay={vi.fn()} onMore={vi.fn()} />);
    expect(screen.getByText('暂无歌曲')).toBeInTheDocument();
  });

  it('forwards the more action from a hot-song button', async () => {
    const user = userEvent.setup();
    const onMore = vi.fn();
    renderWithProviders(<EntityDetailView entity={ARTIST} songs={SONGS} onPlay={vi.fn()} onMore={onMore} />);
    await user.click(screen.getByTestId('detail-more-s1'));
    expect(onMore).toHaveBeenCalledWith(SONGS[0]);
  });
});
