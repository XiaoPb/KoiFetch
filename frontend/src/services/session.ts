import { useAuthStore } from '../stores/authStore';
import { useDownloadsStore } from '../stores/downloadsStore';

/**
 * Single logout entry point: clear the auth session AND tear down the
 * session-local download state (live sockets, poll timer, task list).
 *
 * Used by the header's logout action, the login page's expired-session
 * handler and the 401 interceptor handler, so no path can leave stale
 * download items or live sockets behind for the next admin — downloadsStore
 * has no server-side list to rehydrate from (see its module docstring).
 */
export function logoutSession(): void {
  useAuthStore.getState().logout();
  useDownloadsStore.getState().teardown();
}
