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
  refreshSession: () => Promise<void>;
}

type PersistedAuthState = Pick<AuthState, 'token' | 'username' | 'expiresAt'>;

function migrateAuthState(persistedState: unknown): PersistedAuthState {
  const state = typeof persistedState === 'object' && persistedState !== null
    ? persistedState as Record<string, unknown>
    : {};
  return {
    token: typeof state.token === 'string' ? state.token : null,
    username: typeof state.username === 'string' ? state.username : null,
    expiresAt: typeof state.expiresAt === 'string' ? state.expiresAt : null,
  };
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
  // Keep the guard per store instance. The exported singleton deduplicates
  // production startup calls, while tests and independently-created stores do
  // not share a pending request or revision counter.
  let inFlightRefresh: Promise<void> | null = null;
  let sessionRevision = 0;

  return create<AuthState>()(
    persist(
      (set, get) => ({
        token: null,
        username: null,
        expiresAt: null,

        login: async (username, password) => {
          const loginRevision = sessionRevision;
          const data: LoginData = await authApi.login({ username, password });
          if (sessionRevision !== loginRevision) return;
          sessionRevision += 1;
          set({ token: data.token, username: data.username, expiresAt: data.expires_at });
        },

        logout: () => {
          sessionRevision += 1;
          set({ token: null, username: null, expiresAt: null });
        },

        refreshSession: () => {
          if (inFlightRefresh) return inFlightRefresh;

          const { token, expiresAt } = get();
          if (!token || isExpired(expiresAt)) return Promise.resolve();

          const startedToken = token;
          const startedRevision = sessionRevision;
          inFlightRefresh = authApi.refresh(startedToken)
            .then((data) => {
              if (sessionRevision !== startedRevision || get().token !== startedToken) return;
              sessionRevision += 1;
              set({ token: data.token, username: data.username, expiresAt: data.expires_at });
            })
            .finally(() => {
              inFlightRefresh = null;
            });
          return inFlightRefresh;
        },
      }),
      {
        name: AUTH_STORAGE_KEY,
        // Bump when the persisted session shape changes (see zustand's
        // migrate option) so stale data from an older shape cannot corrupt
        // rehydration in later tasks.
        version: 2,
        migrate: (persistedState) => migrateAuthState(persistedState),
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
