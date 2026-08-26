import { create } from 'zustand';
import { downloadApi } from '../services/api';
import { DownloadWsClient, buildWsUrl } from '../services/wsClient';
import { ApiCodes, ApiError } from '../types/api';
import type {
  DownloadProgress,
  DownloadStatus,
  SubmitData,
  WsCompleteData,
  WsErrorData,
  WsEvent,
} from '../types/api';

/**
 * Download-center state (Task 15): the task list the drawer renders, live
 * progress reconciliation over WebSocket with a polling fallback, retry for
 * failed/expired tasks, and the tokenized-file handoff.
 *
 * Reconciliation policy (documented):
 *
 * - **WS primary, polling reconciles continuously.** Every new download opens
 *   one `DownloadWsClient` to `/ws/download/{id}`. `progress` events update
 *   the item in place; the terminal events (`complete` / `error`) capture the
 *   final state and close the socket (the client itself also closes on
 *   terminal events).
 * - **Polling reconciles ALL active items, socket or not.** A 3s interval
 *   polls `getProgress` for every pending/downloading item regardless of
 *   socket state. WS events (when they arrive) update faster; HTTP polling is
 *   what keeps the UI moving when the worker runs in a SEPARATE process whose
 *   events never reach the API's WS hub — there the socket is OPEN but
 *   silent, and socket-suppressed polling would freeze the item at its
 *   connect-time snapshot (exactly the two-process deployment shape). Poll
 *   snapshots are idempotent and terminal-guarded, so the overlap with live
 *   WS events is harmless. The loop stops entirely once no active item needs
 *   it. Polling can never mint a file link — see below.
 * - **Terminal states stop everything.** Once an item reaches
 *   completed/failed/expired, its socket is released and it stops being
 *   polled; the loop self-terminates when no active item remains.
 * - **download_url comes ONLY from the WS `complete` event.** The backend has
 *   no HTTP endpoint that mints a link token; `issue_download_token` is only
 *   reachable through the WS handler, which sends a fresh `complete` event
 *   (with a new `download_url` + `token_expire_at`) every time it connects to
 *   a completed download. Consequences:
 *   - A download that completes while the client only polls (missed WS) has
 *     `download_url === null`; the drawer shows an honest "链接不可用" state
 *     with a [刷新链接] action that briefly reconnects the socket to capture a
 *     fresh `complete` event.
 *   - The one-time token expires after 5 minutes (`token_expire_at`); the
 *     drawer checks it before opening the file and offers the same refresh.
 * - **Retry = re-submit.** The backend's state graph allows `failed -> pending`
 *   and `expired -> pending`, modeled as a NEW download row (fresh
 *   `download_id`) on `submit`. `retry()` re-submits with the failed item's
 *   format/quality and replaces the item in place so the list keeps one entry
 *   per task. A re-submit of a completed identical variant fails with 3003
 *   (the UI toasts the backend message).
 * - **No cancel.** The v1 backend contract has no cancel endpoint and the
 *   state graph has no cancelling transition, so the drawer renders a disabled
 *   cancel control (never a live one).
 * - **Refresh limitation (accepted for v1).** There is NO download-list HTTP
 *   endpoint (the backend contract is submit/progress/file/WS only), so the
 *   store starts empty on page load: after an F5, in-flight/completed
 *   downloads disappear from the UI even though the server-side work
 *   continues. This is a known, accepted v1 constraint; recovery would need
 *   a v1.1 `GET /api/downloads` endpoint (not in scope for v1).
 * - **Terminal reconciliation.** A late snapshot (poll tick or stale socket
 *   event) can never move a terminal item back to an active state — see
 *   `applySnapshot`.
 *
 * The header badge is NOT stored — `selectActiveCount` derives it from
 * `items`, so it can never drift from the task list.
 *
 * `submit()` maps 3001/3002/3003 failures to a rejection carrying the
 * backend's bilingual ApiError message — the caller toasts it.
 *
 * TODO(v1.1): the reconciler (wsClients map + poll timer + pollTick) is
 * module-scope plumbing inside this file; if it grows, extract it into a
 * dedicated module so the store stays a pure state container.
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
  /**
   * 0..1 fraction (INTERNAL scale). The backend wire scale is 0..100 (see
   * `DownloadProgress`); `applySnapshot` normalizes it at the single ingest
   * point so every consumer here and in the drawer works with 0..1.
   */
  progress: number;
  /** bytes/second (null until the worker reports one). */
  speed: number | null;
  downloaded_bytes: number | null;
  total_bytes: number | null;
  /** seconds. */
  remaining_time: number | null;
  error_code: number | null;
  error_message: string | null;
  /**
   * Tokenized file URL captured from the WS `complete` event — the ONLY way
   * to obtain one (no HTTP mint endpoint). Null until a `complete` event
   * arrives, including when the terminal state was learned via polling.
   */
  download_url: string | null;
  /** ISO-8601 with timezone; the one-time token expires after ~5 minutes. */
  token_expire_at: string | null;
}

