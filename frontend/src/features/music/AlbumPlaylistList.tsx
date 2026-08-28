import { Image, Typography } from 'antd';
import { RightOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import type { MusicAlbum, MusicEntity, MusicPlaylist } from '../../types/music';
import { COVER_FALLBACK } from './cover';
import { formatNumber } from './format';

export interface AlbumPlaylistListProps {
  category: 'album' | 'playlist';
  albums: MusicAlbum[];
  playlists: MusicPlaylist[];
  onOpenDetail: (entity: MusicEntity) => void;
}

/**
 * View C — 专辑/歌单 filter (spec §3 View C): a vertical list of horizontal
 * large cards, square cover on the left and title / publisher-or-creator /
 * song count on the right. Clicking a card opens the detail drawer.
 */
export function AlbumPlaylistList({ category, albums, playlists, onOpenDetail }: AlbumPlaylistListProps): JSX.Element {
  const { t, language } = useTranslation();
  // View C never renders artists, so narrow to albums | playlists — MusicEntity
  // also includes MusicArtist, which has no cover/title (strict TS build gate).
  const items: Array<MusicAlbum | MusicPlaylist> = category === 'album' ? albums : playlists;

  return (
    <div className="album-playlist-list" data-testid="album-playlist-list">
      {items.map((item) => (
        <div
          key={item.id}
          role="button"
          tabIndex={0}
          className="album-playlist-card"
          data-testid="album-playlist-card"
          onClick={() => onOpenDetail(item)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onOpenDetail(item);
          }}
        >
          <Image
            width={72}
            height={72}
            src={item.cover ?? undefined}
            fallback={COVER_FALLBACK}
            preview={false}
            className="album-playlist-cover"
            alt=""
          />
          <div className="album-playlist-info">
            <Typography.Text strong ellipsis={{ tooltip: item.title }} className="album-playlist-title">
              {item.title}
            </Typography.Text>
            <Typography.Text type="secondary" className="album-playlist-meta">
              {category === 'album'
                ? t('music.albumBy', { artist: (item as MusicAlbum).artist })
                : t('music.playlistBy', { creator: (item as MusicPlaylist).creator })}
              {' · '}
              {t('music.songCount', { count: formatNumber(item.songCount, language) })}
            </Typography.Text>
          </div>
          <RightOutlined className="album-playlist-chevron" />
        </div>
      ))}
    </div>
  );
}
