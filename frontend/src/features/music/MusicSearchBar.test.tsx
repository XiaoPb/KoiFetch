import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test/utils';
import { MusicSearchBar } from './MusicSearchBar';

function renderBar(props: Partial<Parameters<typeof MusicSearchBar>[0]> = {}): void {
  renderWithProviders(
    <MusicSearchBar
      value="晴天"
      loading={false}
      onBack={vi.fn()}
      onChange={vi.fn()}
      onSearch={vi.fn()}
      {...props}
    />,
  );
}

describe('MusicSearchBar', () => {
  it('renders the back button, the input with the value, and the search button', () => {
    renderBar();
    expect(screen.getByTestId('music-back')).toBeInTheDocument();
    expect(screen.getByTestId('music-search-input')).toHaveValue('晴天');
    expect(screen.getByTestId('music-search-submit')).toHaveTextContent('搜索');
  });

  it('selects all text on focus', async () => {
    const user = userEvent.setup();
    renderBar();
    const input = screen.getByTestId('music-search-input') as HTMLInputElement;
    const selectSpy = vi.spyOn(input, 'select').mockImplementation(() => {});
    await user.click(input);
    expect(selectSpy).toHaveBeenCalled();
  });

  it('searches on Enter and on the submit button', async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    renderBar({ onSearch });
    await user.type(screen.getByTestId('music-search-input'), '{Enter}');
    expect(onSearch).toHaveBeenCalledTimes(1);
    await user.click(screen.getByTestId('music-search-submit'));
    expect(onSearch).toHaveBeenCalledTimes(2);
  });

  it('forwards input changes', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderBar({ onChange });
    // userEvent.type() clicks the input first, which focuses it and triggers the
    // select-all-on-focus behavior — so typing '歌' replaces the whole value
    // instead of appending to it (this matches real browser behavior too).
    await user.type(screen.getByTestId('music-search-input'), '歌');
    expect(onChange).toHaveBeenCalledWith('歌');
  });
});
