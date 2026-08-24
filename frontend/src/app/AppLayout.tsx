import { Layout } from 'antd';
import { Outlet } from 'react-router-dom';
import { AppHeader } from '../components/AppHeader';
import { PreviewModal } from '../features/preview/PreviewModal';
import { selectIsLoading, useAppStore } from '../stores/appStore';
import { GlobalErrorWatcher } from './GlobalErrorWatcher';

/**
 * The shared page shell: header + content region + the app-wide preview Modal.
 * Wraps the `/` and `/nas` routes (the login page renders standalone). The
 * global loading indicator and error toast live here so every routed page
 * gets them.
 */
export function AppLayout(): JSX.Element {
  const isLoading = useAppStore(selectIsLoading);

  return (
    <Layout className="app-layout">
      <AppHeader />
      {isLoading && <div className="app-loading-bar" data-testid="global-loading" aria-hidden="true" />}
      <Layout.Content className="app-content">
        <Outlet />
      </Layout.Content>
      <PreviewModal />
      <GlobalErrorWatcher />
    </Layout>
  );
}
