import { describe, expect, it } from 'vitest';
import { activeLyricIndex, parseLyrics } from './lyrics';

describe('lyrics parsing', () => {
  it('parses centisecond LRC timestamps and sorts lines', () => {
    const lines = parseLyrics('[00:02.50]second\n[00:01.00]first');
    expect(lines).toEqual([
      { time: 1, text: 'first' },
      { time: 2.5, text: 'second' },
    ]);
    expect(activeLyricIndex(lines, 2)).toBe(0);
    expect(activeLyricIndex(lines, 2.5)).toBe(1);
  });

  it('keeps plain lyrics usable without timestamps', () => {
    expect(parseLyrics('plain lyric')).toEqual([{ time: Number.POSITIVE_INFINITY, text: 'plain lyric' }]);
  });
});
