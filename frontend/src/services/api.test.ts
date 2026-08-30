import { describe, expect, it, vi, type Mock } from 'vitest';
import { apiClient } from './apiClient';
import {
  isSafePublicPreviewRoute,
  normalizeParseResult,
  normalizePreviewData,
  parseApi,
  previewApi,
} from './api';

vi.mock('./apiClient', () => ({
  apiClient: { post: vi.fn(), get: vi.fn() },
  resolveApiUrl: vi.fn((path: string) => path),
}));

const videoBase = {
  task_id: 'video-1',
  url: 'https://example.com/share/video-1',
  type: 'video',
  platform: 'douyin',
  title: 'Video',
  cover: null,
  duration: null,
  file_size_mb: null,
  format: 'mp4',
  available_qualities: [],
  available_bitrates: [],
  manifest: null,
};

const videoManifest = {
  kind: 'video',
  videos: [{ url: '/api/preview/video-1/resources/video/0', format: 'mp4', quality: null }],
};

describe('isSafePublicPreviewRoute', () => {
  it('accepts canonical routes bound to the requested task', () => {
    expect(isSafePublicPreviewRoute('/api/preview/task-1/resources/video/0', 'task-1', 'video')).toBe(true);
    expect(isSafePublicPreviewRoute('/api/preview/task-1/resources/live/0/motion', 'task-1', 'live', 'motion')).toBe(true);
  });

  it('rejects cross-task, wrong-kind, and malformed routes', () => {
    expect(isSafePublicPreviewRoute('/api/preview/other/resources/video/0', 'task-1', 'video')).toBe(false);
    expect(isSafePublicPreviewRoute('/api/preview/task-1/resources/image/0', 'task-1', 'video')).toBe(false);
    expect(isSafePublicPreviewRoute('/api/preview/task-1/resources/live/0/motion', 'task-1', 'live', 'image')).toBe(false);
    expect(isSafePublicPreviewRoute('/api/preview/task-1/resources/video/0?token=secret', 'task-1', 'video')).toBe(false);
  });
});

