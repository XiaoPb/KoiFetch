import { useEffect, useMemo, useState } from 'react';
import { Button, Spin, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from '../../services/i18n';
import { useAppStore } from '../../stores/appStore';
import { musicApi } from '../../services/api';
import { EntityDetailView } from '../../features/music/entityViews';
import { useMusicStore } from '../../features/music/musicStore';
import { SongActionSheet } from '../../features/music/SongActionSheet';
import type { MusicEntity, MusicSong } from '../../types/music';

export interface MusicEntityPageProps {
  kind: 'artist' | 'album' | 'playlist';
}

/**
 * Detail route page for a search-derived entity (P2). The entity arrives via
 * navigation state; on a direct URL visit the page re-searches by the `name`
 * query parameter and rebuilds the entity + its songs from the results.
 */
export function MusicEntityPage({ kind }: MusicEntityPageProps): JSX.Element {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [entity, setEntity] = useState<MusicEntity | null>(null);
  const [name, setName] = useState('');
  const [songs, setSongs] = useState<MusicSong[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  // The music tab stays active in the topbar while a detail route is open.
  useEffect(() => {
    useAppStore.setState({ mediaMode: 'music' });
  }, []);

  // Derive the lookup name from navigation state or `?name=`; re-running on
  // location changes re-hydrates when navigating between detail pages.
  useEffect(() => {
    const stateEntity = (location.state as { entity?: MusicEntity } | null)?.entity;
    const next = stateEntity ? (stateEntity.kind === 'artist' ? stateEntity.name : stateEntity.title) : (searchParams.get('name') ?? '');
    setName(next);
    setEntity(null);
    setSongs([]);
  }, [location.state, searchParams]);

  // Hydration: ALWAYS fetch songs by name (the header may come from state);
  // on a direct visit the entity is rebuilt from the search results.
  useEffect(() => {
    if (!name) {
      setFailed(true);
      return;
    }
    setLoading(true);
    setFailed(false);
    void musicApi
      .search({ keyword: name, category: 'all', page: 1 })
      .then((result) => {
        if (kind === 'artist') {
          setEntity((current) =>
            current ??
            result.artists.find((a) => a.name === name) ??
            { kind: 'artist', id: `artist:${name}`, name, avatar: null, fans: 0, songCount: 0 },
          );
        } else if (kind === 'album') {
          setEntity((current) =>
            current ??
            result.albums.find((a) => a.title === name) ??
            { kind: 'album', id: `album:${name}`, title: name, artist: '', cover: null, songCount: 0 },
          );
        } else {
          setEntity((current) =>
            current ?? { kind: 'playlist', id: `playlist:${name}`, title: name, creator: '', cover: null, songCount: 0 },
          );
        }
        setSongs(result.songs);
      })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  }, [kind, name]);

  const entitySongs = useMemo(() => {
    if (kind === 'artist' && entity?.kind === 'artist') return songs.filter((s) => s.artist === entity.name);
    if (kind === 'album' && entity?.kind === 'album') return songs.filter((s) => s.album === entity.title);
    return [];
  }, [kind, entity, songs]);

  if (loading) {
    return <div className="music-detail-page"><Spin /></div>;
  }
  if (!entity || failed) {
    return (
      <div className="music-detail-page">
        <Typography.Text type="secondary">{t('music.noSongsHint')}</Typography.Text>
        <Button onClick={() => navigate('/')}>{t('music.back')}</Button>
      </div>
    );
  }

  const playSong = (song: MusicSong) => useMusicStore.getState().playSong(song);
  const onMore = (song: MusicSong) => useMusicStore.getState().openActionSheet(song);

  return (
    <div className="music-detail-page" data-testid="music-entity-page">
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} data-testid="detail-back">
        {t('music.back')}
      </Button>
      <EntityDetailView entity={entity} songs={entitySongs} onPlay={playSong} onMore={onMore} />
      <SongActionSheet />
    </div>
  );
}
