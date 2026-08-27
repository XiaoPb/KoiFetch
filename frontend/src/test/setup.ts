// Vitest + Testing Library setup (jsdom).
// antd v5 components (Grid/useBreakpoint, responsive observers) require
// matchMedia and ResizeObserver, which jsdom does not provide — polyfill them
// here so every component test works without per-file boilerplate.
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { installIntersectionObserverMock, IntersectionObserverMock } from './intersectionObserverMock';

// Breakpoint default: treat the viewport as desktop-wide (any `min-width`
// media query matches), so `Grid.useBreakpoint()` reports md+ by default.
// Tests that need a mobile viewport override `window.matchMedia` locally.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('min-width'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

class ResizeObserverMock {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

Object.defineProperty(window, 'ResizeObserver', {
  writable: true,
  value: ResizeObserverMock,
});

// Some antd internals call scrollTo on mount; jsdom lacks it.
Object.defineProperty(window, 'scrollTo', {
  writable: true,
  value: vi.fn(),
});

installIntersectionObserverMock();

afterEach(() => {
  cleanup();
  IntersectionObserverMock.instances.length = 0;
});
