import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppHeader } from './AppHeader';
import { renderWithProviders } from '../test/utils';
import { healthApi } from '../services/api';
import { useAuthStore } from '../stores/authStore';

vi.mock('../services/api', () => ({
  healthApi: { getHealth: vi.fn() },
}));

const healthy = { status: 'ok', services: { api: 'ok' }, storage_roots: {} };

describe('AppHeader', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ token: null, username: null, expiresAt: null });
    (healthApi.getHealth as Mock).mockResolvedValue(healthy);
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
    expect(screen.getByText('视频')).toBeInTheDocument();
    expect(screen.getByText('音乐')).toBeInTheDocument();
  });

  it('hides the mode switch on a mobile viewport', () => {
    const original = window.matchMedia;
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: () => ({
        matches: false,
        media: '',
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });

    renderWithProviders(<AppHeader />);
    expect(screen.queryByText('视频')).not.toBeInTheDocument();

    window.matchMedia = original;
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
});
