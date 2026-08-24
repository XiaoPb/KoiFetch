import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { downloadApi } from '../services/api';
import { ApiError } from '../types/api';
import { selectActiveCount, useDownloadsStore } from './downloadsStore';

vi.mock('../services/api', () => ({
  downloadApi: { submit: vi.fn() },
}));

const submitData = { download_id: 'd1', task_id: 't1', status: 'pending' as const, created_at: '2026-01-01T00:00:00Z' };

describe('downloadsStore', () => {
  beforeEach(() => {
    useDownloadsStore.setState({ items: [], activeCount: 0, submitting: {} });
    vi.clearAllMocks();
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
    });
    expect(selectActiveCount(state)).toBe(1);
    expect(state.activeCount).toBe(1);
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

  it('setActiveCount stays available for Task 15 reconciliation', () => {
    useDownloadsStore.getState().setActiveCount(3);
    expect(useDownloadsStore.getState().activeCount).toBe(3);
  });
});
