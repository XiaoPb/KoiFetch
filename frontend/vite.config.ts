/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Koi Fetch frontend (Task 13 shell).
//
// - Build output stays at `dist/` (Vite's default outDir) — the Task 3
//   frontend Dockerfile copies `/app/dist` into the nginx image, so it must
//   not be changed. The nginx SPA serves from the domain root, so `base`
//   stays the default `/`.
// - Dev server runs on 5173 and proxies /api + /ws to the local backend
//   (http://localhost:8000). Same-origin proxying means local development
//   needs no CORS setup (the backend's CORS_ORIGINS default is empty).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // Run tests in worker threads (vitest 2 defaults to the `forks` pool,
    // which spawns child processes).
    pool: 'threads',
  },
});