describe('normalizeParseResult', () => {
  it('retains a valid public live-photo manifest', () => {
    const result = normalizeParseResult({
      task_id: 'live-1',
      url: 'https://example.com/share/live-1',
      type: 'live_photo',
      platform: 'xiaohongshu',
      title: 'Live photo',
      cover: '/api/preview/live-1/resources/live/0/image',
      duration: null,
      file_size_mb: null,
      format: null,
      available_qualities: [],
      available_bitrates: [],
      manifest: {
        kind: 'live_photo',
        live_photos: [
          {
            image_url: '/api/preview/live-1/resources/live/0/image',
            motion_url: '/api/preview/live-1/resources/live/0/motion',
          },
        ],
        warnings: ['motion available'],
      },
    });

    expect(result).not.toBeNull();
    expect(result?.manifest).toEqual({
      kind: 'live_photo',
      live_photos: [
        {
          image_url: '/api/preview/live-1/resources/live/0/image',
          motion_url: '/api/preview/live-1/resources/live/0/motion',
        },
      ],
      warnings: ['motion available'],
    });
  });

  it('omits legacy upstream media fields from normalized JSON', () => {
    const result = normalizeParseResult({
      task_id: 'video-1',
      url: 'https://example.com/share/video-1',
      type: 'video',
      platform: 'douyin',
      title: 'Video',
      cover: null,
      duration: null,
      file_size_mb: null,
      format: 'mp4',
      available_qualities: [],
      available_bitrates: [],
      manifest: null,
      video_url: 'https://cdn.example/private.mp4',
      images: ['https://cdn.example/private.jpg'],
    });

    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain('cdn.example');
    expect(result).not.toHaveProperty('video_url');
    expect(result).not.toHaveProperty('images');
  });

  it('maps malformed or absent manifests to null', () => {
    const base = {
      task_id: 'legacy-1',
      url: 'https://example.com/share/legacy-1',
      type: 'video' as const,
      platform: 'example',
      title: 'Legacy',
      cover: null,
      duration: null,
      file_size_mb: null,
      format: null,
      available_qualities: [],
      available_bitrates: [],
    };

    expect(normalizeParseResult(base)?.manifest).toBeNull();
    expect(normalizeParseResult({ ...base, manifest: { kind: 'video', videos: [] } })?.manifest).toBeNull();
  });

  it.each([
    'https://cdn.example/private.mp4',
    '//cdn.example/private.mp4',
    '/api/preview/other/resources/video/0',
    '/api/preview/live-1/resources/image/0',
    '/api/preview/live-1/resources/video/../0',
    '/api/preview/live-1/resources/video/-1',
    '/api/preview/live-1/resources/video/0?token=secret',
    '/api/preview/live-1/resources/video/0#fragment',
    '\\api\\preview\\live-1\\resources\\video\\0',
  ])('rejects non-canonical public video route %s', (url) => {
    const result = normalizeParseResult({
      task_id: 'live-1',
      url: 'https://example.com/share/live-1',
      type: 'video',
      platform: 'douyin',
      title: 'Video',
      cover: null,
      duration: null,
      file_size_mb: null,
      format: 'mp4',
      available_qualities: [],
      available_bitrates: [],
      manifest: {
        kind: 'video',
        videos: [{ url, format: 'mp4', quality: null }],
      },
    });

    expect(result?.manifest).toBeNull();
  });

  it('rejects non-canonical live-photo resource routes while retaining valid routes', () => {
    const valid = normalizeParseResult({
      task_id: 'live-1',
      url: 'https://example.com/share/live-1',
      type: 'live_photo',
      platform: 'xiaohongshu',
      title: 'Live photo',
      cover: '/api/preview/live-1/resources/live/0/image',
      duration: null,
      file_size_mb: null,
      format: null,
      available_qualities: [],
      available_bitrates: [],
      manifest: {
        kind: 'live_photo',
        live_photos: [
          {
            image_url: '/api/preview/live-1/resources/live/0/image',
            motion_url: '/api/preview/live-1/resources/live/0/motion',
          },
        ],
        warnings: [],
      },
    });
    expect(valid).not.toBeNull();
    expect(valid?.manifest?.kind).toBe('live_photo');

    const invalid = normalizeParseResult({
      task_id: 'live-1',
      url: 'https://example.com/share/live-1',
      type: 'live_photo',
      platform: 'xiaohongshu',
      title: 'Live photo',
      cover: null,
      duration: null,
      file_size_mb: null,
      format: null,
      available_qualities: [],
      available_bitrates: [],
      manifest: {
        kind: 'live_photo',
        live_photos: [
          {
            image_url: '/api/preview/live-1/resources/live/0/motion',
            motion_url: '/api/preview/live-1/resources/live/0/image',
          },
        ],
        warnings: [],
      },
    });
    expect(invalid?.manifest).toBeNull();
  });

  it.each([
    null,
    undefined,
    42,
    { ...videoBase, task_id: 42 },
    { ...videoBase, url: 42 },
    { ...videoBase, type: 42 },
    { ...videoBase, platform: 42 },
    { ...videoBase, title: 42 },
    { ...videoBase, cover: 42 },
    { ...videoBase, duration: 42 },
    { ...videoBase, file_size_mb: -1 },
    { ...videoBase, file_size_mb: Number.POSITIVE_INFINITY },
    { ...videoBase, format: 42 },
    { ...videoBase, available_qualities: '1080p' },
    { ...videoBase, available_bitrates: [42] },
  ])('drops malformed parse result %j safely', (value) => {
    expect(normalizeParseResult(value)).toBeNull();
  });

  it.each(['video.1', 'video/1', 'video%2F1', 'video 1', '../video', 'video\\1', 'video\n1'])
    ('drops parse result with unsafe task ID %j', (taskId) => {
      expect(normalizeParseResult({ ...videoBase, task_id: taskId })).toBeNull();
    });

  it('uses an explicit safe allowlist while preserving the source share URL', () => {
    const result = normalizeParseResult({
      ...videoBase,
      manifest: videoManifest,
      extra: 'must not survive',
      metadata: { video_url: 'https://cdn.example/private.mp4' },
      video_url: 'https://cdn.example/private.mp4',
      images: ['https://cdn.example/private.jpg'],
    });

    expect(result).toEqual({ ...videoBase, manifest: videoManifest });
    expect(result?.url).toBe(videoBase.url);
  });

  it.each([
    '/api/preview/video-1/resources/video/0',
    '/api/preview/video-1/resources/image/0',
    '/api/preview/video-1/resources/live/0/image',
  ])('rejects a video cover unless it is null', (cover) => {
    expect(normalizeParseResult({ ...videoBase, cover })).toBeNull();
  });

  it('accepts only a same-task cover route appropriate to image/live results', () => {
    const image = normalizeParseResult({
      ...videoBase,
      task_id: 'image-1',
      type: 'image',
      cover: '/api/preview/image-1/resources/image/0',
      manifest: {
        kind: 'image_album',
        images: [{ url: '/api/preview/image-1/resources/image/0', format: 'jpg' }],
      },
    });
    const live = normalizeParseResult({
      ...videoBase,
      task_id: 'live-1',
      type: 'live_photo',
      cover: '/api/preview/live-1/resources/live/0/image',
      manifest: {
        kind: 'live_photo',
        live_photos: [{ image_url: '/api/preview/live-1/resources/live/0/image', motion_url: null }],
        warnings: [],
      },
    });
    expect(image).not.toBeNull();
    expect(live).not.toBeNull();
  });

  it.each([
    'https://cdn.example/private.mp4',
    '//cdn.example/private.mp4',
    'warning\ntext',
    'token=secret',
  ])('drops manifests with unsafe public text %j', (warning) => {
    const result = normalizeParseResult({
      ...videoBase,
      manifest: { kind: 'video', videos: [{ ...videoManifest.videos[0], quality: warning }] },
    });
    expect(result).not.toBeNull();
    expect(result?.manifest).toBeNull();
  });

  it('enforces result type and manifest kind consistency', () => {
    const mismatchedKind = normalizeParseResult({
      ...videoBase,
      manifest: {
        kind: 'image_album',
        images: [{ url: '/api/preview/video-1/resources/image/0', format: 'jpg' }],
      },
    });
    expect(mismatchedKind).not.toBeNull();
    expect(mismatchedKind?.manifest).toBeNull();
    expect(normalizeParseResult({ ...videoBase, type: 'music', manifest: videoManifest })?.manifest).toBeNull();
    expect(normalizeParseResult({ ...videoBase, type: 'music', manifest: null })).not.toBeNull();
  });

  it('drops malformed results at the parse API boundary', async () => {
    (apiClient.post as Mock).mockResolvedValue({
      data: {
        results: [
          null,
          { ...videoBase, task_id: 42 },
          { ...videoBase, manifest: videoManifest },
        ],
        failed: [],
      },
    });

    const data = await parseApi.parse(['https://example.com/share/video-1']);
    expect(data.results).toHaveLength(1);
    expect(data.results[0]?.task_id).toBe('video-1');
  });
});

