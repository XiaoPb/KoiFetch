import { create } from 'zustand';
import { downloadApi } from '../services/api';
import type { DownloadStatus, SubmitData } from '../types/api';

/**
 * Download-center state.
 *
 * Task 13 shipped the header badge stub (`activeCount`); Task 14 adds
 * `submit()` so result cards can start downloads, and `items` so the Task 15
 * drawer has the task list to render. Task 15 extends this store with
 * progress/WebSocket reconciliation; the header already reads `activeCount`,
 * so the badge updates as soon as a submit lands.
 *
 * `submit()` maps 3001/3002/3003 failures to a rejection carrying the
 * backend's bilingual ApiError message — the caller toasts it.
 */
export interface DownloadItem {
  download_id: string;
  task_id: string;
  status: DownloadStatus;
  created_at: string;
  /** Display title from the parse result (for the Task 15 drawer). */
  title: string | null;
  format: string | null;
  quality: string | null;
}

export interface SubmitOptions {
  format?: string | null;
  quality?: string | null;
  /** Optional display title carried from the parse result. */
  title?: string | null;
}

export interface DownloadsState {
  /** Download tasks the drawer will render (Task 15). */
  items: DownloadItem[];
  /** Header badge: number of active (pending/downloading) tasks. */
  activeCount: number;
  /** Per-task loading flags for [下载] buttons, keyed by task_id. */
  submitting: Record<string, boolean>;

  /** Submit a download for a parsed task; rejects with the ApiError on failure. */
  submit: (taskId: string, options?: SubmitOptions) => Promise<void>;
  setActiveCount: (count: number) => void;
}

/** Active badge count derived from the item list (pending/downloading). */
export const selectActiveCount = (state: DownloadsState): number =>
  state.items.filter((item) => item.status === 'pending' || item.status === 'downloading').length;

export const useDownloadsStore = create<DownloadsState>()((set) => ({
  items: [],
  activeCount: 0,
  submitting: {},

  submit: async (taskId, options = {}) => {
    const format = options.format ?? null;
    const quality = options.quality ?? null;
    set((state) => ({ submitting: { ...state.submitting, [taskId]: true } }));
    try {
      const data: SubmitData = await downloadApi.submit(taskId, { format, quality });
      const item: DownloadItem = {
        download_id: data.download_id,
        task_id: data.task_id,
        status: data.status,
        created_at: data.created_at,
        title: options.title ?? null,
        format,
        quality,
      };
      set((state) => {
        const items = [...state.items, item];
        return { items, activeCount: selectActiveCount({ ...state, items }) };
      });
    } finally {
      set((state) => ({ submitting: { ...state.submitting, [taskId]: false } }));
    }
  },

  setActiveCount: (activeCount) => set({ activeCount }),
}));
