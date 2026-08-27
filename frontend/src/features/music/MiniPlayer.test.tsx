import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { MiniPlayer } from './MiniPlayer';
import { useMusicStore } from './musicStore';
import type { MusicSong } from '../../types/music';

const SONG: MusicSong = {
  kind: 'song',
  id: 's1',
  title: '晴天',
  artist: '周杰伦',
  album: '叶惠美',
  cover: null,
  duration: '00:05',
  play_url: null,
};

describe('MiniPlayer', () => {
  afterEach(() => {
    vi.useRealTimers();
    useMusicStore.setState({ currentSong: null, isPlaying: false, currentTime: 0 });
  });

  it('renders nothing without a current song', () => {
    renderWithProviders(<MiniPlayer />);
    expect(screen.queryByTestId('music-mini-player')).not.toBeInTheDocument();
  });

  it('shows the song info and advances simulated progress while playing', () => {
    vi.useFakeTimers();
    act(() => useMusicStore.setState({ currentSong: SONG, isPlaying: true, currentTime: 0 }));
    renderWithProviders(<MiniPlayer />);
    expect(screen.getByText('晴天')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1000));
    expect(useMusicStore.getState().currentTime).toBe(1);
  });

  it('stops at the end of the song and pauses', () => {
    vi.useFakeTimers();
    act(() => useMusicStore.setState({ currentSong: SONG, isPlaying: true, currentTime: 0 }));
    renderWithProviders(<MiniPlayer />);
    act(() => vi.advanceTimersByTime(5000));
    expect(useMusicStore.getState().currentTime).toBe(5);
    expect(useMusicStore.getState().isPlaying).toBe(false);
  });

  it('toggles play/pause on the button', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ currentSong: SONG, isPlaying: true, currentTime: 0 }));
    renderWithProviders(<MiniPlayer />);
    await user.click(screen.getByTestId('mini-player-toggle'));
    expect(useMusicStore.getState().isPlaying).toBe(false);
    await user.click(screen.getByTestId('mini-player-toggle'));
    expect(useMusicStore.getState().isPlaying).toBe(true);
  });

  it('closes via the close button', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ currentSong: SONG, isPlaying: true, currentTime: 0 }));
    renderWithProviders(<MiniPlayer />);
    await user.click(screen.getByTestId('mini-player-close'));
    expect(useMusicStore.getState().currentSong).toBeNull();
  });
});
