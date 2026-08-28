import { Avatar, Button, Image, Typography } from 'antd';
import { MoreOutlined, UserOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { COVER_FALLBACK } from './cover';
import { formatNumber } from './format';
import type { MusicEntity, MusicSong } from '../../types/music';

export interface EntityViewProps {
  entity: MusicEntity;
  /** Songs of the entity (artist/album: matching; playlist: remote = []). */
  songs: MusicSong[];
  onPlay: (song: MusicSong) => void;
  onMore: (song: MusicSong) => void;
}

/** Shared detail body for the entity route pages (upgraded from the drawer). */
export function EntityDetailView({ entity, songs, onPlay, onMore }: EntityViewProps): JSX.Element {
  const { t, language } = useTranslation();
  return (
    <div className="music-detail-body" data-testid="music-detail-body">
      {entity.kind === 'artist' && (
        <>
          <Avatar size={96} shape="circle" src={entity.avatar ?? undefined} icon={<UserOutlined />} />
          <Typography.Title level={4} className="music-detail-name">{entity.name}</Typography.Title>
          {(entity.fans > 0 || entity.songCount > 0) && (
            <Typography.Text type="secondary">
              {t('music.fans', { count: formatNumber(entity.fans, language) })} ·{' '}
              {t('music.hotSongsCount', { count: formatNumber(entity.songCount, language) })}
            </Typography.Text>
          )}
        </>
      )}
      {entity.kind === 'album' && (
        <>
          <Image width={96} height={96} src={entity.cover ?? undefined} fallback={COVER_FALLBACK} preview={false} />
          <Typography.Title level={4} className="music-detail-name">{entity.title}</Typography.Title>
          <Typography.Text type="secondary">
            {t('music.albumBy', { artist: entity.artist })} · {t('music.songCount', { count: formatNumber(entity.songCount, language) })}
          </Typography.Text>
        </>
      )}
      {entity.kind === 'playlist' && (
        <>
          <Image width={96} height={96} src={entity.cover ?? undefined} fallback={COVER_FALLBACK} preview={false} />
          <Typography.Title level={4} className="music-detail-name">{entity.title}</Typography.Title>
          <Typography.Text type="secondary">
            {t('music.playlistBy', { creator: entity.creator })} · {t('music.songCount', { count: formatNumber(entity.songCount, language) })}
          </Typography.Text>
        </>
      )}
      {entity.kind !== 'playlist' && (
        <div className="music-detail-songs">
          <Typography.Text strong>{t('music.detail.hotSongs')}</Typography.Text>
          {songs.length === 0 ? (
            <Typography.Text type="secondary">{t('music.noSongsHint')}</Typography.Text>
          ) : (
            songs.map((song) => (
              <div key={song.id} role="button" tabIndex={0} className="music-detail-song" onClick={() => onPlay(song)} onKeyDown={(event) => { if (event.key === 'Enter') onPlay(song); }}>
                <Typography.Text ellipsis className="music-detail-song-title">{song.title}</Typography.Text>
                <Typography.Text type="secondary">{song.duration}</Typography.Text>
                <Button type="text" size="small" icon={<MoreOutlined />} aria-label={t('music.more')} onClick={(event) => { event.stopPropagation(); onMore(song); }} data-testid={`detail-more-${song.id}`} />
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
