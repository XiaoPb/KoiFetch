import {
  getAuthHydrationStatus,
  onAuthHydrationSettled,
  useAuthStore,
  type AuthHydrationStatus,
} from '../stores/authStore';
import { useDownloadsStore } from '../stores/downloadsStore';

/**
 * Resolve only after Zustand has finished loading the persisted auth state.
 * Sync localStorage normally makes this an immediate resolution, while the
 * subscription path also supports asynchronous storage adapters.
 */
export function waitForAuthHydration(signal?: AbortSignal): Promise<AuthHydrationStatus> {
  const persist = useAuthStore.persist;
  const hydrated = persist.hasHydrated();
  const status = getAuthHydrationStatus();
  if (hydrated || signal?.aborted || status.state === 'error') {
    return Promise.resolve(status);
  }

  return new Promise((resolve) => {
    let unsubscribeFinish: (() => void) | undefined;
    let unsubscribeStatus: (() => void) | undefined;
    let finished = false;
    let cleanupBeforeSubscribe = false;
    let resolvedStatus: AuthHydrationStatus = { state: 'pending' };
    const onAbort = (): void => finish(getAuthHydrationStatus());
    const cleanup = (): void => {
      if (unsubscribeFinish) {
        unsubscribeFinish();
      } else {
        cleanupBeforeSubscribe = true;
      }
      unsubscribeStatus?.();
      signal?.removeEventListener('abort', onAbort);
    };
    const finish = (nextStatus: AuthHydrationStatus): void => {
      if (finished) return;
      finished = true;
      resolvedStatus = nextStatus;
      cleanup();
      resolve(resolvedStatus);
    };
    const onStatus = (nextStatus: AuthHydrationStatus): void => {
      if (nextStatus.state === 'error') {
        finish(nextStatus);
      } else if (nextStatus.state === 'ready' && persist.hasHydrated()) {
        finish(nextStatus);
      }
    };

    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) {
      finish(status);
      return;
    }
    unsubscribeStatus = onAuthHydrationSettled(onStatus);
    unsubscribeFinish = persist.onFinishHydration(() => {
      finish(getAuthHydrationStatus());
    });
    // Handle adapters that invoke a listener synchronously while it is being
    // registered, and close the check/subscribe race.
    if (cleanupBeforeSubscribe) {
      unsubscribeFinish();
    } else if (!finished && persist.hasHydrated()) {
      finish(getAuthHydrationStatus());
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
