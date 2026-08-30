import { describe, expect, it } from 'vitest';
import { normalizeParseResult } from './api';

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

    expect(result.manifest).toEqual({
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

    expect(normalizeParseResult(base).manifest).toBeNull();
    expect(normalizeParseResult({ ...base, manifest: { kind: 'video', videos: [] } }).manifest).toBeNull();
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

    expect(result.manifest).toBeNull();
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
    expect(valid.manifest?.kind).toBe('live_photo');

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
    expect(invalid.manifest).toBeNull();
  });
});
