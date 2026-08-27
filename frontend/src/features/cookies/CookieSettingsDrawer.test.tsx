import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CookieSettingsDrawer } from './CookieSettingsDrawer';
import { renderWithProviders } from '../../test/utils';
import { cookieApi } from '../../services/api';
import { useCookieStore } from './cookieStore';

vi.mock('../../services/api', () => ({
  cookieApi: {
    list: vi.fn(),
    set: vi.fn(),
    remove: vi.fn(),
  },
}));

describe('CookieSettingsDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useCookieStore.setState({
      entries: [],
      drawerOpen: true,
      loading: false,
      error: null,
    });
  });

  afterEach(() => {
    useCookieStore.setState({ drawerOpen: false });
  });

  it('renders a row per f2 platform', () => {
    renderWithProviders(<CookieSettingsDrawer />);
    expect(screen.getByTestId('cookie-row-douyin')).toBeInTheDocument();
    expect(screen.getByTestId('cookie-row-weibo')).toBeInTheDocument();
    expect(screen.getByTestId('cookie-row-tiktok')).toBeInTheDocument();
  });

  it('shows configured tags from the store entries', () => {
    useCookieStore.setState({
      entries: [{ platform: 'douyin', configured: true, updated_at: '2026-08-26T00:00:00+00:00' }],
    });
    renderWithProviders(<CookieSettingsDrawer />);
    const row = within(screen.getByTestId('cookie-row-douyin'));
    expect(row.getByText('已配置')).toBeInTheDocument();
    expect(row.getByText('2026-08-26T00:00:00+00:00')).toBeInTheDocument();
    expect(within(screen.getByTestId('cookie-row-weibo')).getByText('未配置')).toBeInTheDocument();
  });

  it('saves the typed cookie for a platform', async () => {
    (cookieApi.set as Mock).mockResolvedValue({ platform: 'douyin', configured: true, updated_at: null });
    (cookieApi.list as Mock).mockResolvedValue({ cookies: [] });
    const user = userEvent.setup();
    renderWithProviders(<CookieSettingsDrawer />);

    await user.type(screen.getByTestId('cookie-input-douyin'), 'sessionid=abc');
    await user.click(screen.getByTestId('cookie-save-douyin'));

    await waitFor(() => expect(cookieApi.set).toHaveBeenCalledWith('douyin', 'sessionid=abc'));
    expect(await screen.findByText('Cookie 已保存')).toBeInTheDocument();
  });

  it('clears a configured cookie and toasts', async () => {
    (cookieApi.remove as Mock).mockResolvedValue(undefined);
    (cookieApi.list as Mock).mockResolvedValue({ cookies: [] });
    useCookieStore.setState({
      entries: [{ platform: 'douyin', configured: true, updated_at: 'x' }],
    });
    const user = userEvent.setup();
    renderWithProviders(<CookieSettingsDrawer />);

    await user.click(screen.getByTestId('cookie-clear-douyin'));
    await waitFor(() => expect(cookieApi.remove).toHaveBeenCalledWith('douyin'));
    expect(await screen.findByText('Cookie 已清除')).toBeInTheDocument();
  });

  it('disables clear when the platform is not configured', () => {
    renderWithProviders(<CookieSettingsDrawer />);
    expect(screen.getByTestId('cookie-clear-douyin')).toBeDisabled();
  });
});
