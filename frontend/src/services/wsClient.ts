import type { WsEvent } from '../types/api';

// Native WebSocket client for the download progress stream.
//
// The backend exposes a plain WebSocket protocol at /ws/download/{id} sending
// JSON events {type: "progress"|"complete"|"error", data} — NOT socket.io —
// so this client uses the browser WebSocket API directly. It reconnects with
// exponential backoff after unexpected drops and stops (closing the socket)
// on terminal events, so a completed/failed download never reconnects.

export type WsClientStatus = 'idle' | 'connecting' | 'open' | 'closed';

export interface WsClientOptions {
  url: string;
  /** Initial reconnect delay (ms). */
  reconnectDelayMs?: number;
  /** Reconnect delay cap (ms). */
  maxReconnectDelayMs?: number;
  /**
   * Abandon a socket that has not opened within this window (ms). Guards
   * against a black-holed handshake that would otherwise leave the download
   * with neither WS events nor polling (the store only counts an OPEN socket
   * as a live stream).
   */
  connectTimeoutMs?: number;
}

type Listener = (event: WsEvent) => void;

const DEFAULT_RECONNECT_DELAY_MS = 1000;
const DEFAULT_MAX_RECONNECT_DELAY_MS = 10_000;
const DEFAULT_CONNECT_TIMEOUT_MS = 10_000;

/**
 * Derive the WebSocket URL for a download. Uses `VITE_WS_BASE_URL` when set;
 * otherwise derives ws:// or wss:// from the current page origin so the Vite
 * dev proxy and the backend's same-origin /ws serving both work unchanged.
 */
export function buildWsUrl(downloadId: string): string {
  const override = import.meta.env.VITE_WS_BASE_URL as string | undefined;
  if (override) {
    return `${override.replace(/\/+$/, '')}/ws/download/${downloadId}`;
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/download/${downloadId}`;
}

export class DownloadWsClient {
  readonly url: string;
  readonly reconnectDelayMs: number;
  readonly maxReconnectDelayMs: number;
  readonly connectTimeoutMs: number;

  status: WsClientStatus = 'idle';

  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private manuallyClosed = false;

  constructor(options: WsClientOptions) {
    this.url = options.url;
    this.reconnectDelayMs = options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS;
    this.maxReconnectDelayMs = options.maxReconnectDelayMs ?? DEFAULT_MAX_RECONNECT_DELAY_MS;
    this.connectTimeoutMs = options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS;
  }

  connect(): void {
    if (this.status === 'connecting' || this.status === 'open') return;
    this.manuallyClosed = false;
    this.status = 'connecting';
    const socket = new WebSocket(this.url);
    this.socket = socket;
    this.startConnectTimeout();

    socket.onopen = () => {
      this.clearConnectTimeout();
      this.status = 'open';
      this.reconnectAttempts = 0;
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      const parsed = this.parse(event.data);
      if (!parsed) return;
      for (const listener of this.listeners) listener(parsed);
      // Terminal events end the stream: close and never reconnect.
      if (parsed.type === 'complete' || parsed.type === 'error') {
        this.close();
      }
    };

    socket.onerror = () => {
      // The browser follows an error with a close event; reconnect there.
    };

    socket.onclose = () => {
      this.clearConnectTimeout();
      if (this.manuallyClosed) return;
      this.status = 'closed';
      this.scheduleReconnect();
    };
  }

  /** Register a listener; returns an unsubscribe function. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Close the socket and stop any reconnect schedule. Closes in ANY state —
   * including CONNECTING: a hung handshake must not leave a socket alive whose
   * late events would still mutate the store after the caller moved on. Event
   * handlers are detached so no late frame can fire post-close.
   */
  close(): void {
    this.manuallyClosed = true;
    this.clearConnectTimeout();
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      if (socket.readyState !== WebSocket.CLOSED && socket.readyState !== WebSocket.CLOSING) {
        socket.close();
      }
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
    }
    this.status = 'closed';
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    const delay = Math.min(
      this.reconnectDelayMs * 2 ** this.reconnectAttempts,
      this.maxReconnectDelayMs,
    );
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  /**
   * Abandon the socket if the handshake never completes within
   * `connectTimeoutMs`: the browser gives no connect timeout of its own, and
   * a socket stuck in CONNECTING would starve both WS events and the store's
   * polling fallback (only OPEN sockets count as a live stream).
   */
  private startConnectTimeout(): void {
    this.clearConnectTimeout();
    this.connectTimer = setTimeout(() => {
      this.connectTimer = null;
      // Manual close → no reconnect; the caller's polling takes over.
      this.close();
    }, this.connectTimeoutMs);
  }

  private clearConnectTimeout(): void {
    if (this.connectTimer !== null) {
      clearTimeout(this.connectTimer);
      this.connectTimer = null;
    }
  }

  private parse(raw: string): WsEvent | null {
    try {
      const value: unknown = JSON.parse(raw);
      if (isWsEvent(value)) return value;
    } catch {
      // Ignore malformed frames; the socket stays open for the next event.
    }
    return null;
  }
}

/**
 * Validate a parsed frame against the wire contract so subscribers never see
 * half-shaped events:
 * - `error` must carry {code: number, message: string} (protocol errors carry
 *   ONLY those two fields — no snapshot state);
 * - `progress` must carry the snapshot state (download_id/status/progress);
 * - `complete` must additionally carry download_url + token_expire_at.
 */
function isWsEvent(value: unknown): value is WsEvent {
  if (typeof value !== 'object' || value === null) return false;
  const { type, data } = value as { type?: unknown; data?: unknown };
  if (type !== 'progress' && type !== 'complete' && type !== 'error') return false;
  if (typeof data !== 'object' || data === null) return false;

  if (type === 'error') {
    const { code, message } = data as { code?: unknown; message?: unknown };
    return typeof code === 'number' && typeof message === 'string';
  }

  const state = data as {
    download_id?: unknown;
    status?: unknown;
    progress?: unknown;
    download_url?: unknown;
    token_expire_at?: unknown;
  };
  if (
    typeof state.download_id !== 'string' ||
    typeof state.status !== 'string' ||
    typeof state.progress !== 'number'
  ) {
    return false;
  }
  if (type === 'complete') {
    return typeof state.download_url === 'string' && typeof state.token_expire_at === 'string';
  }
  return true;
}
