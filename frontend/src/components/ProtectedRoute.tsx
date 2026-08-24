import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore, selectIsAuthenticated } from '../stores/authStore';

/**
 * Route guard for admin-only pages (e.g. /nas). Unauthenticated visitors are
 * redirected to /login (carrying the original location so the login page can
 * return there); expired sessions count as unauthenticated.
 */
export function ProtectedRoute({ children }: { children: ReactNode }): JSX.Element {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const location = useLocation();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <>{children}</>;
}
