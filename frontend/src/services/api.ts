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
  AssetSelector,
  PreparedTransfer,
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
      results: data.results
        .map(normalizeParseResult)
        .filter((result): result is ParseResult => result !== null),
    };
  },
};

/** Bounds applied to untrusted parse response values before renderer state. */
const MAX_TASK_ID_LENGTH = 128;
const MAX_URL_LENGTH = 4096;
const MAX_LABEL_LENGTH = 256;
const MAX_FORMAT_LENGTH = 64;
const MAX_PUBLIC_TEXT_LENGTH = 256;
const MAX_OPTION_COUNT = 128;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/;
const UNSAFE_PUBLIC_TEXT = /(?:[a-z][a-z0-9+.-]{1,31}:\/\/|\/\/[^\s]+|(?:access[_-]?token|auth|expires|key|secret|sig(?:nature)?|token)=)/i;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function parseResultType(value: unknown): ParseResult['type'] | null {
  switch (value) {
    case 'video':
    case 'image':
    case 'live_photo':
    case 'music':
      return value;
    default:
      return null;
  }
}

function isSafeTaskId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value.length <= MAX_TASK_ID_LENGTH &&
    /^[A-Za-z0-9_-]+$/.test(value)
  );
}

export type PublicPreviewResourceKind = 'video' | 'image' | 'live';
export type PublicPreviewResourceSuffix = 'image' | 'motion';

/** Validate one canonical same-origin preview route and bind it to a task. */
export function isSafePublicPreviewRoute(
  value: unknown,
  taskId: unknown,
  resourceKind?: PublicPreviewResourceKind,
  suffix?: PublicPreviewResourceSuffix,
): value is string {
  if (!isSafeTaskId(taskId) || typeof value !== 'string') return false;
  const prefix = `/api/preview/${taskId}/resources/`;
  if (!value.startsWith(prefix)) return false;
  const path = value.slice(prefix.length).split('/');
  if (path.length !== (suffix === undefined ? 2 : 3)) return false;
  if (resourceKind !== undefined && path[0] !== resourceKind) return false;
  if (!['video', 'image', 'live'].includes(path[0])) return false;
  if (!/^(0|[1-9]\d*)$/.test(path[1])) return false;
  return suffix === undefined || path[2] === suffix;
}

function isSafeString(value: unknown, maxLength: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength && !CONTROL_CHARACTER.test(value);
}

function isSafePublicText(value: unknown): value is string {
  return isSafeString(value, MAX_PUBLIC_TEXT_LENGTH) && !UNSAFE_PUBLIC_TEXT.test(value);
}

function isSafeStringArray(value: unknown, maxLength: number): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= MAX_OPTION_COUNT &&
    value.every((item) => isSafeString(item, maxLength))
  );
}

