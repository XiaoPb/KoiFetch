import { create } from 'zustand';
import type { Language } from '../services/i18n';

export type MediaMode = 'video' | 'music';

export type HealthStatus = 'online' | 'offline' | 'unknown';

export interface HealthState {
  status: HealthStatus;
  checkedAt: string | null;
}

/**
 * App-wide UI state (Task 13 shell): language, media mode, global loading
 * (a counter fed by the axios interceptors), global error/toast, and the
 * backend health indicator.
 */
export interface AppState {
  language: Language;
  mediaMode: MediaMode;
  /** Number of in-flight API requests; > 0 means a global loading state. */
  pendingRequests: number;
  /** Most recent global error message (consumed + cleared by the shell). */
  lastError: string | null;
  health: HealthState;

  setLanguage: (language: Language) => void;
  setMediaMode: (mode: MediaMode) => void;
  beginRequest: () => void;
  endRequest: () => void;
  showError: (message: string) => void;
  clearError: () => void;
  setHealth: (status: HealthStatus) => void;
}

export const useAppStore = create<AppState>()((set) => ({
  language: 'zh',
  mediaMode: 'video',
  pendingRequests: 0,
  lastError: null,
  health: { status: 'unknown', checkedAt: null },

  setLanguage: (language) => set({ language }),
  setMediaMode: (mediaMode) => set({ mediaMode }),
  beginRequest: () => set((state) => ({ pendingRequests: state.pendingRequests + 1 })),
  endRequest: () =>
    set((state) => ({ pendingRequests: Math.max(0, state.pendingRequests - 1) })),
  showError: (message) => set({ lastError: message }),
  clearError: () => set({ lastError: null }),
  setHealth: (status) => set({ health: { status, checkedAt: new Date().toISOString() } }),
}));

export const selectIsLoading = (state: AppState): boolean => state.pendingRequests > 0;
