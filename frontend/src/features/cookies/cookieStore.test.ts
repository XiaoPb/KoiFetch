import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { waitFor } from '@testing-library/react';
import { cookieApi } from '../../services/api';
import {
  COOKIE_PLATFORMS,
  isConfigured,
  updatedAtOf,
  useCookieStore,
} from './cookieStore';

vi.mock('../../services/api', () => ({
  cookieApi: {
    list: vi.fn(),
    set: vi.fn(),
    remove: vi.fn(),
  },
}));

describe('cookieStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useCookieStore.setState({
      entries: [],
      drawerOpen: false,
      loading: false,
      error: null,
    });
  });

  it('lists the three f2 platforms for the settings UI', () => {
    expect(COOKIE_PLATFORMS.map((p) => p.platform)).toEqual([
      'douyin',
      'weibo',
      'tiktok',
    ]);
  });

  it('loads entries from the API', async () => {
    (cookieApi.list as Mock).mockResolvedValue({
      cookies: [{ platform: 'douyin', configured: true, updated_at: '2026-08-26T00:00:00+00:00' }],
    });
    await useCookieStore.getState().load();
    expect(useCookieStore.getState().entries).toHaveLength(1);
    expect(useCookieStore.getState().error).toBeNull();
  });

  it('records a load failure in state.error', async () => {
    (cookieApi.list as Mock).mockRejectedValue(new Error('network'));
    await useCookieStore.getState().load();
    expect(useCookieStore.getState().error).toBeTruthy();
  });

  it('save pushes the cookie then reloads', async () => {
    (cookieApi.set as Mock).mockResolvedValue({ platform: 'douyin', configured: true, updated_at: null });
    (cookieApi.list as Mock).mockResolvedValue({ cookies: [] });
    await useCookieStore.getState().save('douyin', 'a=1');
    expect(cookieApi.set).toHaveBeenCalledWith('douyin', 'a=1');
    expect(cookieApi.list).toHaveBeenCalledTimes(1);
  });

  it('remove clears the cookie then reloads', async () => {
    (cookieApi.remove as Mock).mockResolvedValue(undefined);
    (cookieApi.list as Mock).mockResolvedValue({ cookies: [] });
    await useCookieStore.getState().remove('douyin');
    expect(cookieApi.remove).toHaveBeenCalledWith('douyin');
  });

  it('openDrawer opens and kicks a load', async () => {
    (cookieApi.list as Mock).mockResolvedValue({ cookies: [] });
    useCookieStore.getState().openDrawer();
    expect(useCookieStore.getState().drawerOpen).toBe(true);
    await waitFor(() => expect(cookieApi.list).toHaveBeenCalled());
  });

  it('isConfigured / updatedAtOf read the entries', () => {
    const entries = [{ platform: 'douyin', configured: true, updated_at: 'x' }];
    expect(isConfigured(entries, 'douyin')).toBe(true);
    expect(isConfigured(entries, 'weibo')).toBe(false);
    expect(updatedAtOf(entries, 'douyin')).toBe('x');
    expect(updatedAtOf(entries, 'weibo')).toBeNull();
  });
});
