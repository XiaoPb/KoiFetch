import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { MusicSong } from '../../types/music';

export interface LocalPlaylist {
  id: string;
  name: string;
  songs: MusicSong[];
  createdAt: string;
  updatedAt: string;
}

export interface PlaylistsState {
  playlists: LocalPlaylist[];
  /** Create a playlist; returns it (null when the name is blank). */
  create: (name: string) => LocalPlaylist | null;
  /** Add a song; returns false when already present. */
  addSong: (playlistId: string, song: MusicSong) => boolean;
  removeSong: (playlistId: string, songId: string) => void;
  rename: (playlistId: string, name: string) => void;
  delete: (playlistId: string) => void;
}

function newId(): string {
  return `pl-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export const usePlaylistsStore = create<PlaylistsState>()(
  persist(
    (set, get) => ({
      playlists: [],

      create: (name) => {
        const trimmed = name.trim();
        if (!trimmed) return null;
        const playlist: LocalPlaylist = {
          id: newId(),
          name: trimmed,
          songs: [],
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
        set((state) => ({ playlists: [...state.playlists, playlist] }));
        return playlist;
      },

      addSong: (playlistId, song) => {
        const playlist = get().playlists.find((p) => p.id === playlistId);
        if (!playlist) return false;
        if (playlist.songs.some((s) => s.id === song.id)) return false;
        set((state) => ({
          playlists: state.playlists.map((p) =>
            p.id === playlistId
              ? { ...p, songs: [...p.songs, song], updatedAt: new Date().toISOString() }
              : p,
          ),
        }));
        return true;
      },

      removeSong: (playlistId, songId) =>
        set((state) => ({
          playlists: state.playlists.map((p) =>
            p.id === playlistId
              ? { ...p, songs: p.songs.filter((s) => s.id !== songId), updatedAt: new Date().toISOString() }
              : p,
          ),
        })),

      rename: (playlistId, name) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        set((state) => ({
          playlists: state.playlists.map((p) =>
            p.id === playlistId ? { ...p, name: trimmed, updatedAt: new Date().toISOString() } : p,
          ),
        }));
      },

      delete: (playlistId) =>
        set((state) => ({ playlists: state.playlists.filter((p) => p.id !== playlistId) })),
    }),
    { name: 'koifetch-music-playlists' },
  ),
);

/** Test hook: wipe persisted state (localStorage in jsdom is per-file). */
export function __resetPlaylistsForTests(): void {
  usePlaylistsStore.setState({ playlists: [] });
}
