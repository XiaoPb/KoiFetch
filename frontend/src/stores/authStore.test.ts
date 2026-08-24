import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { authApi } from '../services/api';
import { AUTH_STORAGE_KEY, createAuthStore, isExpired, selectIsAuthenticated } from './authStore';

vi.mock('../services/api', () => ({
  authApi: { login: vi.fn() },
}));

const FUTURE = '2099-01-01T00:00:00Z';
const PAST = '2020-01-01T00:00:00Z';

describe('authStore', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('stores the session after login and reports authenticated', async () => {
    (authApi.login as Mock).mockResolvedValue({ token: 'tok', username: 'admin', expires_at: FUTURE });
    const store = createAuthStore();
    await store.getState().login('admin', 'secret');

    const state = store.getState();
    expect(state.token).toBe('tok');
    expect(state.username).toBe('admin');
    expect(state.expiresAt).toBe(FUTURE);
    expect(selectIsAuthenticated(state)).toBe(true);
    expect(authApi.login).toHaveBeenCalledWith({ username: 'admin', password: 'secret' });
  });

  it('persists the session to localStorage and rehydrates a fresh store', async () => {
    (authApi.login as Mock).mockResolvedValue({ token: 'tok', username: 'admin', expires_at: FUTURE });
    const storeA = createAuthStore();
    await storeA.getState().login('admin', 'secret');

    const stored = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}');
    expect(stored.state?.token).toBe('tok');
    expect(stored.state?.username).toBe('admin');
    expect(stored.state?.expiresAt).toBe(FUTURE);

    // A brand-new store created from the same key rehydrates the session.
    const storeB = createAuthStore();
    expect(storeB.getState().token).toBe('tok');
    expect(selectIsAuthenticated(storeB.getState())).toBe(true);
  });

  it('clears the session and its persisted state on logout', async () => {
    (authApi.login as Mock).mockResolvedValue({ token: 'tok', username: 'admin', expires_at: FUTURE });
    const store = createAuthStore();
    await store.getState().login('admin', 'secret');

    store.getState().logout();

    const state = store.getState();
    expect(state.token).toBeNull();
    expect(state.username).toBeNull();
    expect(state.expiresAt).toBeNull();
    expect(selectIsAuthenticated(state)).toBe(false);
    const stored = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}');
    expect(stored.state?.token).toBeNull();
  });

  it('treats an expired session as unauthenticated', () => {
    const store = createAuthStore();
    store.setState({ token: 'tok', username: 'admin', expiresAt: PAST });
    expect(selectIsAuthenticated(store.getState())).toBe(false);
  });

  it('isExpired handles missing and malformed timestamps', () => {
    expect(isExpired(null)).toBe(true);
    expect(isExpired(undefined)).toBe(true);
    expect(isExpired('not-a-date')).toBe(true);
    expect(isExpired(PAST)).toBe(true);
    expect(isExpired(FUTURE)).toBe(false);
  });

  it('propagates login failures without storing a session', async () => {
    (authApi.login as Mock).mockRejectedValue(new Error('401'));
    const store = createAuthStore();
    await expect(store.getState().login('admin', 'wrong')).rejects.toThrow('401');
    expect(store.getState().token).toBeNull();
    expect(selectIsAuthenticated(store.getState())).toBe(false);
  });
});
