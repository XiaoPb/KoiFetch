import { Button, Image, Spin, Typography } from 'antd';
import { CaretRightOutlined, MoreOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import type { MusicSong } from '../../types/music';
import { COVER_FALLBACK } from './cover';
import { useInfiniteScroll } from './useInfiniteScroll';

export interface SongResultListProps {
  songs: MusicSong[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onPlay: (song: MusicSong) => void;
  onMore: (song: MusicSong) => void;
}

/**
 * View A — the default 综合/单曲 song list (spec §3 View A). Fixed-height rows:
 * gray rank number, 40x40 rounded cover, bold ellipsized title over a gray
 * "artist · album" line, then a play icon and a more icon. Clicking a row or
 * the play icon plays the song; the more icon opens the Action Sheet. A
 * sentinel at the end drives the infinite scroll; the bottom shows a small
 * spinner while loading more and an end hint when exhausted.
 */
export function SongResultList({ songs, hasMore, loadingMore, onLoadMore, onPlay, onMore }: SongResultListProps): JSX.Element {
  const { t } = useTranslation();
  const { sentinelRef } = useInfiniteScroll(onLoadMore, { hasMore, loading: loadingMore });

  return (
    <div className="music-song-list" role="list" data-testid="music-song-list">
      {songs.map((song, index) => (
        <div
          role="listitem"
          key={song.id}
          className="music-song-row"
          data-testid="music-song-row"
          onClick={() => onPlay(song)}
        >
          <span className="music-song-rank">{index + 1}</span>
          <Image
            width={40}
            height={40}
            src={song.cover ?? undefined}
            fallback={COVER_FALLBACK}
            preview={false}
            className="music-song-cover"
            alt=""
          />
          <div className="music-song-info">
            <Typography.Text strong ellipsis={{ tooltip: song.title }} className="music-song-title">
              {song.title}
            </Typography.Text>
            <Typography.Text type="secondary" className="music-song-meta">
              {t('music.byArtist', { artist: song.artist, album: song.album })}
            </Typography.Text>
          </div>
          <Button
            type="text"
            icon={<CaretRightOutlined />}
            aria-label={t('music.play')}
            onClick={(event) => {
              event.stopPropagation();
              onPlay(song);
            }}
            data-testid={`play-${song.id}`}
          />
          <Button
            type="text"
            icon={<MoreOutlined />}
            aria-label={t('music.more')}
            onClick={(event) => {
              event.stopPropagation();
              onMore(song);
            }}
            data-testid={`more-${song.id}`}
          />
        </div>
      ))}
      <div ref={sentinelRef} className="music-list-sentinel" aria-hidden="true" />
      {loadingMore && (
        <div className="music-loading-more" data-testid="music-loading-more">
          <Spin size="small" />
        </div>
      )}
      {!hasMore && songs.length > 0 && (
        <Typography.Text type="secondary" className="music-end-hint" data-testid="music-end-hint">
          {t('music.noMore')}
        </Typography.Text>
      )}
    </div>
  );
}
