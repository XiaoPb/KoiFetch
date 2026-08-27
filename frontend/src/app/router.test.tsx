import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { App } from './App';
import { authApi } from '../services/api';
import { useAuthStore } from '../stores/authStore';
import { useDownloadsStore, type DownloadItem } from '../stores/downloadsStore';

vi.mock('../services/api', () => ({
  authApi: { login: vi.fn() },
  healthApi: { getHealth: vi.fn().mockResolvedValue({ status: 'ok', services: {}, storage_roots: {} }) },
  // The home page renders the parser workspace, whose stores import these;
  // provide them so a future render-time access cannot crash on undefined.
  parseApi: { parse: vi.fn() },
  downloadApi: { submit: vi.fn() },
  // The /nas page imports the save endpoint (used on admin action only).
  nasApi: { save: vi.fn() },
}));

function renderAt(route: string): void {
  render(
    <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </MemoryRouter>,
  );
}

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

describe('router', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ token: null, username: null, expiresAt: null });
    useDownloadsStore.setState({ items: [], submitting: {} });
  });

  it('renders the parser workspace at /', async () => {
    renderAt('/');
    expect(await screen.findByText('解析工作台')).toBeInTheDocument();
    expect(screen.getByTestId('music-entry')).toBeInTheDocument();
  });

  it('renders the home hero band with the subtitle', async () => {
    renderAt('/');
    expect(await screen.findByTestId('home-hero')).toBeInTheDocument();
    expect(screen.getByText('视频 · 音乐 · 图片，粘贴链接一键解析下载')).toBeInTheDocument();
  });

  it('redirects unauthenticated users from /nas to /login', async () => {
    renderAt('/nas');
    expect(await screen.findByText(/管理员登录/)).toBeInTheDocument();
  });

  it('renders /nas for authenticated admins', async () => {
    useAuthStore.setState({ token: 'tok', username: 'admin', expiresAt: '2099-01-01T00:00:00Z' });
    renderAt('/nas');
    expect(await screen.findByText('NAS 管理')).toBeInTheDocument();
  });

  it('renders the login page at /login without a header', async () => {
    renderAt('/login');
    expect(await screen.findByText(/管理员登录/)).toBeInTheDocument();
    expect(screen.queryByTestId('app-header')).not.toBeInTheDocument();
  });

  it('renders the 404 page for unknown routes', async () => {
    renderAt('/no-such-route');
    expect(await screen.findByText('页面不存在')).toBeInTheDocument();
  });

  it('returns to the originating protected page after login', async () => {
    (authApi.login as Mock).mockResolvedValue({ token: 'tok', username: 'admin', expires_at: '2099-01-01T00:00:00Z' });
    const user = userEvent.setup();

    renderAt('/nas');
    await screen.findByText(/管理员登录/);

    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'secret');
    await user.click(screen.getByRole('button', { name: /登\s*录/ }));

    // Login lands back on the originally requested admin page, not home.
    expect(await screen.findByText('NAS 管理')).toBeInTheDocument();
    expect(authApi.login).toHaveBeenCalledWith({ username: 'admin', password: 'secret' });
  });

  it('handles an expired session: banner on /login, teardown, and re-login returns to /nas', async () => {
    // A logged-in admin with session-local download state whose token has
    // already expired tries to open /nas.
    useAuthStore.setState({ token: 'stale', username: 'admin', expiresAt: '2020-01-01T00:00:00Z' });
    useDownloadsStore.setState({
      items: [seedDownloadItem({ download_id: 'd1', task_id: 't1', status: 'downloading', title: 'Video A' })],
    });
    (authApi.login as Mock).mockResolvedValue({ token: 'fresh', username: 'admin', expires_at: '2099-01-01T00:00:00Z' });
    const user = userEvent.setup();

    renderAt('/nas');

    // ProtectedRoute bounces the expired session to /login with the origin…
    expect(await screen.findByText('会话已过期，请重新登录')).toBeInTheDocument();
    // …and the stale download state is torn down at the session boundary.
    expect(useDownloadsStore.getState().items).toHaveLength(0);

    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'secret');
    await user.click(screen.getByRole('button', { name: /登\s*录/ }));

    // The fresh login returns to the originally requested /nas page.
    expect(await screen.findByText('NAS 管理')).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBe('fresh');
  });

  it('renders the music search page at /music', async () => {
    renderAt('/music');
    expect(await screen.findByTestId('music-search-input')).toBeInTheDocument();
    expect(screen.getByTestId('music-page')).toBeInTheDocument();
  });
});
