import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { App } from './App';
import { authApi } from '../services/api';
import { AUTH_STORAGE_KEY, useAuthStore, type AuthState } from '../stores/authStore';
import { useDownloadsStore, type DownloadItem } from '../stores/downloadsStore';
import { ApiError } from '../types/api';

vi.mock('../services/api', () => ({
  authApi: {
    login: vi.fn(),
    refresh: vi.fn().mockResolvedValue({ token: 'refreshed', username: 'admin', expires_at: '2099-01-01T00:00:00Z' }),
  },
  healthApi: { getHealth: vi.fn().mockResolvedValue({ status: 'ok', services: {}, storage_roots: {} }) },
  // The home page renders the parser workspace, whose stores import these;
  // provide them so a future render-time access cannot crash on undefined.
  parseApi: { parse: vi.fn() },
  downloadApi: { submit: vi.fn() },
  // The /nas page imports the save endpoint (used on admin action only).
  nasApi: { save: vi.fn() },
  // The music detail routes re-search by name on direct visits.
  musicApi: { search: vi.fn().mockResolvedValue({ totals: { all: 0, song: 0, artist: 0, album: 0, playlist: 0 }, songs: [], artists: [], albums: [], playlists: [], hasMore: false }), importSong: vi.fn(), getHotKeywords: vi.fn() },
}));

function renderAt(route: string, strict = false): ReturnType<typeof render> {
  const app = strict ? <StrictMode><App /></StrictMode> : <App />;
  return render(
    <MemoryRouter initialEntries={[route]}>
      {app}
    </MemoryRouter>,
  );
}

async function seedPersistedAuth(session: Pick<AuthState, 'token' | 'username' | 'expiresAt'>): Promise<void> {
  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ state: session, version: 2 }));
  await useAuthStore.persist.rehydrate();
  vi.clearAllMocks();
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

  it('switches the main page content to music via the Topbar tab', async () => {
    const user = userEvent.setup();
    renderAt('/');

    // One main page: video mode shows the parser workspace…
    expect(await screen.findByText('解析工作台')).toBeInTheDocument();

    // …and the Topbar's 音乐 tab swaps the content area to the music search
    // page on the SAME page (no route change, no back arrow).
    await user.click(screen.getByText('音乐'));
    expect(await screen.findByTestId('music-search-input')).toBeInTheDocument();
    expect(screen.queryByText('解析工作台')).not.toBeInTheDocument();
    expect(screen.getByTestId('app-header')).toBeInTheDocument();
  });

  it('renders a music artist detail route', async () => {
    renderAt('/music/artist/a1?name=%E5%91%A8%E6%9D%B0%E4%BC%A6');
    // The music detail page stays inside the shell (topbar present) and
    // shows the honest no-results body for an unknown/empty re-search.
    expect(await screen.findByTestId('music-entity-page')).toBeInTheDocument();
    expect(screen.getByTestId('app-header')).toBeInTheDocument();
  });

  it('refreshes one valid persisted session after hydration, including StrictMode', async () => {
    await seedPersistedAuth({ token: 'old', username: 'admin', expiresAt: '2099-01-01T00:00:00Z' });
    (authApi.refresh as Mock).mockResolvedValue({
      token: 'fresh',
      username: 'admin',
      expires_at: '2099-01-02T00:00:00Z',
    });

    renderAt('/', true);

    await waitFor(() => expect(authApi.refresh).toHaveBeenCalledTimes(1));
    expect(authApi.refresh).toHaveBeenCalledWith('old');
    expect(useAuthStore.getState().token).toBe('fresh');
  });

  it('logs out and redirects to login when startup refresh is rejected', async () => {
    await seedPersistedAuth({ token: 'old', username: 'admin', expiresAt: '2099-01-01T00:00:00Z' });
    (authApi.refresh as Mock).mockRejectedValue(new ApiError('expired', 2004, 401));

    renderAt('/nas', true);

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    await waitFor(() => expect(useAuthStore.getState().token).toBeNull());
    expect(authApi.refresh).toHaveBeenCalledTimes(1);
  });

  it('does not request refresh for a locally expired persisted session', async () => {
    await seedPersistedAuth({ token: 'expired', username: 'admin', expiresAt: '2020-01-01T00:00:00Z' });

    renderAt('/nas');

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    expect(authApi.refresh).not.toHaveBeenCalled();
  });

  it('does not request refresh for a malformed persisted expiry and redirects to login', async () => {
    await seedPersistedAuth({ token: 'malformed', username: 'admin', expiresAt: 'not-a-date' });

    renderAt('/nas');

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    await waitFor(() => expect(useAuthStore.getState().token).toBeNull());
    expect(authApi.refresh).not.toHaveBeenCalled();
  });

  it('does not log out after an in-flight startup refresh rejects post-unmount', async () => {
    await seedPersistedAuth({ token: 'old', username: 'admin', expiresAt: '2099-01-01T00:00:00Z' });
    let rejectRefresh!: (reason: unknown) => void;
    (authApi.refresh as Mock).mockReturnValue(new Promise((_resolve, reject) => { rejectRefresh = reject; }));

    const rendered = renderAt('/');
    await waitFor(() => expect(authApi.refresh).toHaveBeenCalledTimes(1));
    rendered.unmount();

    rejectRefresh(new ApiError('expired', 2004, 401));
    await Promise.resolve();
    await Promise.resolve();
    expect(useAuthStore.getState().token).toBe('old');
  });
});
