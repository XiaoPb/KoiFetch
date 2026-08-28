import { useState } from 'react';
import { App, Button, Input, List, Modal, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { usePlaylistsStore } from './playlistsStore';
import type { MusicSong } from '../../types/music';

export interface AddToPlaylistModalProps {
  open: boolean;
  song: MusicSong | null;
  onClose: () => void;
}

/** 添加到歌单: create-or-select modal (P2). Adding is idempotent per song. */
export function AddToPlaylistModal({ open, song, onClose }: AddToPlaylistModalProps): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const playlists = usePlaylistsStore((state) => state.playlists);
  const create = usePlaylistsStore((state) => state.create);
  const addSong = usePlaylistsStore((state) => state.addSong);
  const [name, setName] = useState('');

  const handleCreate = () => {
    const created = create(name);
    if (!created) {
      void message.error(t('music.playlist.nameRequired'));
      return;
    }
    setName('');
    if (song) addSong(created.id, song);
    void message.success(t('music.playlist.created'));
    onClose();
  };

  const handlePick = (playlistId: string) => {
    if (!song) return;
    if (addSong(playlistId, song)) {
      const playlist = playlists.find((p) => p.id === playlistId);
      void message.success(t('music.playlist.added', { name: playlist?.name ?? '' }));
    } else {
      void message.info(t('music.playlist.addFailed'));
    }
    onClose();
  };

  return (
    <Modal open={open && song != null} onCancel={onClose} footer={null} title={t('music.addToPlaylist')} data-testid="add-to-playlist-modal">
      <List
        size="small"
        locale={{ emptyText: <Typography.Text type="secondary">{t('music.playlist.empty')}</Typography.Text> }}
        dataSource={playlists}
        renderItem={(playlist) => (
          <List.Item
            actions={[
              <Button key="add" size="small" type="link" onClick={() => handlePick(playlist.id)} data-testid={`pick-playlist-${playlist.id}`}>
                {t('music.addToPlaylist')}
              </Button>,
            ]}
          >
            <List.Item.Meta title={playlist.name} description={`${playlist.songs.length} ${t('music.songCount', { count: playlist.songs.length })}`} />
          </List.Item>
        )}
      />
      <div className="music-new-playlist">
        <Input
          placeholder={t('music.playlist.name')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          onPressEnter={handleCreate}
          data-testid="new-playlist-name"
        />
        <Button icon={<PlusOutlined />} onClick={handleCreate} data-testid="new-playlist-create">
          {t('music.playlist.new')}
        </Button>
      </div>
    </Modal>
  );
}
