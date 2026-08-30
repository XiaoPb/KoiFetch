import { useEffect } from 'react';
import { App as AntdApp, ConfigProvider } from 'antd';
import { useNavigate } from 'react-router-dom';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { ErrorBoundary } from './ErrorBoundary';
import { AppRoutes } from './AppRoutes';
import { useAppStore } from '../stores/appStore';
import { useAuthStore } from '../stores/authStore';
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

  useEffect(() => {
    let cancelled = false;
    const startSession = async (): Promise<void> => {
      await waitForAuthHydration();
      if (cancelled) return;

      try {
        await useAuthStore.getState().refreshSession();
      } catch {
        if (cancelled) return;
        logoutSession();
        navigate('/login', { replace: true });
      }
    };

    void startSession();
    return () => {
      cancelled = true;
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
        <ErrorBoundary>
          <AppRoutes />
        </ErrorBoundary>
      </AntdApp>
    </ConfigProvider>
  );
}
