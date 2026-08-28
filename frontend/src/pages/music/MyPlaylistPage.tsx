import { useEffect } from 'react';
import { Button, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from '../../services/i18n';
import { useAppStore } from '../../stores/appStore';
import { usePlaylistsStore } from '../../features/music/playlistsStore';
import { useMusicStore } from '../../features/music/musicStore';

/** Route page for a user-created (local) playlist (P2). */
export function MyPlaylistPage(): JSX.Element {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams();
  const playlist = usePlaylistsStore((state) => state.playlists.find((p) => p.id === id));
  const removeSong = usePlaylistsStore((state) => state.removeSong);
  const playSong = useMusicStore((state) => state.playSong);

  useEffect(() => {
    useAppStore.setState({ mediaMode: 'music' });
  }, []);

  if (!playlist) {
    return (
      <div className="music-detail-page">
        <Typography.Text type="secondary">{t('music.playlist.empty')}</Typography.Text>
        <Button onClick={() => navigate('/')}>{t('music.back')}</Button>
      </div>
    );
  }

  return (
    <div className="music-detail-page" data-testid="my-playlist-page">
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} data-testid="detail-back">
        {t('music.back')}
      </Button>
      <div className="music-detail-body">
        <Typography.Title level={4} className="music-detail-name">{playlist.name}</Typography.Title>
        <Typography.Text type="secondary">{playlist.songs.length} {t('music.songCount', { count: playlist.songs.length })}</Typography.Text>
        <div className="music-detail-songs">
          {playlist.songs.length === 0 ? (
            <Typography.Text type="secondary">{t('music.playlist.empty')}</Typography.Text>
          ) : (
            playlist.songs.map((song) => (
              <div key={song.id} role="button" tabIndex={0} className="music-detail-song" onClick={() => playSong(song)} data-testid={`song-${song.id}`}>
                <Typography.Text ellipsis className="music-detail-song-title">{song.title}</Typography.Text>
                <Typography.Text type="secondary">{song.duration}</Typography.Text>
                <Button size="small" type="text" onClick={(event) => { event.stopPropagation(); removeSong(playlist.id, song.id); }} data-testid={`remove-${song.id}`}>
                  {t('music.playlist.removeSong')}
                </Button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