function normalizePublicManifest(
  value: unknown,
  taskId: string,
  resultType: string,
): PublicMediaManifest | null {
  if (!isRecord(value) || typeof value.kind !== 'string') return null;
  if (resultType === 'music' || value.kind !== (resultType === 'image' ? 'image_album' : resultType)) {
    return null;
  }

  if (
    value.kind === 'video' &&
    Array.isArray(value.videos) &&
    value.videos.length > 0 &&
    value.videos.length <= MAX_OPTION_COUNT
  ) {
    const videos = value.videos.map((item) => {
      if (!isRecord(item)) return null;
      const url = stringValue(item.url);
      const format = stringValue(item.format);
      const quality = item.quality === null ? null : stringValue(item.quality);
      return (
        url !== null &&
        isSafePublicPreviewRoute(url, taskId, 'video') &&
        isSafeString(format, MAX_FORMAT_LENGTH) &&
        (item.quality === null || (quality !== null && isSafePublicText(quality)))
      )
        ? { url, format, quality }
        : null;
    });
    if (!videos.every((item): item is NonNullable<typeof item> => item !== null)) return null;
    return { kind: 'video', videos };
  }

  if (
    value.kind === 'image_album' &&
    Array.isArray(value.images) &&
    value.images.length > 0 &&
    value.images.length <= MAX_OPTION_COUNT
  ) {
    const images = value.images.map((item) => {
      if (!isRecord(item)) return null;
      const url = stringValue(item.url);
      const format = stringValue(item.format);
      return url !== null && isSafePublicPreviewRoute(url, taskId, 'image') && isSafeString(format, MAX_FORMAT_LENGTH)
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
    value.live_photos.length <= MAX_OPTION_COUNT &&
    Array.isArray(value.warnings)
  ) {
    const livePhotos = value.live_photos.map((item) => {
      if (!isRecord(item)) return null;
      const imageUrl = stringValue(item.image_url);
      const motionUrl = item.motion_url === null ? null : stringValue(item.motion_url);
      return (
        imageUrl !== null &&
        isSafePublicPreviewRoute(imageUrl, taskId, 'live', 'image') &&
        (item.motion_url === null ||
          (motionUrl !== null && isSafePublicPreviewRoute(motionUrl, taskId, 'live', 'motion')))
      )
        ? { image_url: imageUrl, motion_url: motionUrl }
        : null;
    });
    if (!livePhotos.every((item): item is NonNullable<typeof item> => item !== null)) return null;
    if (value.warnings.length > MAX_OPTION_COUNT || !value.warnings.every(isSafePublicText)) return null;
    return {
      kind: 'live_photo',
      live_photos: livePhotos,
      warnings: [...value.warnings],
    };
  }

  return null;
}

/**
 * Normalize preview metadata and bind every public media route to the task
 * requested by the caller. Preview responses are untrusted wire data just
 * like parse responses; keeping this at the API boundary prevents renderers
 * from ever receiving a third-party media URL.
 */
export function normalizePreviewData(value: unknown, taskId: string): PreviewData | null {
  if (!isRecord(value) || !isSafeTaskId(taskId)) return null;
  if (value.task_id !== taskId) return null;

  const previewType = stringValue(value.preview_type);
  if (previewType !== 'video' && previewType !== 'image' && previewType !== 'live_photo' && previewType !== 'music') {
    return null;
  }
  const url = stringValue(value.url);
  const platform = stringValue(value.platform);
  if (url === null || !isSafeString(url, MAX_URL_LENGTH) || platform === null || !isSafeString(platform, MAX_LABEL_LENGTH)) {
    return null;
  }
  const title = value.title === null || value.title === undefined ? null : stringValue(value.title);
  if (title !== null && !isSafeString(title, MAX_LABEL_LENGTH)) return null;

  let cover: string | null = null;
  if (value.cover !== null && value.cover !== undefined) {
    const candidate = stringValue(value.cover);
    const isValid =
      candidate !== null &&
      ((previewType === 'image' && isSafePublicPreviewRoute(candidate, taskId, 'image')) ||
        (previewType === 'live_photo' && isSafePublicPreviewRoute(candidate, taskId, 'live', 'image')));
    if (isValid) cover = candidate;
  }

  const duration = value.duration === null || value.duration === undefined ? null : stringValue(value.duration);
  if (duration !== null && !isSafeString(duration, MAX_LABEL_LENGTH)) return null;
  const format = value.format === null || value.format === undefined ? null : stringValue(value.format);
  if (format !== null && !isSafeString(format, MAX_FORMAT_LENGTH)) return null;
  const fileSizeMb = value.file_size_mb;
  if (
    fileSizeMb !== null &&
    fileSizeMb !== undefined &&
    (typeof fileSizeMb !== 'number' || !Number.isFinite(fileSizeMb) || fileSizeMb < 0)
  ) {
    return null;
  }
  if (!isSafeStringArray(value.available_qualities, MAX_LABEL_LENGTH)) return null;
  if (!isSafeStringArray(value.available_bitrates, MAX_LABEL_LENGTH)) return null;

  const streams = Array.isArray(value.streams)
    ? value.streams.flatMap((stream) => {
        if (!isRecord(stream)) return [];
        const quality = stream.quality === null || stream.quality === undefined ? null : stringValue(stream.quality);
        const bitrate = stream.bitrate === null || stream.bitrate === undefined ? null : stringValue(stream.bitrate);
        const streamFormat = stream.format === null || stream.format === undefined ? null : stringValue(stream.format);
        if (
          (quality !== null && !isSafePublicText(quality)) ||
          (bitrate !== null && !isSafePublicText(bitrate)) ||
          (streamFormat !== null && !isSafeString(streamFormat, MAX_FORMAT_LENGTH))
        ) {
          return [];
        }
        return [{ quality, bitrate, format: streamFormat }];
      })
    : [];

  const manifest =
    value.manifest === null || value.manifest === undefined
      ? null
      : normalizePublicManifest(value.manifest, taskId, previewType);

  return {
    task_id: taskId,
    preview_type: previewType,
    url,
    platform,
    title,
    cover,
    duration,
    format,
    file_size_mb: fileSizeMb === undefined ? null : fileSizeMb,
    available_qualities: [...value.available_qualities],
    available_bitrates: [...value.available_bitrates],
    streams,
    manifest,
  };
}

/**
 * Normalize a backend parse result at the API boundary.
 *
 * Public manifests are copied only when they satisfy the wire contract. The
 * legacy URL-bearing compatibility fields are intentionally dropped so a
 * private CDN URL cannot reach renderer state or JSON serialization.
 */
export function normalizeParseResult(result: unknown): ParseResult | null {
  if (!isRecord(result)) return null;
  const taskId = result.task_id;
  const normalizedType = parseResultType(result.type);
  if (!isSafeTaskId(taskId) || !isSafeString(result.url, MAX_URL_LENGTH) || normalizedType === null) {
    return null;
  }
  const url = result.url;
  const platform = result.platform;
  const title = result.title;
  if (!isSafeString(platform, MAX_LABEL_LENGTH) || !isSafeString(title, MAX_LABEL_LENGTH)) return null;
  const cover = result.cover === null ? null : stringValue(result.cover);
  if (cover === null && result.cover !== null) return null;
  if (
    cover !== null &&
    !(
      (normalizedType === 'image' && isSafePublicPreviewRoute(cover, taskId, 'image')) ||
      (normalizedType === 'live_photo' && isSafePublicPreviewRoute(cover, taskId, 'live', 'image'))
    )
  ) {
    return null;
  }
  const duration = result.duration === null ? null : stringValue(result.duration);
  if (duration === null && result.duration !== null) return null;
  const fileSizeMb = result.file_size_mb;
  if (
    fileSizeMb !== null &&
    (typeof fileSizeMb !== 'number' || !Number.isFinite(fileSizeMb) || fileSizeMb < 0)
  ) {
    return null;
  }
  const format = result.format === null ? null : stringValue(result.format);
  if (format === null && result.format !== null) return null;
  const availableQualities = result.available_qualities;
  const availableBitrates = result.available_bitrates;
  if (!isSafeStringArray(availableQualities, MAX_LABEL_LENGTH)) return null;
  if (!isSafeStringArray(availableBitrates, MAX_LABEL_LENGTH)) return null;
  const manifest =
    result.manifest === null || result.manifest === undefined
      ? null
      : normalizePublicManifest(result.manifest, taskId, normalizedType);
  return {
    task_id: taskId,
    url,
    type: normalizedType,
    platform,
    title,
    cover,
    duration,
    file_size_mb: fileSizeMb,
    format,
    available_qualities: [...availableQualities],
    available_bitrates: [...availableBitrates],
    manifest,
  };
}

export const previewApi = {
  /** GET /api/preview/{task_id} → preview metadata + streams. */
  async getPreview(taskId: string): Promise<PreviewData> {
    const { data } = await apiClient.get<PreviewData>(`/preview/${taskId}`);
    const normalized = normalizePreviewData(data, taskId);
    if (!normalized) throw new Error('Invalid preview response');
    return normalized;
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
  async prepare(taskId: string, asset: AssetSelector, forceStaged = false): Promise<PreparedTransfer> {
    const { data } = await apiClient.post<PreparedTransfer>('/download/prepare', {
      task_id: taskId, asset, force_staged: forceStaged,
    });
    return data;
  },
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
    // Lyrics are an optional payload; request them for the music player while
    // keeping the legacy API shape unchanged for other callers.
    const { data } = await apiClient.get<MusicSearchResult>('/music/search', {
      params: { ...params, include_lyrics: true },
    });
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
