import { App, Button, Drawer } from 'antd';
import { CaretRightOutlined, CloseOutlined, PlusOutlined, UserOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { artistFromName } from './musicSource';
import { useMusicStore } from './musicStore';

/**
 * Bottom Action Sheet (spec §3 View A interaction): antd `Drawer` anchored to
 * the bottom, sliding up with the song's action menu. v1 behaviors:
 * 下一首播放 plays the song immediately (no queue yet); 添加到歌单 acknowledges
 * (playlists are not persisted); 查看歌手 opens the artist detail drawer,
 * resolving the artist by name with a minimal fallback entity.
 */
export function SongActionSheet(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const song = useMusicStore((state) => state.actionSheetSong);
  const artists = useMusicStore((state) => state.artists);
  const closeActionSheet = useMusicStore((state) => state.closeActionSheet);
  const playSong = useMusicStore((state) => state.playSong);
  const openDetail = useMusicStore((state) => state.openDetail);

  const handleNext = () => {
    if (!song) return;
    playSong(song);
    closeActionSheet();
  };

  const handleAdd = () => {
    closeActionSheet();
    void message.success(t('music.addedToPlaylist'));
  };

  const handleViewArtist = () => {
    if (!song) return;
    const found = artists.find((artist) => artist.name === song.artist);
    openDetail(found ?? artistFromName(song.artist));
    closeActionSheet();
  };

  return (
    <Drawer
      placement="bottom"
      height="auto"
      open={song != null}
      onClose={closeActionSheet}
      title={song?.title}
      className="music-action-sheet"
      data-testid="music-action-sheet"
    >
      <div className="music-action-items">
        <Button block type="text" icon={<CaretRightOutlined />} onClick={handleNext} data-testid="action-next">
          {t('music.nextPlay')}
        </Button>
        <Button block type="text" icon={<PlusOutlined />} onClick={handleAdd} data-testid="action-add">
          {t('music.addToPlaylist')}
        </Button>
        <Button block type="text" icon={<UserOutlined />} onClick={handleViewArtist} data-testid="action-artist">
          {t('music.viewArtist')}
        </Button>
        <Button block type="text" icon={<CloseOutlined />} onClick={closeActionSheet} data-testid="action-cancel">
          {t('music.cancel')}
        </Button>
      </div>
    </Drawer>
  );
}
