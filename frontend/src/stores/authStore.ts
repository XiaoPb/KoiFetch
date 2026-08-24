import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { authApi } from '../services/api';
import type { LoginData } from '../types/api';

export const AUTH_STORAGE_KEY = 'koi-fetch.auth';

/**
 * Admin auth state (Task 13 shell): the JWT session, persisted to
 * localStorage, with login/logout actions and token-expiry handling.
 *
 * The session triple (token / username / expires_at from the login response)
 * is what gets persisted; `isAuthenticated` additionally requires the token
 * to be unexpired. Feature stores (NAS, download center) read
 * `selectIsAuthenticated` to gate admin-only UI.
 */
export interface AuthState {
  token: string | null;
  username: string | null;
  /** ISO-8601 with timezone; null when logged out. */
  expiresAt: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

/** True when an ISO timestamp is missing, malformed, or in the past. */
export function isExpired(expiresAt: string | null | undefined, now: Date = new Date()): boolean {
  if (!expiresAt) return true;
  const time = Date.parse(expiresAt);
  return Number.isNaN(time) || time <= now.getTime();
}

export const selectIsAuthenticated = (state: AuthState): boolean =>
  Boolean(state.token) && !isExpired(state.expiresAt);

export function createAuthStore() {
  return create<AuthState>()(
    persist(
      (set) => ({
        token: null,
        username: null,
        expiresAt: null,

        login: async (username, password) => {
          const data: LoginData = await authApi.login({ username, password });
          set({ token: data.token, username: data.username, expiresAt: data.expires_at });
        },

        logout: () => set({ token: null, username: null, expiresAt: null }),
      }),
      {
        name: AUTH_STORAGE_KEY,
        // Only the session triple is persisted — actions are recreated.
        partialize: (state) => ({
          token: state.token,
          username: state.username,
          expiresAt: state.expiresAt,
        }),
      },
    ),
  );
}

/** The app-wide singleton auth store. */
export const useAuthStore = createAuthStore();
