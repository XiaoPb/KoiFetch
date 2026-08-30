import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LyricsPanel } from './LyricsPanel';
import { useMusicStore } from './musicStore';

describe('LyricsPanel', () => {
  it('highlights and scrolls the active LRC line', () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    act(() => useMusicStore.setState({
      currentSong: {
        kind: 'song', id: 's1', title: 'Song', artist: 'Artist', album: '', cover: null,
        duration: '01:00', play_url: null, lyric: '[00:01.00]one\n[00:03.00]two',
      }, currentTime: 3,
    }));
    render(<LyricsPanel />);
    expect(screen.getByTestId('music-lyric-1')).toHaveClass('active');
    expect(scrollIntoView).toHaveBeenCalled();
  });
});
