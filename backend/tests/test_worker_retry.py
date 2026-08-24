"""Worker retry tests (Task 11): the failed -> pending re-queue policy.

Design (documented in ``app.workers.worker``): the worker re-queues a failed
download (``failed -> pending`` via the domain transition) while
``retry_count < MAX_RETRIES`` (3); at the cap the task stays ``failed`` with
``error_message`` set. ``retry_count`` increments exactly once per download
attempt; a re-queued pending row returns clean (``error_message`` cleared, so
the progress endpoint never shows a stale error); only the *terminal* failure
publishes a WS ``error`` event (automatic retries are silent — the client
reconciles via the progress endpoint).
"""

from pathlib import Path

from app.domain import DownloadProgress, DownloadResult, DownloadStatus
from app.infrastructure.database import session_scope
from app.infrastructure.models import DownloadTask
from app.workers.worker import MAX_RETRIES, run_once
from tests.conftest import (
    FakeHub,
    load_download,
    seed_download,
    seed_parse_task,
)


class FailingDownloader:
    """Raises on the first ``failures`` calls, then succeeds like the stub."""

    def __init__(self, failures=1, total_bytes=2048, chunk_size=1024):
        self.failures = failures
        self.calls = 0
        self.total_bytes = total_bytes
        self.chunk_size = chunk_size

    def download(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("simulated download failure")
        target = Path(request.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open("wb") as out:
            while written < self.total_bytes:
                count = min(self.chunk_size, self.total_bytes - written)
                out.write(b"r" * count)
                written += count
                if request.progress_callback is not None:
                    request.progress_callback(
                        DownloadProgress(
                            download_id=request.download_id,
                            status=DownloadStatus.DOWNLOADING,
                            progress=written / self.total_bytes * 100.0,
                            speed=1024.0,
                            downloaded_bytes=written,
                            total_bytes=self.total_bytes,
                        )
                    )
        return DownloadResult(
            download_id=request.download_id,
            task_id=request.command.task_id,
            title=request.title,
            media_type=request.media_type,
            format=request.command.format,
            quality=request.command.quality,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            speed=1024.0,
            total_bytes=self.total_bytes,
            downloaded_bytes=self.total_bytes,
            retry_count=0,
            error_message=None,
        )


class AlwaysFailingDownloader:
    def __init__(self):
        self.calls = 0

    def download(self, request):
        self.calls += 1
        raise RuntimeError("simulated download failure")


class TestRetryPolicy:
    def test_max_retries_cap_is_three(self):
        assert MAX_RETRIES == 3

    def test_first_failure_requeues_pending_and_increments_once(self, env):
        settings, engine, storage, token_provider = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()
        downloader = FailingDownloader(failures=1)

        handled = run_once(
            engine, downloader, storage, hub, token_provider=token_provider
        )

        assert handled == 1
        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.PENDING  # re-queued for retry
        assert row.retry_count == 1  # exactly one per attempt
        assert row.error_message is None  # a pending row carries no stale error
        # an automatic retry is not announced as a terminal error
        assert hub.for_download(download_id) == []

    def test_success_after_retry_completes(self, env):
        settings, engine, storage, token_provider = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()
        downloader = FailingDownloader(failures=1)

        run_once(engine, downloader, storage, hub, token_provider=token_provider)
        run_once(engine, downloader, storage, hub, token_provider=token_provider)

        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.COMPLETED
        assert row.retry_count == 1  # counts the failed attempt, not reset
        stored = Path(row.bubble_path)
        assert stored.is_file()
        assert stored.read_bytes() == b"r" * 2048

    def test_failure_beyond_cap_stays_failed(self, env):
        settings, engine, storage, token_provider = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()
        downloader = AlwaysFailingDownloader()

        for _ in range(3):
            run_once(engine, downloader, storage, hub, token_provider=token_provider)

        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.FAILED
        assert row.retry_count == MAX_RETRIES
        assert downloader.calls == MAX_RETRIES
        errors = [e for e in hub.for_download(download_id) if e["type"] == "error"]
        assert len(errors) == 1  # only the terminal failure announces itself
        assert errors[0]["data"]["code"] == 5002
        assert errors[0]["data"]["error_message"] == "simulated download failure"
        assert errors[0]["data"]["status"] == "failed"
        # a failed row is never reclaimed
        assert (
            run_once(engine, downloader, storage, hub, token_provider=token_provider)
            == 0
        )
        assert downloader.calls == MAX_RETRIES

    def test_preexisting_retry_count_counts_toward_cap(self, env):
        settings, engine, storage, token_provider = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, retry_count=2)
        hub = FakeHub()
        downloader = AlwaysFailingDownloader()

        run_once(engine, downloader, storage, hub, token_provider=token_provider)

        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.FAILED
        assert row.retry_count == MAX_RETRIES
        assert downloader.calls == 1  # one attempt, cap reached immediately

    def test_retry_count_increments_once_per_failed_attempt(self, env):
        settings, engine, storage, token_provider = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()
        downloader = FailingDownloader(failures=2)

        for _ in range(3):  # two failures, then a success
            run_once(engine, downloader, storage, hub, token_provider=token_provider)

        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.COMPLETED
        assert row.retry_count == 2
        assert downloader.calls == 3

    def test_row_leaving_downloading_midflight_is_not_touched(self, env):
        # A row that leaves DOWNLOADING mid-flight (e.g. cleanup swept it to
        # expired) must not be re-queued or failed by this worker — recovery
        # is Task 12's job, and the domain graph forbids downloading -> pending.
        settings, engine, storage, token_provider = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()

        class ExpireThenFailDownloader:
            """Simulates a concurrent actor expiring the row mid-download."""

            def __init__(self, engine):
                self.engine = engine

            def download(self, request):
                with session_scope(self.engine) as session:
                    row = session.get(DownloadTask, request.download_id)
                    row.status = DownloadStatus.EXPIRED
                raise RuntimeError("simulated download failure")

        run_once(
            engine, ExpireThenFailDownloader(engine), storage, hub,
            token_provider=token_provider,
        )

        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.EXPIRED  # untouched by the worker
        assert row.retry_count == 0
