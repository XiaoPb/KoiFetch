import { create } from 'zustand';

/**
 * Download-center state (Task 13 stub).
 *
 * Only the header badge count lives here today. Task 15 wires the full
 * download task list (submit/progress/WebSocket reconciliation) into this
 * store; the header and shell already read `activeCount` so the badge renders
 * without further changes.
 */
export interface DownloadsState {
  activeCount: number;
  setActiveCount: (count: number) => void;
}

export const useDownloadsStore = create<DownloadsState>()((set) => ({
  activeCount: 0,
  setActiveCount: (activeCount) => set({ activeCount }),
}));
