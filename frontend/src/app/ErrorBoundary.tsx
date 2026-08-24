import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Result } from 'antd';
import { useTranslation } from '../services/i18n';

function ErrorFallback({ onReload }: { onReload: () => void }): JSX.Element {
  const { t } = useTranslation();
  return (
    <Result
      status="error"
      title={t('errorBoundary.title')}
      extra={
        <Button type="primary" onClick={onReload}>
          {t('errorBoundary.reload')}
        </Button>
      }
    />
  );
}

interface ErrorBoundaryState {
  hasError: boolean;
}

interface ErrorBoundaryProps {
  children: ReactNode;
}

/**
 * Top-level error boundary: catches render errors anywhere below the app
 * shell and shows a recoverable fallback instead of a white screen.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Uncaught render error:', error, info);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return <ErrorFallback onReload={this.handleReload} />;
    }
    return this.props.children;
  }
}
