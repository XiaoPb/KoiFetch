import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { ResultCard } from './ResultCard';
import { renderWithProviders } from '../../test/utils';
import type { ParseResult } from '../../types/api';

vi.mock('./VideoPlayer', () => ({
  VideoPlayer: ({ sources }: { sources: Array<{ url: string }> }) => (
    <div data-testid="mock-video-player" data-sources={sources.map((source) => source.url).join(',')} />
  ),
}));

vi.mock('./ImageCarousel', () => ({
  ImageCarousel: ({ images }: { images: string[] }) => (
    <div data-testid="mock-image-carousel" data-images={images.join(',')} />
  ),
  COVER_FALLBACK: 'data:image/svg+xml;utf8,fallback',
}));

const liveResult: ParseResult = {
  task_id: 'task-live',
  url: 'https://example.com/live',
  type: 'live_photo',
  platform: 'douyin',
  title: 'Live moment',
  cover: '/api/preview/task-live/resources/live/0/image',
  duration: null,
  file_size_mb: null,
  format: 'heic',
  available_qualities: [],
  available_bitrates: [],
  manifest: {
    kind: 'live_photo',
    live_photos: [
      {
        image_url: '/api/preview/task-live/resources/live/0/image',
        motion_url: '/api/preview/task-live/resources/live/0/motion',
      },
    ],
    warnings: [],
  },
};

describe('ResultCard Live Photo integration', () => {
  it('renders the manifest-backed viewer without exposing an upstream URL', () => {
    renderWithProviders(
      <ResultCard
        result={liveResult}
        downloading={false}
        onPreview={vi.fn()}
        onDownload={vi.fn()}
        onDownloadImage={vi.fn()}
        onDownloadAlbum={vi.fn()}
      />,
    );

    expect(screen.getByTestId('live-photo-task-live')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Live moment 1' })).toHaveAttribute(
      'src',
      '/api/preview/task-live/resources/live/0/image',
    );
    expect(document.body).not.toHaveTextContent('https://cdn.example.com');
  });

  it('falls back to the cover when the live-photo manifest is absent', () => {
    renderWithProviders(
      <ResultCard
        result={{ ...liveResult, manifest: null }}
        downloading={false}
        onPreview={vi.fn()}
        onDownload={vi.fn()}
        onDownloadImage={vi.fn()}
        onDownloadAlbum={vi.fn()}
      />,
    );

    expect(screen.queryByTestId('live-photo-task-live')).not.toBeInTheDocument();
    expect(screen.getByAltText('Live moment')).toBeInTheDocument();
  });
});

describe('ResultCard legacy URL isolation', () => {
  it('does not render raw legacy video or image URLs', () => {
    const legacyVideo: ParseResult = {
      task_id: 'legacy-video',
      url: 'https://example.com/video',
      type: 'video',
      platform: 'douyin',
      title: 'Legacy video',
      cover: '/api/preview/legacy-video/resources/image/0',
      duration: null,
      file_size_mb: null,
      format: 'mp4',
      available_qualities: [],
      available_bitrates: [],
      manifest: null,
      video_url: 'https://cdn.example/private-token-video.mp4?token=secret',
    };
    const legacyImage: ParseResult = {
      task_id: 'legacy-image',
      url: 'https://example.com/image',
      type: 'image',
      platform: 'xiaohongshu',
      title: 'Legacy image',
      cover: '/api/preview/legacy-image/resources/image/0',
      duration: null,
      file_size_mb: null,
      format: 'jpg',
      available_qualities: [],
      available_bitrates: [],
      manifest: null,
      images: ['https://cdn.example/private-token-image.jpg?token=secret'],
    };
    const props = {
      downloading: false,
      onPreview: vi.fn(),
      onDownload: vi.fn(),
      onDownloadImage: vi.fn(),
      onDownloadAlbum: vi.fn(),
    };

    const { rerender } = renderWithProviders(<ResultCard result={legacyVideo} {...props} />);
    expect(screen.queryByTestId('mock-video-player')).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('cdn.example');
    expect(document.body).not.toHaveTextContent('private-token');

    rerender(<ResultCard result={legacyImage} {...props} />);
    expect(screen.getByTestId('mock-image-carousel')).toHaveAttribute(
      'data-images',
      '/api/preview/legacy-image/resources/image/0',
    );
    expect(document.body).not.toHaveTextContent('cdn.example');
    expect(document.body).not.toHaveTextContent('private-token');

    rerender(
      <ResultCard
        result={{ ...legacyImage, cover: 'https://cdn.example/private-cover-token.jpg' }}
        {...props}
      />,
    );
    expect(screen.queryByTestId('mock-image-carousel')).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('cdn.example');
    expect(document.body).not.toHaveTextContent('private-cover-token');
  });
});

describe('ResultCard public route isolation', () => {
  it('does not render manifest resources or covers belonging to another task', () => {
    const props = {
      downloading: false,
      onPreview: vi.fn(),
      onDownload: vi.fn(),
      onDownloadImage: vi.fn(),
      onDownloadAlbum: vi.fn(),
    };
    const crossTaskVideo: ParseResult = {
      task_id: 'task-video', url: 'https://example.com/video', type: 'video', platform: 'douyin',
      title: 'Video', cover: '/api/preview/other-task/resources/image/0', duration: null,
      file_size_mb: null, format: 'mp4', available_qualities: [], available_bitrates: [],
      manifest: { kind: 'video', videos: [{ url: '/api/preview/other-task/resources/video/0', format: 'mp4', quality: null }] },
    };
    const crossTaskImage: ParseResult = {
      task_id: 'task-image', url: 'https://example.com/image', type: 'image', platform: 'douyin',
      title: 'Image', cover: '/api/preview/other-task/resources/image/0', duration: null,
      file_size_mb: null, format: 'jpg', available_qualities: [], available_bitrates: [],
      manifest: { kind: 'image_album', images: [{ url: '/api/preview/other-task/resources/image/0', format: 'jpg' }] },
    };
    const crossTaskLive: ParseResult = {
      task_id: 'task-live', url: 'https://example.com/live', type: 'live_photo', platform: 'douyin',
      title: 'Live', cover: '/api/preview/other-task/resources/live/0/image', duration: null,
      file_size_mb: null, format: 'heic', available_qualities: [], available_bitrates: [],
      manifest: {
        kind: 'live_photo',
        live_photos: [{ image_url: '/api/preview/other-task/resources/live/0/image', motion_url: '/api/preview/other-task/resources/live/0/motion' }],
        warnings: [],
      },
    };

    const { rerender } = renderWithProviders(<ResultCard result={crossTaskVideo} {...props} />);
    expect(screen.queryByTestId('mock-video-player')).not.toBeInTheDocument();
    rerender(<ResultCard result={crossTaskImage} {...props} />);
    expect(screen.queryByTestId('mock-image-carousel')).not.toBeInTheDocument();
    rerender(<ResultCard result={crossTaskLive} {...props} />);
    expect(screen.queryByTestId('live-photo-task-live')).not.toBeInTheDocument();
  });
});
