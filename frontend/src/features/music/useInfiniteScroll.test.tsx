import { act, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntersectionObserverMock } from '../../test/intersectionObserverMock';
import { useInfiniteScroll } from './useInfiniteScroll';

function Probe({
  hasMore,
  loading,
  onLoadMore,
}: {
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
}): JSX.Element {
  const { sentinelRef } = useInfiniteScroll(onLoadMore, { hasMore, loading });
  return <div ref={sentinelRef} data-testid="sentinel" />;
}

describe('useInfiniteScroll', () => {
  beforeEach(() => {
    IntersectionObserverMock.instances.length = 0;
  });

  it('calls onLoadMore when the sentinel intersects and more is available', () => {
    const onLoadMore = vi.fn();
    render(<Probe hasMore loading={false} onLoadMore={onLoadMore} />);
    const observer = IntersectionObserverMock.instances[0];
    expect(observer).toBeDefined();
    act(() => observer.fire([{ isIntersecting: true }]));
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it('does not call onLoadMore while a request is in flight', () => {
    const onLoadMore = vi.fn();
    render(<Probe hasMore loading onLoadMore={onLoadMore} />);
    const observer = IntersectionObserverMock.instances[0];
    act(() => observer.fire([{ isIntersecting: true }]));
    expect(onLoadMore).not.toHaveBeenCalled();
  });

  it('does not call onLoadMore when no more data exists', () => {
    const onLoadMore = vi.fn();
    render(<Probe hasMore={false} loading={false} onLoadMore={onLoadMore} />);
    const observer = IntersectionObserverMock.instances[0];
    act(() => observer.fire([{ isIntersecting: true }]));
    expect(onLoadMore).not.toHaveBeenCalled();
  });
});
