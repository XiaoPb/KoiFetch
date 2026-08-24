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
}

type Listener = (event: WsEvent) => void;

const DEFAULT_RECONNECT_DELAY_MS = 1000;
const DEFAULT_MAX_RECONNECT_DELAY_MS = 10_000;

/**
 * Derive the WebSocket URL for a download. Uses `VITE_WS_BASE_URL` when set;
 * otherwise derives ws:// or wss:// from the current page origin so the Vite
 * dev proxy and the nginx /ws location both work unchanged.
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

  status: WsClientStatus = 'idle';

  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private manuallyClosed = false;

  constructor(options: WsClientOptions) {
    this.url = options.url;
    this.reconnectDelayMs = options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS;
    this.maxReconnectDelayMs = options.maxReconnectDelayMs ?? DEFAULT_MAX_RECONNECT_DELAY_MS;
  }

  connect(): void {
    if (this.status === 'connecting' || this.status === 'open') return;
    this.manuallyClosed = false;
    this.status = 'connecting';
    const socket = new WebSocket(this.url);
    this.socket = socket;

    socket.onopen = () => {
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

  /** Close the socket and stop any reconnect schedule. */
  close(): void {
    this.manuallyClosed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.close();
    }
    this.socket = null;
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

  private parse(raw: string): WsEvent | null {
    try {
      const value: unknown = JSON.parse(raw);
      if (
        typeof value === 'object' &&
        value !== null &&
        typeof (value as { type?: unknown }).type === 'string'
      ) {
        return value as WsEvent;
      }
    } catch {
      // Ignore malformed frames; the socket stays open for the next event.
    }
    return null;
  }
}
