// Test double for window.IntersectionObserver (jsdom does not implement it).
// Instances register themselves so tests can grab the observer their component
// created and fire synthetic intersections via `fire()`.
export type ObserverCallback = (entries: { isIntersecting: boolean }[]) => void;

export class IntersectionObserverMock {
  static instances: IntersectionObserverMock[] = [];
  readonly callback: ObserverCallback;

  constructor(callback: ObserverCallback) {
    this.callback = callback;
    IntersectionObserverMock.instances.push(this);
  }

  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}

  /** Test helper: invoke the observer callback with synthetic entries. */
  fire(entries: { isIntersecting: boolean }[]): void {
    this.callback(entries);
  }
}

export function installIntersectionObserverMock(): void {
  Object.defineProperty(window, 'IntersectionObserver', {
    writable: true,
    value: IntersectionObserverMock,
  });
}
