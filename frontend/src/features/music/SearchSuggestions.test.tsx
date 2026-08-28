import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/utils';
import { SearchSuggestions } from './SearchSuggestions';
import { useMusicStore } from './musicStore';
import { musicApi } from '../../services/api';

vi.mock('../../services/api', () => ({
  musicApi: { getHotKeywords: vi.fn(), search: vi.fn(), importSong: vi.fn() },
}));

describe('SearchSuggestions', () => {
  beforeEach(() => {
    vi.mocked(musicApi.getHotKeywords).mockReset();
    vi.mocked(musicApi.getHotKeywords).mockResolvedValue({ keywords: ['热1', '热2'] });
    useMusicStore.setState({ history: [] });
  });

  it('renders nothing when not visible', () => {
    renderWithProviders(<SearchSuggestions visible={false} onPick={vi.fn()} />);
    expect(screen.queryByTestId('music-suggestions')).not.toBeInTheDocument();
  });

  it('shows history and fetched hot keywords; picking a history term calls onPick', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    useMusicStore.setState({ history: ['晴天', '周杰伦'] });
    renderWithProviders(<SearchSuggestions visible onPick={onPick} />);
    expect(await screen.findByText('搜索历史')).toBeInTheDocument();
    expect(screen.getByText('热1')).toBeInTheDocument();
    await user.click(screen.getByTestId('history-晴天'));
    expect(onPick).toHaveBeenCalledWith('晴天');
  });

  it('clears history from the clear button', async () => {
    const user = userEvent.setup();
    useMusicStore.setState({ history: ['晴天'] });
    renderWithProviders(<SearchSuggestions visible onPick={vi.fn()} />);
    await user.click(screen.getByTestId('clear-history'));
    expect(useMusicStore.getState().history).toEqual([]);
  });
});
