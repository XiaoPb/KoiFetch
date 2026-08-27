import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { EntityDetailDrawer } from './EntityDetailDrawer';
import { useMusicStore } from './musicStore';
import type { MusicAlbum, MusicArtist, MusicSong } from '../../types/music';

const ARTIST: MusicArtist = { kind: 'artist', id: 'a1', name: '周杰伦', avatar: null, fans: 100, songCount: 3 };
const ALBUM: MusicAlbum = { kind: 'album', id: 'al1', title: '叶惠美精选', artist: '周杰伦', cover: null, songCount: 12 };
const SONGS: MusicSong[] = [
  { kind: 'song', id: 's1', title: '晴天', artist: '周杰伦', album: '叶惠美精选', cover: null, duration: '04:29', play_url: null },
  { kind: 'song', id: 's2', title: '夜曲', artist: '周杰伦', album: '叶惠美精选', cover: null, duration: '03:45', play_url: null },
];

describe('EntityDetailDrawer', () => {
  afterEach(() => {
    useMusicStore.setState({ detailEntity: null, currentSong: null, songs: [] });
  });

  it('renders nothing when closed', () => {
    renderWithProviders(<EntityDetailDrawer />);
    expect(screen.queryByTestId('music-detail-drawer')).not.toBeInTheDocument();
  });

  it('shows artist details and its hot songs', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ detailEntity: ARTIST, songs: SONGS }));
    renderWithProviders(<EntityDetailDrawer />);
    expect(screen.getByText('歌手详情')).toBeInTheDocument();
    expect(screen.getByText('热门歌曲')).toBeInTheDocument();
    await user.click(screen.getByText('晴天'));
    expect(useMusicStore.getState().currentSong?.id).toBe('s1');
  });

  it('shows album details', () => {
    act(() => useMusicStore.setState({ detailEntity: ALBUM, songs: SONGS }));
    renderWithProviders(<EntityDetailDrawer />);
    expect(screen.getByText('专辑详情')).toBeInTheDocument();
    expect(screen.getByText('叶惠美精选')).toBeInTheDocument();
  });

  it('shows playlist details without a hot-songs section', () => {
    act(() =>
      useMusicStore.setState({
        detailEntity: { kind: 'playlist', id: 'p1', title: '周杰伦的热门歌单', creator: '网易云音乐', cover: null, songCount: 50 },
      }),
    );
    renderWithProviders(<EntityDetailDrawer />);
    expect(screen.getByText('歌单详情')).toBeInTheDocument();
    expect(screen.getByText('周杰伦的热门歌单')).toBeInTheDocument();
    expect(screen.queryByText('热门歌曲')).not.toBeInTheDocument();
  });

  it('shows the no-songs hint when no songs match the entity', () => {
    act(() => useMusicStore.setState({ detailEntity: ARTIST, songs: [] }));
    renderWithProviders(<EntityDetailDrawer />);
    expect(screen.getByText('歌手详情')).toBeInTheDocument();
    expect(screen.getByText('暂无歌曲')).toBeInTheDocument();
  });
});
