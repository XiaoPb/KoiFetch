import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Tag } from 'antd';
import {
  LeftOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';

export interface LivePhotoPair {
  image_url: string;
  motion_url: string | null;
}

export interface LivePhotoViewerProps {
  pairs: LivePhotoPair[];
  title: string;
  /** Stable identifier for integration tests and assistive-technology hooks. */
  testId?: string;
}

function resetVideo(video: HTMLVideoElement): void {
  try {
    video.pause();
  } catch {
    // Media methods can throw when a browser has already detached the element.
  }
  try {
    video.currentTime = 0;
  } catch {
    // Some media implementations do not allow seeking before metadata loads.
  }
}

/**
 * Inline Live Photo still/motion viewer. Motion is deliberately opt-in so a
 * result grid never starts media unexpectedly; only the active pair receives
 * a video element.
 */
export function LivePhotoViewer({ pairs, title, testId }: LivePhotoViewerProps): JSX.Element {
  const { t } = useTranslation();
  const [activeIndex, setActiveIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const activeIndexRef = useRef(activeIndex);
  const playingRef = useRef(playing);
  activeIndexRef.current = activeIndex;
  playingRef.current = playing;

  const stopVideo = useCallback(() => {
    if (videoRef.current) resetVideo(videoRef.current);
  }, []);

  const setVideoRef = useCallback(
    (video: HTMLVideoElement | null) => {
      if (video === null && videoRef.current) resetVideo(videoRef.current);
      videoRef.current = video;
    },
    [],
  );

  // A replaced manifest starts from its first still and never carries motion
  // playback across tasks or modal openings.
  useEffect(() => {
    stopVideo();
    setActiveIndex(0);
    setPlaying(false);
  }, [pairs, stopVideo]);

  // Ensure the current media is stopped if the card/modal is removed.
  useEffect(() => () => stopVideo(), [stopVideo]);

  const pair = pairs[activeIndex] ?? null;
  const hasNavigation = pairs.length > 1;
  const viewerId = testId ? `${testId}` : undefined;

  const changeIndex = (nextIndex: number) => {
    if (nextIndex < 0 || nextIndex >= pairs.length || nextIndex === activeIndex) return;
    stopVideo();
    setPlaying(false);
    setActiveIndex(nextIndex);
  };

  const togglePlayback = () => {
    if (!pair?.motion_url) return;
    if (playingRef.current) stopVideo();
    setPlaying((current) => !current);
  };

  const handleEnded = (event: React.SyntheticEvent<HTMLVideoElement>) => {
    // The identity and index checks protect a newly selected pair from a late
    // `ended` event queued by the previous element.
    if (event.currentTarget !== videoRef.current || activeIndexRef.current !== activeIndex || !playingRef.current) {
      return;
    }
    stopVideo();
    setPlaying(false);
  };

  const handleMediaFailure = (event: React.SyntheticEvent<HTMLVideoElement>) => {
    if (event.currentTarget !== videoRef.current || activeIndexRef.current !== activeIndex || !playingRef.current) {
      return;
    }
    stopVideo();
    setPlaying(false);
  };

  return (
    <div className="live-photo-viewer" data-testid={viewerId}>
      {pair ? (
        <>
          <div className="live-photo-media">
            <img
              src={pair.image_url}
              alt={`${title} ${activeIndex + 1}`}
              className="live-photo-still"
            />
            {playing && pair.motion_url && (
              <video
                ref={setVideoRef}
                src={pair.motion_url}
                key={`${activeIndex}:${pair.motion_url}`}
                autoPlay
                muted
                playsInline
                aria-label={`${title} ${activeIndex + 1} motion`}
                data-testid={viewerId ? `${viewerId}-motion` : undefined}
                className="live-photo-motion"
                onEnded={handleEnded}
                onError={handleMediaFailure}
                onAbort={handleMediaFailure}
              />
            )}
          </div>
          <div className="live-photo-controls">
            {hasNavigation && (
              <Button
                type="text"
                icon={<LeftOutlined />}
                aria-label={t('parser.previous')}
                onClick={() => changeIndex(activeIndex - 1)}
                disabled={activeIndex === 0}
              />
            )}
            {pair.motion_url && (
              <Button
                type="primary"
                icon={playing ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                aria-label={t(playing ? 'parser.pauseLivePhoto' : 'parser.playLivePhoto')}
                onClick={togglePlayback}
                data-testid={viewerId ? `${viewerId}-toggle` : undefined}
              >
                {t(playing ? 'parser.pauseLivePhoto' : 'parser.playLivePhoto')}
              </Button>
            )}
            {hasNavigation && (
              <Button
                type="text"
                icon={<RightOutlined />}
                aria-label={t('parser.next')}
                onClick={() => changeIndex(activeIndex + 1)}
                disabled={activeIndex === pairs.length - 1}
              />
            )}
            {hasNavigation && (
              <Tag className="live-photo-count">
                {activeIndex + 1} / {pairs.length}
              </Tag>
            )}
          </div>
        </>
      ) : (
        <div className="result-card-cover-empty" aria-label={title} />
      )}
    </div>
  );
}
