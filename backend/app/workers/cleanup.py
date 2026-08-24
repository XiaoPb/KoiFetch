"""Bubble cleanup and stale-task expiry (Task 12).

The scheduled cleanup pass that keeps the temporary Bubble storage bounded and
recovers from crashed workers:

* **Expired bubble files** — every file under the bubble roots whose mtime is
  older than ``expire_hours`` (the configured ``BUBBLE_EXPIRE_HOURS`` retention
  window) is deleted, best-effort. Pond roots are permanent and never swept.
* **Stale ``downloading`` tasks → ``expired``** — the worker-crash recovery
  path. A row stuck in ``downloading`` longer than ``stale_minutes``
  (``STALE_DOWNLOAD_MINUTES``) is marked ``expired`` via the domain
  :func:`transition`, after which the state graph's ``expired -> pending`` edge
  lets the user re-download. There is deliberately **no** direct
  ``downloading -> pending`` edge (see ``transitions.py``); cleanup is the only
  sanctioned way a crashed download re-enters the queue.
* **``completed`` tasks → ``expired``** — once the task's retention window
  elapsed (anchored on ``completed_at``, falling back to ``created_at``), the
  temp bubble file is gone (either swept here or already absent) and the
  download link is dead; the row moves to the honest terminal state. Its
  ``bubble_path`` file is removed best-effort if still present.
* **Stale ``pending`` tasks → ``expired``** — a queued task that was never
  claimed within the expiry window (its data source is long gone) is expired
  rather than left pending forever.

Design decisions (stable contract for later tasks):

* **Where cleanup runs.** The APScheduler job runs *inside the worker daemon*
  (``app.workers.main``) because compose defines exactly one worker service
  running ``python -m app.workers.main`` (asserted by ``test_compose.py``) and
  a separate container would need compose changes and duplicate the
  engine/storage wiring. A standalone entrypoint ``python -m
  app.workers.cleanup --once`` is provided for ops/tests and for environments
  where the worker is not running. The job interval is
  ``settings.cleanup_interval_minutes`` (default 60, per the PRD's hourly
  cleanup).
* **Idempotency.** A second pass removes nothing and expires nothing: swept
  files are gone, and rows that moved to ``expired`` are no longer selected.
  Missing files never raise — deletion races (a file vanishing between listing
  and delete) are caught as ``FileNotFoundError`` and counted as
  ``files_already_absent``.
* **All status moves go through :func:`transition`.** Cleanup selects only
  ``downloading``/``completed``/``pending`` rows (whose edges to ``expired``
  exist in the graph), so an illegal move would raise loudly instead of
  corrupting a row.
* **Staleness anchors on ``created_at``.** The v1 model has no heartbeat /
  ``updated_at`` column, so ``downloading`` staleness is measured from row
  creation. ``stale_download_minutes`` must therefore be far larger than the
  worker poll interval and a realistic download time (default 30 min; the stub
  downloader completes in milliseconds). A real engine with long downloads
  should add a heartbeat column before relying on this pass.
* **No WebSocket events.** Cleanup does not publish through the event hub: in
  the Docker topology the worker is a separate process whose hub is
  process-local, so events would never reach API subscribers. The WS endpoint
  reconciles via the DB-backed snapshot on connect and HTTP polling, so
  cleanup-driven state changes are visible without events (documented in
  ``download_events.py``).
* **Ordering.** The file sweep runs first; task expiry follows. Completed rows
  whose file was just swept find it already absent and simply move to
  ``expired`` (no double-counting: the sweep counted the removal).
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import signal
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import Engine, select

from app.adapters.factory import get_storage
from app.adapters.protocols import StorageAdapter
from app.domain import DownloadStatus, MediaType, transition
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.database import get_engine, session_scope
from app.infrastructure.models import DownloadTask

__all__ = [
    "CleanupReport",
    "TaskExpiryCounts",
    "build_cleanup_scheduler",
    "main",
    "run_cli",
    "run_cleanup",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskExpiryCounts:
    """Tasks moved to ``expired`` this pass, by category."""

    downloading_stale: int = 0
    completed_expired: int = 0
    pending_stale: int = 0

    @property
    def total(self) -> int:
        return self.downloading_stale + self.completed_expired + self.pending_stale


@dataclass(frozen=True)
class CleanupReport:
    """Counts of one cleanup pass; a zeroed report means nothing to do."""

    files_removed: int = 0
    files_already_absent: int = 0
    tasks_expired_by_category: TaskExpiryCounts = field(default_factory=TaskExpiryCounts)

    @property
    def tasks_expired(self) -> int:
        return self.tasks_expired_by_category.total


def run_cleanup(
    engine: Engine,
    storage: StorageAdapter | None = None,
    *,
    now: datetime | None = None,
    expire_hours: int | None = None,
    stale_minutes: int | None = None,
) -> CleanupReport:
    """Run one cleanup pass; return the counts.

    ``now``/``expire_hours``/``stale_minutes`` default to the current UTC time
    and the configured settings values, so the scheduled job and the CLI can
    call this with no arguments while tests inject fixed values. ``storage``
    defaults to the configured storage adapter (all tests pass one explicitly).
    """
    now = now or datetime.now(timezone.utc)
    expire_hours = (
        get_settings().bubble_expire_hours if expire_hours is None else expire_hours
    )
    stale_minutes = (
        get_settings().stale_download_minutes if stale_minutes is None else stale_minutes
    )
    storage = storage or get_storage()

    report = _sweep_bubble_files(storage, now, expire_hours)
    report = _expire_stale_tasks(engine, storage, now, expire_hours, stale_minutes, report)

    logger.info(
        "cleanup: removed %d bubble file(s), %d already absent; "
        "expired %d task(s) (downloading-stale=%d, completed=%d, pending-stale=%d)",
        report.files_removed,
        report.files_already_absent,
        report.tasks_expired,
        report.tasks_expired_by_category.downloading_stale,
        report.tasks_expired_by_category.completed_expired,
        report.tasks_expired_by_category.pending_stale,
    )
    return report


def _sweep_bubble_files(
    storage: StorageAdapter, now: datetime, expire_hours: int
) -> CleanupReport:
    """Delete bubble files older than the retention window (best-effort).

    Walks every bubble root through the adapter's own listing (never the pond
    roots) and deletes files whose mtime is strictly older than
    ``now - expire_hours``. A file that vanishes between listing and deletion
    raises ``FileNotFoundError`` from the adapter; that is caught and counted
    as ``files_already_absent`` — the pass never crashes on a missing file.
    Any other ``OSError`` (e.g. permissions) is logged and skipped so one
    un-deletable file cannot abort the whole pass.
    """
    cutoff = now - timedelta(hours=expire_hours)
    removed = 0
    already_absent = 0
    for media_type in MediaType:
        for path in storage.list_files(media_type):
            try:
                old = path.stat().st_mtime < cutoff.timestamp()
            except FileNotFoundError:  # vanished before we could even stat it
                already_absent += 1
                continue
            if not old:
                continue  # fresh file — keep
            try:
                storage.delete(path)
                removed += 1
            except FileNotFoundError:  # vanished between listing and delete
                already_absent += 1
            except OSError:
                logger.debug("could not remove %s during cleanup", path, exc_info=True)
    return CleanupReport(files_removed=removed, files_already_absent=already_absent)


def _expire_stale_tasks(
    engine: Engine,
    storage: StorageAdapter,
    now: datetime,
    expire_hours: int,
    stale_minutes: int,
    report: CleanupReport,
) -> CleanupReport:
    """Mark stale tasks ``expired`` (all moves through the domain transition).

    * ``downloading`` rows older than ``stale_minutes`` (crashed worker).
    * ``pending`` rows older than the ``expire_hours`` window (never claimed).
    * ``completed`` rows whose retention anchor (``completed_at``, falling
      back to ``created_at``) is older than the window — their bubble file is
      gone (swept above or already absent); the file is removed best-effort if
      it still exists (counted as a removal).

    Rows in any other status (``failed``, ``expired``) are never selected, and
    every status move goes through :func:`transition` so an illegal move would
    raise loudly instead of corrupting a row.
    """
    counts = report.tasks_expired_by_category
    files_removed = report.files_removed

    stale_cutoff = now - timedelta(minutes=stale_minutes)
    expire_cutoff = now - timedelta(hours=expire_hours)

    with session_scope(engine) as session:
        downloading = session.scalars(
            select(DownloadTask).where(
                DownloadTask.status == DownloadStatus.DOWNLOADING,
                DownloadTask.created_at < stale_cutoff,
            )
        )
        for row in downloading:
            row.status = transition(row.status, DownloadStatus.EXPIRED)
            counts = _bump(counts, "downloading_stale")

        pending = session.scalars(
            select(DownloadTask).where(
                DownloadTask.status == DownloadStatus.PENDING,
                DownloadTask.created_at < expire_cutoff,
            )
        )
        for row in pending:
            row.status = transition(row.status, DownloadStatus.EXPIRED)
            counts = _bump(counts, "pending_stale")

        # Completed rows are few in v1; filter the anchor (completed_at or
        # created_at) in Python so a NULL completed_at never mis-expires a
        # freshly created row.
        completed = session.scalars(
            select(DownloadTask).where(DownloadTask.status == DownloadStatus.COMPLETED)
        )
        for row in completed:
            anchor = row.completed_at or row.created_at
            if anchor >= expire_cutoff:
                continue
            row.status = transition(row.status, DownloadStatus.EXPIRED)
            counts = _bump(counts, "completed_expired")
            if row.bubble_path:
                bubble = Path(row.bubble_path)
                if storage.exists(bubble):
                    try:
                        storage.delete(bubble)
                    except FileNotFoundError:  # vanished under us — fine
                        pass
                    except OSError:
                        logger.debug(
                            "could not remove %s during cleanup", bubble, exc_info=True
                        )
                    else:
                        files_removed += 1

    return CleanupReport(
        files_removed=files_removed,
        files_already_absent=report.files_already_absent,
        tasks_expired_by_category=counts,
    )


def _bump(counts: TaskExpiryCounts, category: str) -> TaskExpiryCounts:
    """Return ``counts`` with ``category`` incremented (immutable dataclass)."""
    return dataclasses.replace(
        counts, **{category: getattr(counts, category) + 1}
    )


def build_cleanup_scheduler(
    engine: Engine,
    storage: StorageAdapter,
    *,
    expire_hours: int,
    stale_minutes: int,
    interval_minutes: int,
) -> BackgroundScheduler:
    """Build a stopped APScheduler with the cleanup job registered.

    The job runs :func:`run_cleanup` on an interval, with the settings values
    bound at scheduling time so a settings change mid-run never surprises the
    daemon. ``coalesce=True`` + ``max_instances=1`` keep a slow pass from
    piling up overlapping runs (cleanup is idempotent, so dropping a missed
    tick is harmless). The caller starts and shuts the scheduler down.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_cleanup,
        trigger="interval",
        minutes=interval_minutes,
        args=[engine],
        kwargs={
            "storage": storage,
            "expire_hours": expire_hours,
            "stale_minutes": stale_minutes,
        },
        id="bubble-cleanup",
        name="bubble cleanup and stale task expiry",
        coalesce=True,
        max_instances=1,
    )
    return scheduler


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.cleanup",
        description="Bubble cleanup and stale download-task expiry.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one cleanup pass and exit (default: run the scheduler daemon)",
    )
    parser.add_argument(
        "--expire-hours",
        type=int,
        default=None,
        help="override the bubble retention window in hours",
    )
    parser.add_argument(
        "--stale-minutes",
        type=int,
        default=None,
        help="override the stale-downloading threshold in minutes",
    )
    return parser


