import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppHeader } from './AppHeader';
import { renderWithProviders } from '../test/utils';
import { healthApi } from '../services/api';
import { useAppStore } from '../stores/appStore';
import { useAuthStore } from '../stores/authStore';
import { useDownloadsStore, type DownloadItem } from '../stores/downloadsStore';

vi.mock('../services/api', () => ({
  healthApi: { getHealth: vi.fn() },
  // The download-center Drawer (Task 15) reads downloadApi only on actions;
  // the mock keeps the module importable in header tests.
  downloadApi: { getFileUrl: vi.fn(), submit: vi.fn(), getProgress: vi.fn() },
}));

function seedDownloadItem(partial: Partial<DownloadItem> & Pick<DownloadItem, 'download_id' | 'task_id' | 'status'>): DownloadItem {
  return {
    title: null,
    format: null,
    quality: null,
    created_at: '2026-01-01T00:00:00Z',
    progress: 0,
    speed: null,
    downloaded_bytes: null,
    total_bytes: null,
    remaining_time: null,
    error_code: null,
    error_message: null,
    download_url: null,
    token_expire_at: null,
    ...partial,
  };
}

const healthy = { status: 'ok', services: { api: 'ok' }, storage_roots: {} };

// A matchMedia that reports no breakpoints — i.e. a small/mobile viewport.
function mobileMatchMedia(): () => {
  matches: boolean;
  media: string;
  onchange: null;
  addListener: ReturnType<typeof vi.fn>;
  removeListener: ReturnType<typeof vi.fn>;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
  dispatchEvent: ReturnType<typeof vi.fn>;
} {
  return () => ({
    matches: false,
    media: '',
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
}

describe('AppHeader', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ token: null, username: null, expiresAt: null });
    useAppStore.setState({ mediaMode: 'video' });
    (healthApi.getHealth as Mock).mockResolvedValue(healthy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the logo, status indicator, login button and download center', async () => {
    renderWithProviders(<AppHeader />);

    expect(screen.getByText('🎏 Koi Fetch')).toBeInTheDocument();
    expect(await screen.findByText('在线')).toBeInTheDocument();
    // antd auto-inserts a space in two-CJK-character buttons ("登 录").
    expect(screen.getByRole('button', { name: /登\s*录/ })).toBeInTheDocument();
    expect(screen.getByTestId('download-center')).toBeInTheDocument();
  });

  it('shows the video/music mode switch on desktop', async () => {
    renderWithProviders(<AppHeader />);
    expect(screen.getByTestId('mode-switch-desktop')).toBeInTheDocument();
    expect(screen.getByText('视频')).toBeInTheDocument();
    expect(screen.getByText('音乐')).toBeInTheDocument();
  });

  it('replaces the segmented mode switch with a compact control on mobile', () => {
    vi.stubGlobal('matchMedia', mobileMatchMedia());
    renderWithProviders(<AppHeader />);

    expect(screen.queryByTestId('mode-switch-desktop')).not.toBeInTheDocument();
    expect(screen.getByTestId('mode-switch-mobile')).toBeInTheDocument();
  });

  it('lets mobile users switch the media mode', async () => {
    vi.stubGlobal('matchMedia', mobileMatchMedia());
    const user = userEvent.setup();
    renderWithProviders(<AppHeader />);

    await user.click(screen.getByTestId('mode-switch-mobile'));
    await user.click(await screen.findByRole('menuitem', { name: '音乐' }));

    expect(useAppStore.getState().mediaMode).toBe('music');
  });

  it('toggles between Chinese and English', async () => {
    const user = userEvent.setup();
    renderWithProviders(<AppHeader />);
    expect(await screen.findByText('在线')).toBeInTheDocument();

    await user.click(screen.getByTestId('language-toggle'));
    expect(screen.getByText('Online')).toBeInTheDocument();
    expect(screen.getByText('Log in')).toBeInTheDocument();

    await user.click(screen.getByTestId('language-toggle'));
    expect(await screen.findByText('在线')).toBeInTheDocument();
  });

  it('shows the admin username instead of the login button when authenticated', () => {
    useAuthStore.setState({ token: 'tok', username: 'admin', expiresAt: '2099-01-01T00:00:00Z' });
    renderWithProviders(<AppHeader />);
    expect(screen.getByText('admin')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /登\s*录/ })).not.toBeInTheDocument();
  });

  it('opens the download-center drawer from the header badge', async () => {
    const user = userEvent.setup();
    renderWithProviders(<AppHeader />);

    await user.click(screen.getByTestId('download-center'));
    expect(await screen.findByTestId('download-center-drawer')).toBeInTheDocument();
  });

  it('clears the session-local download state on logout', async () => {
    const user = userEvent.setup();
    useAuthStore.setState({ token: 'tok', username: 'admin', expiresAt: '2099-01-01T00:00:00Z' });
    useDownloadsStore.setState({
      items: [seedDownloadItem({ download_id: 'd1', task_id: 't1', status: 'downloading', title: 'Video A' })],
    });
    renderWithProviders(<AppHeader />);

    await user.click(screen.getByRole('button', { name: /admin/ }));
    await user.click(await screen.findByRole('menuitem', { name: /退出登录/ }));

    expect(useAuthStore.getState().token).toBeNull();
    // The next admin starts from a clean slate (Task 16 teardown seam).
    expect(useDownloadsStore.getState().items).toHaveLength(0);
  });
});