export interface SubmitOptions {
  format?: string | null;
  quality?: string | null;
  /** Optional display title carried from the parse result. */
  title?: string | null;
}

export interface DownloadsState {
  /** Download tasks the drawer renders. */
  items: DownloadItem[];
  /** Per-task loading flags for [下载] buttons, keyed by task_id. */
  submitting: Record<string, boolean>;

  /** Submit a download for a parsed task; rejects with the ApiError on failure. */
  submit: (taskId: string, options?: SubmitOptions) => Promise<void>;
  /**
   * Re-submit a failed/expired download (backend: failed/expired -> pending as
   * a fresh row). Replaces the item in place; rejects with the ApiError when
   * the backend refuses (e.g. 3003 identical completed variant).
   */
  retry: (downloadId: string) => Promise<void>;
  /**
   * Reconnect the socket for a completed download to capture a fresh
   * `complete` event (new one-time `download_url`). Used when the link was
   * missed (polling-only path) or its token expired.
   */
  refreshFileLink: (downloadId: string) => void;
  /**
   * Remove an item from the task list (e.g. after a successful NAS save, the
   * backend MOVED the bubble file into the pond — the item's file link and a
   * re-save would both fail, so it must no longer be offered anywhere).
   * Terminal items have no live socket, so no stream cleanup is needed.
   */
  remove: (downloadId: string) => void;
  /**
   * Tear down the session-local download state: close every live socket,
   * stop the polling timer and clear the task list. Called on logout so the
   * next admin (or the same one) starts from a clean slate — the store has
   * no server-side list to rehydrate from (see the module docstring).
   */
  teardown: () => void;

  // --- internal reconciliation (public so WS/polling handlers can call them) ---
  /** Apply a progress/polling snapshot to an item (never clears download_url). */
  applySnapshot: (downloadId: string, snapshot: DownloadProgress) => void;
  /**
   * Upsert a snapshot: adds the item when unknown (recovery lookup from
   * `GET /api/download/by-task/{task_id}` after a page reload), otherwise
   * applies it like `applySnapshot`. The added item has no `download_url`
   * yet — the caller can `refreshFileLink` to mint one.
   */
  upsertSnapshot: (
    snapshot: DownloadProgress,
    options?: { taskId?: string; title?: string | null },
  ) => void;
  /** Capture the terminal completed state + tokenized file URL. */
  applyComplete: (downloadId: string, data: WsCompleteData) => void;
  /** Mark failed/expired from an error event or a failed poll. */
  applyError: (downloadId: string, data: WsErrorData) => void;
  /** Open the WS stream for a download (no-op when one is already live). */
  connectWs: (downloadId: string) => void;
}

/**
 * Active badge count, purely derived from the item list (pending/downloading).
 * There is deliberately no stored counter — see the module docstring.
 */
export const selectActiveCount = (state: DownloadsState): number =>
  state.items.filter((item) => item.status === 'pending' || item.status === 'downloading').length;

/** Polling interval for the fallback path (documented policy: WS primary). */
export const POLL_INTERVAL_MS = 3000;

/**
 * Internal test hook: tear down every live socket and the polling timer.
 * The app never calls this (the SPA lives as long as the tab); the test suite
 * uses it to reset the module-level reconciliation state between cases.
 */
