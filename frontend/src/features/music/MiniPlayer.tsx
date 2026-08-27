import { useEffect } from 'react';
import { Button, Image, Typography } from 'antd';
import { CaretRightOutlined, CloseOutlined, PauseOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { COVER_FALLBACK } from './cover';
import { formatSeconds, parseDurationSeconds } from './format';
import { useMusicStore } from './musicStore';

/**
 * Bottom mini player (spec §3 View A interaction + §关键交互). v1 simulates
 * playback with a 1-second timer because mock songs carry `play_url: null`;
 * a real <audio> source replaces the timer when the backend plan lands. The
 * bar slides up with a CSS animation on mount.
 */
export function MiniPlayer(): JSX.Element | null {
  const { t } = useTranslation();
  const song = useMusicStore((state) => state.currentSong);
  const isPlaying = useMusicStore((state) => state.isPlaying);
  const currentTime = useMusicStore((state) => state.currentTime);
  const togglePlay = useMusicStore((state) => state.togglePlay);
  const updateProgress = useMusicStore((state) => state.updateProgress);
  const pause = useMusicStore((state) => state.pause);
  const closePlayer = useMusicStore((state) => state.closePlayer);

  // Read the live store position on every tick instead of a render-time ref:
  // store updates are batched inside test `act` scopes, so a ref would go
  // stale between ticks; getState() always sees the latest simulated time.
  const durationSeconds = song ? parseDurationSeconds(song.duration) : 0;

  const handleToggle = () => {
    if (!isPlaying && durationSeconds > 0 && currentTime >= durationSeconds) {
      // Replaying a finished song: restart from 0 (the first tick would
      // otherwise immediately re-pause at the end).
      updateProgress(0);
    }
    togglePlay();
  };

  useEffect(() => {
    if (!isPlaying || !song) return;
    const timer = setInterval(() => {
      const next = useMusicStore.getState().currentTime + 1;
      if (durationSeconds > 0 && next >= durationSeconds) {
        updateProgress(durationSeconds);
        pause();
      } else {
        updateProgress(next);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [isPlaying, song, durationSeconds, updateProgress, pause]);

  if (!song) return null;

  const percent = durationSeconds > 0 ? Math.min(100, (currentTime / durationSeconds) * 100) : 0;

  return (
    <div className="music-mini-player" data-testid="music-mini-player">
      <Image
        width={40}
        height={40}
        src={song.cover ?? undefined}
        fallback={COVER_FALLBACK}
        preview={false}
        className="music-mini-cover"
        alt=""
      />
      <div className="music-mini-info">
        <Typography.Text strong ellipsis className="music-mini-title">
          {song.title}
        </Typography.Text>
        <Typography.Text type="secondary" className="music-mini-meta">
          {song.artist}
        </Typography.Text>
      </div>
      <div
        className="music-mini-progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
        data-testid="music-mini-progress"
      >
        <div className="music-mini-progress-inner" style={{ width: `${percent}%` }} />
      </div>
      <Typography.Text type="secondary" className="music-mini-time">
        {formatSeconds(currentTime)} / {song.duration}
      </Typography.Text>
      <Button
        type="text"
        aria-label={isPlaying ? t('music.pause') : t('music.play')}
        icon={isPlaying ? <PauseOutlined /> : <CaretRightOutlined />}
        onClick={handleToggle}
        data-testid="mini-player-toggle"
      />
      <Button
        type="text"
        aria-label={t('music.cancel')}
        icon={<CloseOutlined />}
        onClick={closePlayer}
        data-testid="mini-player-close"
      />
    </div>
  );
}
