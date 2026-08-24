import { create } from 'zustand';
import type { ParseResult } from '../../types/api';

/**
 * Preview seam between Task 14 (parser workspace) and Task 15 (preview Modal).
 *
 * Contract:
 * - The parser workspace calls `openPreview(result)` on [预览] click — the
 *   store only records WHICH parse result the user clicked; no fetch happens.
 * - Task 15 renders the preview Modal from this store: `activeTask !== null`
 *   means "open", and the modal loads full metadata via
 *   `previewApi.getPreview(activeTask.task_id)`, renders the media, and calls
 *   `closePreview()` to dismiss (which nulls `activeTask` and unmounts).
 *
 * The stored value is the lightweight ParseResult (already in the store), not
 * the richer PreviewData — Task 15 owns the preview fetch so the workspace
 * stays fast and the seam stays one-directional.
 */
export interface PreviewState {
  /** The parse result the user clicked, or null when the modal is closed. */
  activeTask: ParseResult | null;
  openPreview: (task: ParseResult) => void;
  closePreview: () => void;
}

export const usePreviewStore = create<PreviewState>()((set) => ({
  activeTask: null,
  openPreview: (activeTask) => set({ activeTask }),
  closePreview: () => set({ activeTask: null }),
}));
