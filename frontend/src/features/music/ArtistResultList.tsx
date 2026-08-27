import { Avatar, Typography } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import type { MusicArtist } from '../../types/music';

export interface ArtistResultListProps {
  artists: MusicArtist[];
  onOpenDetail: (artist: MusicArtist) => void;
}

/**
 * View B — 歌手 filter (spec §3 View B): a horizontally scrollable row of
 * large circular-avatar cards with the name centered and a fan/hot-song meta
 * line underneath. Clicking a card opens the artist detail drawer.
 */
export function ArtistResultList({ artists, onOpenDetail }: ArtistResultListProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="artist-result-list" data-testid="artist-result-list">
      {artists.map((artist) => (
        <div
          key={artist.id}
          role="button"
          tabIndex={0}
          className="artist-card"
          data-testid="artist-card"
          onClick={() => onOpenDetail(artist)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onOpenDetail(artist);
          }}
        >
          <Avatar size={96} shape="circle" src={artist.avatar ?? undefined} icon={<UserOutlined />} className="artist-avatar" />
          <Typography.Text strong ellipsis className="artist-name">
            {artist.name}
          </Typography.Text>
          <Typography.Text type="secondary" className="artist-meta">
            {t('music.fans', { count: artist.fans.toLocaleString() })} ·{' '}
            {t('music.hotSongsCount', { count: artist.songCount })}
          </Typography.Text>
        </div>
      ))}
    </div>
  );
}
