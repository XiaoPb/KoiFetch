import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { VideoPlayer, type PlayableSource } from './VideoPlayer';

// xgplayer is browser-only (video element, media APIs); jsdom cannot run it.
// Mock the module and capture the config the component passes to it.
const destroy = vi.fn();
const off = vi.fn();
const on = vi.fn();
let lastConfig: Record<string, unknown> | null = null;

vi.mock('xgplayer', () => ({
  __esModule: true,
  default: class FakePlayer {
    constructor(config: Record<string, unknown>) {
      lastConfig = config;
    }
    on = on;
    off = off;
    destroy = destroy;
  },
}));
vi.mock('xgplayer-hls', () => ({ __esModule: true, default: class HlsPlugin {} }));
vi.mock('xgplayer-flv', () => ({ __esModule: true, default: class FlvPlugin {} }));

const sources: PlayableSource[] = [
  { url: '/api/preview/t1/stream', format: 'mp4' },
  { url: 'https://cdn.example.com/v.mp4', format: 'mp4' },
];

describe('VideoPlayer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastConfig = null;
  });

  it('creates the xgplayer with the first source and a poster', () => {
    render(<VideoPlayer sources={sources} poster="https://cdn.example.com/c.jpg" testId="vp" />);
    expect(lastConfig).toMatchObject({
      url: '/api/preview/t1/stream',
      poster: 'https://cdn.example.com/c.jpg',
      autoplay: false,
      controls: true,
    });
    expect(screen.getByTestId('vp')).toBeInTheDocument();
  });

  it('uses the flv plugin config for flv sources', () => {
    render(
      <VideoPlayer
        sources={[{ url: '/api/preview/t1/stream', format: 'flv' }]}
        testId="vp"
      />,
    );
    // xgplayer v3 (verified in Task 4): plugins are registered via the config
    // `plugins` array and CORS uses fetchOptions — not v2's `cors: true`.
    expect(lastConfig).toMatchObject({
      flv: { fetchOptions: { mode: 'cors' } },
      plugins: [expect.any(Function)],
    });
  });

  it('uses the hls plugin config for m3u8 sources', () => {
    render(
      <VideoPlayer
        sources={[{ url: '/api/preview/t1/stream', format: 'm3u8' }]}
        testId="vp"
      />,
    );
    expect(lastConfig).toMatchObject({
      hls: { fetchOptions: { mode: 'cors' } },
      plugins: [expect.any(Function)],
    });
  });

  it('registers no plugin for native mp4 sources', () => {
    render(<VideoPlayer sources={sources} testId="vp" />);
    expect(lastConfig?.plugins).toEqual([]);
  });

  it('advances to the next source when the player errors', () => {
    render(<VideoPlayer sources={sources} testId="vp" />);
    expect(lastConfig?.url).toBe('/api/preview/t1/stream');
    const onError = on.mock.calls.find(([event]) => event === 'error')?.[1];
    act(() => {
      onError?.();
    });
    expect(lastConfig?.url).toBe('https://cdn.example.com/v.mp4');
  });

  it('shows the playback-failed hint after all sources error', () => {
    render(<VideoPlayer sources={sources} testId="vp" />);
    const fireError = () => {
      // Each source advance registers a NEW 'error' handler, so the currently
      // active player is the LAST one registered — scan from the tail
      // (Array.prototype.findLast is ES2023; the project lib is ES2020).
      const handler = [...on.mock.calls]
        .reverse()
        .find(([event]) => event === 'error')?.[1];
      act(() => {
        handler?.();
      });
    };
    fireError();
    fireError();
    expect(screen.getByTestId('vp-failed')).toBeInTheDocument();
  });

  it('destroys the player on unmount', () => {
    const { unmount } = render(<VideoPlayer sources={sources} testId="vp" />);
    unmount();
    expect(destroy).toHaveBeenCalledTimes(1);
  });
});
