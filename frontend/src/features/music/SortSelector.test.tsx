import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/utils';
import { SortSelector } from './SortSelector';

describe('SortSelector', () => {
  it('shows the active sort and reports changes', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<SortSelector sort="comprehensive" onChange={onChange} />);
    expect(screen.getByText('综合')).toBeInTheDocument();
    await user.click(screen.getByTestId('sort-selector'));
    await user.click(screen.getByText('热度'));
    expect(onChange).toHaveBeenCalledWith('hot');
  });
});
