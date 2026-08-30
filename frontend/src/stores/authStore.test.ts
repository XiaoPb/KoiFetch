import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { authApi } from '../services/api';
import type { LoginData } from '../types/api';
import { AUTH_STORAGE_KEY, createAuthStore, isExpired, selectIsAuthenticated } from './authStore';

vi.mock('../services/api', () => ({
  authApi: { login: vi.fn(), refresh: vi.fn() },
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

  it('deduplicates concurrent startup refreshes', async () => {
    (authApi.refresh as Mock).mockResolvedValue({ token: 'new', username: 'admin', expires_at: FUTURE });
    const store = createAuthStore();
    store.setState({ token: 'old', username: 'admin', expiresAt: FUTURE });

    await Promise.all([store.getState().refreshSession(), store.getState().refreshSession()]);

    expect(authApi.refresh).toHaveBeenCalledTimes(1);
    expect(authApi.refresh).toHaveBeenCalledWith('old');
    expect(store.getState().token).toBe('new');
  });

  it('does not refresh without a token or with an expired session', async () => {
    const store = createAuthStore();

    await store.getState().refreshSession();
    store.setState({ token: 'expired', username: 'admin', expiresAt: PAST });
    await store.getState().refreshSession();

    expect(authApi.refresh).not.toHaveBeenCalled();
  });

  it('does not let an older refresh overwrite a newer login', async () => {
    let resolveRefresh!: (data: LoginData) => void;
    (authApi.refresh as Mock).mockReturnValue(new Promise<LoginData>((resolve) => {
      resolveRefresh = resolve;
    }));
    (authApi.login as Mock).mockResolvedValue({ token: 'login-token', username: 'admin', expires_at: FUTURE });
    const store = createAuthStore();
    store.setState({ token: 'old', username: 'admin', expiresAt: FUTURE });

    const refresh = store.getState().refreshSession();
    await store.getState().login('admin', 'pw');
    resolveRefresh({ token: 'stale', username: 'admin', expires_at: FUTURE });
    await refresh;

    expect(store.getState().token).toBe('login-token');
  });

  it('swallows a refresh rejection after a newer login has started', async () => {
    let rejectRefresh!: (reason: unknown) => void;
    let resolveLogin!: (data: LoginData) => void;
    (authApi.refresh as Mock).mockReturnValue(new Promise<LoginData>((_resolve, reject) => {
      rejectRefresh = reject;
    }));
    (authApi.login as Mock).mockReturnValue(new Promise<LoginData>((resolve) => {
      resolveLogin = resolve;
    }));
    const store = createAuthStore();
    store.setState({ token: 'old', username: 'admin', expiresAt: FUTURE });

    const refresh = store.getState().refreshSession();
    const login = store.getState().login('admin', 'pw');
    rejectRefresh(new Error('expired'));
    await refresh;
    resolveLogin({ token: 'login-token', username: 'admin', expires_at: FUTURE });
    await login;

    expect(store.getState().token).toBe('login-token');
  });

  it('lets a login started after refresh win even when refresh returns first', async () => {
    let resolveRefresh!: (data: LoginData) => void;
    let resolveLogin!: (data: LoginData) => void;
    (authApi.refresh as Mock).mockReturnValue(new Promise<LoginData>((resolve) => {
      resolveRefresh = resolve;
    }));
    (authApi.login as Mock).mockReturnValue(new Promise<LoginData>((resolve) => {
      resolveLogin = resolve;
    }));
    const store = createAuthStore();
    store.setState({ token: 'old', username: 'admin', expiresAt: FUTURE });

    const refresh = store.getState().refreshSession();
    const login = store.getState().login('admin', 'pw');
    resolveRefresh({ token: 'stale-refresh', username: 'admin', expires_at: FUTURE });
    await refresh;
    resolveLogin({ token: 'login-token', username: 'admin', expires_at: FUTURE });
    await login;

    expect(store.getState().token).toBe('login-token');
  });

  it('makes the last initiated concurrent login win regardless of response order', async () => {
    let resolveFirst!: (data: LoginData) => void;
    let resolveSecond!: (data: LoginData) => void;
    (authApi.login as Mock)
      .mockReturnValueOnce(new Promise<LoginData>((resolve) => { resolveFirst = resolve; }))
      .mockReturnValueOnce(new Promise<LoginData>((resolve) => { resolveSecond = resolve; }));
    const store = createAuthStore();

    const firstLogin = store.getState().login('admin', 'first');
    const secondLogin = store.getState().login('admin', 'second');
    resolveSecond({ token: 'second-token', username: 'admin', expires_at: FUTURE });
    await secondLogin;
    resolveFirst({ token: 'first-token', username: 'admin', expires_at: FUTURE });
    await firstLogin;

    expect(store.getState().token).toBe('second-token');
  });

  it('still makes the last initiated login win when the first response arrives first', async () => {
    let resolveFirst!: (data: LoginData) => void;
    let resolveSecond!: (data: LoginData) => void;
    (authApi.login as Mock)
      .mockReturnValueOnce(new Promise<LoginData>((resolve) => { resolveFirst = resolve; }))
      .mockReturnValueOnce(new Promise<LoginData>((resolve) => { resolveSecond = resolve; }));
    const store = createAuthStore();

    const firstLogin = store.getState().login('admin', 'first');
    const secondLogin = store.getState().login('admin', 'second');
    resolveFirst({ token: 'first-token', username: 'admin', expires_at: FUTURE });
    await firstLogin;
    resolveSecond({ token: 'second-token', username: 'admin', expires_at: FUTURE });
    await secondLogin;

    expect(store.getState().token).toBe('second-token');
  });

  it('keeps the existing session when an initiated login fails', async () => {
    (authApi.login as Mock).mockRejectedValue(new Error('invalid credentials'));
    const store = createAuthStore();
    store.setState({ token: 'existing', username: 'admin', expiresAt: FUTURE });

    await expect(store.getState().login('admin', 'wrong')).rejects.toThrow('invalid credentials');

    expect(store.getState().token).toBe('existing');
    expect(store.getState().username).toBe('admin');
  });

  it('does not let an older refresh restore a logged-out session', async () => {
    let resolveRefresh!: (data: LoginData) => void;
    (authApi.refresh as Mock).mockReturnValue(new Promise<LoginData>((resolve) => {
      resolveRefresh = resolve;
    }));
    const store = createAuthStore();
    store.setState({ token: 'old', username: 'admin', expiresAt: FUTURE });

    const refresh = store.getState().refreshSession();
    store.getState().logout();
    resolveRefresh({ token: 'stale', username: 'admin', expires_at: FUTURE });
    await refresh;

    expect(store.getState().token).toBeNull();
  });

  it('keeps the current session when refresh fails', async () => {
    (authApi.refresh as Mock).mockRejectedValue(new Error('expired'));
    const store = createAuthStore();
    store.setState({ token: 'old', username: 'admin', expiresAt: FUTURE });

    await expect(store.getState().refreshSession()).rejects.toThrow('expired');

    expect(store.getState().token).toBe('old');
    expect(store.getState().username).toBe('admin');
  });

  it('rehydrates the v1 session shape and persists version 2', async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({
      state: { token: 'old', username: 'admin', expiresAt: FUTURE },
      version: 1,
    }));

    const store = createAuthStore();

    expect(store.getState().token).toBe('old');
    expect(JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}').version).toBe(2);
  });
});
