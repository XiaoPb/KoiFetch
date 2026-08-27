import { useEffect, useRef, type RefObject } from 'react';

export interface InfiniteScrollOptions {
  /** Whether more data exists to load. */
  hasMore: boolean;
  /** True while a load-more request is in flight (dedup guard). */
  loading: boolean;
}

/**
 * Observes a sentinel element placed at the end of a scrollable list and
 * calls `onLoadMore` when it scrolls into view (200px lookahead) — provided
 * `hasMore` is true and no request is in flight. The observer is re-created
 * whenever `hasMore`/`loading` change so the guard always reads current props.
 */
export function useInfiniteScroll(
  onLoadMore: () => void,
  { hasMore, loading }: InfiniteScrollOptions,
): { sentinelRef: RefObject<HTMLDivElement> } {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const onLoadMoreRef = useRef(onLoadMore);
  onLoadMoreRef.current = onLoadMore;

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasMore && !loading) {
          onLoadMoreRef.current();
        }
      },
      { rootMargin: '200px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loading]);

  return { sentinelRef };
}
