import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { ResultCard } from './ResultCard';
import { renderWithProviders } from '../../test/utils';
import type { ParseResult } from '../../types/api';

vi.mock('./VideoPlayer', () => ({
  VideoPlayer: () => <div data-testid="mock-video-player" />,
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
