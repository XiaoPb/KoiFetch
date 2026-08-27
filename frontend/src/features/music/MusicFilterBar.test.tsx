import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { MusicFilterBar } from './MusicFilterBar';

describe('MusicFilterBar', () => {
  it('shows the total count for the active category', () => {
    renderWithProviders(<MusicFilterBar category="song" total={1235} onChange={vi.fn()} />);
    expect(screen.getByTestId('music-count')).toHaveTextContent('1,235');
    expect(screen.getByTestId('music-count')).toHaveTextContent('首单曲');
  });

  it('renders all five filter tabs', () => {
    renderWithProviders(<MusicFilterBar category="all" total={0} onChange={vi.fn()} />);
    expect(screen.getByText('综合')).toBeInTheDocument();
    expect(screen.getByText('单曲')).toBeInTheDocument();
    expect(screen.getByText('歌手')).toBeInTheDocument();
    expect(screen.getByText('专辑')).toBeInTheDocument();
    expect(screen.getByText('歌单')).toBeInTheDocument();
  });

  it('fires onChange with the clicked category', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<MusicFilterBar category="all" total={0} onChange={onChange} />);
    await user.click(screen.getByText('歌手'));
    expect(onChange).toHaveBeenCalledWith('artist');
  });
});
