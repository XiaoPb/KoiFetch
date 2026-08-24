import { App as AntdApp, ConfigProvider } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { ErrorBoundary } from './ErrorBoundary';
import { AppRoutes } from './AppRoutes';
import { useAppStore } from '../stores/appStore';

/**
 * Root app component: antd providers (locale follows the active UI language),
 * the top-level error boundary, and the route table. Rendered inside a router
 * (BrowserRouter in main.tsx; MemoryRouter in tests).
 */
export function App(): JSX.Element {
  const language = useAppStore((state) => state.language);

  return (
    <ConfigProvider locale={language === 'zh' ? zhCN : enUS}>
      <AntdApp>
        <ErrorBoundary>
          <AppRoutes />
        </ErrorBoundary>
      </AntdApp>
    </ConfigProvider>
  );
}
