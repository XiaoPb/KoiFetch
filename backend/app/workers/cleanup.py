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
  ``bubble_path`` file is removed best-effort if still present — but **only**
  when the path is inside a bubble root; a corrupt/foreign path (e.g. pointing
  into the pond) is skipped so permanent pond files are never deleted.
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
* **Expiry writes are atomic and status-guarded.** Each category is a single
  conditional ``UPDATE ... WHERE status = ...`` (mirroring the worker's
  :func:`app.workers.worker.claim_pending_tasks`), with the target status
  validated through :func:`transition`. A row that leaves its category
  mid-pass — e.g. the worker completing a download between cleanup's read and
  write — is never overwritten: the WHERE guard simply does not match. This
  mirrors the worker's own re-check (``worker.py`` ``_record_completion``
  abandons a row that left ``downloading``) and makes the pass safe to run
  concurrently with an active worker.
* **Idempotency.** A second pass removes nothing and expires nothing: swept
  files are gone, and rows that moved to ``expired`` are no longer selected.
  Missing files never raise — deletion races (a file vanishing between listing
  and delete) are caught as ``FileNotFoundError`` and counted as
  ``files_already_absent``.
* **Staleness anchors on ``created_at``.** The v1 model has no heartbeat /
  ``updated_at`` column and the worker claim does not update ``created_at``,
  so a ``downloading`` task is measured from row *creation*: the risk window is
  **queueing time + download time** and has no hard upper bound. The default
  (``STALE_DOWNLOAD_MINUTES`` = 60) assumes a task is claimed and finished
  within an hour of submission — true for the stub downloader (milliseconds);
  a real engine with long downloads should add a heartbeat column before
  relying on this pass, and operators should size the threshold against their
  worst realistic queue + download duration.
* **No WebSocket events.** Cleanup does not publish through the event hub: in
  the Docker topology the worker is a separate process whose hub is
  process-local, so events would never reach API subscribers. The WS endpoint
  reconciles via the DB-backed snapshot on connect and HTTP polling, so
  cleanup-driven state changes are visible without events (documented in
  ``download_events.py``).
* **Ordering.** The file sweep runs first; task expiry follows. Completed rows
  whose file was just swept find it already absent and simply move to
  ``expired`` (no double-counting: the sweep counted the removal). The
  just-expired completed rows' bubble files are removed *after* the status
  flip commits, so a crash mid-pass never leaves files deleted under rows that
  are still ``completed``.
* **CLI daemon mode.** ``python -m app.workers.cleanup`` without ``--once``
  starts a second cleanup daemon for environments where the worker is not
  running. Running it alongside the worker means the pass fires twice per
  interval (idempotent, so harmless, but worth knowing); prefer the worker's
  built-in scheduler and reserve the CLI for ``--once`` ops runs.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import Engine, and_, or_, select, update

