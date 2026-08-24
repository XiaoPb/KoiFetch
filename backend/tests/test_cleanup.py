"""Cleanup tests (Task 12): expired bubble-file sweep and stale task expiry.

Covers :mod:`app.workers.cleanup`:

* **File sweep** — only bubble files older than ``expire_hours`` (mtime-based)
  are removed; fresh bubble files and *all* pond files are kept. A file that
  vanishes between listing and deletion (the ``FileNotFoundError`` race) is
  counted as ``files_already_absent`` and never crashes the pass.
* **Task expiry** — stale ``downloading`` rows (crashed worker) are marked
  ``expired`` via the domain transition (the crash-recovery path);
  ``completed`` rows whose retention window elapsed are marked ``expired`` and
  their bubble file removed if still present; ``pending`` rows older than the
  expiry window are marked ``expired``. Fresh rows are never touched, and rows
  in states cleanup must not move (``failed``) are left alone.
* **Idempotency** — a second run removes nothing and expires nothing.
* **Scheduling** — :func:`build_cleanup_scheduler` registers exactly one
  APScheduler interval job bound to the given interval/settings values.
* **CLI** — ``run_cli(settings, ["--once", ...])`` runs one cleanup pass
  against a temp engine and exits 0.

All timestamps are injected via ``now=`` so tests never sleep; file mtimes and
row ``created_at``/``completed_at`` are backdated relative to the same fixed
``NOW``.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.triggers.interval import IntervalTrigger

from app.adapters.factory import get_storage
from app.domain import DownloadStatus, MediaType
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask
from app.workers import cleanup as cleanup_module
from app.workers.cleanup import build_cleanup_scheduler, run_cleanup
from tests.conftest import (
    load_download,
    make_settings,
    seed_download,
    seed_parse_task,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
EXPIRE_HOURS = 24
STALE_MINUTES = 30


def backdate_file(path: Path, hours_back: float) -> None:
    """Set a file's mtime to ``hours_back`` before :data:`NOW`."""
    stamp = (NOW - timedelta(hours=hours_back)).timestamp()
    os.utime(path, (stamp, stamp))


def write_bubble(
    storage, media_type: MediaType, name: str, *, hours_back: float | None = None
) -> Path:
    """Create a bubble file (optionally backdated) and return its path."""
    path = storage.resolve_bubble(media_type, name)
    path.write_bytes(b"x" * 16)
    if hours_back is not None:
        backdate_file(path, hours_back)
    return path


def seed_completed_with_bubble(
    engine, storage, *, title="示例视频", hours_back: float = EXPIRE_HOURS + 6
) -> tuple[str, Path]:
    """A completed row with an old bubble file; return (download_id, path)."""
    task_id = seed_parse_task(engine, title=title)
    download_id = seed_download(
        engine,
        task_id=task_id,
        status=DownloadStatus.COMPLETED,
        progress=100.0,
        created_at=NOW - timedelta(hours=hours_back),
        completed_at=NOW - timedelta(hours=hours_back),
    )
    path = write_bubble(storage, MediaType.VIDEO, f"{download_id[:8]}.mp4", hours_back=hours_back)
    with session_scope(engine) as session:
        session.get(DownloadTask, download_id).bubble_path = str(path)
    return download_id, path


