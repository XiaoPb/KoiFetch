import { useEffect } from 'react';
import { App } from 'antd';
import { useAppStore } from '../stores/appStore';

/**
 * Renders the app-wide error toast. Watches the app store's `lastError`
 * (set by the API client / callers via `showError`) and surfaces it through
 * antd's message system, then clears it so a re-render does not re-toast.
 */
export function GlobalErrorWatcher(): null {
  const { message } = App.useApp();
  const lastError = useAppStore((state) => state.lastError);
  const clearError = useAppStore((state) => state.clearError);

  useEffect(() => {
    if (lastError) {
      void message.error(lastError);
      clearError();
    }
  }, [lastError, message, clearError]);

  return null;
}
