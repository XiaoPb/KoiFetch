/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Koi Fetch frontend (Task 13 shell).
//
// - Build output stays at `dist/` (Vite's default outDir) — the multi-stage
//   backend Dockerfile copies /build/dist into the backend image, which serves
//   it at "/". `base` stays the default `/` (absolute asset paths), matching
//   that root serving.
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
    include: ['src/**/*.{test,spec}.{js,ts,jsx,tsx}'],
    css: false,
    // Keep test execution in worker threads for deterministic parallelism.
    pool: 'threads',
  },
});
