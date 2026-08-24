import { apiClient, resolveApiUrl } from './apiClient';
import type {
  HealthData,
  LoginData,
  LoginRequest,
  NasSaveData,
  ParseData,
  PreviewData,
  SubmitData,
  DownloadProgress,
} from '../types/api';

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
  /** GET /api/health → {status, services, storage_roots}. */
  async getHealth(): Promise<HealthData> {
    const { data } = await apiClient.get<HealthData>('/health');
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