describe('normalizePreviewData', () => {
  const valid = {
    task_id: 'live-1',
    preview_type: 'live_photo',
    url: 'https://example.com/live-1',
    platform: 'douyin',
    title: 'Live photo',
    cover: '/api/preview/live-1/resources/live/0/image',
    duration: null,
    format: 'heic',
    file_size_mb: 1,
    available_qualities: [],
    available_bitrates: [],
    streams: [],
    manifest: {
      kind: 'live_photo',
      live_photos: [
        {
          image_url: '/api/preview/live-1/resources/live/0/image',
          motion_url: '/api/preview/live-1/resources/live/0/motion',
        },
      ],
      warnings: [],
    },
  };

  it('retains valid same-task public routes and previewApi applies the normalizer', async () => {
    expect(normalizePreviewData(valid, 'live-1')).toMatchObject(valid);
    (apiClient.get as Mock).mockResolvedValue({ data: valid });

    await expect(previewApi.getPreview('live-1')).resolves.toMatchObject(valid);
  });

  it.each([
    {
      manifest: {
        ...valid.manifest,
        live_photos: [{ image_url: 'https://cdn.example/image.jpg', motion_url: 'https://cdn.example/motion.mov' }],
      },
    },
    {
      manifest: {
        ...valid.manifest,
        live_photos: [{ image_url: '/api/preview/other-task/resources/live/0/image', motion_url: null }],
      },
    },
  ])('drops an unsafe or cross-task manifest while preserving safe metadata', (unsafe) => {
    const normalized = normalizePreviewData({ ...valid, ...unsafe }, 'live-1');
    expect(normalized?.manifest).toBeNull();
    expect(normalized?.cover).toBe(valid.cover);
  });

  it.each(['https://cdn.example/cover.jpg', '//cdn.example/cover.jpg', '/api/preview/other-task/resources/live/0/image'])(
    'drops an unsafe or cross-task cover route %s',
    (cover) => {
      const normalized = normalizePreviewData({ ...valid, cover }, 'live-1');
      expect(normalized?.cover).toBeNull();
    },
  );

  it('rejects a preview payload whose task id differs from the requested task', () => {
    expect(normalizePreviewData({ ...valid, task_id: 'other-task' }, 'live-1')).toBeNull();
  });
});
