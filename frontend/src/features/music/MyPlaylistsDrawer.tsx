import { useState } from 'react';
import { App, Button, Drawer, List, Popconfirm, Typography } from 'antd';
import { CaretRightOutlined, DeleteOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { usePlaylistsStore } from './playlistsStore';
import { useMusicStore } from './musicStore';

export interface MyPlaylistsDrawerProps {
  open: boolean;
  onClose: () => void;
}

/** 我的歌单 (P2): list local playlists; expand to manage songs (play/remove). */
export function MyPlaylistsDrawer({ open, onClose }: MyPlaylistsDrawerProps): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const playlists = usePlaylistsStore((state) => state.playlists);
  const removeSong = usePlaylistsStore((state) => state.removeSong);
  const deletePlaylist = usePlaylistsStore((state) => state.delete);
  const playSong = useMusicStore((state) => state.playSong);
  const [expanded, setExpanded] = useState<string | null>(null);

  const handlePlayAll = (id: string) => {
    const playlist = playlists.find((p) => p.id === id);
    if (!playlist || playlist.songs.length === 0) return;
    // Queue the whole playlist starting from its first song.
    useMusicStore.getState().playSong(playlist.songs[0]);
    playlist.songs.slice(1).forEach((song) => useMusicStore.getState().enqueueNext(song));
    onClose();
  };

  return (
    <Drawer open={open} onClose={onClose} title={t('music.myPlaylists')} width={400} data-testid="my-playlists-drawer">
      <List
        locale={{ emptyText: <Typography.Text type="secondary">{t('music.playlist.empty')}</Typography.Text> }}
        dataSource={playlists}
        renderItem={(playlist) => (
          <List.Item
            onClick={() => setExpanded((current) => (current === playlist.id ? null : playlist.id))}
            actions={[
              <Button key="play" size="small" type="link" icon={<CaretRightOutlined />} onClick={(event) => { event.stopPropagation(); handlePlayAll(playlist.id); }} data-testid={`playall-${playlist.id}`}>
                {t('music.playlist.playAll')}
              </Button>,
              <Popconfirm
                key="delete"
                title={t('music.playlist.delete')}
                onConfirm={(event) => {
                  event?.stopPropagation();
                  deletePlaylist(playlist.id);
                  void message.success(t('music.playlist.deleted'));
                }}
              >
                <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={t('music.playlist.delete')} onClick={(event) => event.stopPropagation()} data-testid={`delete-playlist-${playlist.id}`} />
              </Popconfirm>,
            ]}
          >
            <List.Item.Meta
              title={<Typography.Text strong>{playlist.name}</Typography.Text>}
              description={`${playlist.songs.length} ${t('music.songCount', { count: playlist.songs.length })}`}
            />
            {expanded === playlist.id && (
              <div className="music-playlist-songs" data-testid={`playlist-songs-${playlist.id}`}>
                {playlist.songs.length === 0 ? (
                  <Typography.Text type="secondary">{t('music.playlist.empty')}</Typography.Text>
                ) : (
                  playlist.songs.map((song) => (
                    <div key={song.id} className="music-detail-song" role="button" tabIndex={0} onClick={() => playSong(song)} data-testid={`playlist-song-${playlist.id}-${song.id}`}>
                      <Typography.Text ellipsis className="music-detail-song-title">{song.title}</Typography.Text>
                      <Button size="small" type="text" aria-label={t('music.playlist.removeSong')} onClick={(event) => { event.stopPropagation(); removeSong(playlist.id, song.id); }} data-testid={`remove-song-${playlist.id}-${song.id}`}>
                        {t('music.playlist.removeSong')}
                      </Button>
                    </div>
                  ))
                )}
              </div>
            )}
          </List.Item>
        )}
      />
    </Drawer>
  );
}
