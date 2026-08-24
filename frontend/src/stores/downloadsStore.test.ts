import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { downloadApi } from '../services/api';
import { ApiError } from '../types/api';
import {
  __resetDownloadStreams,
  POLL_INTERVAL_MS,
  selectActiveCount,
  useDownloadsStore,
  type DownloadItem,
} from './downloadsStore';
import type { WsEvent } from '../types/api';

// A controllable stand-in for the real DownloadWsClient: tests grab the
// instance created after submit/retry and drive it with `emit(...)`.
const wsMock = vi.hoisted(() => {
  class MockWsClient {
    static instances: MockWsClient[] = [];
    url: string;
    status = 'idle';
    closeCalls = 0;
    private listeners: Array<(event: WsEvent) => void> = [];
    constructor(options: { url: string }) {
      this.url = options.url;
      MockWsClient.instances.push(this);
    }
    subscribe(listener: (event: WsEvent) => void): () => void {
      this.listeners.push(listener);
      return () => {
        this.listeners = this.listeners.filter((l) => l !== listener);
      };
    }
    connect(): void {
      // Realistic: the handshake takes a moment — the store must cover the
      // connecting window with polling (hasLiveSocket only counts OPEN).
      this.status = 'connecting';
    }
    close(): void {
      this.closeCalls += 1;
      this.status = 'closed';
    }
    emit(event: WsEvent): void {
      for (const listener of [...this.listeners]) listener(event);
    }
  }
  return {
    MockWsClient,
    buildWsUrl: vi.fn((downloadId: string) => `ws://test/ws/download/${downloadId}`),
  };
});

vi.mock('../services/api', () => ({
  downloadApi: { submit: vi.fn(), getProgress: vi.fn() },
}));

vi.mock('../services/wsClient', () => ({
  DownloadWsClient: wsMock.MockWsClient,
  buildWsUrl: wsMock.buildWsUrl,
}));

const submitData = { download_id: 'd1', task_id: 't1', status: 'pending' as const, created_at: '2026-01-01T00:00:00Z' };

function seedItem(partial: Partial<DownloadItem> & Pick<DownloadItem, 'download_id' | 'task_id' | 'status'>): DownloadItem {
  return {
    title: null,
    format: null,
    quality: null,
    created_at: '2026-01-01T00:00:00Z',
    progress: 0,
    speed: null,
    downloaded_bytes: null,
    total_bytes: null,
    remaining_time: null,
    error_code: null,
    error_message: null,
    download_url: null,
    token_expire_at: null,
    ...partial,
  };
}

function lastClient(): InstanceType<typeof wsMock.MockWsClient> {
  const instances = wsMock.MockWsClient.instances;
  return instances[instances.length - 1];
}

