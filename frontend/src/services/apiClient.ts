import axios, { AxiosError } from 'axios';
import { useAppStore } from '../stores/appStore';
import {
  ApiCodes,
  ApiError,
  CODE_NETWORK_ERROR,
  NETWORK_ERROR_MESSAGE,
  type ApiEnvelope,
} from '../types/api';

// Opt-out flag for the global loading counter, e.g. background health polls
// that should not flash the shell's loading bar. Used via per-request config.
declare module 'axios' {
  export interface AxiosRequestConfig {
    skipGlobalLoading?: boolean;
  }
}

/**
 * Base URL of the backend REST API. Defaults to the same-origin `/api`
 * prefix — the Vite dev server proxies it to http://localhost:8000 and the
 * nginx deployment proxies it to the backend service — so local development
 * needs no CORS setup. Deployments on a different origin override it with
 * `VITE_API_BASE_URL`.
 */
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api';

/** True when a body has the backend's {code, message, data} envelope shape. */
function isEnvelope(value: unknown): value is ApiEnvelope<unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    'code' in value &&
    'message' in value &&
    'data' in value
  );
}

/**
 * Callback invoked when the session is no longer valid (HTTP 401 with a
 * session-level auth code, e.g. 2001/2003/2004/2005). The app registers a
 * handler that clears the auth store and redirects to /login. File-token
 * errors (5003) deliberately do NOT count — they concern a download URL, not
 * the admin session.
 */
let unauthorizedHandler: (() => void) | null = null;

export function setOnUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/**
 * Bearer-token source. Injected by the app shell
 * (`setTokenGetter(() => useAuthStore.getState().token)`) so this module does
 * not import the auth store — keeping the dependency direction one-way
 * (apiClient ← api ← authStore) and avoiding an import cycle.
 */
let tokenGetter: () => string | null = () => null;

export function setTokenGetter(getter: () => string | null): void {
  tokenGetter = getter;
}

function isSessionAuthFailure(code: number): boolean {
  return (
    code === ApiCodes.UNAUTHORIZED ||
    code === ApiCodes.INVALID_TOKEN ||
    code === ApiCodes.TOKEN_EXPIRED ||
    code === ApiCodes.INVALID_CREDENTIALS
  );
}

/** Notify the registered handler when a session-level auth error is seen. */
function maybeNotifyUnauthorized(apiError: ApiError): void {
  if (apiError.httpStatus === 401 && isSessionAuthFailure(apiError.code)) {
    unauthorizedHandler?.();
  }
}

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15_000,
});

apiClient.interceptors.request.use((config) => {
  const token = tokenGetter();
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  if (!config.skipGlobalLoading) {
    useAppStore.getState().beginRequest();
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => {
    if (!response.config.skipGlobalLoading) {
      useAppStore.getState().endRequest();
    }
    if (isEnvelope(response.data)) {
      const envelope = response.data;
      if (envelope.code !== 0) {
        // Defensive: a 2xx carrying an error envelope (the backend maps
        // errors to proper HTTP statuses, but be robust either way).
        const apiError = new ApiError(envelope.message, envelope.code, response.status, envelope.data);
        maybeNotifyUnauthorized(apiError);
        throw apiError;
      }
      // Success: resolve with the unwrapped payload.
      response.data = envelope.data;
    }
    // Non-envelope responses (e.g. raw file bytes) pass through untouched.
    return response;
  },
  (error: unknown) => {
    if (!(axios.isAxiosError(error) ? error.config?.skipGlobalLoading : false)) {
      useAppStore.getState().endRequest();
    }
    const apiError = toApiError(error);
    maybeNotifyUnauthorized(apiError);
    return Promise.reject(apiError);
  },
);

/** Normalize any thrown value into a typed ApiError. */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;

  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<unknown>;
    const status = axiosError.response?.status ?? 0;
    const body = axiosError.response?.data;
    if (isEnvelope(body)) {
      return new ApiError(body.message, body.code, status, body.data);
    }
    if (axiosError.response) {
      // Non-envelope HTTP error (e.g. a proxy 502): mirror the HTTP status.
      return new ApiError(`HTTP ${status}`, status, status);
    }
    return new ApiError(NETWORK_ERROR_MESSAGE, CODE_NETWORK_ERROR, 0);
  }

  const message = error instanceof Error ? error.message : '未知错误 / Unknown error';
  return new ApiError(message, CODE_NETWORK_ERROR, 0);
}

/** A human-readable message for any thrown value (for toasts/forms). */
export function getErrorMessage(error: unknown, fallback: string = NETWORK_ERROR_MESSAGE): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/** Resolve a backend-relative path to an absolute URL for <a>/window usage. */
export function resolveApiUrl(path: string): string {
  if (/^https?:\/\//.test(path) || path.startsWith('/')) return path;
  return `${API_BASE_URL}/${path}`;
}
