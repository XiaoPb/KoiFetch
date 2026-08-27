import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { MusicEmptyState } from './MusicEmptyState';

describe('MusicEmptyState', () => {
  it('shows the description and the hot-search section', () => {
    renderWithProviders(
      <MusicEmptyState description="没找到相关歌曲，试试其他关键词" hotKeywords={['周杰伦', '晴天']} onSearch={vi.fn()} />,
    );
    expect(screen.getByText('没找到相关歌曲，试试其他关键词')).toBeInTheDocument();
    expect(screen.getByText('热门搜索')).toBeInTheDocument();
    expect(screen.getByText('周杰伦')).toBeInTheDocument();
  });

  it('searches from a hot keyword click', async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    renderWithProviders(<MusicEmptyState description="x" hotKeywords={['周杰伦']} onSearch={onSearch} />);
    await user.click(screen.getByText('周杰伦'));
    expect(onSearch).toHaveBeenCalledWith('周杰伦');
  });

  it('searches from a hot keyword via the keyboard (Enter)', async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    renderWithProviders(<MusicEmptyState description="x" hotKeywords={['周杰伦', '晴天']} onSearch={onSearch} />);
    const tag = screen.getByText('周杰伦');
    tag.focus();
    await user.keyboard('{Enter}');
    expect(onSearch).toHaveBeenCalledWith('周杰伦');
  });
});
