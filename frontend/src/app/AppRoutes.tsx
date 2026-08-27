import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';
import { AppLayout } from './AppLayout';
import { ProtectedRoute } from '../components/ProtectedRoute';

// Lazy route pages: each chunk loads on first navigation (code splitting).
const HomePage = lazy(() => import('../pages/HomePage'));
const LoginPage = lazy(() => import('../pages/LoginPage'));
const NasPage = lazy(() => import('../pages/NasPage'));
const NotFoundPage = lazy(() => import('../pages/NotFoundPage'));
const MusicSearchPage = lazy(() => import('../pages/MusicSearchPage'));

/**
 * Route table (PRD): `/` parser workspace, `/login` standalone admin login,
 * `/nas` admin-only NAS management behind ProtectedRoute, `/music` standalone
 * music search; anything else falls back to the 404 page. The shell layout
 * wraps the workspace and NAS routes; the login and music search pages render
 * without the header.
 */
export function AppRoutes(): JSX.Element {
  return (
    <Suspense
      fallback={
        <div className="route-loading">
          <Spin size="large" />
        </div>
      }
    >
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<AppLayout />}>
          <Route path="/" element={<HomePage />} />
          <Route
            path="/nas"
            element={
              <ProtectedRoute>
                <NasPage />
              </ProtectedRoute>
            }
          />
        </Route>
        <Route path="/music" element={<MusicSearchPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  );
}
