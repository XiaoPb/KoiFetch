"""Worker entrypoint: ``python -m app.workers.main`` (Task 11).

The long-running polling loop that turns the pure-ish :func:`run_once` batch
execution into a daemon: builds the production dependencies from settings,
polls for pending downloads on a configurable interval, and shuts down
gracefully on SIGTERM/SIGINT (finishes the current batch, then exits).

Design decisions:

* **Testable by construction.** :func:`build_worker_deps` wires the engine /
  downloader / storage / service from a :class:`Settings` object (production
  uses the process singleton), and :func:`run_forever` is the whole loop
  body with its dependencies injected — tests drive both directly and never
  need to run a real daemon or touch signals.
* **Poll interval.** ``settings.worker_poll_interval`` (default 1.0 s) paces
  idle polls. After a batch that claimed nothing the loop sleeps the interval;
  after a batch that did work it polls again immediately, so a queue drains
  without artificial delay.
* **Resilience.** A raised iteration (e.g. a transient DB error) is logged and
  the loop continues — a long-running daemon must not die on one hiccup.
* **In-process event hub.** The worker publishes WS progress/complete events
  through the module-level :data:`app.application.download_events.event_hub`.
  In the single-process dev/test setup this reaches the API's WebSocket
  subscribers directly; under Docker the worker is a separate process, where
  the hub is process-local and live WS updates degrade to snapshot-on-connect
  + HTTP polling (the documented v1 limitation, see ``download_events.py``).
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

from sqlalchemy import Engine

from app.adapters.factory import (
    get_downloader,
    get_one_time_token_provider,
    get_storage,
)
from app.adapters.protocols import DownloaderAdapter, StorageAdapter
from app.application.download_events import event_hub
from app.application.download_service import DownloadService
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.database import get_engine
from app.workers.worker import MAX_RETRIES, run_once

__all__ = ["build_worker_deps", "main", "run_forever"]

logger = logging.getLogger(__name__)

_POLL_INTERVAL_DEFAULT = 1.0


def build_worker_deps(
    settings: Settings | None = None,
) -> tuple[Engine, DownloaderAdapter, StorageAdapter, DownloadService]:
    """Build the engine/downloader/storage/service the polling loop needs.

    All adapters come from ``app.adapters.factory`` so a future engine/storage
    swap touches only the factory — and ``settings.download_speed_limit`` flows
    into the downloader here (the stub maps it to a per-chunk delay).
    """
    settings = settings or get_settings()
    engine = get_engine(settings.database_url)
    downloader = get_downloader(settings)
    storage = get_storage(settings)
    service = DownloadService(
        token_provider=get_one_time_token_provider(settings),
        storage=storage,
        downloader=downloader,
        engine=engine,
    )
    return engine, downloader, storage, service


def run_forever(
    run_once_fn: Callable[[], int],
    stop: threading.Event,
    *,
    poll_interval: float = _POLL_INTERVAL_DEFAULT,
) -> None:
    """Poll ``run_once_fn`` until ``stop`` is set; returns after the batch.

    A batch that handled tasks loops again immediately; an idle batch sleeps
    ``poll_interval`` (interruptible by ``stop``). Exceptions from
    ``run_once_fn`` are logged and the loop continues.
    """
    while not stop.is_set():
        try:
            handled = run_once_fn()
        except Exception:  # a daemon must survive one bad iteration
            logger.exception("worker iteration failed; continuing")
            handled = 0
        if handled == 0 and stop.wait(poll_interval):
            break


def main() -> None:
    """Start the worker daemon: wire deps, install signal handlers, poll."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    engine, downloader, storage, service = build_worker_deps(settings)
    stop = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        logger.info("received signal %s; finishing current batch and stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    logger.info(
        "Koi Fetch worker starting "
        "(poll interval %.1fs, max concurrent %d, retries %d)",
        settings.worker_poll_interval,
        settings.max_concurrent,
        MAX_RETRIES,
    )

    def _run_once() -> int:
        return run_once(
            engine,
            downloader,
            storage,
            event_hub,
            max_concurrent=settings.max_concurrent,
            download_service=service,
        )

    run_forever(
        _run_once,
        stop,
        poll_interval=settings.worker_poll_interval,
    )
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
