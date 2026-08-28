import { beforeEach, describe, expect, it } from 'vitest';
import { usePlaylistsStore, __resetPlaylistsForTests } from './playlistsStore';
import type { MusicSong } from '../../types/music';

function makeSong(id: string): MusicSong {
  return { kind: 'song', id, title: `Song ${id}`, artist: 'A', album: 'X', cover: null, duration: '03:00', play_url: null };
}

describe('playlistsStore', () => {
  beforeEach(() => {
    __resetPlaylistsForTests();
  });

  it('creates a playlist and adds songs idempotently', () => {
    const created = usePlaylistsStore.getState().create('我的最爱');
    expect(created?.name).toBe('我的最爱');
    const id = created!.id;
    expect(usePlaylistsStore.getState().addSong(id, makeSong('s1'))).toBe(true);
    expect(usePlaylistsStore.getState().addSong(id, makeSong('s1'))).toBe(false); // duplicate
    const list = usePlaylistsStore.getState().playlists.find((p) => p.id === id)!;
    expect(list.songs).toHaveLength(1);
  });

  it('removes, renames and deletes playlists', () => {
    const created = usePlaylistsStore.getState().create('P')!;
    usePlaylistsStore.getState().addSong(created.id, makeSong('s1'));
    usePlaylistsStore.getState().removeSong(created.id, 's1');
    expect(usePlaylistsStore.getState().playlists[0].songs).toHaveLength(0);
    usePlaylistsStore.getState().rename(created.id, 'P2');
    expect(usePlaylistsStore.getState().playlists[0].name).toBe('P2');
    usePlaylistsStore.getState().delete(created.id);
    expect(usePlaylistsStore.getState().playlists).toHaveLength(0);
  });
});
