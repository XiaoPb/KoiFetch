import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { ErrorBoundary } from './ErrorBoundary';
import { renderWithProviders } from '../test/utils';

function Boom(): JSX.Element {
  throw new Error('boom');
}

function Fine(): JSX.Element {
  return <div>fine</div>;
}

describe('ErrorBoundary', () => {
  it('renders children when nothing throws', () => {
    renderWithProviders(
      <ErrorBoundary>
        <Fine />
      </ErrorBoundary>,
    );
    expect(screen.getByText('fine')).toBeInTheDocument();
  });

  it('renders the error fallback when a child throws', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderWithProviders(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText('页面出错了')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新页面' })).toBeInTheDocument();
    spy.mockRestore();
  });
});
