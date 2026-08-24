// Typed DTOs mirroring the backend API contract (Tasks 7-10).
//
// Every endpoint returns the unified envelope {code, message, data}; success
// is code === 0 and errors carry a stable numeric code (the table below).
// Error codes match backend/app/api/responses.py. The client additionally
// uses code -1 for transport-level failures (network/timeout) that never
// reach the backend envelope.

export const ApiCodes = {
  OK: 0,
  BAD_REQUEST: 400,
  URL_EMPTY: 1001,
  URL_INVALID: 1002,
  PLATFORM_UNSUPPORTED: 1003,
  TASK_NOT_FOUND: 3001,
  TASK_ALREADY_DOWNLOADING: 3002,
  TASK_ALREADY_COMPLETED: 3003,
  FILE_NOT_FOUND: 5001,
  FILE_NOT_DOWNLOADED: 5002,
  FILE_TOKEN_INVALID: 5003,
  FILE_EXPIRED: 5004,
  UNAUTHORIZED: 2001,
  FORBIDDEN: 2002,
  INVALID_TOKEN: 2003,
  TOKEN_EXPIRED: 2004,
  INVALID_CREDENTIALS: 2005,
  INTERNAL_ERROR: 9001,
} as const;

export type ApiCode = (typeof ApiCodes)[keyof typeof ApiCodes];

/** Client-side code for transport failures (no envelope ever arrives). */
export const CODE_NETWORK_ERROR = -1;

export const NETWORK_ERROR_MESSAGE = '网络错误 / Network error';

/** The unified response envelope every backend endpoint returns. */
export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T | null;
}

/** A normalized API failure with the envelope code + HTTP status preserved. */
export class ApiError extends Error {
  readonly code: number;
  readonly httpStatus: number;
  readonly data: unknown;

  constructor(message: string, code: number, httpStatus: number, data?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.httpStatus = httpStatus;
    this.data = data;
  }
}

// ---------------------------------------------------------------------------
// Auth (POST /api/auth/login)
// ---------------------------------------------------------------------------

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginData {
  token: string;
  username: string;
  /** ISO-8601 with timezone, e.g. "2026-08-23T15:30:00+00:00". */
  expires_at: string;
}

// ---------------------------------------------------------------------------
// Parse (POST /api/parse)
// ---------------------------------------------------------------------------

export type MediaType = 'video' | 'music' | 'image';

export interface ParseResult {
  task_id: string;
  url: string;
  type: string;
  platform: string;
  title: string;
  cover: string | null;
  /** "MM:SS" */
  duration: string | null;
  file_size_mb: number | null;
  format: string | null;
  available_qualities: string[];
  available_bitrates: string[];
}

export interface ParseFailure {
  url: string;
  error: string;
}

export interface ParseData {
  results: ParseResult[];
  failed: ParseFailure[];
}

// ---------------------------------------------------------------------------
// Preview (GET /api/preview/{task_id})
// ---------------------------------------------------------------------------

export interface PreviewStream {
  quality?: string | null;
  bitrate?: string | null;
  format?: string | null;
}

export interface PreviewData {
  task_id: string;
  preview_type: string;
  url: string;
  platform: string;
  title: string | null;
  cover: string | null;
  duration: string | null;
  format: string | null;
  file_size_mb: number | null;
  available_qualities: string[];
  available_bitrates: string[];
  streams: PreviewStream[];
}

// ---------------------------------------------------------------------------
// Download (submit / progress / file / WebSocket)
// ---------------------------------------------------------------------------

export type DownloadStatus =
  | 'pending'
  | 'downloading'
  | 'completed'
  | 'failed'
  | 'expired';

export interface SubmitData {
  download_id: string;
  task_id: string;
  status: DownloadStatus;
  /** ISO-8601 (UTC). */
  created_at: string;
}

export interface DownloadProgress {
  download_id: string;
  status: DownloadStatus;
  /** 0..1 fraction. */
  progress: number;
  /** bytes/second. */
  speed: number | null;
  downloaded_bytes: number | null;
  total_bytes: number | null;
  /** seconds. */
  remaining_time: number | null;
  error_message?: string | null;
}

/** Progress event plus the tokenized file URL (WS complete event). */
export interface WsCompleteData extends DownloadProgress {
  download_url: string;
  /** ISO-8601 with timezone. */
  token_expire_at: string;
}

/**
 * Error event payload. The backend sends TWO shapes:
 * - task-state errors (failed/expired) carry the snapshot state fields PLUS
 *   code/message, and
 * - protocol errors (invalid/unknown download id) carry ONLY {code, message}.
 * Every field except code/message is therefore optional; consumers must treat
 * missing state fields as "no snapshot available", not as a stalled download.
 */
export interface WsErrorData extends Partial<DownloadProgress> {
  code: number;
  message: string;
}

export type WsEvent =
  | { type: 'progress'; data: DownloadProgress }
  | { type: 'complete'; data: WsCompleteData }
  | { type: 'error'; data: WsErrorData };

// ---------------------------------------------------------------------------
// NAS (POST /api/nas/save)
// ---------------------------------------------------------------------------

export interface NasSaveData {
  /** NAS-style logical path, e.g. "/视频/抖音/x.mp4". */
  nas_path: string;
  /** bytes. */
  file_size: number;
  /** ISO-8601 with timezone (UTC). */
  saved_at: string;
}

// ---------------------------------------------------------------------------
// Health (GET /api/health)
// ---------------------------------------------------------------------------

export interface HealthData {
  status: 'ok' | 'degraded';
  services: Record<string, string>;
  storage_roots: Record<string, string>;
}