def run_cli(settings: Settings, argv: list[str] | None = None) -> int:
    """Run the cleanup CLI against ``settings``; return a process exit code.

    ``--once`` runs a single pass and exits (the ops/testing mode). Without it
    the CLI starts the scheduler daemon and blocks until SIGTERM/SIGINT — for
    environments where the worker daemon is not running. ``--expire-hours`` /
    ``--stale-minutes`` override the settings values for one-shot runs.
    """
    args = _build_parser().parse_args(argv)

    engine = get_engine(settings.database_url)
    storage = get_storage(settings)
    expire_hours = args.expire_hours or settings.bubble_expire_hours
    stale_minutes = args.stale_minutes or settings.stale_download_minutes

    if args.once:
        report = run_cleanup(
            engine,
            storage,
            expire_hours=expire_hours,
            stale_minutes=stale_minutes,
        )
        logger.info(
            "cleanup --once finished: removed %d file(s), expired %d task(s)",
            report.files_removed,
            report.tasks_expired,
        )
        return 0

    scheduler = build_cleanup_scheduler(
        engine,
        storage,
        expire_hours=expire_hours,
        stale_minutes=stale_minutes,
        interval_minutes=settings.cleanup_interval_minutes,
    )
    stop = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        logger.info("received signal %s; stopping cleanup scheduler", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    scheduler.start()
    logger.info(
        "cleanup scheduler started (every %d minutes)",
        settings.cleanup_interval_minutes,
    )
    try:
        while not stop.is_set():
            stop.wait(1.0)
    finally:
        scheduler.shutdown(wait=False)
    logger.info("cleanup scheduler stopped")
    return 0


def main() -> None:
    """Entrypoint for ``python -m app.workers.cleanup``.

    Args are parsed *before* settings are read so ``--help`` works without a
    configured environment; a real run then fails fast if the secrets are
    missing (the documented Settings fail-fast contract).
    """
    args = _build_parser().parse_args()
    raise SystemExit(run_cli(get_settings(), None))


if __name__ == "__main__":
    main()
