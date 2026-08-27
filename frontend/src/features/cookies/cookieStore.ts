import { create } from 'zustand';
import { cookieApi } from '../../services/api';
import { getErrorMessage } from '../../services/apiClient';
import type { CookieEntry } from '../../types/api';

/**
 * The platforms the f2 parser supports today; each can hold an admin cookie.
 * The backend is platform-agnostic, so adding an f2 platform later only
 * touches this list (plus an i18n label).
 */
export const COOKIE_PLATFORMS = [
  { platform: 'douyin', labelKey: 'cookies.platform.douyin' },
  { platform: 'weibo', labelKey: 'cookies.platform.weibo' },
  { platform: 'tiktok', labelKey: 'cookies.platform.tiktok' },
] as const;

export type CookiePlatform = (typeof COOKIE_PLATFORMS)[number]['platform'];

/** True when an entry for ``platform`` exists and is configured. */
export function isConfigured(entries: CookieEntry[], platform: string): boolean {
  return entries.some((entry) => entry.platform === platform && entry.configured);
}

/** The last-updated timestamp for ``platform`` (null when unset). */
export function updatedAtOf(entries: CookieEntry[], platform: string): string | null {
  return entries.find((entry) => entry.platform === platform)?.updated_at ?? null;
}

/**
 * Admin cookie settings state. Cookies are stored server-side; the frontend
 * never persists the cookie value itself — the backend returns only
 * `configured` + `updated_at`. `drawerOpen` lives here so both the header
 * gear and the parse-time cookie-error prompt can open the same drawer.
 *
 * Error contract (asymmetric by design):
 * - `load` swallows failures into `state.error` — the drawer renders a retry
 *   Alert, so it never rejects.
 * - `save`/`remove` REJECT on failure so callers can toast the error.
 * - A successful save/remove followed by a failed reload resolves normally:
 *   the toast and the retry Alert may coexist — intended, not a bug.
 */
export interface CookieState {
  entries: CookieEntry[];
  drawerOpen: boolean;
  loading: boolean;
  error: string | null;
  openDrawer: () => void;
  closeDrawer: () => void;
  load: () => Promise<void>;
  save: (platform: string, cookie: string) => Promise<void>;
  remove: (platform: string) => Promise<void>;
}

export const useCookieStore = create<CookieState>()((set, get) => ({
  entries: [],
  drawerOpen: false,
  loading: false,
  error: null,

  openDrawer: () => {
    set({ drawerOpen: true });
    // Load failures land in state.error (the drawer renders a retry Alert).
    void get().load();
  },

  closeDrawer: () => set({ drawerOpen: false }),

  load: async () => {
    set({ loading: true, error: null });
    try {
      const data = await cookieApi.list();
      set({ entries: data.cookies, loading: false });
    } catch (err) {
      set({ error: getErrorMessage(err), loading: false });
    }
  },

  save: async (platform, cookie) => {
    await cookieApi.set(platform, cookie);
    await get().load();
  },

  remove: async (platform) => {
    await cookieApi.remove(platform);
    await get().load();
  },
}));
