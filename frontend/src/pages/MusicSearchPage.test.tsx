import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { renderWithProviders } from '../test/utils';
import MusicSearchPage from './MusicSearchPage';
import { useMusicStore } from '../features/music/musicStore';

describe('MusicSearchPage', () => {
  beforeEach(() => {
    useMusicStore.getState().reset();
  });

  it('renders the top search bar and the idle empty state', () => {
    renderWithProviders(<MusicSearchPage />, { route: '/music' });
    expect(screen.getByTestId('music-search-input')).toBeInTheDocument();
    expect(screen.getByTestId('music-empty')).toBeInTheDocument();
    expect(screen.getByText('热门搜索')).toBeInTheDocument();
  });

  it('searches and renders the song list with the result count', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/music' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    expect(await screen.findByTestId('music-song-list')).toBeInTheDocument();
    expect(screen.getByTestId('music-count')).toHaveTextContent('约');
    expect(screen.getAllByTestId('music-song-row').length).toBeGreaterThan(0);
  });

  it('switches to the artist view via the filter tab', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/music' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    await user.click(screen.getByText('歌手'));
    expect(await screen.findByTestId('artist-result-list')).toBeInTheDocument();
    expect(screen.getByTestId('music-count')).toHaveTextContent('位歌手');
  });

  it('shows the empty state with hot keywords when there are no results', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/music' });
    await user.type(screen.getByTestId('music-search-input'), '一二三四五六七八九十X');
    await user.click(screen.getByTestId('music-search-submit'));
    expect(await screen.findByTestId('music-empty')).toBeInTheDocument();
    expect(screen.getByText('没找到相关歌曲，试试其他关键词')).toBeInTheDocument();
  });

  it('opens the mini player when a song row is clicked', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/music' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    await user.click(screen.getAllByTestId('music-song-row')[0]);
    expect(await screen.findByTestId('music-mini-player')).toBeInTheDocument();
  });

  it('opens the action sheet from the more button', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MusicSearchPage />, { route: '/music' });
    await user.type(screen.getByTestId('music-search-input'), '周杰伦');
    await user.click(screen.getByTestId('music-search-submit'));
    await screen.findByTestId('music-song-list');
    const moreButtons = screen.getAllByTestId(/^more-/);
    await user.click(moreButtons[0]);
    expect(await screen.findByTestId('music-action-sheet')).toBeInTheDocument();
    expect(screen.getByText('下一首播放')).toBeInTheDocument();
  });
});
