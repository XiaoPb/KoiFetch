import type { MusicSong } from '../../types/music';
import type { MusicSort } from './musicStore';

/**
 * Client-side song sorting (P2). Honest v1 semantics, documented:
 * - comprehensive: as returned by the backend (source priority order).
 * - hot: bitrate descending as a documented proxy for popularity (musicdl
 *   exposes no play counts); songs without a bitrate sort last.
 * - latest: identical to comprehensive — musicdl exposes no reliable publish
 *   timestamp (documented in code).
 */
export function sortSongs(songs: MusicSong[], sort: MusicSort): MusicSong[] {
  if (sort !== 'hot') return songs;
  return [...songs].sort((a, b) => (b.bitrate ?? 0) - (a.bitrate ?? 0));
}
