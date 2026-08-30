import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Typography } from 'antd';
import { FolderOpenOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from '../services/i18n';
import { isCategoryEmpty, useMusicStore } from '../features/music/musicStore';
import { HOT_KEYWORDS } from '../features/music/musicSource';
import { formatNumber } from '../features/music/format';
import { sortSongs } from '../features/music/sort';
import { MusicSearchBar } from '../features/music/MusicSearchBar';
import { MusicFilterBar } from '../features/music/MusicFilterBar';
import { SortSelector } from '../features/music/SortSelector';
import { SearchSuggestions } from '../features/music/SearchSuggestions';
import { SongResultList } from '../features/music/SongResultList';
import { ArtistResultList } from '../features/music/ArtistResultList';
import { AlbumPlaylistList } from '../features/music/AlbumPlaylistList';
import { MiniPlayer } from '../features/music/MiniPlayer';
import { SongActionSheet } from '../features/music/SongActionSheet';
import { MusicEmptyState } from '../features/music/MusicEmptyState';
import { MyPlaylistsDrawer } from '../features/music/MyPlaylistsDrawer';
import { LyricsPanel } from '../features/music/LyricsPanel';
import type { MusicCategory, MusicEntity } from '../types/music';
import '../styles/music.css';

/**
 * Music content area (one main page — the Topbar's 音乐 tab shows this inside
 * the shared shell, so there is no back arrow; the Topbar tab is the
 * navigation). A flex column filling the viewport below the Topbar:
 *
 *   [search row]  [thin loading bar when searching]  [centered stats + tabs]
 *   [scrollable result area]  [mini player]  [action sheet]  [detail drawer]
 *
 * Re-searching clears the list and jumps the scroll area back to the top; the
 * list view is keyed by category so switching tabs replays the fade-in
 * animation.
 */
export default function MusicSearchPage(): JSX.Element {
  const { t, language } = useTranslation();
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [playlistsOpen, setPlaylistsOpen] = useState(false);
  const [inputFocused, setInputFocused] = useState(false);

  const input = useMusicStore((state) => state.input);
  const keyword = useMusicStore((state) => state.keyword);
  const category = useMusicStore((state) => state.category);
  const status = useMusicStore((state) => state.status);
  const error = useMusicStore((state) => state.error);
  const totals = useMusicStore((state) => state.totals);
  const songs = useMusicStore((state) => state.songs);
  const artists = useMusicStore((state) => state.artists);
  const albums = useMusicStore((state) => state.albums);
  const playlists = useMusicStore((state) => state.playlists);
  const hasMore = useMusicStore((state) => state.hasMore);
  const loadingMore = useMusicStore((state) => state.loadingMore);
  const loadMoreError = useMusicStore((state) => state.loadMoreError);
  const sort = useMusicStore((state) => state.sort);
  const setInput = useMusicStore((state) => state.setInput);
  const search = useMusicStore((state) => state.search);
  const setCategory = useMusicStore((state) => state.setCategory);
  const loadMore = useMusicStore((state) => state.loadMore);
  const clearLoadMoreError = useMusicStore((state) => state.clearLoadMoreError);
  const setSort = useMusicStore((state) => state.setSort);
  const playSong = useMusicStore((state) => state.playSong);
  const openActionSheet = useMusicStore((state) => state.openActionSheet);

  // Detail navigation (P2): entity cards and 查看歌手 navigate to the routed
  // detail pages, carrying the entity via location state (name in `?name=`
  // covers direct visits).
  const openDetail = (entity: MusicEntity): void => {
    const name = entity.kind === 'artist' ? entity.name : entity.title;
    const route =
      entity.kind === 'artist'
        ? `/music/artist/${entity.id}`
        : entity.kind === 'album'
          ? `/music/album/${entity.id}`
          : `/music/playlist/${entity.id}`;
    navigate(`${route}?name=${encodeURIComponent(name)}`, { state: { entity } });
  };

  // New search or category switch → refresh from the top of the scroll area.
  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: 0 });
  }, [keyword, category]);

  const isLoading = status === 'loading';
  const isEmpty = status === 'success' && isCategoryEmpty(category, songs, artists, albums, playlists);

  const emptyDescription = (): string => {
    switch (category) {
      case 'artist': return t('music.emptyArtist');
      case 'album': return t('music.emptyAlbum');
      case 'playlist': return t('music.emptyPlaylist');
      default: return t('music.empty');
    }
  };

  // Totals of the categories NOT shown by the active view, for the hint.
  const otherCategories: Record<MusicCategory, MusicCategory[]> = {
    all: ['artist', 'album', 'playlist'],
    song: ['artist', 'album', 'playlist'],
    artist: ['song', 'album', 'playlist'],
    album: ['song', 'artist', 'playlist'],
    playlist: ['song', 'artist', 'album'],
  };
  const otherTotal = otherCategories[category].reduce((sum, key) => sum + (totals[key] ?? 0), 0);

  const handleEmptySearch = (hotKeyword: string) => {
    setInput(hotKeyword);
    void search();
  };

  // Client-side sorting (热度 = bitrate-desc proxy, documented in sort.ts).
  const visibleSongs = useMemo(() => sortSongs(songs, sort), [songs, sort]);

  return (
    <div className="music-page" data-testid="music-page">
      <div className="music-search-row">
        <MusicSearchBar
          value={input}
          loading={isLoading}
          onChange={setInput}
          onSearch={() => void search()}
          onFocusChange={setInputFocused}
        />
        <Button
          icon={<FolderOpenOutlined />}
          onClick={() => setPlaylistsOpen(true)}
          data-testid="open-my-playlists"
        >
          {t('music.myPlaylists')}
        </Button>
      </div>

      {keyword === '' && (
        <SearchSuggestions visible={inputFocused} onPick={handleEmptySearch} />
      )}

      {isLoading && <div className="music-loading-bar" data-testid="music-loading-bar" aria-hidden="true" />}

      {/* The filter bar stays mounted once a search has happened, so the
          active tab's underline never flickers while a category reloads. */}
      {keyword !== '' && (
        <div className="music-filter-row">
          <MusicFilterBar category={category} total={totals[category]} onChange={(value) => void setCategory(value)} />
          <SortSelector sort={sort} onChange={setSort} />
        </div>
      )}

      <div className="music-scroll-area" data-testid="music-scroll-area" ref={scrollRef}>
        {status === 'error' && (
          <Alert
            type="error"
            showIcon
            message={error}
            action={
              <Button size="small" onClick={() => void search()} data-testid="music-error-retry">
                {t('music.retry')}
              </Button>
            }
            data-testid="music-error"
          />
        )}

        {(status === 'idle' || isEmpty) && (
          <MusicEmptyState
            description={status === 'idle' ? t('music.initialHint') : emptyDescription()}
            hotKeywords={HOT_KEYWORDS}
            onSearch={handleEmptySearch}
          >
            {status === 'success' && isEmpty && otherTotal > 0 && (
              <Typography.Text type="secondary" data-testid="music-empty-others">
                {t('music.emptyOthers', { count: formatNumber(otherTotal, language) })}
              </Typography.Text>
            )}
          </MusicEmptyState>
        )}

        {status === 'success' && !isEmpty && (
          <div key={category} className="music-list-view" data-testid="music-list-view">
            {(category === 'all' || category === 'song') && (
              <SongResultList
                songs={visibleSongs}
                hasMore={hasMore}
                loadingMore={loadingMore}
                onLoadMore={() => void loadMore()}
                onPlay={playSong}
                onMore={openActionSheet}
              />
            )}
            {category === 'artist' && <ArtistResultList artists={artists} onOpenDetail={openDetail} />}
            {(category === 'album' || category === 'playlist') && (
              <AlbumPlaylistList category={category} albums={albums} playlists={playlists} onOpenDetail={openDetail} />
            )}
          </div>
        )}

        {status === 'success' && loadMoreError && (
          <Alert
            type="warning"
            showIcon
            message={loadMoreError}
            className="music-load-more-error"
            action={
              <Button
                size="small"
                onClick={() => {
                  clearLoadMoreError();
                  void loadMore();
                }}
                data-testid="music-load-more-retry"
              >
                {t('music.retry')}
              </Button>
            }
            data-testid="music-load-more-error"
          />
        )}
      </div>

      <MiniPlayer />
      <LyricsPanel />
      <SongActionSheet />
      <MyPlaylistsDrawer open={playlistsOpen} onClose={() => setPlaylistsOpen(false)} />
    </div>
  );
}
