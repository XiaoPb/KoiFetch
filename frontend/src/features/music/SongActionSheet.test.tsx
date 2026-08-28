import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { SongActionSheet } from './SongActionSheet';
import { useMusicStore } from './musicStore';
import { __resetDownloadStreams, useDownloadsStore } from '../../stores/downloadsStore';
import { musicApi, downloadApi } from '../../services/api';
import type { MusicArtist, MusicSong } from '../../types/music';

vi.mock('../../services/api', () => ({
  musicApi: { importSong: vi.fn(), search: vi.fn() },
  downloadApi: { submit: vi.fn(), getProgress: vi.fn() },
}));

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
  beforeEach(() => {
    // downloadsStore keeps module-level socket/poll state; reset it so items
    // never leak across cases.
    __resetDownloadStreams();
    useDownloadsStore.setState({ items: [], submitting: {} });
    vi.mocked(musicApi.importSong).mockReset();
    vi.mocked(downloadApi.submit).mockReset();
  });

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
    expect(screen.getByText('下载')).toBeInTheDocument();
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

  it('downloads the song through the existing pipeline', async () => {
    vi.mocked(musicApi.importSong).mockResolvedValue({ task_id: 'task-1' });
    vi.mocked(downloadApi.submit).mockResolvedValue({
      download_id: 'dl-1', task_id: 'task-1', status: 'pending', created_at: '2026-01-01T00:00:00Z',
    });
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ actionSheetSong: SONG }));
    renderWithProviders(<SongActionSheet />);
    await user.click(screen.getByTestId('action-download'));
    expect(await screen.findByText('已提交下载任务')).toBeInTheDocument();
    expect(musicApi.importSong).toHaveBeenCalledWith('s1');
    // downloadsStore.submit forwards only format/quality to the API; the
    // title rides along on the local item.
    expect(downloadApi.submit).toHaveBeenCalledWith('task-1', { format: null, quality: null });
    expect(useDownloadsStore.getState().items[0].title).toBe('晴天');
  });

  it('shows an error message when import fails', async () => {
    vi.mocked(musicApi.importSong).mockRejectedValue(new Error('歌曲不存在'));
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ actionSheetSong: SONG }));
    renderWithProviders(<SongActionSheet />);
    await user.click(screen.getByTestId('action-download'));
    expect(await screen.findByText('歌曲不存在')).toBeInTheDocument();
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
