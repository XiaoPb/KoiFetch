import { useMemo } from 'react';
import { Avatar, Button, Drawer, Image, Typography } from 'antd';
import { MoreOutlined, UserOutlined } from '@ant-design/icons';
import type { TranslationKey } from '../../services/i18n';
import { useTranslation } from '../../services/i18n';
import { COVER_FALLBACK } from './cover';
import { formatNumber } from './format';
import { useMusicStore } from './musicStore';

/**
 * Entity detail drawer (spec §3 View B/C interactions): antd `Drawer` anchored
 * to the LEFT so it slides in from the left like a detail page. Shows the
 * artist/album/playlist info; for artists and albums the drawer lists up to
 * five matching songs from the current result set, each playable.
 */
export function EntityDetailDrawer(): JSX.Element {
  const { t, language } = useTranslation();
  const entity = useMusicStore((state) => state.detailEntity);
  const songs = useMusicStore((state) => state.songs);
  const closeDetail = useMusicStore((state) => state.closeDetail);
  const playSong = useMusicStore((state) => state.playSong);
  const openActionSheet = useMusicStore((state) => state.openActionSheet);

  const artistSongs = useMemo(() => {
    if (!entity || entity.kind === 'playlist') return [];
    return songs
      .filter((song) => (entity.kind === 'artist' ? song.artist === entity.name : song.album === entity.title))
      .slice(0, 5);
  }, [entity, songs]);

  return (
    <Drawer
      placement="left"
      width={360}
      open={entity != null}
      onClose={closeDetail}
      title={entity ? t(`music.detail.${entity.kind}` as TranslationKey) : ''}
      className="music-detail-drawer"
      data-testid="music-detail-drawer"
    >
      {entity && (
        <div className="music-detail-body">
          {entity.kind === 'artist' && (
            <>
              <Avatar size={96} shape="circle" src={entity.avatar ?? undefined} icon={<UserOutlined />} />
              <Typography.Title level={4} className="music-detail-name">
                {entity.name}
              </Typography.Title>
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
              <Typography.Title level={4} className="music-detail-name">
                {entity.title}
              </Typography.Title>
              <Typography.Text type="secondary">
                {t('music.albumBy', { artist: entity.artist })} · {t('music.songCount', { count: formatNumber(entity.songCount, language) })}
              </Typography.Text>
            </>
          )}
          {entity.kind === 'playlist' && (
            <>
              <Image width={96} height={96} src={entity.cover ?? undefined} fallback={COVER_FALLBACK} preview={false} />
              <Typography.Title level={4} className="music-detail-name">
                {entity.title}
              </Typography.Title>
              <Typography.Text type="secondary">
                {t('music.playlistBy', { creator: entity.creator })} · {t('music.songCount', { count: formatNumber(entity.songCount, language) })}
              </Typography.Text>
            </>
          )}
          {entity.kind !== 'playlist' && (
            <div className="music-detail-songs">
              <Typography.Text strong>{t('music.detail.hotSongs')}</Typography.Text>
              {artistSongs.length === 0 ? (
                <Typography.Text type="secondary">{t('music.noSongsHint')}</Typography.Text>
              ) : (
                artistSongs.map((song) => (
                  <div
                    key={song.id}
                    role="button"
                    tabIndex={0}
                    className="music-detail-song"
                    onClick={() => playSong(song)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') playSong(song);
                    }}
                  >
                    <Typography.Text ellipsis className="music-detail-song-title">
                      {song.title}
                    </Typography.Text>
                    <Typography.Text type="secondary">{song.duration}</Typography.Text>
                    <Button
                      type="text"
                      size="small"
                      icon={<MoreOutlined />}
                      aria-label={t('music.more')}
                      onClick={(event) => {
                        event.stopPropagation();
                        openActionSheet(song);
                      }}
                      data-testid={`detail-more-${song.id}`}
                    />
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
