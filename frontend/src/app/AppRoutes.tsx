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
// Named-export pages need a default-mapping shim for lazy().
const MusicEntityPage = lazy(() =>
  import('../pages/music/MusicEntityPage').then((m) => ({ default: m.MusicEntityPage })),
);
const MyPlaylistPage = lazy(() =>
  import('../pages/music/MyPlaylistPage').then((m) => ({ default: m.MyPlaylistPage })),
);

/**
 * Route table (PRD): `/` is the SINGLE main page — the Topbar's 视频/音乐
 * switch swaps its content area (parser workspace vs. music search). `/login`
 * is the standalone admin login; `/nas` is admin-only NAS management behind
 * ProtectedRoute; `/music/*` are the music detail route pages (P2, inside the
 * shell so the topbar stays); anything else falls back to the 404 page.
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
          <Route path="/music/artist/:id" element={<MusicEntityPage kind="artist" />} />
          <Route path="/music/album/:id" element={<MusicEntityPage kind="album" />} />
          <Route path="/music/playlist/:id" element={<MusicEntityPage kind="playlist" />} />
          <Route path="/music/my-playlist/:id" element={<MyPlaylistPage />} />
          <Route
            path="/nas"
            element={
              <ProtectedRoute>
                <NasPage />
              </ProtectedRoute>
            }
          />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  );
}
