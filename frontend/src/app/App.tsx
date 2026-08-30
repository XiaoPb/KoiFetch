import { useEffect, useRef, useState } from 'react';
import { App as AntdApp, ConfigProvider } from 'antd';
import { useNavigate } from 'react-router-dom';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { ErrorBoundary } from './ErrorBoundary';
import { AppRoutes } from './AppRoutes';
import { useAppStore } from '../stores/appStore';
import { getAuthHydrationStatus, useAuthStore } from '../stores/authStore';
import { logoutSession, waitForAuthHydration } from '../services/session';

/**
 * Root app component: antd providers (locale follows the active UI language,
 * theme carries the Koi brand look — vibrant blue primary with softer radii),
 * the top-level error boundary, and the route table. Rendered inside a router
 * (BrowserRouter in main.tsx; MemoryRouter in tests).
 */
export function App(): JSX.Element {
  const language = useAppStore((state) => state.language);
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  const [authHydrated, setAuthHydrated] = useState(() => (
    useAuthStore.persist.hasHydrated() && getAuthHydrationStatus().state !== 'error'
  ));

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const startSession = async (): Promise<void> => {
      const hydration = await waitForAuthHydration(controller.signal);
      if (cancelled || controller.signal.aborted) return;

      if (hydration.state === 'error') {
        logoutSession();
        setAuthHydrated(true);
        navigateRef.current('/login', { replace: true });
        return;
      }
      setAuthHydrated(true);

      const startedSession = useAuthStore.getState();

      try {
        await startedSession.refreshSession();
      } catch {
        if (cancelled || controller.signal.aborted) return;
        const currentSession = useAuthStore.getState();
        const isSameSession = currentSession.token === startedSession.token
          && currentSession.username === startedSession.username
          && currentSession.expiresAt === startedSession.expiresAt;
        if (!isSameSession) return;
        logoutSession();
        navigateRef.current('/login', { replace: true });
      }
    };

    void startSession();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  return (
    <ConfigProvider
      locale={language === 'zh' ? zhCN : enUS}
      theme={{
        token: {
          colorPrimary: '#2f6bff',
          borderRadius: 10,
        },
        components: {
          Button: { borderRadius: 10 },
          Card: { borderRadiusLG: 14 },
        },
      }}
    >
      <AntdApp>
        {authHydrated ? (
          <ErrorBoundary>
            <AppRoutes />
          </ErrorBoundary>
        ) : (
          <div
            className="route-loading"
            data-testid="auth-hydration-loading"
            role="status"
            aria-live="polite"
            aria-busy="true"
          >
            正在恢复会话 / Restoring session…
          </div>
        )}
      </AntdApp>
    </ConfigProvider>
  );
}
