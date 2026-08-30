import { useEffect, useMemo, useRef } from 'react';
import { Empty, Typography } from 'antd';
import { useMusicStore } from './musicStore';
import { activeLyricIndex, parseLyrics } from './lyrics';

export function LyricsPanel(): JSX.Element | null {
  const song = useMusicStore((state) => state.currentSong);
  const currentTime = useMusicStore((state) => state.currentTime);
  const activeRef = useRef<HTMLDivElement>(null);
  const lines = useMemo(() => parseLyrics(song?.lyric), [song?.lyric]);
  const active = activeLyricIndex(lines, currentTime);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [active]);

  if (!song) return null;
  return (
    <section className="music-lyrics-panel" data-testid="music-lyrics-panel" aria-label="歌词">
      <Typography.Text strong>{song.title} · 歌词</Typography.Text>
      {lines.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无歌词" />
      ) : (
        <div className="music-lyrics-scroll" data-testid="music-lyrics-scroll">
          {lines.map((line, index) => (
            <div
              key={`${line.time}-${index}`}
              ref={index === active ? activeRef : undefined}
              className={index === active ? 'music-lyric-line active' : 'music-lyric-line'}
              data-testid={`music-lyric-${index}`}
            >
              {line.text || '♪'}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
