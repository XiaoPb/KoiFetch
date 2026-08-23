"""In-process publish/subscribe bus for download progress events.

Task 11's worker publishes progress/terminal events here as it executes a
download; the WebSocket endpoint (``app.api.download``) subscribes per
connection and forwards each event to the client verbatim. The hub is
deliberately transport-free: it knows nothing about WebSockets or the DB, so
any publisher (worker, cleanup loop, future admin tooling) can drive live
updates without touching the WS contract.

Design decisions (stable contract for Task 11):

* **One module-level singleton, mirrored on ``app.state``.** ``create_app``
  stores :data:`event_hub` on ``app.state.download_event_hub`` so handlers
  read it through DI; an in-process worker (Task 11) publishes through the
  same singleton — ``await event_hub.publish(download_id, event)`` — and
  reaches every connected client of every app instance in this process.
  (A cross-process worker would need a different transport, e.g. Redis pub/sub
  behind the same ``publish`` signature.)
* **Per-connection queues bound to the connection's event loop.** Each
  ``subscribe`` creates a fresh ``asyncio.Queue`` inside the endpoint's
  running loop and records the loop alongside it, so ``publish`` can deliver
  through ``loop.call_soon_threadsafe`` — safe to call from any thread/loop
  (the test suite and real workers both use it).
* **Bounded queues, drop-oldest.** Subscriber queues are capped
  (:data:`_SUBSCRIBER_QUEUE_MAXSIZE`); a slow consumer that cannot keep up
  loses its *oldest* queued event so the newest progress always lands. This
  keeps a stalled connection from growing memory without bound.
* **Dropped events are acceptable.** An event published before the subscriber
  awaits its queue (or to a connection that is tearing down, or into a full
  queue) is skipped; the client reconciles via ``GET /api/download/progress/{id}``.
  Progress is lossy by nature; the WS is a live-update channel, not a record.
* **``subscriber_count`` exists for tests/observability** — asserting that a
  closed connection releases its subscription.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict

__all__ = ["DownloadEventHub", "event_hub"]

# Cap on queued events per subscriber: progress is lossy by design, so a slow
# consumer drops its oldest events instead of growing memory without bound.
_SUBSCRIBER_QUEUE_MAXSIZE = 100


class DownloadEventHub:
    """Fan progress events from any publisher to all subscribed connections."""

    def __init__(self) -> None:
        # A plain threading lock (loop-agnostic) guards the registry; the
        # per-connection asyncio.Queues are never touched cross-loop directly.
        self._lock = threading.Lock()
        self._subscribers: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = (
            defaultdict(dict)
        )

    async def subscribe(self, download_id: str) -> asyncio.Queue:
        """Register a new subscriber queue for ``download_id``.

        Must be called from within the connection's event loop (the WebSocket
        handler does this); the queue is bound to that loop so ``publish`` can
        deliver thread-safely.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAXSIZE)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers[download_id][queue] = loop
        return queue

    async def unsubscribe(self, download_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber; forget the id when the last one leaves."""
        with self._lock:
            subscribers = self._subscribers.get(download_id)
            if subscribers is None:
                return
            subscribers.pop(queue, None)
            if not subscribers:
                del self._subscribers[download_id]

    async def publish(self, download_id: str, event: dict) -> None:
        """Deliver ``event`` to every current subscriber of ``download_id``.

        Safe from any thread/event loop: delivery is scheduled on each
        subscriber's own loop via ``call_soon_threadsafe`` and never blocks on
        a slow consumer — a full queue drops its oldest event instead.
        Subscribers whose loop is closed (torn-down connections) are skipped.
        """
        with self._lock:
            pairs = list(self._subscribers.get(download_id, {}).items())
        for queue, loop in pairs:
            if loop.is_closed():
                continue
            try:
                # The loop may be closing between the is_closed check and the
                # schedule (teardown race); skip such subscribers.
                loop.call_soon_threadsafe(self._enqueue, queue, event)
            except RuntimeError:
                continue

    @staticmethod
    def _enqueue(queue: asyncio.Queue, event: dict) -> None:
        """Push ``event`` onto ``queue`` (drop-oldest when full).

        Runs inside the subscriber's event loop via ``call_soon_threadsafe``,
        so queue operations never cross loops.
        """
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # drop the oldest event
            except asyncio.QueueEmpty:  # pragma: no cover - race with the reader
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - hopelessly behind
                pass

    def subscriber_count(self, download_id: str) -> int:
        """Number of live subscriptions for ``download_id`` (tests/observability)."""
        with self._lock:
            return len(self._subscribers.get(download_id, {}))


event_hub = DownloadEventHub()
"""The process-wide hub; ``create_app`` mirrors it on ``app.state``."""
