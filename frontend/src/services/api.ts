import { apiClient, resolveApiUrl } from './apiClient';
import type {
  ByTaskData,
  CookieEntry,
  CookieListData,
  HealthData,
  LoginData,
  LoginRequest,
  NasSaveData,
  ParseData,
  PreviewData,
  SubmitData,
  DownloadProgress,
} from '../types/api';
import type { MusicSearchParams, MusicSearchResult } from '../types/music';

// Typed endpoint functions over the shared axios client (which unwraps the
// {code, message, data} envelope, so every function below resolves with the
// `data` payload directly). Tasks 14-16 build the features on top of these.

export const authApi = {
  /** POST /api/auth/login → {token, username, expires_at}. */
  async login(body: LoginRequest): Promise<LoginData> {
    const { data } = await apiClient.post<LoginData>('/auth/login', body);
    return data;
  },
};

export const healthApi = {
  /**
   * GET /api/health → {status, services, storage_roots}. Polled on a
   * background interval, so it opts out of the global loading counter to
   * avoid flashing the shell's loading bar on every poll.
   *
   * Tolerates the degraded envelope: the backend reports degraded storage as
   * HTTP 200 + code 1 + data.status "degraded" (the container readiness probe
   * depends on that wire shape), which the shared client would otherwise
   * reject — the NAS page must be able to render which root failed.
   */
  async getHealth(): Promise<HealthData> {
    const { data } = await apiClient.get<HealthData>('/health', {
      skipGlobalLoading: true,
      tolerateErrorEnvelope: true,
    });
    return data;
  },
};

export const parseApi = {
  /** POST /api/parse — batch URL parse → {results, failed}. */
  async parse(urls: string[]): Promise<ParseData> {
    const { data } = await apiClient.post<ParseData>('/parse', { urls });
    return data;
  },
};

export const previewApi = {
  /** GET /api/preview/{task_id} → preview metadata + streams. */
  async getPreview(taskId: string): Promise<PreviewData> {
    const { data } = await apiClient.get<PreviewData>(`/preview/${taskId}`);
    return data;
  },
};

export const mediaApi = {
  /**
   * Same-origin stream-proxy URL for inline video playback
   * (GET /api/preview/{task_id}/stream). Plays through the backend so
   * flv.js/hls.js work without CORS and platform URLs never reach the client.
   */
  streamUrl(taskId: string): string {
    return resolveApiUrl(`/api/preview/${taskId}/stream`);
  },
  /** Attachment URL for one album image (browser saves the file). */
  imageUrl(taskId: string, index: number): string {
    return resolveApiUrl(`/api/preview/${taskId}/images/${index}`);
  },
  /** Attachment URL for the whole album as a ZIP. */
  albumZipUrl(taskId: string): string {
    return resolveApiUrl(`/api/preview/${taskId}/images.zip`);
  },
};

export const downloadApi = {
  /** POST /api/download/submit → {download_id, task_id, status, created_at}. */
  async submit(
    taskId: string,
    options: { format?: string | null; quality?: string | null } = {},
  ): Promise<SubmitData> {
    const { data } = await apiClient.post<SubmitData>('/download/submit', {
      task_id: taskId,
      format: options.format ?? null,
      quality: options.quality ?? null,
    });
    return data;
  },

  /** GET /api/download/progress/{download_id} → current snapshot. */
  async getProgress(downloadId: string): Promise<DownloadProgress> {
    const { data } = await apiClient.get<DownloadProgress>(`/download/progress/${downloadId}`);
    return data;
  },

  /** GET /api/download/by-task/{task_id} → the task's NEWEST download snapshot. */
  async getLatestByTask(taskId: string): Promise<ByTaskData> {
    const { data } = await apiClient.get<ByTaskData>(`/download/by-task/${taskId}`);
    return data;
  },

  /**
   * Resolve a download URL (e.g. the WS `download_url`) to an absolute URL
   * suitable for <a href> / window.open. Envelope-based endpoints are
   * unaffected; this is used for the raw file endpoint.
   */
  getFileUrl(pathOrUrl: string): string {
    return resolveApiUrl(pathOrUrl);
  },
};

export const nasApi = {
  /** POST /api/nas/save (admin, Bearer) → {nas_path, file_size, saved_at}. */
  async save(downloadId: string, targetPath: string): Promise<NasSaveData> {
    const { data } = await apiClient.post<NasSaveData>('/nas/save', {
      download_id: downloadId,
      target_path: targetPath,
    });
    return data;
  },
};

export const cookieApi = {
  /** GET /api/cookies → {cookies: [{platform, configured, updated_at}]}. */
  async list(): Promise<CookieListData> {
    const { data } = await apiClient.get<CookieListData>('/cookies');
    return data;
  },

  /** PUT /api/cookies/{platform} — upsert the cookie (value never echoed back). */
  async set(platform: string, cookie: string): Promise<CookieEntry> {
    const { data } = await apiClient.put<CookieEntry>(`/cookies/${platform}`, { cookie });
    return data;
  },

  /** DELETE /api/cookies/{platform} — clear the stored cookie. */
  async remove(platform: string): Promise<void> {
    await apiClient.delete(`/cookies/${platform}`);
  },
};

export const musicApi = {
  /** GET /api/music/search → MusicSearchResult (wire shape is the contract). */
  async search(params: MusicSearchParams): Promise<MusicSearchResult> {
    const { data } = await apiClient.get<MusicSearchResult>('/music/search', { params });
    return data;
  },
  /** POST /api/music/import {song_id} → {task_id} (download closure). */
  async importSong(songId: string): Promise<{ task_id: string }> {
    const { data } = await apiClient.post<{ task_id: string }>('/music/import', { song_id: songId });
    return data;
  },
};
