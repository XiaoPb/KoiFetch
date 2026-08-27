import { useEffect, useRef } from 'react';
import { Button, Image, Typography } from 'antd';
import { CaretRightOutlined, CloseOutlined, PauseOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { COVER_FALLBACK } from './cover';
import { formatSeconds, parseDurationSeconds } from './format';
import { useMusicStore } from './musicStore';

/**
 * Bottom mini player (spec §3 View A interaction + §关键交互). The bar slides
 * up with a CSS animation on mount.
 *
 * Playback has two paths:
 * 1. REAL audio — when `song.play_url` exists (mock songs now carry a tone
 *    WAV; the future backend supplies real URLs), a hidden <audio> element
 *    plays it: `timeupdate` drives the progress bar, `ended` stops at the end,
 *    and replay restarts from 0.
 * 2. SIMULATED fallback — when `play_url` is null (e.g. stub mode), a 1-second
 *    timer advances the progress. Reads the live store position on every tick
 *    instead of a render-time ref: store updates are batched inside test `act`
 *    scopes, so a ref would go stale between ticks; getState() always sees the
 *    latest simulated time.
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

  const audioRef = useRef<HTMLAudioElement>(null);
  const durationSeconds = song ? parseDurationSeconds(song.duration) : 0;
  const hasAudio = Boolean(song?.play_url);

  const handleToggle = () => {
    if (!isPlaying) {
      const audio = audioRef.current;
      if (hasAudio && audio) {
        // Replaying a finished (or any) song: restart the audio from 0.
        audio.currentTime = 0;
      } else if (durationSeconds > 0 && currentTime >= durationSeconds) {
        // Simulated path: a finished song would otherwise immediately re-pause.
        updateProgress(0);
      }
    }
    togglePlay();
  };

  // Simulated playback (only when the song has no playable source).
  useEffect(() => {
    if (hasAudio || !isPlaying || !song) return;
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
  }, [hasAudio, isPlaying, song, durationSeconds, updateProgress, pause]);

  // Real playback: keep the hidden <audio> element in sync with the store.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !hasAudio) return;
    if (isPlaying) {
      // Browsers return a promise (rejecting on autoplay blocks); jsdom
      // returns undefined — guard both.
      const playResult = audio.play();
      if (playResult) void playResult.catch(() => pause());
    } else {
      audio.pause();
    }
  }, [isPlaying, hasAudio, song, pause]);

  if (!song) return null;

  const percent = durationSeconds > 0 ? Math.min(100, (currentTime / durationSeconds) * 100) : 0;

  return (
    <div className="music-mini-player" data-testid="music-mini-player">
      <audio
        ref={audioRef}
        src={song.play_url ?? undefined}
        preload="none"
        onTimeUpdate={(event) => updateProgress(event.currentTarget.currentTime)}
        onEnded={() => {
          updateProgress(durationSeconds);
          pause();
        }}
        data-testid="mini-audio"
      />
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
