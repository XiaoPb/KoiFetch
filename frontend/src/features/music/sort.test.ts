import { describe, expect, it } from 'vitest';
import { sortSongs } from './sort';
import type { MusicSong } from '../../types/music';

function song(id: string, bitrate?: number): MusicSong {
  return { kind: 'song', id, title: id, artist: 'A', album: '', cover: null, duration: '03:00', play_url: null, bitrate };
}

describe('sortSongs', () => {
  it('keeps comprehensive order as returned', () => {
    const songs = [song('a', 128), song('b', 320)];
    expect(sortSongs(songs, 'comprehensive').map((s) => s.id)).toEqual(['a', 'b']);
  });
  it('sorts hot by bitrate descending, unknowns last', () => {
    const songs = [song('a'), song('b', 128), song('c', 320)];
    expect(sortSongs(songs, 'hot').map((s) => s.id)).toEqual(['c', 'b', 'a']);
  });
  it('latest equals comprehensive in v1', () => {
    const songs = [song('a', 128), song('b', 320)];
    expect(sortSongs(songs, 'latest').map((s) => s.id)).toEqual(['a', 'b']);
  });
});
