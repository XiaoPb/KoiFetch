import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuthStore, selectIsAuthenticated } from '../stores/authStore';

/**
 * Route guard for admin-only pages (e.g. /nas). Unauthenticated visitors are
 * redirected to /login; expired sessions count as unauthenticated.
 */
export function ProtectedRoute({ children }: { children: ReactNode }): JSX.Element {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
