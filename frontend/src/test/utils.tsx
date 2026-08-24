import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { App as AntdApp, ConfigProvider } from 'antd';

// Render a component with the app's runtime providers (antd ConfigProvider +
// App for message/notification context) inside a MemoryRouter, so components
// that use Link/useNavigate or App.useApp() work in tests.
export function renderWithProviders(ui: ReactElement, { route = '/' }: { route?: string } = {}): ReturnType<typeof render> {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          {ui}
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>
  );
}
