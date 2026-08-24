import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DownloadWsClient, buildWsUrl } from './wsClient';
import type { WsEvent } from '../types/api';

// jsdom has no WebSocket implementation; drive the client with a controllable
// stand-in that records instances and lets tests emit open/message/close.
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1; // matches the real WebSocket constant used by the client
  url: string;
  readyState = 0;
  closeCalls = 0;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = 3;
    this.onclose?.(new CloseEvent('close'));
  }

  static open(ws: MockWebSocket): void {
    ws.readyState = 1;
    ws.onopen?.(new Event('open'));
  }

  static message(ws: MockWebSocket, data: unknown): void {
    ws.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

const URL = 'ws://localhost/ws/download/abc';

describe('buildWsUrl', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('derives a ws:// URL from the current location', () => {
    expect(buildWsUrl('abc')).toMatch(/^ws:\/\/localhost(:\d+)?\/ws\/download\/abc$/);
  });

  it('honours the VITE_WS_BASE_URL override', () => {
    vi.stubEnv('VITE_WS_BASE_URL', 'wss://api.example.com');
    expect(buildWsUrl('abc')).toBe('wss://api.example.com/ws/download/abc');
  });
});

describe('DownloadWsClient', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('opens a WebSocket to the configured url', () => {
    const client = new DownloadWsClient({ url: URL });
    client.connect();
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toBe(URL);
    expect(client.status).toBe('connecting');
  });

  it('notifies subscribers of progress events', () => {
    const client = new DownloadWsClient({ url: URL });
    const events: WsEvent[] = [];
    client.subscribe((event) => events.push(event));
    client.connect();

    MockWebSocket.open(MockWebSocket.instances[0]);
    MockWebSocket.message(MockWebSocket.instances[0], {
      type: 'progress',
      data: { download_id: '1', status: 'downloading', progress: 0.5, speed: 1, downloaded_bytes: 1, total_bytes: 2, remaining_time: 3 },
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: 'progress' });
    expect(client.status).toBe('open');
  });

  it('ignores malformed messages without crashing', () => {
    const client = new DownloadWsClient({ url: URL });
    const events: WsEvent[] = [];
    client.subscribe((event) => events.push(event));
    client.connect();
    MockWebSocket.open(MockWebSocket.instances[0]);
    MockWebSocket.instances[0].onmessage?.({ data: 'not json' } as MessageEvent);
    expect(events).toHaveLength(0);
    expect(client.status).toBe('open');
  });

  it('closes the socket on a terminal complete event and stops reconnecting', () => {
    const client = new DownloadWsClient({ url: URL });
    client.connect();
    const ws = MockWebSocket.instances[0];
    MockWebSocket.open(ws);
    MockWebSocket.message(ws, {
      type: 'complete',
      data: {
        download_id: '1', status: 'completed', progress: 1, speed: null, downloaded_bytes: 10, total_bytes: 10, remaining_time: 0,
        download_url: '/api/download/file/1?token=t', token_expire_at: '2099-01-01T00:00:00Z',
      },
    });

    expect(client.status).toBe('closed');
    expect(ws.closeCalls).toBe(1);
    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('closes the socket on a terminal error event', () => {
    const client = new DownloadWsClient({ url: URL });
    client.connect();
    const ws = MockWebSocket.instances[0];
    MockWebSocket.open(ws);
    MockWebSocket.message(ws, { type: 'error', data: { code: 5002, message: '文件未下载完成 / File not fully downloaded' } });
    expect(client.status).toBe('closed');
    expect(ws.closeCalls).toBe(1);
  });

  it('reconnects with backoff after an unexpected close', () => {
    const client = new DownloadWsClient({ url: URL });
    client.connect();
    MockWebSocket.open(MockWebSocket.instances[0]);
    MockWebSocket.instances[0].onclose?.(new CloseEvent('close'));

    expect(client.status).toBe('closed');
    expect(MockWebSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(1000); // first backoff tick
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1].url).toBe(URL);
  });

  it('grows the backoff delay on repeated failures', () => {
    const client = new DownloadWsClient({ url: URL });
    client.connect();
    // The server is down: connections never open, so close fires without an
    // `open` resetting the backoff counter.
    MockWebSocket.instances[0].onclose?.(new CloseEvent('close'));

    vi.advanceTimersByTime(1000); // retry #1 (delay 1000ms)
    expect(MockWebSocket.instances).toHaveLength(2);
    MockWebSocket.instances[1].onclose?.(new CloseEvent('close'));

    vi.advanceTimersByTime(1000); // not yet: delay has doubled to 2000ms
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1000); // t=3000 → retry #2
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it('close() stops reconnecting and closes an open socket', () => {
    const client = new DownloadWsClient({ url: URL });
    client.connect();
    const ws = MockWebSocket.instances[0];
    MockWebSocket.open(ws);
    client.close();
    expect(client.status).toBe('closed');
    expect(ws.closeCalls).toBe(1);
    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('unsubscribe stops future delivery', () => {
    const client = new DownloadWsClient({ url: URL });
    const events: WsEvent[] = [];
    const unsubscribe = client.subscribe((event) => events.push(event));
    client.connect();
    MockWebSocket.open(MockWebSocket.instances[0]);
    unsubscribe();
    MockWebSocket.message(MockWebSocket.instances[0], {
      type: 'progress',
      data: { download_id: '1', status: 'downloading', progress: 0.1, speed: null, downloaded_bytes: 1, total_bytes: 10, remaining_time: 9 },
    });
    expect(events).toHaveLength(0);
  });
});
