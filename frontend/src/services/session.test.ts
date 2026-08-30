import { beforeEach, describe, expect, it, vi } from 'vitest';
import { waitForAuthHydration } from './session';
import { useAuthStore } from '../stores/authStore';

describe('waitForAuthHydration', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('resolves immediately without subscribing when hydration already finished', async () => {
    vi.spyOn(useAuthStore.persist, 'hasHydrated').mockReturnValue(true);
    const onFinishHydration = vi.spyOn(useAuthStore.persist, 'onFinishHydration');

    await waitForAuthHydration();

    expect(onFinishHydration).not.toHaveBeenCalled();
  });

  it('subscribes while hydrating and unsubscribes after hydration finishes', async () => {
    let finishHydration: (() => void) | undefined;
    const unsubscribe = vi.fn();
    vi.spyOn(useAuthStore.persist, 'hasHydrated').mockReturnValue(false);
    vi.spyOn(useAuthStore.persist, 'onFinishHydration').mockImplementation((listener) => {
      finishHydration = () => listener(useAuthStore.getState());
      return unsubscribe;
    });

    let settled = false;
    const hydration = waitForAuthHydration().then(() => { settled = true; });
    await Promise.resolve();
    expect(settled).toBe(false);

    finishHydration?.();
    await hydration;
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it('unsubscribes when the hydration wait is aborted', async () => {
    const controller = new AbortController();
    const unsubscribe = vi.fn();
    vi.spyOn(useAuthStore.persist, 'hasHydrated').mockReturnValue(false);
    vi.spyOn(useAuthStore.persist, 'onFinishHydration').mockReturnValue(unsubscribe);

    const hydration = waitForAuthHydration(controller.signal);
    controller.abort();
    await hydration;

    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });
});
