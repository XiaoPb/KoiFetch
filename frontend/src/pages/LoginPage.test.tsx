import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { App as AntdApp, ConfigProvider } from 'antd';
import LoginPage from './LoginPage';
import { authApi } from '../services/api';
import { ApiError } from '../types/api';
import { useAuthStore } from '../stores/authStore';
import { useDownloadsStore, type DownloadItem } from '../stores/downloadsStore';

vi.mock('../services/api', () => ({
  authApi: { login: vi.fn() },
}));

const FUTURE = '2099-01-01T00:00:00Z';
const PAST = '2020-01-01T00:00:00Z';

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

/**
 * Render the login page inside a real router so the post-login navigation
 * (back to state.from) is observable through a second route.
 */
function renderLoginAtOrigin(from: string): void {
  render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={[{ pathname: '/login', state: { from } }]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/nas" element={<div>nas-page</div>} />
            <Route path="/" element={<div>home-page</div>} />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>,
  );
}

function renderLogin(): void {
  render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={['/login']}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<div>home-page</div>} />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>,
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    useAuthStore.setState({ token: null, username: null, expiresAt: null });
    useDownloadsStore.setState({ items: [], submitting: {} });
  });

  it('renders the login form with bilingual placeholders', () => {
    renderLogin();
    expect(screen.getByText(/管理员登录/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('密码')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /登\s*录/ })).toBeInTheDocument();
  });

  it('logs in and returns to the originally requested admin page', async () => {
    const user = userEvent.setup();
    (authApi.login as Mock).mockResolvedValue({ token: 'tok', username: 'admin', expires_at: FUTURE });

    renderLoginAtOrigin('/nas');

    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'secret');
    await user.click(screen.getByRole('button', { name: /登\s*录/ }));

    expect(authApi.login).toHaveBeenCalledWith({ username: 'admin', password: 'secret' });
    expect(await screen.findByText('nas-page')).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBe('tok');
  });

  it('returns home when no origin was recorded', async () => {
    const user = userEvent.setup();
    (authApi.login as Mock).mockResolvedValue({ token: 'tok', username: 'admin', expires_at: FUTURE });

    renderLogin();

    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'secret');
    await user.click(screen.getByRole('button', { name: /登\s*录/ }));

    expect(await screen.findByText('home-page')).toBeInTheDocument();
  });

  it('shows a loading state while the request is in flight', async () => {
    const user = userEvent.setup();
    let resolveLogin: (value: unknown) => void = () => undefined;
    (authApi.login as Mock).mockReturnValue(
      new Promise((resolve) => { resolveLogin = resolve; }),
    );

    renderLogin();
    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'secret');
    await user.click(screen.getByRole('button', { name: /登\s*录/ }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /登\s*录/ })).toHaveClass('ant-btn-loading'),
    );

    // Once the login resolves, the flow completes (navigates home), which
    // also proves the loading state ended.
    resolveLogin({ token: 'tok', username: 'admin', expires_at: FUTURE });
    expect(await screen.findByText('home-page')).toBeInTheDocument();
  });

  it('surfaces the backend wrong-credentials message (2005) and stores no session', async () => {
    const user = userEvent.setup();
    (authApi.login as Mock).mockRejectedValue(
      new ApiError('用户名或密码错误 / Invalid username or password', 2005, 401),
    );

    renderLogin();
    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'wrong');
    await user.click(screen.getByRole('button', { name: /登\s*录/ }));

    expect(await screen.findByText(/Invalid username or password/)).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBeNull();
    expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument();
  });

  it('shows an expired-session notice and clears the stale session on mount', () => {
    // A rehydrated session whose token has already expired, with session-local
    // download state still around (e.g. the user was redirected here from
    // /nas by ProtectedRoute).
    useAuthStore.setState({ token: 'stale', username: 'admin', expiresAt: PAST });
    useDownloadsStore.setState({
      items: [seedDownloadItem({ download_id: 'd1', task_id: 't1', status: 'downloading', title: 'Video A' })],
    });

    renderLogin();

    expect(screen.getByText('会话已过期，请重新登录')).toBeInTheDocument();
    // The stale session is cleared so the next login starts clean.
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().expiresAt).toBeNull();
    // ...and the downloads state is torn down too: stale items and live
    // sockets must not survive the session boundary (Task 16 review).
    expect(useDownloadsStore.getState().items).toHaveLength(0);
  });

  it('does not show the expired notice for a fresh login page', () => {
    renderLogin();
    expect(screen.queryByText('会话已过期，请重新登录')).not.toBeInTheDocument();
  });
});
