import { describe, expect, it } from 'vitest';
import { mediaApi } from './api';

describe('mediaApi', () => {
  // These exact strings pin the backend route contract
  // (/api/preview/{task_id}/stream, /images/{index}, /images.zip) — they are
  // same-origin paths, so resolveApiUrl returns them unchanged.
  it('builds the stream-proxy URL', () => {
    expect(mediaApi.streamUrl('t1')).toBe('/api/preview/t1/stream');
  });

  it('builds the per-image attachment URL', () => {
    expect(mediaApi.imageUrl('t1', 3)).toBe('/api/preview/t1/images/3');
  });

  it('builds the album-zip attachment URL', () => {
    expect(mediaApi.albumZipUrl('t1')).toBe('/api/preview/t1/images.zip');
  });
});
