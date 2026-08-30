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
  ParseResult,
  PreviewData,
  SubmitData,
  DownloadProgress,
  RefreshData,
} from '../types/api';
import type { PublicMediaManifest } from '../types/mediaManifest';
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
  /** POST /api/auth/refresh → a rotated session; caller owns 401 handling. */
  async refresh(token: string): Promise<RefreshData> {
    const { data } = await apiClient.post<RefreshData>('/auth/refresh', null, {
      authToken: token,
      skipUnauthorized: true,
    });
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
    return {
      ...data,
      results: data.results.map(normalizeParseResult),
    };
  },
};

/** Values that were historically populated with private upstream media URLs. */
const LEGACY_MEDIA_FIELDS = new Set([
  'video_url',
  'images',
  'upstream_url',
  'upstream_urls',
  'cdn_url',
  'cdn_urls',
  'media_url',
  'media_urls',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function isSafeTaskId(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && !/[\\/?#]/.test(value);
}

function isPublicResourceUrl(
  value: unknown,
  taskId: string,
  resourceKind: 'video' | 'image' | 'live',
  suffix?: 'image' | 'motion',
): value is string {
  if (typeof value !== 'string') return false;
  const prefix = `/api/preview/${taskId}/resources/`;
  if (!value.startsWith(prefix)) return false;
  const path = value.slice(prefix.length).split('/');
  if (path.length !== (suffix === undefined ? 2 : 3)) return false;
  if (path[0] !== resourceKind || !/^(0|[1-9]\d*)$/.test(path[1])) return false;
  return suffix === undefined || path[2] === suffix;
}

function normalizePublicManifest(value: unknown, taskId: unknown): PublicMediaManifest | null {
  if (!isSafeTaskId(taskId)) return null;
  if (!isRecord(value) || typeof value.kind !== 'string') return null;

  if (value.kind === 'video' && Array.isArray(value.videos) && value.videos.length > 0) {
    const videos = value.videos.map((item) => {
      if (!isRecord(item)) return null;
      const url = stringValue(item.url);
      const format = stringValue(item.format);
      const quality = item.quality === null ? null : stringValue(item.quality);
      return (
        url !== null &&
        isPublicResourceUrl(url, taskId, 'video') &&
        format !== null &&
        (item.quality === null || quality !== null)
      )
        ? { url, format, quality }
        : null;
    });
    if (!videos.every((item): item is NonNullable<typeof item> => item !== null)) return null;
    return { kind: 'video', videos };
  }

  if (value.kind === 'image_album' && Array.isArray(value.images) && value.images.length > 0) {
    const images = value.images.map((item) => {
      if (!isRecord(item)) return null;
      const url = stringValue(item.url);
      const format = stringValue(item.format);
      return url !== null && isPublicResourceUrl(url, taskId, 'image') && format !== null
        ? { url, format }
        : null;
    });
    if (!images.every((item): item is NonNullable<typeof item> => item !== null)) return null;
    return { kind: 'image_album', images };
  }

  if (
    value.kind === 'live_photo' &&
    Array.isArray(value.live_photos) &&
    value.live_photos.length > 0 &&
    Array.isArray(value.warnings)
  ) {
    const livePhotos = value.live_photos.map((item) => {
      if (!isRecord(item)) return null;
      const imageUrl = stringValue(item.image_url);
      const motionUrl = item.motion_url === null ? null : stringValue(item.motion_url);
      return (
        imageUrl !== null &&
        isPublicResourceUrl(imageUrl, taskId, 'live', 'image') &&
        (item.motion_url === null ||
          (motionUrl !== null && isPublicResourceUrl(motionUrl, taskId, 'live', 'motion')))
      )
        ? { image_url: imageUrl, motion_url: motionUrl }
        : null;
    });
    if (!livePhotos.every((item): item is NonNullable<typeof item> => item !== null)) return null;
    if (!value.warnings.every((item) => typeof item === 'string')) return null;
    return {
      kind: 'live_photo',
      live_photos: livePhotos,
      warnings: [...value.warnings],
    };
  }

  return null;
}

/**
 * Normalize a backend parse result at the API boundary.
 *
 * Public manifests are copied only when they satisfy the wire contract. The
 * legacy URL-bearing compatibility fields are intentionally dropped so a
 * private CDN URL cannot reach renderer state or JSON serialization.
 */
export function normalizeParseResult(result: unknown): ParseResult {
  const source = isRecord(result) ? result : {};
  const normalized = Object.fromEntries(
    Object.entries(source).filter(([key]) => !LEGACY_MEDIA_FIELDS.has(key)),
  );
  return {
    ...normalized,
    manifest: normalizePublicManifest(source.manifest, source.task_id),
  } as ParseResult;
}

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
  /** GET /api/music/hot → {keywords}. */
  async getHotKeywords(): Promise<{ keywords: string[] }> {
    const { data } = await apiClient.get<{ keywords: string[] }>('/music/hot');
    return data;
  },
};
