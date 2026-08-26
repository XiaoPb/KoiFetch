"""Worker tests (Task 11): claiming, download execution, progress persistence,
hub events, and the polling-loop wiring.

Covers :mod:`app.workers.worker` and :mod:`app.workers.main`:

* ``claim_pending_tasks`` — atomically claims ``pending`` rows up to a limit,
  never touches ``downloading`` rows, never over-claims, and claims disjointly
  from concurrent claimers.
* ``run_once`` — executes claimed tasks through the downloader adapter, writing
  the bubble file, persisting progress/speed/bytes during the download,
  recording completion (status/progress/bubble_path/completed_at) and issuing a
  short-lived token in the WS ``complete`` event. A DB outage during progress
  writes must not abort the transfer.
* Main-loop wiring — ``build_worker_deps`` pipes ``download_speed_limit`` into
  the stub downloader; ``run_forever`` polls until the stop event fires and
  survives transient iteration errors; ``schema_ready`` fails fast on an
  un-migrated database.
* ``_publish`` — reuses one per-thread event loop and delivers to real
  :class:`DownloadEventHub` subscribers.

The happy path uses the real :class:`StubDownloaderAdapter` with small
size/chunk parameters; deterministic mid-flight observation, failure and
expiry scenarios use small fake downloaders (see the ``app.workers.worker``
module docstring for why the worker processes one batch sequentially).
"""

import asyncio
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.adapters.downloader_stub import StubDownloaderAdapter
from app.api.responses import CODE_FILE_TOKEN_INVALID
from app.application.download_events import DownloadEventHub
from app.application.download_service import DownloadService
from app.domain import DownloadProgress, DownloadResult, DownloadStatus
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask
from app.workers import worker as worker_module
from app.workers.main import build_worker_deps, run_forever, schema_ready
from app.workers.worker import _publish, claim_pending_tasks, run_once
from tests.conftest import (
    FakeHub,
    VIDEO_URL,
    expected_stub_bytes,
    load_download,
    make_settings,
    seed_download,
    seed_parse_task,
)

STUB_TOTAL = 4096
STUB_CHUNK = 1024


