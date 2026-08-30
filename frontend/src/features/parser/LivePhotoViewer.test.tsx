import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LivePhotoViewer } from './LivePhotoViewer';
import { renderWithProviders } from '../../test/utils';

const pairs = [
  { image_url: '/api/preview/task/resources/live/0/image', motion_url: '/api/preview/task/resources/live/0/motion' },
  { image_url: '/api/preview/task/resources/live/1/image', motion_url: '/api/preview/task/resources/live/1/motion' },
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('LivePhotoViewer', () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  });

  it('starts with the still and only renders autoplay motion after play is requested', async () => {
    const user = userEvent.setup();
    renderWithProviders(<LivePhotoViewer pairs={[pairs[0]]} title="Moment" testId="live-photo-task" />);

    expect(screen.getByRole('img', { name: 'Moment 1' })).toHaveAttribute('src', pairs[0].image_url);
    expect(screen.queryByRole('video')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '播放实况' }));

    const video = document.querySelector('video') as HTMLVideoElement;
    expect(video).toHaveAttribute('src', pairs[0].motion_url);
    expect(video).toHaveAttribute('autoplay');
    expect(video).toHaveProperty('muted', true);
    expect(video).toHaveAttribute('playsinline');
    expect(screen.getByRole('button', { name: '暂停实况' })).toBeInTheDocument();
  });

  it('does not render a play button for an unpaired still', () => {
    renderWithProviders(
      <LivePhotoViewer
        pairs={[{ image_url: pairs[0].image_url, motion_url: null }]}
        title="Still"
        testId="live-photo-still"
      />,
    );

    expect(screen.getByRole('img', { name: 'Still 1' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '播放实况' })).not.toBeInTheDocument();
  });

  it('pauses and resumes the motion with the localized toggle', async () => {
    const user = userEvent.setup();
    renderWithProviders(<LivePhotoViewer pairs={[pairs[0]]} title="Moment" />);
    await user.click(screen.getByRole('button', { name: '播放实况' }));

    const video = document.querySelector('video') as HTMLVideoElement;
    const pause = vi.spyOn(video, 'pause').mockImplementation(() => undefined);
    await user.click(screen.getByRole('button', { name: '暂停实况' }));

    expect(pause).toHaveBeenCalled();
    expect(screen.queryByRole('video')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '播放实况' })).toBeInTheDocument();
  });

  it('returns to the still when motion ends', async () => {
    const user = userEvent.setup();
    renderWithProviders(<LivePhotoViewer pairs={[pairs[0]]} title="Moment" />);
    await user.click(screen.getByRole('button', { name: '播放实况' }));

    fireEvent.ended(document.querySelector('video') as HTMLVideoElement);

    expect(screen.queryByRole('video')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '播放实况' })).toBeInTheDocument();
  });

  it('pauses and resets the old motion when changing index', async () => {
    const user = userEvent.setup();
    renderWithProviders(<LivePhotoViewer pairs={pairs} title="Moment" testId="live-photo-many" />);
    await user.click(screen.getByRole('button', { name: '播放实况' }));
    const firstVideo = document.querySelector('video') as HTMLVideoElement;
    const pause = vi.spyOn(firstVideo, 'pause').mockImplementation(() => undefined);
    Object.defineProperty(firstVideo, 'currentTime', { configurable: true, writable: true, value: 12 });

    await user.click(screen.getByRole('button', { name: /下一张|Next/ }));

    expect(pause).toHaveBeenCalled();
    expect(firstVideo.currentTime).toBe(0);
    expect(screen.getByRole('img', { name: 'Moment 2' })).toBeInTheDocument();
    expect(screen.queryByRole('video')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '播放实况' })).toBeInTheDocument();
  });

  it('ignores an ended event from a stale video after changing index', async () => {
    const user = userEvent.setup();
    renderWithProviders(<LivePhotoViewer pairs={pairs} title="Moment" />);
    await user.click(screen.getByRole('button', { name: '播放实况' }));
    const oldVideo = document.querySelector('video') as HTMLVideoElement;
    await user.click(screen.getByRole('button', { name: /下一张|Next/ }));

    act(() => {
      fireEvent.ended(oldVideo);
    });

    expect(screen.getByRole('img', { name: 'Moment 2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '播放实况' })).toBeInTheDocument();
  });

  it('cleans up the active video on unmount', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<LivePhotoViewer pairs={[pairs[0]]} title="Moment" />);
    await user.click(screen.getByRole('button', { name: '播放实况' }));
    const video = document.querySelector('video') as HTMLVideoElement;
    const pause = vi.spyOn(video, 'pause').mockImplementation(() => undefined);
    Object.defineProperty(video, 'currentTime', { configurable: true, writable: true, value: 4 });

    unmount();

    expect(pause).toHaveBeenCalled();
    expect(video.currentTime).toBe(0);
  });
});