from app.adapters.factory import get_storage
from app.adapters.protocols import StorageAdapter
from app.domain import DownloadStatus, MediaType, is_within, transition
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
    Any other ``OSError`` (e.g. permissions) while stat-ing *or* deleting is
    logged and skipped so one unreadable/un-deletable file cannot abort the
    whole pass.
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
            except OSError:
                logger.debug("could not stat %s during cleanup", path, exc_info=True)
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
    """Mark stale tasks ``expired``; every write is atomic and status-guarded.

    * ``downloading`` rows older than ``stale_minutes`` (crashed worker).
    * ``pending`` rows older than the ``expire_hours`` window (never claimed).
    * ``completed`` rows whose retention anchor (``completed_at``, falling
      back to ``created_at``) is older than the window.

    Each category is one conditional ``UPDATE ... WHERE status = ...`` with the
    target status validated through :func:`transition` — mirroring the
    worker's atomic claim — so a row that leaves its category between cleanup's
    read and write (e.g. the worker completing a download mid-pass) is never
    overwritten to ``expired``. ``RETURNING`` hands back exactly the rows this
    statement flipped; their bubble files are removed afterwards, contained to
    the bubble roots (a foreign path — e.g. into the pond — is skipped and
    counted as already absent; permanent pond files are never deleted).
    """
    stale_cutoff = now - timedelta(minutes=stale_minutes)
    expire_cutoff = now - timedelta(hours=expire_hours)
    files_removed = report.files_removed
    files_already_absent = report.files_already_absent

    with session_scope(engine) as session:
        stale = session.execute(
            update(DownloadTask)
            .where(
                DownloadTask.status == DownloadStatus.DOWNLOADING,
                DownloadTask.created_at < stale_cutoff,
            )
            .values(
                status=transition(DownloadStatus.DOWNLOADING, DownloadStatus.EXPIRED)
            )
        )
        downloading_stale = stale.rowcount

        never_claimed = session.execute(
            update(DownloadTask)
            .where(
                DownloadTask.status == DownloadStatus.PENDING,
                DownloadTask.created_at < expire_cutoff,
            )
            .values(status=transition(DownloadStatus.PENDING, DownloadStatus.EXPIRED))
        )
        pending_stale = never_claimed.rowcount

        # Retention anchor: completed_at, falling back to created_at — a NULL
        # completed_at must never expire a freshly created row.
        anchor_expired = or_(
            and_(
                DownloadTask.completed_at.is_(None),
                DownloadTask.created_at < expire_cutoff,
            ),
            DownloadTask.completed_at < expire_cutoff,
        )
        completed = session.execute(
            update(DownloadTask)
            .where(
                DownloadTask.status == DownloadStatus.COMPLETED,
                anchor_expired,
            )
            .values(status=transition(DownloadStatus.COMPLETED, DownloadStatus.EXPIRED))
            .returning(DownloadTask.download_id)
        )
        completed_ids = list(completed.scalars())

    # Best-effort bubble-file removal for the just-expired completed rows, run
    # after the status flip committed so a crash mid-pass never leaves files
    # deleted under rows that are still ``completed``.
    if completed_ids:
        with session_scope(engine) as session:
            rows = session.scalars(
                select(DownloadTask).where(
                    DownloadTask.download_id.in_(completed_ids)
                )
            )
            for row in rows:
                if not row.bubble_path:
                    continue
                bubble = Path(row.bubble_path)
                if not _is_bubble_path(storage, bubble):
                    # Out of the bubble roots (corrupt/foreign path): never
                    # delete — pond is permanent. Counted as already absent.
                    files_already_absent += 1
                    continue
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
        files_already_absent=files_already_absent,
        tasks_expired_by_category=TaskExpiryCounts(
            downloading_stale=downloading_stale,
            completed_expired=len(completed_ids),
            pending_stale=pending_stale,
        ),
    )


def _is_bubble_path(storage: StorageAdapter, path: Path) -> bool:
    """True when ``path`` lives inside one of the adapter's bubble roots.

    The invariant that matters is "never delete pond files", so *any* bubble
    root counts — bucket attribution (which media type owns the row) is not
    required to safely remove an actual bubble temp file.
    """
    return any(
        is_within(storage.bubble_root(media_type), path) for media_type in MediaType
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


def _positive_int(value: str) -> int:
    """Argparse type: a positive integer (>= 1).

    The CLI is a second entry point to a file-deleting tool, outside the
    Settings ``ge=1`` validation — a 0 or negative ``--expire-hours`` would
    silently widen the sweep to *every* bubble file (a negative window puts
    the cutoff in the future). Reject such values with a clear argparse error
    (exit code 2) instead of letting them through.
    """
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


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
        type=_positive_int,
        default=None,
        help="override the bubble retention window in hours (>= 1)",
    )
    parser.add_argument(
        "--stale-minutes",
        type=_positive_int,
        default=None,
        help="override the stale-downloading threshold in minutes (>= 1)",
    )
    return parser


def run_cli(settings: Settings, argv: list[str] | None = None) -> int:
    """Run the cleanup CLI against ``settings``; return a process exit code.

    ``--once`` runs a single pass and exits (the ops/testing mode). Without it
    the CLI starts the scheduler daemon and blocks until SIGTERM/SIGINT — for
    environments where the worker daemon is not running. ``--expire-hours`` /
    ``--stale-minutes`` override the settings values for one-shot runs and
    must be >= 1 (argparse rejects 0/negative with exit code 2).
    """
    return _run_with_args(settings, _build_parser().parse_args(argv))


def _run_with_args(settings: Settings, args: argparse.Namespace) -> int:
    engine = get_engine(settings.database_url)
    storage = get_storage(settings)
    expire_hours = (
        args.expire_hours
        if args.expire_hours is not None
        else settings.bubble_expire_hours
    )
    stale_minutes = (
        args.stale_minutes
        if args.stale_minutes is not None
        else settings.stale_download_minutes
    )

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
    raise SystemExit(_run_with_args(get_settings(), args))


if __name__ == "__main__":
    main()
