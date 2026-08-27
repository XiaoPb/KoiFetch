import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { SongResultList } from './SongResultList';
import type { MusicSong } from '../../types/music';

const SONGS: MusicSong[] = [
  { kind: 'song', id: 's1', title: '晴天', artist: '周杰伦', album: '叶惠美', cover: null, duration: '04:29', play_url: null },
  { kind: 'song', id: 's2', title: '夜曲', artist: '周杰伦', album: '十一月的萧邦', cover: null, duration: '03:45', play_url: null },
];

function renderList(props: Partial<Parameters<typeof SongResultList>[0]> = {}): void {
  renderWithProviders(
    <SongResultList
      songs={SONGS}
      hasMore={false}
      loadingMore={false}
      onLoadMore={vi.fn()}
      onPlay={vi.fn()}
      onMore={vi.fn()}
      {...props}
    />,
  );
}

describe('SongResultList', () => {
  it('renders rank, title and artist · album for each song', () => {
    renderList();
    expect(screen.getByText('晴天')).toBeInTheDocument();
    expect(screen.getByText('周杰伦 · 叶惠美')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getAllByTestId('music-song-row')).toHaveLength(2);
  });

  it('plays on row click and on the play button', async () => {
    const user = userEvent.setup();
    const onPlay = vi.fn();
    renderList({ onPlay });
    await user.click(screen.getByText('晴天'));
    expect(onPlay).toHaveBeenCalledWith(SONGS[0]);
    await user.click(screen.getByTestId('play-s1'));
    expect(onPlay).toHaveBeenCalledTimes(2);
  });

  it('opens the action sheet from the more button without playing', async () => {
    const user = userEvent.setup();
    const onPlay = vi.fn();
    const onMore = vi.fn();
    renderList({ onPlay, onMore });
    await user.click(screen.getByTestId('more-s1'));
    expect(onMore).toHaveBeenCalledWith(SONGS[0]);
    expect(onPlay).not.toHaveBeenCalled();
  });

  it('shows the end hint when there is no more data', () => {
    renderList();
    expect(screen.getByTestId('music-end-hint')).toHaveTextContent('没有更多了');
  });

  it('shows the loading spinner while loading more', () => {
    renderList({ hasMore: true, loadingMore: true });
    expect(screen.getByTestId('music-loading-more')).toBeInTheDocument();
    expect(screen.queryByTestId('music-end-hint')).not.toBeInTheDocument();
  });
});
