import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios';
import { authApi, healthApi } from './api';
import { apiClient, setOnUnauthorized, setTokenGetter } from './apiClient';
import { useAppStore } from '../stores/appStore';
import { useAuthStore } from '../stores/authStore';
import { ApiError, CODE_NETWORK_ERROR } from '../types/api';

// A test adapter that answers API calls with synthetic envelope bodies, so the
// axios interceptors (unwrapping, error normalization, 401 handling) run
// against real client code without any network.
type Responder = (config: InternalAxiosRequestConfig) => { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;

function stubAdapter(responder: Responder): void {
  const adapter: AxiosAdapter = async (config) => {
    const { status = 200, body } = await responder(config);
    return {
      data: body,
      status,
      statusText: status >= 200 && status < 300 ? 'OK' : 'ERROR',
      headers: {},
      // Mirror axios defaults so `settle` rejects non-2xx like the real
      // dispatcher does (the envelope error path is what the backend uses).
      config: { ...config, validateStatus: (s: number) => s >= 200 && s < 300 },
    };
  };
  apiClient.defaults.adapter = adapter;
}

const okEnvelope = (data: unknown) => ({ code: 0, message: 'ok', data });

describe('apiClient', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, username: null, expiresAt: null });
    useAppStore.setState({ pendingRequests: 0 });
    // main.tsx registers this getter in the real app; mirror it here so the
    // bearer-token behavior is exercised end to end.
    setTokenGetter(() => useAuthStore.getState().token);
    setOnUnauthorized(null);
  });

  afterEach(() => {
    setTokenGetter(() => null);
    setOnUnauthorized(null);
    apiClient.defaults.adapter = undefined;
  });

  it('unwraps the envelope and resolves with data', async () => {
    stubAdapter(() => ({ body: okEnvelope({ username: 'admin' }) }));
    const data = await authApi.login({ username: 'admin', password: 'x' });
    expect(data).toEqual({ username: 'admin' });
  });

  it('normalizes envelope errors into ApiError with code and http status', async () => {
    stubAdapter(() => ({
      status: 401,
      body: { code: 2005, message: '用户名或密码错误 / Invalid username or password', data: null },
    }));
    await expect(authApi.login({ username: 'a', password: 'b' })).rejects.toMatchObject({
      name: 'ApiError',
      code: 2005,
      httpStatus: 401,
    });
  });

  it('calls the unauthorized handler for session auth errors', async () => {
    const handler = vi.fn();
    setOnUnauthorized(handler);
    stubAdapter(() => ({ status: 401, body: { code: 2001, message: '未登录 / Not logged in', data: null } }));
    await expect(authApi.login({ username: 'a', password: 'b' })).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does not treat file-token errors (5003) as a session expiry', async () => {
    const handler = vi.fn();
    setOnUnauthorized(handler);
    stubAdapter(() => ({ status: 401, body: { code: 5003, message: 'Token无效或已过期 / Invalid token', data: null } }));
    await expect(authApi.login({ username: 'a', password: 'b' })).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });

  it('normalizes transport failures into ApiError with the network code', async () => {
    stubAdapter(() => {
      throw new Error('Network Error');
    });
    await expect(healthApi.getHealth()).rejects.toMatchObject({
      name: 'ApiError',
      code: CODE_NETWORK_ERROR,
      httpStatus: 0,
    });
  });

  it('attaches the bearer token when a session exists', async () => {
    useAuthStore.setState({ token: 'tok', username: 'admin', expiresAt: '2099-01-01T00:00:00Z' });
    stubAdapter((config) => {
      expect(config.headers?.get('Authorization')).toBe('Bearer tok');
      return { body: okEnvelope({ status: 'ok' }) };
    });
    await healthApi.getHealth();
  });

  it('omits the bearer token when logged out', async () => {
    stubAdapter((config) => {
      expect(config.headers?.get('Authorization')).toBeUndefined();
      return { body: okEnvelope({ status: 'ok' }) };
    });
    await healthApi.getHealth();
  });

  it('passes non-envelope responses through untouched (file downloads)', async () => {
    stubAdapter(() => ({ body: { type: 'application/octet-stream' } }));
    const response = await apiClient.get('/download/file/abc?token=t');
    expect(response.data).toEqual({ type: 'application/octet-stream' });
  });

  it('tracks in-flight requests in the global loading counter by default', async () => {
    let resolveRequest!: (value: { status?: number; body: unknown }) => void;
    stubAdapter(() => new Promise((resolve) => { resolveRequest = resolve; }));

    const pending = authApi.login({ username: 'a', password: 'b' });
    await Promise.resolve();
    await Promise.resolve();
    expect(useAppStore.getState().pendingRequests).toBe(1);

    resolveRequest({ body: okEnvelope({ username: 'admin' }) });
    await pending;
    expect(useAppStore.getState().pendingRequests).toBe(0);
  });

  it('does not touch the loading counter for skipGlobalLoading requests', async () => {
    let resolveRequest!: (value: { status?: number; body: unknown }) => void;
    stubAdapter(() => new Promise((resolve) => { resolveRequest = resolve; }));

    // healthApi polls on a background interval — it opts out of the global
    // loading bar so the shell does not flash on every poll.
    const pending = healthApi.getHealth();
    await Promise.resolve();
    await Promise.resolve();
    expect(useAppStore.getState().pendingRequests).toBe(0);

    resolveRequest({ body: okEnvelope({ status: 'ok' }) });
    await pending;
    expect(useAppStore.getState().pendingRequests).toBe(0);
  });
});