class TestFileCleanup:
    def test_removes_only_old_bubble_files_keeps_pond(self, env):
        settings, engine, storage, _ = env
        old = write_bubble(storage, MediaType.VIDEO, "old.mp4", hours_back=30)
        fresh = write_bubble(storage, MediaType.VIDEO, "fresh.mp4", hours_back=1)
        pond_old = storage.pond_root(MediaType.VIDEO) / "old-pond.mp4"
        pond_old.write_bytes(b"pond")
        backdate_file(pond_old, 30)

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert report.files_removed == 1
        assert not old.exists()
        assert fresh.exists()
        assert pond_old.exists()  # pond is permanent; never swept
        assert report.files_already_absent == 0

    def test_old_files_under_every_media_type_swept(self, env):
        settings, engine, storage, _ = env
        paths = [
            write_bubble(storage, MediaType.VIDEO, "old.mp4", hours_back=30),
            write_bubble(storage, MediaType.IMAGE, "old.jpg", hours_back=30),
            write_bubble(storage, MediaType.MUSIC, "old.mp3", hours_back=30),
        ]

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert report.files_removed == 3
        assert all(not p.exists() for p in paths)

    def test_file_at_exact_cutoff_is_kept(self, env):
        # Only files *strictly older* than the window are swept.
        settings, engine, storage, _ = env
        boundary = write_bubble(storage, MediaType.VIDEO, "boundary.mp4", hours_back=EXPIRE_HOURS)

        run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert boundary.exists()

    def test_vanished_file_counted_absent_not_crash(self, env, monkeypatch):
        settings, engine, storage, _ = env
        old = write_bubble(storage, MediaType.VIDEO, "vanish.mp4", hours_back=30)
        # Simulate the file disappearing between listing and deletion.
        monkeypatch.setattr(
            storage, "delete", lambda path: (_ for _ in ()).throw(FileNotFoundError(str(path)))
        )

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert report.files_removed == 0
        assert report.files_already_absent == 1

    def test_file_vanished_before_stat_counted_absent(self, env, monkeypatch):
        # A file that disappears between listing and the mtime check (an even
        # narrower race) is also counted absent, never a crash.
        settings, engine, storage, _ = env
        old = write_bubble(storage, MediaType.VIDEO, "gone-before-stat.mp4", hours_back=30)
        old.unlink()
        monkeypatch.setattr(
            storage,
            "list_files",
            lambda media_type: [old] if media_type is MediaType.VIDEO else [],
        )

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert report.files_removed == 0
        assert report.files_already_absent == 1


class TestTaskExpiry:
    def test_stale_downloading_expires(self, env):
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.DOWNLOADING,
            created_at=NOW - timedelta(minutes=STALE_MINUTES + 15),
        )

        report = run_cleanup(engine, storage, now=NOW, stale_minutes=STALE_MINUTES)

        assert load_download(engine, download_id).status is DownloadStatus.EXPIRED
        assert report.tasks_expired_by_category.downloading_stale == 1
        assert report.tasks_expired == 1

    def test_fresh_downloading_not_expired(self, env):
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.DOWNLOADING,
            created_at=NOW - timedelta(minutes=5),
        )

        run_cleanup(engine, storage, now=NOW, stale_minutes=STALE_MINUTES)

        assert load_download(engine, download_id).status is DownloadStatus.DOWNLOADING

    def test_completed_task_with_expired_bubble_expires_and_file_removed(self, env):
        settings, engine, storage, _ = env
        download_id, path = seed_completed_with_bubble(engine, storage)

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.EXPIRED
        assert not path.exists()
        # The sweep removed the old file; the task step found it already gone.
        assert report.files_removed == 1
        assert report.tasks_expired_by_category.completed_expired == 1

    def test_completed_task_with_missing_bubble_file_expires(self, env):
        # The bubble file is gone (swept earlier / deleted out-of-band): the
        # completed task is still expired since its temp file no longer exists.
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        missing = storage.resolve_bubble(MediaType.VIDEO, "gone.mp4")  # never created
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            created_at=NOW - timedelta(hours=30),
            completed_at=NOW - timedelta(hours=30),
        )
        with session_scope(engine) as session:
            session.get(DownloadTask, download_id).bubble_path = str(missing)

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert load_download(engine, download_id).status is DownloadStatus.EXPIRED
        assert report.tasks_expired_by_category.completed_expired == 1
        assert report.files_removed == 0

    def test_fresh_completed_task_kept(self, env):
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        fresh = write_bubble(storage, MediaType.VIDEO, "fresh.mp4", hours_back=1)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            created_at=NOW - timedelta(hours=1),
            completed_at=NOW - timedelta(hours=1),
        )
        with session_scope(engine) as session:
            session.get(DownloadTask, download_id).bubble_path = str(fresh)

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert load_download(engine, download_id).status is DownloadStatus.COMPLETED
        assert fresh.exists()
        assert report.tasks_expired == 0

    def test_stale_pending_expires(self, env):
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.PENDING,
            created_at=NOW - timedelta(hours=EXPIRE_HOURS + 6),
        )

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert load_download(engine, download_id).status is DownloadStatus.EXPIRED
        assert report.tasks_expired_by_category.pending_stale == 1

    def test_fresh_pending_kept(self, env):
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine, task_id=task_id, created_at=NOW - timedelta(hours=1)
        )

        run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert load_download(engine, download_id).status is DownloadStatus.PENDING

    def test_failed_rows_never_touched(self, env):
        # failed -> expired is illegal in the domain graph; cleanup must not
        # select failed rows at all.
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        failed = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.FAILED,
            retry_count=3,
            created_at=NOW - timedelta(hours=48),
        )
        expired = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.EXPIRED,
            created_at=NOW - timedelta(hours=48),
        )

        report = run_cleanup(engine, storage, now=NOW, expire_hours=EXPIRE_HOURS)

        assert load_download(engine, failed).status is DownloadStatus.FAILED
        assert load_download(engine, expired).status is DownloadStatus.EXPIRED
        assert report.tasks_expired == 0


