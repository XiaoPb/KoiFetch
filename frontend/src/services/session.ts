import { useAuthStore } from '../stores/authStore';
import { useDownloadsStore } from '../stores/downloadsStore';

/**
 * Resolve only after Zustand has finished loading the persisted auth state.
 * Sync localStorage normally makes this an immediate resolution, while the
 * subscription path also supports asynchronous storage adapters.
 */
export function waitForAuthHydration(signal?: AbortSignal): Promise<void> {
  const persist = useAuthStore.persist;
  if (persist.hasHydrated() || signal?.aborted) return Promise.resolve();

  return new Promise((resolve) => {
    let unsubscribe: (() => void) | undefined;
    let finished = false;
    let cleanupBeforeSubscribe = false;
    const onAbort = (): void => finish();
    const cleanup = (): void => {
      if (unsubscribe) {
        unsubscribe();
      } else {
        cleanupBeforeSubscribe = true;
      }
      signal?.removeEventListener('abort', onAbort);
    };
    const finish = (): void => {
      if (finished) return;
      finished = true;
      cleanup();
      resolve();
    };

    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) {
      finish();
      return;
    }
    unsubscribe = persist.onFinishHydration(finish);
    // Handle adapters that invoke the listener synchronously while it is
    // being registered, and close the check/subscribe race.
    if (cleanupBeforeSubscribe) {
      unsubscribe();
    } else if (!finished && persist.hasHydrated()) {
      finish();
    }
  });
}

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
