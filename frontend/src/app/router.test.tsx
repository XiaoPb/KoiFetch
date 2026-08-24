import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { App } from './App';
import { authApi } from '../services/api';
import { useAuthStore } from '../stores/authStore';

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

describe('router', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ token: null, username: null, expiresAt: null });
  });

  it('renders the parser workspace at /', async () => {
    renderAt('/');
    expect(await screen.findByText('解析工作台')).toBeInTheDocument();
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
});
