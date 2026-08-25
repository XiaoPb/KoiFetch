import { App as AntdApp, ConfigProvider } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { ErrorBoundary } from './ErrorBoundary';
import { AppRoutes } from './AppRoutes';
import { useAppStore } from '../stores/appStore';

/**
 * Root app component: antd providers (locale follows the active UI language,
 * theme carries the Koi brand look — vibrant blue primary with softer radii),
 * the top-level error boundary, and the route table. Rendered inside a router
 * (BrowserRouter in main.tsx; MemoryRouter in tests).
 */
export function App(): JSX.Element {
  const language = useAppStore((state) => state.language);

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