class TestIdempotency:
    def test_second_run_removes_and_expires_nothing(self, env):
        settings, engine, storage, _ = env
        task_id = seed_parse_task(engine)
        seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.DOWNLOADING,
            created_at=NOW - timedelta(minutes=STALE_MINUTES + 15),
        )
        seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.PENDING,
            created_at=NOW - timedelta(hours=EXPIRE_HOURS + 6),
        )
        seed_completed_with_bubble(engine, storage)

        first = run_cleanup(
            engine, storage, now=NOW, expire_hours=EXPIRE_HOURS, stale_minutes=STALE_MINUTES
        )
        assert first.tasks_expired == 3

        second = run_cleanup(
            engine, storage, now=NOW, expire_hours=EXPIRE_HOURS, stale_minutes=STALE_MINUTES
        )

        assert second.files_removed == 0
        assert second.files_already_absent == 0
        assert second.tasks_expired == 0


class TestScheduler:
    def test_build_registers_one_interval_job(self, env):
        settings, engine, storage, _ = env
        scheduler = build_cleanup_scheduler(
            engine,
            storage,
            expire_hours=EXPIRE_HOURS,
            stale_minutes=STALE_MINUTES,
            interval_minutes=60,
        )
        scheduler.start()
        try:
            jobs = scheduler.get_jobs()
            assert len(jobs) == 1
            job = jobs[0]
            assert isinstance(job.trigger, IntervalTrigger)
            assert job.trigger.interval == timedelta(minutes=60)
            assert job.args == (engine,)
            assert job.kwargs == {
                "storage": storage,
                "expire_hours": EXPIRE_HOURS,
                "stale_minutes": STALE_MINUTES,
            }
        finally:
            scheduler.shutdown(wait=False)

    def test_build_respects_interval_from_settings(self, env):
        settings, engine, storage, _ = env
        scheduler = build_cleanup_scheduler(
            engine,
            storage,
            expire_hours=settings.bubble_expire_hours,
            stale_minutes=settings.stale_download_minutes,
            interval_minutes=settings.cleanup_interval_minutes,
        )
        scheduler.start()
        try:
            job = scheduler.get_jobs()[0]
            assert job.trigger.interval == timedelta(
                minutes=settings.cleanup_interval_minutes
            )
        finally:
            scheduler.shutdown(wait=False)


class TestCli:
    def _env_settings(self, tmp_path):
        settings = make_settings(
            database_url=f"sqlite:///{tmp_path / 'cli.db'}",
            video_storage_path=tmp_path / "pond/video",
            image_storage_path=tmp_path / "pond/image",
            music_storage_path=tmp_path / "pond/music",
            temp_video_path=tmp_path / "bubble/video",
            temp_image_path=tmp_path / "bubble/image",
            temp_music_path=tmp_path / "bubble/music",
        )
        engine = build_engine(settings.database_url)
        Base.metadata.create_all(engine)
        return settings, engine, get_storage(settings)

    def test_once_runs_cleanup_and_exits_zero(self, tmp_path):
        settings, engine, storage = self._env_settings(tmp_path)
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=30),
        )

        code = cleanup_module.run_cli(
            settings, ["--once", "--expire-hours", "24", "--stale-minutes", "30"]
        )

        assert code == 0
        assert load_download(engine, download_id).status is DownloadStatus.EXPIRED

    def test_once_uses_settings_defaults_without_overrides(self, tmp_path):
        settings, engine, storage = self._env_settings(tmp_path)
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=48),
        )

        code = cleanup_module.run_cli(settings, ["--once"])

        assert code == 0
        assert load_download(engine, download_id).status is DownloadStatus.EXPIRED

    def test_once_accepts_expire_hours_and_stale_minutes_overrides(self, tmp_path):
        settings, engine, storage = self._env_settings(tmp_path)
        task_id = seed_parse_task(engine)
        # Old enough to be pending-expired under the default 24 h window, but
        # fresh under the --expire-hours 100 override.
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=48),
        )

        code = cleanup_module.run_cli(
            settings, ["--once", "--expire-hours", "100", "--stale-minutes", "5"]
        )

        assert code == 0
        assert load_download(engine, download_id).status is DownloadStatus.PENDING