class ObservingDownloader:
    """Writes chunks and fires progress callbacks, snapshotting the persisted
    row after each non-final chunk to prove the callback wrote the DB."""

    def __init__(self, engine, total_bytes=STUB_TOTAL, chunk_size=STUB_CHUNK):
        self.engine = engine
        self.total_bytes = total_bytes
        self.chunk_size = chunk_size
        self.observed = []  # (progress, speed, downloaded_bytes) mid-flight

    def download(self, request):
        target = Path(request.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open("wb") as out:
            while written < self.total_bytes:
                count = min(self.chunk_size, self.total_bytes - written)
                out.write(b"z" * count)
                written += count
                if request.progress_callback is not None:
                    request.progress_callback(
                        DownloadProgress(
                            download_id=request.download_id,
                            status=DownloadStatus.DOWNLOADING,
                            progress=written / self.total_bytes * 100.0,
                            speed=2048.0,
                            downloaded_bytes=written,
                            total_bytes=self.total_bytes,
                        )
                    )
                if written < self.total_bytes:
                    with session_scope(self.engine) as session:
                        row = session.get(DownloadTask, request.download_id)
                        self.observed.append(
                            (row.progress, row.speed, row.downloaded_bytes)
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
            speed=2048.0,
            total_bytes=self.total_bytes,
            downloaded_bytes=self.total_bytes,
            retry_count=0,
            error_message=None,
        )


def stub_downloader() -> StubDownloaderAdapter:
    return StubDownloaderAdapter(total_bytes=STUB_TOTAL, chunk_size=STUB_CHUNK)


class TestClaiming:
    def test_claims_pending_rows_up_to_limit(self, engine):
        task_id = seed_parse_task(engine)
        ids = [seed_download(engine, task_id=task_id) for _ in range(5)]

        claimed = claim_pending_tasks(engine, limit=3)

        assert len(claimed) == 3
        assert set(claimed) <= set(ids)
        for download_id in claimed:
            assert (
                load_download(engine, download_id).status is DownloadStatus.DOWNLOADING
            )
        for download_id in set(ids) - set(claimed):
            assert load_download(engine, download_id).status is DownloadStatus.PENDING

    def test_never_claims_downloading_rows(self, engine):
        task_id = seed_parse_task(engine)
        seed_download(engine, task_id=task_id, status=DownloadStatus.DOWNLOADING)
        pending = seed_download(engine, task_id=task_id)

        claimed = claim_pending_tasks(engine, limit=5)

        assert claimed == [pending]
        assert load_download(engine, pending).status is DownloadStatus.DOWNLOADING

    def test_empty_claim_when_nothing_pending(self, engine):
        task_id = seed_parse_task(engine)
        seed_download(
            engine, task_id=task_id, status=DownloadStatus.COMPLETED, progress=100.0
        )

        assert claim_pending_tasks(engine, limit=3) == []

    def test_sequential_claims_never_overclaim(self, engine):
        task_id = seed_parse_task(engine)
        ids = [seed_download(engine, task_id=task_id) for _ in range(4)]

        first = claim_pending_tasks(engine, limit=3)
        second = claim_pending_tasks(engine, limit=3)

        assert len(first) == 3
        assert len(second) == 1
        assert set(first) | set(second) == set(ids)
        assert set(first) & set(second) == set()

    def test_zero_or_negative_limit_claims_nothing(self, engine):
        task_id = seed_parse_task(engine)
        seed_download(engine, task_id=task_id)

        assert claim_pending_tasks(engine, limit=0) == []
        assert claim_pending_tasks(engine, limit=-1) == []

    def test_concurrent_claims_are_disjoint(self, engine):
        task_id = seed_parse_task(engine)
        ids = [seed_download(engine, task_id=task_id) for _ in range(6)]
        results: list[list[str]] = []
        errors: list[Exception] = []

        def claim():
            try:
                results.append(claim_pending_tasks(engine, limit=3))
            except Exception as exc:  # pragma: no cover - surfaces flakiness
                errors.append(exc)

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        claimed = [download_id for batch in results for download_id in batch]
        assert len(claimed) == 6
        assert len(set(claimed)) == 6  # disjoint: no row claimed twice
        assert set(claimed) == set(ids)
        assert all(
            load_download(engine, d).status is DownloadStatus.DOWNLOADING
            for d in ids
        )


class TestRunOnce:
    def test_happy_path_downloads_and_completes(self, engine, storage, token_provider):
        task_id = seed_parse_task(engine, title="示例视频")
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()

        handled = run_once(
            engine, stub_downloader(), storage, hub,
            token_provider=token_provider,
        )

        assert handled == 1
        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.COMPLETED
        assert row.progress == 100.0
        assert row.downloaded_bytes == STUB_TOTAL
        assert row.total_bytes == STUB_TOTAL
        assert row.completed_at is not None
        assert row.bubble_path is not None
        stored = Path(row.bubble_path)
        assert stored.is_file()
        assert stored.read_bytes() == expected_stub_bytes(download_id, "示例视频", STUB_TOTAL)

    def test_publishes_progress_and_complete_events(self, engine, storage, token_provider):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()

        run_once(engine, stub_downloader(), storage, hub, token_provider=token_provider)

        events = hub.for_download(download_id)
        progress = [e for e in events if e["type"] == "progress"]
        complete = [e for e in events if e["type"] == "complete"]
        assert len(progress) == STUB_TOTAL // STUB_CHUNK
        assert all(e["data"]["status"] == "downloading" for e in progress)
        assert any(0 < e["data"]["progress"] < 100 for e in progress)
        assert len(complete) == 1
        assert complete[0]["data"]["status"] == "completed"
        assert complete[0]["data"]["progress"] == 100.0
        url = complete[0]["data"]["download_url"]
        assert url.startswith(f"/api/download/file/{download_id}?token=")
        assert complete[0]["data"]["token_expire_at"]

    def test_complete_event_token_serves_repeatedly(self, engine, storage, token_provider):
        task_id = seed_parse_task(engine, title="示例视频")
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()

        run_once(engine, stub_downloader(), storage, hub, token_provider=token_provider)

        complete = [
            e for e in hub.for_download(download_id) if e["type"] == "complete"
        ]
        assert len(complete) == 1
        token = complete[0]["data"]["download_url"].rsplit("token=", 1)[1]
        assert token
        service = DownloadService(
            token_provider=token_provider, storage=storage, engine=engine
        )
        downloaded = service.get_file(download_id, token)
        assert downloaded.path.read_bytes() == expected_stub_bytes(
            download_id, "示例视频", STUB_TOTAL
        )
        # Short-lived, not single-use: playback issues repeated requests.
        again = service.get_file(download_id, token)
        assert again.path.read_bytes() == expected_stub_bytes(
            download_id, "示例视频", STUB_TOTAL
        )

    def test_persists_progress_midflight(self, engine, storage, token_provider):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()
        downloader = ObservingDownloader(engine)

        run_once(engine, downloader, storage, hub, token_provider=token_provider)

        assert downloader.observed, "expected mid-flight progress snapshots"
        for progress, speed, downloaded in downloader.observed:
            assert 0 < progress < 100
            assert speed == 2048.0
            assert downloaded == int(progress / 100.0 * downloader.total_bytes)

    def test_progress_db_failure_does_not_abort_download(
        self, engine, storage, token_provider, monkeypatch
    ):
        # The progress callback must never abort the transfer (adapter
        # protocol): a DB outage during a progress write is logged and skipped,
        # and the terminal update still reconciles the row.
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()
        fail = False

        real_scope = worker_module.session_scope

        @contextmanager
        def flaky_scope(engine=None):
            if fail:
                raise sqlite3.OperationalError("simulated db outage during progress")
            with real_scope(engine) as session:
                yield session

        monkeypatch.setattr(worker_module, "session_scope", flaky_scope)

        class FlakyProgressDownloader:
            total_bytes = 2048
            chunk_size = 1024

            def download(self, request):
                nonlocal fail
                target = Path(request.target_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                fail = True
                try:
                    with target.open("wb") as out:
                        written = 0
                        while written < self.total_bytes:
                            count = min(
                                self.chunk_size, self.total_bytes - written
                            )
                            out.write(b"q" * count)
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
                finally:
                    fail = False
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

        handled = run_once(
            engine, FlakyProgressDownloader(), storage, hub,
            token_provider=token_provider,
        )

        assert handled == 1
        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.COMPLETED
        assert row.progress == 100.0
        assert row.downloaded_bytes == 2048

    def test_row_leaving_downloading_midflight_not_completed(
        self, engine, storage, token_provider
    ):
        # A row that leaves DOWNLOADING mid-flight (e.g. cleanup swept it to
        # expired) must not be completed by this worker — recovery is Task 12's
        # job, and the domain graph forbids downloading -> completed from any
        # other state.
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = FakeHub()

        class ExpireThenCompleteDownloader:
            def __init__(self, engine):
                self.engine = engine

            def download(self, request):
                with session_scope(self.engine) as session:
                    row = session.get(DownloadTask, request.download_id)
                    row.status = DownloadStatus.EXPIRED
                target = Path(request.target_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x" * 1024)
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
                    total_bytes=1024,
                    downloaded_bytes=1024,
                    retry_count=0,
                    error_message=None,
                )

        run_once(
            engine, ExpireThenCompleteDownloader(engine), storage, hub,
            token_provider=token_provider,
        )

        row = load_download(engine, download_id)
        assert row.status is DownloadStatus.EXPIRED
        assert row.bubble_path is None
        assert row.completed_at is None
        assert hub.for_download(download_id) == []  # no complete event

    def test_bubble_filename_uses_sanitized_extension(self, engine, storage, token_provider):
        # "mp4.webm" keeps the last dot segment (webm), never the slug "mp4-webm";
        # a row without a format falls back to the media type's default extension.
        task_id = seed_parse_task(engine)
        multi_ext = seed_download(engine, task_id=task_id, format="mp4.webm")
        no_format = seed_download(engine, task_id=task_id, format=None)
        hub = FakeHub()

        run_once(
            engine, stub_downloader(), storage, hub,
            max_concurrent=2, token_provider=token_provider,
        )

        names = {
            Path(load_download(engine, d).bubble_path).name
            for d in (multi_ext, no_format)
        }
        assert any(name.endswith(".webm") for name in names)
        assert any(name.endswith(".mp4") for name in names)

    def test_respects_max_concurrent(self, engine, storage, token_provider):
        task_a = seed_parse_task(engine, title="a")
        task_b = seed_parse_task(engine, title="b")
        task_c = seed_parse_task(engine, title="c")
        d1 = seed_download(engine, task_id=task_a)
        d2 = seed_download(engine, task_id=task_b)
        d3 = seed_download(engine, task_id=task_c)
        hub = FakeHub()

        handled = run_once(
            engine, stub_downloader(), storage, hub,
            max_concurrent=2, token_provider=token_provider,
        )
        assert handled == 2
        rows = [load_download(engine, d) for d in (d1, d2, d3)]
        assert sum(r.status is DownloadStatus.COMPLETED for r in rows) == 2
        assert sum(r.status is DownloadStatus.PENDING for r in rows) == 1

        handled = run_once(
            engine, stub_downloader(), storage, hub,
            max_concurrent=2, token_provider=token_provider,
        )
        assert handled == 1
        assert all(
            load_download(engine, d).status is DownloadStatus.COMPLETED
            for d in (d1, d2, d3)
        )

    def test_leaves_downloading_rows_alone(self, engine, storage, token_provider):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, status=DownloadStatus.DOWNLOADING)
        hub = FakeHub()

        handled = run_once(
            engine, stub_downloader(), storage, hub,
            max_concurrent=5, token_provider=token_provider,
        )

        assert handled == 0
        assert load_download(engine, download_id).status is DownloadStatus.DOWNLOADING
        assert hub.events == []


class TestMainLoop:
    def test_build_worker_deps_wires_speed_limit(self, tmp_path):
        settings = make_settings(
            database_url=f"sqlite:///{tmp_path / 'deps.db'}",
            video_storage_path=tmp_path / "pond/video",
            image_storage_path=tmp_path / "pond/image",
            music_storage_path=tmp_path / "pond/music",
            temp_video_path=tmp_path / "bubble/video",
            temp_image_path=tmp_path / "bubble/image",
            temp_music_path=tmp_path / "bubble/music",
            download_speed_limit=7,
        )

        engine, downloader, storage, service = build_worker_deps(settings)

        assert downloader.speed_limit_mb_s == 7.0
        assert isinstance(service, DownloadService)

    def test_run_forever_returns_immediately_when_stop_pre_set(self):
        stop = threading.Event()
        stop.set()
        calls = []

        def run_once_fn():
            calls.append(1)
            return 0

        run_forever(run_once_fn, stop, poll_interval=0.05)
        assert calls == []

    def test_run_forever_polls_until_stop(self):
        stop = threading.Event()
        calls = []

        def run_once_fn():
            calls.append(1)
            if len(calls) >= 2:
                stop.set()
            return 0

        run_forever(run_once_fn, stop, poll_interval=0.01)
        assert len(calls) == 2

    def test_run_forever_survives_iteration_errors(self):
        stop = threading.Event()
        calls = []

        def run_once_fn():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            stop.set()
            return 0

        run_forever(run_once_fn, stop, poll_interval=0.01)
        assert len(calls) == 2


class TestSchemaCheck:
    def test_schema_ready_false_before_migrations(self, tmp_path):
        engine = build_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

        assert schema_ready(engine) is False

    def test_schema_ready_true_after_create_all(self, tmp_path):
        engine = build_engine(f"sqlite:///{tmp_path / 'ready.db'}")
        Base.metadata.create_all(engine)

        assert schema_ready(engine) is True


class TestPublish:
    def test_publish_reuses_one_per_thread_event_loop(self, monkeypatch):
        created = []
        real_new_event_loop = asyncio.new_event_loop

        def counting_new_event_loop():
            loop = real_new_event_loop()
            created.append(loop)
            return loop

        monkeypatch.setattr(asyncio, "new_event_loop", counting_new_event_loop)
        result = {}

        def worker():
            hub = FakeHub()
            _publish(hub, "d-1", {"type": "progress"})
            _publish(hub, "d-1", {"type": "complete"})
            result["events"] = [event for _, event in hub.events]

        # A fresh thread starts with empty thread-local state, so the loop
        # count is deterministic regardless of other tests' publish calls.
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert len(created) == 1  # created once, reused for the second publish
        assert result["events"] == [{"type": "progress"}, {"type": "complete"}]

    def test_publish_delivers_to_real_hub_subscribers(self):
        hub = DownloadEventHub()
        loop = asyncio.new_event_loop()
        try:
            queue = loop.run_until_complete(hub.subscribe("d-1"))

            _publish(hub, "d-1", {"type": "progress", "data": {"p": 1}})
            _publish(hub, "d-1", {"type": "complete", "data": {"p": 2}})

            async def drain():
                return [await queue.get() for _ in range(2)]

            events = loop.run_until_complete(drain())
            assert events == [
                {"type": "progress", "data": {"p": 1}},
                {"type": "complete", "data": {"p": 2}},
            ]
        finally:
            loop.close()


class TestRequestCarriesParseContext:
    def test_download_request_carries_source_url_and_metadata(self, engine, storage, token_provider):
        captured = []

        class CapturingDownloader:
            def download(self, request):
                captured.append(request)
                return DownloadResult(
                    download_id=request.download_id,
                    task_id=request.command.task_id,
                    title=request.title,
                    media_type=request.media_type,
                    status=DownloadStatus.COMPLETED,
                    progress=100.0,
                    total_bytes=1,
                    downloaded_bytes=1,
                )

        task_id = seed_parse_task(
            engine,
            metadata={"engine": "parse-video-py", "video_url": "https://cdn.example/v.mp4"},
        )
        seed_download(engine, task_id=task_id)

        run_once(engine, CapturingDownloader(), storage, DownloadEventHub(),
                 token_provider=token_provider)
        assert len(captured) == 1
        assert captured[0].source_url == VIDEO_URL  # the seeded parse task's URL
        assert captured[0].metadata["video_url"] == "https://cdn.example/v.mp4"
