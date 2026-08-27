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
    vi.restoreAllMocks();
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

  it('restarts a finished song from the beginning on play', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ currentSong: SONG, isPlaying: false, currentTime: 5 }));
    renderWithProviders(<MiniPlayer />);
    await user.click(screen.getByTestId('mini-player-toggle'));
    expect(useMusicStore.getState().isPlaying).toBe(true);
    expect(useMusicStore.getState().currentTime).toBe(0);
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

  it('plays real audio when the song has a playable source', async () => {
    const user = userEvent.setup();
    const playMock = vi.fn().mockResolvedValue(undefined);
    const pauseMock = vi.fn();
    // jsdom does not implement HTMLMediaElement playback — stub it.
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(playMock);
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(pauseMock);

    act(() =>
      useMusicStore.setState({
        currentSong: { ...SONG, play_url: 'data:audio/wav;base64,AAAA' },
        isPlaying: true,
        currentTime: 0,
      }),
    );
    renderWithProviders(<MiniPlayer />);
    expect(screen.getByTestId('mini-audio')).toBeInTheDocument();
    expect(playMock).toHaveBeenCalled();

    // Pausing stops the audio element; replay restarts it from 0.
    await user.click(screen.getByTestId('mini-player-toggle'));
    expect(useMusicStore.getState().isPlaying).toBe(false);
    expect(pauseMock).toHaveBeenCalled();

    await user.click(screen.getByTestId('mini-player-toggle'));
    expect(useMusicStore.getState().isPlaying).toBe(true);
    expect(playMock).toHaveBeenCalledTimes(2);
  });
});