describe('downloadsStore', () => {
  beforeEach(() => {
    useDownloadsStore.setState({ items: [], submitting: {} });
    __resetDownloadStreams();
    wsMock.MockWsClient.instances = [];
    vi.clearAllMocks();
    vi.useRealTimers();
    // jsdom has no WebSocket; stub one so the WS path is exercised (the real
    // browser has it; the store falls back to polling when it is absent).
    vi.stubGlobal('WebSocket', class {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('submit calls the API and appends the task for the Task 15 drawer/badge', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1', { format: 'mp4', quality: '1080p', title: 'Video A' });

    expect(downloadApi.submit).toHaveBeenCalledWith('t1', { format: 'mp4', quality: '1080p' });
    const state = useDownloadsStore.getState();
    expect(state.items).toHaveLength(1);
    expect(state.items[0]).toMatchObject({
      download_id: 'd1',
      task_id: 't1',
      status: 'pending',
      format: 'mp4',
      quality: '1080p',
      title: 'Video A',
      progress: 0,
      download_url: null,
    });
    expect(selectActiveCount(state)).toBe(1);
  });

  it('submit omits blank format/quality from the API call', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1');
    expect(downloadApi.submit).toHaveBeenCalledWith('t1', { format: null, quality: null });
  });

  it('a 3001/3002/3003 failure rejects, adds no task, and clears the submitting flag', async () => {
    (downloadApi.submit as Mock).mockRejectedValue(new ApiError('任务不存在 / Task not found', 3001, 400));
    await expect(useDownloadsStore.getState().submit('t1')).rejects.toMatchObject({ code: 3001 });

    const state = useDownloadsStore.getState();
    expect(state.items).toHaveLength(0);
    expect(selectActiveCount(state)).toBe(0);
    expect(state.submitting.t1).toBe(false);
  });

  it('tracks per-task submitting flags so buttons can show a loading state', async () => {
    let resolveSubmit!: (value: unknown) => void;
    (downloadApi.submit as Mock).mockReturnValue(new Promise((resolve) => { resolveSubmit = resolve; }));

    const pending = useDownloadsStore.getState().submit('t1');
    expect(useDownloadsStore.getState().submitting.t1).toBe(true);

    resolveSubmit(submitData);
    await pending;
    expect(useDownloadsStore.getState().submitting.t1).toBe(false);
  });

  it('derives the badge purely from items (terminal states stop counting)', () => {
    useDownloadsStore.setState({
      items: [
        seedItem({ download_id: 'd1', task_id: 't1', status: 'pending' }),
        seedItem({ download_id: 'd2', task_id: 't2', status: 'downloading' }),
        seedItem({ download_id: 'd3', task_id: 't3', status: 'completed' }),
      ],
    });
    expect(selectActiveCount(useDownloadsStore.getState())).toBe(2);
  });

  // -------------------------------------------------------------------------
  // WebSocket reconciliation (Task 15)
  // -------------------------------------------------------------------------

  it('connects a WS client after submit and applies progress events', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1', { format: 'mp4', title: 'Video A' });

    expect(wsMock.buildWsUrl).toHaveBeenCalledWith('d1');
    expect(wsMock.MockWsClient.instances).toHaveLength(1);
    const client = lastClient();

    client.emit({
      type: 'progress',
      data: { download_id: 'd1', status: 'downloading', progress: 0.5, speed: 1024, downloaded_bytes: 50, total_bytes: 100, remaining_time: 2 },
    });

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('downloading');
    expect(item.progress).toBe(0.5);
    expect(item.speed).toBe(1024);
    expect(item.downloaded_bytes).toBe(50);
    expect(item.remaining_time).toBe(2);
    expect(selectActiveCount(useDownloadsStore.getState())).toBe(1);
  });

  it('captures download_url from the complete event and closes the socket', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1');
    const client = lastClient();

    client.emit({
      type: 'complete',
      data: {
        download_id: 'd1', status: 'completed', progress: 1, speed: null,
        downloaded_bytes: 10, total_bytes: 10, remaining_time: 0,
        download_url: '/api/download/file/d1?token=t', token_expire_at: '2099-01-01T00:00:00Z',
      },
    });

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('completed');
    expect(item.progress).toBe(1);
    expect(item.download_url).toBe('/api/download/file/d1?token=t');
    expect(item.token_expire_at).toBe('2099-01-01T00:00:00Z');
    expect(client.closeCalls).toBeGreaterThan(0);
    expect(selectActiveCount(useDownloadsStore.getState())).toBe(0);
  });

  it('marks failed with code/message on a task-state error event', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1');
    const client = lastClient();

    client.emit({
      type: 'error',
      data: { code: 5002, message: '文件未下载完成 / File not fully downloaded', status: 'failed' },
    });

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('failed');
    expect(item.error_code).toBe(5002);
    expect(item.error_message).toContain('File not fully downloaded');
    expect(client.closeCalls).toBeGreaterThan(0);
  });

  it('treats protocol errors (code/message only) as failed without phantom state', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1');
    const client = lastClient();

    client.emit({ type: 'error', data: { code: 3001, message: '任务不存在 / Task not found' } });

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('failed');
    expect(item.error_code).toBe(3001);
    expect(client.closeCalls).toBeGreaterThan(0);
  });

  it('maps an expired error event to the expired status', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1');
    const client = lastClient();

    client.emit({ type: 'error', data: { code: 5004, message: '文件已过期 / File expired', status: 'expired' } });

    expect(useDownloadsStore.getState().items[0].status).toBe('expired');
  });

  it('surfaces the specific error_message from a task-state failed event', async () => {
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    await useDownloadsStore.getState().submit('t1');
    const client = lastClient();

    // Backend failed events carry the generic 5002 message PLUS the real
    // cause in error_message (e.g. "404 from upstream").
    client.emit({
      type: 'error',
      data: {
        code: 5002, message: '文件未下载完成 / File not fully downloaded', status: 'failed',
        download_id: 'd1', progress: 0.2, speed: null, downloaded_bytes: 20, total_bytes: 100, remaining_time: null,
        error_message: '404 from upstream',
      },
    });

    expect(useDownloadsStore.getState().items[0].error_message).toBe('404 from upstream');
  });

  // -------------------------------------------------------------------------
  // Terminal reconciliation (IMPORTANT: late snapshots must not regress)
  // -------------------------------------------------------------------------

  it('never regresses a terminal item from a late snapshot (WS/polling race)', () => {
    useDownloadsStore.setState({
      items: [
        seedItem({
          download_id: 'd1', task_id: 't1', status: 'completed', progress: 1,
          download_url: '/api/download/file/d1?token=t', token_expire_at: '2099-01-01T00:00:00Z',
        }),
      ],
    });

    // A poll tick read the pre-terminal DB row and resolves AFTER the WS
    // complete already moved the item to completed.
    useDownloadsStore.getState().applySnapshot('d1', {
      download_id: 'd1', status: 'downloading', progress: 0.4, speed: null,
      downloaded_bytes: 40, total_bytes: 100, remaining_time: null,
    });

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('completed');
    expect(item.progress).toBe(1);
    expect(item.download_url).toBe('/api/download/file/d1?token=t');
    expect(selectActiveCount(useDownloadsStore.getState())).toBe(0);
  });

  it('a late poll snapshot cannot regress a terminal state reached via WS', async () => {
    vi.stubGlobal('WebSocket', undefined);
    vi.useFakeTimers();
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    let resolvePoll!: (value: unknown) => void;
    (downloadApi.getProgress as Mock).mockReturnValue(new Promise((resolve) => { resolvePoll = resolve; }));

    await useDownloadsStore.getState().submit('t1');
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS); // a poll is now in flight

    // The WS complete lands first (socket had opened, event delivered).
    useDownloadsStore.getState().applyComplete('d1', {
      download_id: 'd1', status: 'completed', progress: 1, speed: null,
      downloaded_bytes: 100, total_bytes: 100, remaining_time: 0,
      download_url: '/api/download/file/d1?token=t', token_expire_at: '2099-01-01T00:00:00Z',
    });
    resolvePoll({
      download_id: 'd1', status: 'downloading', progress: 0.4, speed: null,
      downloaded_bytes: 40, total_bytes: 100, remaining_time: null,
    });
    await vi.advanceTimersByTimeAsync(0); // flush the stale poll resolution

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('completed');
    expect(item.progress).toBe(1);
    expect(item.download_url).toBe('/api/download/file/d1?token=t');
  });

  // -------------------------------------------------------------------------
  // Polling fallback (WS primary, polling every POLL_INTERVAL_MS)
  // -------------------------------------------------------------------------

  it('falls back to polling when no WebSocket is available', async () => {
    vi.stubGlobal('WebSocket', undefined);
    vi.useFakeTimers();
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    (downloadApi.getProgress as Mock).mockResolvedValue({
      download_id: 'd1', status: 'downloading', progress: 0.5, speed: 100,
      downloaded_bytes: 50, total_bytes: 100, remaining_time: 1,
    });

    await useDownloadsStore.getState().submit('t1');
    expect(downloadApi.getProgress).not.toHaveBeenCalled();
    expect(wsMock.MockWsClient.instances).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(downloadApi.getProgress).toHaveBeenCalledWith('d1');
    expect(useDownloadsStore.getState().items[0].progress).toBe(0.5);
  });

  it('covers the connecting window with polling and stops once the socket opens', async () => {
    vi.useFakeTimers();
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    (downloadApi.getProgress as Mock).mockResolvedValue({
      download_id: 'd1', status: 'pending', progress: 0, speed: null,
      downloaded_bytes: null, total_bytes: null, remaining_time: null,
    });

    await useDownloadsStore.getState().submit('t1');
    const client = lastClient();
    // Handshake in progress — hasLiveSocket only counts OPEN, so the poll
    // loop must cover this window (a black-holed socket otherwise starves
    // the item; the client abandons it after connectTimeoutMs).
    expect(client.status).toBe('connecting');

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(downloadApi.getProgress).toHaveBeenCalledWith('d1');

    // Socket opens → polling stops for this item.
    client.status = 'open';
    const callCount = (downloadApi.getProgress as Mock).mock.calls.length;
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    expect(downloadApi.getProgress).toHaveBeenCalledTimes(callCount);
  });

  it('stops polling once a terminal state is reached via polling (no link from polling)', async () => {
    vi.stubGlobal('WebSocket', undefined);
    vi.useFakeTimers();
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    (downloadApi.getProgress as Mock)
      .mockResolvedValueOnce({
        download_id: 'd1', status: 'downloading', progress: 0.9, speed: null,
        downloaded_bytes: 90, total_bytes: 100, remaining_time: null,
      })
      .mockResolvedValueOnce({
        download_id: 'd1', status: 'completed', progress: 1, speed: null,
        downloaded_bytes: 100, total_bytes: 100, remaining_time: null,
      });

    await useDownloadsStore.getState().submit('t1');
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('completed');
    expect(item.progress).toBe(1);
    // Polling can never mint a link (only the WS complete event can) — the
    // drawer shows the honest "链接不可用" state.
    expect(item.download_url).toBeNull();

    const callCount = (downloadApi.getProgress as Mock).mock.calls.length;
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5);
    expect(downloadApi.getProgress).toHaveBeenCalledTimes(callCount);
  });

  it('marks a download failed when polling reports an unknown download (3001)', async () => {
    vi.stubGlobal('WebSocket', undefined);
    vi.useFakeTimers();
    (downloadApi.submit as Mock).mockResolvedValue(submitData);
    (downloadApi.getProgress as Mock).mockRejectedValue(new ApiError('任务不存在 / Task not found', 3001, 400));

    await useDownloadsStore.getState().submit('t1');
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

    const item = useDownloadsStore.getState().items[0];
    expect(item.status).toBe('failed');
    expect(item.error_code).toBe(3001);
  });

  // -------------------------------------------------------------------------
  // Retry (re-submit) and link refresh
  // -------------------------------------------------------------------------

  it('retry re-submits a failed item and replaces it with a fresh pending download', async () => {
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'failed', format: 'mp4', quality: '1080p', error_message: 'boom' })],
    });
    (downloadApi.submit as Mock).mockResolvedValue({
      download_id: 'd2', task_id: 't1', status: 'pending', created_at: '2026-02-01T00:00:00Z',
    });

    await useDownloadsStore.getState().retry('d1');

    expect(downloadApi.submit).toHaveBeenCalledWith('t1', { format: 'mp4', quality: '1080p' });
    const items = useDownloadsStore.getState().items;
    expect(items).toHaveLength(1);
    expect(items[0].download_id).toBe('d2');
    expect(items[0].status).toBe('pending');
    expect(items[0].format).toBe('mp4');
    expect(items[0].error_message).toBeNull();
    expect(selectActiveCount(useDownloadsStore.getState())).toBe(1);
    // A fresh socket opens for the new download id.
    expect(lastClient().url).toContain('/d2');
  });

  it('retry rejects with the backend error and leaves the item failed', async () => {
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'failed' })],
    });
    (downloadApi.submit as Mock).mockRejectedValue(new ApiError('任务已完成 / Task already completed', 3003, 400));

    await expect(useDownloadsStore.getState().retry('d1')).rejects.toMatchObject({ code: 3003 });
    expect(useDownloadsStore.getState().items[0].status).toBe('failed');
  });

  it('retry is a no-op for non-failed items', async () => {
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'completed' })],
    });
    await useDownloadsStore.getState().retry('d1');
    expect(downloadApi.submit).not.toHaveBeenCalled();
  });

  it('refreshFileLink reconnects the socket to capture a fresh link', async () => {
    useDownloadsStore.setState({
      items: [seedItem({ download_id: 'd1', task_id: 't1', status: 'completed', download_url: null })],
    });
    useDownloadsStore.getState().refreshFileLink('d1');

    const client = lastClient();
    expect(client.url).toContain('/d1');
    client.emit({
      type: 'complete',
      data: {
        download_id: 'd1', status: 'completed', progress: 1, speed: null,
        downloaded_bytes: 10, total_bytes: 10, remaining_time: 0,
        download_url: '/api/download/file/d1?token=fresh', token_expire_at: '2099-01-01T00:00:00Z',
      },
    });
    expect(useDownloadsStore.getState().items[0].download_url).toBe('/api/download/file/d1?token=fresh');
    expect(useDownloadsStore.getState().items[0].status).toBe('completed');
  });
});
