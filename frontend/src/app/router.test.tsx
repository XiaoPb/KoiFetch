import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { App } from './App';
import { useAuthStore } from '../stores/authStore';

vi.mock('../services/api', () => ({
  authApi: { login: vi.fn() },
  healthApi: { getHealth: vi.fn().mockResolvedValue({ status: 'ok', services: {}, storage_roots: {} }) },
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
});
