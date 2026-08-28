import { act, fireEvent, screen } from '@testing-library/react';
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
    useMusicStore.setState({
      currentSong: null,
      isPlaying: false,
      currentTime: 0,
      queue: [],
      queueIndex: -1,
      loopMode: 'sequence',
    });
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

  it('seeks to the tapped position on the progress bar', () => {
    act(() =>
      useMusicStore.setState({
        currentSong: { ...SONG, duration: '04:00' },
        isPlaying: false,
        currentTime: 0,
      }),
    );
    renderWithProviders(<MiniPlayer />);
    const bar = screen.getByTestId('music-mini-progress');
    // jsdom reports a zero rect; stub it so the ratio is computable.
    vi.spyOn(bar, 'getBoundingClientRect').mockReturnValue({
      left: 0, right: 200, top: 0, bottom: 4, width: 200, height: 4,
      x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect);
    // jsdom's PointerEvent drops `clientX`; a MouseEvent-typed pointerdown
    // carries it (React listens by event type, not event class).
    fireEvent(bar, new MouseEvent('pointerdown', { clientX: 100, bubbles: true }));
    expect(useMusicStore.getState().currentTime).toBe(120); // 50% of 240s
  });

  it('uses the real audio duration once metadata loads', () => {
    act(() =>
      useMusicStore.setState({
        currentSong: { ...SONG, duration: '04:00', play_url: 'data:audio/wav;base64,AAAA' },
        isPlaying: false,
        currentTime: 0,
      }),
    );
    renderWithProviders(<MiniPlayer />);
    const audio = screen.getByTestId('mini-audio');
    // jsdom's HTMLMediaElement reports NaN duration/currentTime; stub them so
    // the loadedmetadata handler picks up the REAL duration (300s ≠ 240s meta).
    Object.defineProperty(audio, 'duration', { value: 300, configurable: true });
    Object.defineProperty(audio, 'currentTime', { value: 150, configurable: true });
    fireEvent.loadedMetadata(audio);
    fireEvent.timeUpdate(audio);
    // 150s of the real 300s → 50%; the time text shows the real total
    // (formatSeconds pads seconds, not minutes: "2:30 / 5:00").
    expect(screen.getByTestId('music-mini-time')).toHaveTextContent('2:30 / 5:00');
  });

  it('toasts and pauses when the audio source errors', () => {
    act(() =>
      useMusicStore.setState({
        currentSong: { ...SONG, play_url: 'http://bad/1.mp3' },
        isPlaying: true,
        currentTime: 0,
      }),
    );
    renderWithProviders(<MiniPlayer />);
    fireEvent.error(screen.getByTestId('mini-audio'));
    expect(screen.getByText('播放失败，可下载后播放')).toBeInTheDocument();
    expect(useMusicStore.getState().isPlaying).toBe(false);
  });

  it('advances to the next queue entry on ended', () => {
    act(() => {
      useMusicStore.getState().playSong({ ...SONG, play_url: 'data:audio/wav;base64,AAAA' });
      useMusicStore.getState().enqueueNext({ ...SONG, id: 's2', title: '夜曲' });
    });
    renderWithProviders(<MiniPlayer />);
    fireEvent.ended(screen.getByTestId('mini-audio'));
    expect(useMusicStore.getState().currentSong?.id).toBe('s2');
    expect(useMusicStore.getState().isPlaying).toBe(true);
  });

  it('repeats the current song on ended in loopOne mode', () => {
    act(() => {
      useMusicStore.setState({ loopMode: 'loopOne' });
      useMusicStore.getState().playSong({ ...SONG, play_url: 'data:audio/wav;base64,AAAA' });
    });
    renderWithProviders(<MiniPlayer />);
    fireEvent.ended(screen.getByTestId('mini-audio'));
    expect(useMusicStore.getState().currentSong?.id).toBe('s1');
    expect(useMusicStore.getState().isPlaying).toBe(true);
  });

  it('cycles the loop mode from the loop button', async () => {
    const user = userEvent.setup();
    act(() => useMusicStore.setState({ currentSong: SONG, isPlaying: false }));
    renderWithProviders(<MiniPlayer />);
    expect(useMusicStore.getState().loopMode).toBe('sequence');
    await user.click(screen.getByTestId('mini-player-loop'));
    expect(useMusicStore.getState().loopMode).toBe('loopOne');
  });
});
