import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { SongActionSheet } from './SongActionSheet';
import { useMusicStore } from './musicStore';
import type { MusicArtist, MusicSong } from '../../types/music';

const SONG: MusicSong = {
  kind: 'song',
  id: 's1',
  title: '晴天',
  artist: '周杰伦',
  album: '叶惠美',
  cover: null,
  duration: '04:29',
  play_url: null,
};

const ARTIST: MusicArtist = { kind: 'artist', id: 'a1', name: '周杰伦', avatar: null, fans: 100, songCount: 3 };

describe('SongActionSheet', () => {
  afterEach(() => {
    useMusicStore.setState({
      actionSheetSong: null,
      currentSong: null,
      detailEntity: null,
      artists: [],
    });
  });

  it('renders nothing when closed', () => {
    renderWithProviders(<SongActionSheet />);
    expect(screen.queryByTestId('music-action-sheet')).not.toBeInTheDocument();
  });

  it('opens with the song title and the action items', () => {
    act(() => useMusicStore.setState({ actionSheetSong: SONG }));
    renderWithProviders(<SongActionSheet />);
    expect(screen.getByText('下一首播放')).toBeInTheDocument();
    expect(screen.getByText('添加到歌单')).toBeInTheDocument();
    expect(screen.getByText('查看歌手')).toBeInTheDocument();
    expect(screen.getByText('取消')).toBeInTheDocument();
  });

  it('plays the song from 下一首播放 and closes', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ actionSheetSong: SONG }));
    renderWithProviders(<SongActionSheet />);
    await user.click(screen.getByText('下一首播放'));
    expect(useMusicStore.getState().currentSong?.id).toBe('s1');
    expect(useMusicStore.getState().actionSheetSong).toBeNull();
  });

  it('opens the artist detail from 查看歌手', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ actionSheetSong: SONG, artists: [ARTIST] }));
    renderWithProviders(<SongActionSheet />);
    await user.click(screen.getByText('查看歌手'));
    expect(useMusicStore.getState().detailEntity?.kind).toBe('artist');
    // detailEntity is MusicEntity | null; the kind assertion above narrows to
    // MusicArtist, so cast for the `.name` access (only artists carry `name`).
    expect((useMusicStore.getState().detailEntity as MusicArtist | null)?.name).toBe('周杰伦');
    expect(useMusicStore.getState().actionSheetSong).toBeNull();
  });

  it('closes on 取消', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ actionSheetSong: SONG }));
    renderWithProviders(<SongActionSheet />);
    await user.click(screen.getByText('取消'));
    expect(useMusicStore.getState().actionSheetSong).toBeNull();
  });
});