export function __resetDownloadStreams(): void {
  for (const client of wsClients.values()) {
    client.close();
  }
  wsClients.clear();
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// ---------------------------------------------------------------------------
// Module-level reconciliation plumbing (not reactive; survives HMR-less tests)
// ---------------------------------------------------------------------------

/** Live WebSocket clients keyed by download_id. */
const wsClients = new Map<string, DownloadWsClient>();
let pollTimer: ReturnType<typeof setInterval> | null = null;

function isActiveStatus(status: DownloadStatus): boolean {
  return status === 'pending' || status === 'downloading';
}

/** completed/failed/expired — once reached, the item stops being reconciled. */
function isTerminalStatus(status: DownloadStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'expired';
}

/**
 * A socket counts as "live" only while OPEN — but polling no longer consults
 * it: in a two-process deployment the worker's events never reach the API's
 * WS hub, so an OPEN socket can be silent and WS-suppressed polling would
 * freeze the item at its connect snapshot. HTTP polling therefore reconciles
 * every active item regardless of socket state (idempotent, terminal-guarded).
 */
function needsPolling(items: DownloadItem[]): boolean {
  return items.some((item) => isActiveStatus(item.status));
}

function maybeStartPolling(): void {
  if (pollTimer !== null) return;
  if (!needsPolling(useDownloadsStore.getState().items)) return;
  pollTimer = setInterval(() => {
    void pollTick();
  }, POLL_INTERVAL_MS);
  // Never keep the process/worker alive just for progress polling.
  const timerObj = pollTimer as unknown as { unref?: () => void };
  if (typeof timerObj.unref === 'function') {
    timerObj.unref();
  }
}

function maybeStopPolling(): void {
  if (pollTimer === null) return;
  if (!needsPolling(useDownloadsStore.getState().items)) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollTick(): Promise<void> {
  const state = useDownloadsStore.getState();
  const targets = state.items.filter((item) => isActiveStatus(item.status));
  if (targets.length === 0) {
    maybeStopPolling();
    return;
  }
  await Promise.all(
    targets.map(async (item) => {
      try {
        // Defensive: environments/tests that never mock getProgress must not
        // crash the poll loop.
        if (typeof downloadApi.getProgress !== 'function') return;
        const snapshot = await downloadApi.getProgress(item.download_id);
        useDownloadsStore.getState().applySnapshot(item.download_id, snapshot);
      } catch (err) {
        // A task-level failure (3001 unknown download) means the server no
        // longer knows this download → mark failed honestly. Non-3001 poll
        // errors are transient by nature (network blip, race with the
        // reconnecting socket) and are deliberately swallowed: the next tick
        // or a fresh socket snapshot reconciles — accepted for v1, no
        // failure counter.
        if (err instanceof ApiError && err.code === ApiCodes.TASK_NOT_FOUND) {
          useDownloadsStore.getState().applyError(item.download_id, {
            code: err.code,
            message: err.message,
          });
        }
      }
    }),
  );
  maybeStopPolling();
}

function releaseWs(downloadId: string): void {
  const client = wsClients.get(downloadId);
  if (client) {
    client.close();
    wsClients.delete(downloadId);
  }
  maybeStopPolling();
}

function handleWsEvent(downloadId: string, event: WsEvent): void {
  const store = useDownloadsStore.getState();
  if (event.type === 'progress') {
    store.applySnapshot(downloadId, event.data);
  } else if (event.type === 'complete') {
    store.applyComplete(downloadId, event.data);
    releaseWs(downloadId);
  } else if (event.type === 'error') {
    store.applyError(downloadId, event.data);
    releaseWs(downloadId);
  }
}

function makeItem(data: SubmitData, options: SubmitOptions): DownloadItem {
  return {
    download_id: data.download_id,
    task_id: data.task_id,
    status: data.status,
    created_at: data.created_at,
    title: options.title ?? null,
    format: options.format ?? null,
    quality: options.quality ?? null,
    progress: 0,
    speed: null,
    downloaded_bytes: null,
    total_bytes: null,
    remaining_time: null,
    error_code: null,
    error_message: null,
    download_url: null,
    token_expire_at: null,
  };
}

export const useDownloadsStore = create<DownloadsState>()((set, get) => ({
  items: [],
  submitting: {},

  submit: async (taskId, options = {}) => {
    const format = options.format ?? null;
    const quality = options.quality ?? null;
    set((state) => ({ submitting: { ...state.submitting, [taskId]: true } }));
    try {
      const data: SubmitData = await downloadApi.submit(taskId, { format, quality });
      const item = makeItem(data, options);
      set((state) => ({ items: [...state.items, item] }));
      get().connectWs(data.download_id);
    } finally {
      set((state) => ({ submitting: { ...state.submitting, [taskId]: false } }));
    }
  },

  retry: async (downloadId) => {
    const item = get().items.find((i) => i.download_id === downloadId);
    if (!item) return;
    if (item.status !== 'failed' && item.status !== 'expired') return;
    const data: SubmitData = await downloadApi.submit(item.task_id, {
      format: item.format,
      quality: item.quality,
    });
    // The backend re-claim creates a NEW row (fresh download_id); replace the
    // failed item in place so the list keeps one entry per task.
    set((state) => ({
      items: state.items.map((i) =>
        i.download_id === downloadId ? makeItem(data, { title: item.title, format: item.format, quality: item.quality }) : i,
      ),
    }));
    get().connectWs(data.download_id);
  },

  refreshFileLink: (downloadId) => {
    const item = get().items.find((i) => i.download_id === downloadId);
    if (!item) return;
    // Drop any stale socket, then reconnect: the backend sends a fresh
    // `complete` event (new download_url) whenever it connects to a completed
    // download. If the task was swept server-side, the socket sends an `error`
    // event instead and the item honestly moves to expired.
    releaseWs(downloadId);
    get().connectWs(downloadId);
  },

  remove: (downloadId) =>
    set((state) => ({ items: state.items.filter((i) => i.download_id !== downloadId) })),

  teardown: () => {
    __resetDownloadStreams();
    set({ items: [], submitting: {} });
  },

  upsertSnapshot: (snapshot, options = {}) => {
    const state = get();
    if (!state.items.some((item) => item.download_id === snapshot.download_id)) {
      set((prev) => ({
        items: [
          ...prev.items,
          {
            download_id: snapshot.download_id,
            task_id: options.taskId ?? snapshot.download_id,
            status: snapshot.status,
            created_at: new Date().toISOString(),
            title: options.title ?? null,
            format: null,
            quality: null,
            progress: (snapshot.progress ?? 0) / 100,
            speed: snapshot.speed ?? null,
            downloaded_bytes: snapshot.downloaded_bytes ?? null,
            total_bytes: snapshot.total_bytes ?? null,
            remaining_time: snapshot.remaining_time ?? null,
            error_code: null,
            error_message: snapshot.error_message ?? null,
            download_url: null,
            token_expire_at: null,
          },
        ],
      }));
    }
    get().applySnapshot(snapshot.download_id, snapshot);
  },

  applySnapshot: (downloadId, snapshot) =>
    set((state) => ({
      items: state.items.map((item) => {
        if (item.download_id !== downloadId) return item;
        // Terminal reconciliation guard: a late snapshot (an in-flight poll
        // that read the pre-terminal DB row, or a stale socket event) must
        // never move a completed/failed/expired item back to an active state.
        // The WS `complete` can land before such a poll resolves; without
        // this guard the item's status, badge and progress would regress.
        if (isTerminalStatus(item.status) && !isTerminalStatus(snapshot.status)) {
          return item;
        }
        return {
          ...item,
          status: snapshot.status,
          // Normalize the backend wire scale (0..100) to the store's internal
          // 0..1 fraction at the single ingest point. `applyComplete` writes
          // the normalized 1 directly and must NOT be re-divided.
          progress: (snapshot.progress ?? 0) / 100,
          speed: snapshot.speed ?? null,
          downloaded_bytes: snapshot.downloaded_bytes ?? null,
          total_bytes: snapshot.total_bytes ?? null,
          remaining_time: snapshot.remaining_time ?? null,
          // Progress snapshots carry no link; keep any captured URL.
          error_code: null,
          error_message: snapshot.error_message ?? null,
        };
      }),
    })),

  applyComplete: (downloadId, data) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.download_id === downloadId
          ? {
              ...item,
              status: 'completed',
              progress: 1,
              speed: data.speed ?? null,
              downloaded_bytes: data.downloaded_bytes ?? null,
              total_bytes: data.total_bytes ?? null,
              remaining_time: null,
              error_code: null,
              error_message: null,
              download_url: data.download_url,
              token_expire_at: data.token_expire_at,
            }
          : item,
      ),
    })),

  applyError: (downloadId, data) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.download_id === downloadId
          ? {
              ...item,
              // Task-state errors carry `status` (failed/expired); protocol
              // errors carry only {code, message} → treat as failed.
              status: data.status === 'expired' ? 'expired' : 'failed',
              error_code: data.code,
              // The backend's task-state failed event carries BOTH the
              // generic 5002 message AND the real cause in `error_message`
              // (e.g. "404 from upstream") — surface the specific reason.
              error_message: data.error_message ?? data.message,
            }
          : item,
      ),
    })),

  connectWs: (downloadId) => {
    if (wsClients.has(downloadId)) return;
    // No WebSocket (e.g. jsdom in tests, exotic embeds) → polling fallback.
    if (typeof WebSocket === 'undefined') {
      maybeStartPolling();
      return;
    }
    const client = new DownloadWsClient({ url: buildWsUrl(downloadId) });
    wsClients.set(downloadId, client);
    client.subscribe((event) => handleWsEvent(downloadId, event));
    client.connect();
    maybeStartPolling();
  },
}));
