"""Worker tests (Task 11): claiming, download execution, progress persistence,
hub events, and the polling-loop wiring.

Covers :mod:`app.workers.worker` and :mod:`app.workers.main`:

* ``claim_pending_tasks`` — atomically claims ``pending`` rows up to a limit,
  never touches ``downloading`` rows, and never over-claims.
* ``run_once`` — executes claimed tasks through the downloader adapter, writing
  the bubble file, persisting progress/speed/bytes during the download,
  recording completion (status/progress/bubble_path/completed_at) and issuing a
  one-time token in the WS ``complete`` event.
* Main-loop wiring — ``build_worker_deps`` pipes ``download_speed_limit`` into
  the stub downloader; ``run_forever`` polls until the stop event fires and
  survives transient iteration errors.

The happy path uses the real :class:`StubDownloaderAdapter` with small
size/chunk parameters; deterministic mid-flight observation uses a small fake
downloader (see the ``app.workers.worker`` module docstring for why the worker
processes one batch sequentially).
"""

import hashlib
import threading
import uuid
from pathlib import Path

import pytest

from app.adapters.downloader_stub import StubDownloaderAdapter
from app.adapters.factory import get_one_time_token_provider, get_storage
from app.api.responses import CODE_FILE_TOKEN_INVALID
from app.application.download_service import DownloadService
from app.domain import DownloadProgress, DownloadResult, DownloadStatus, MediaType
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask, ParseTask
from app.workers.main import build_worker_deps, run_forever
from app.workers.worker import claim_pending_tasks, run_once

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"

STUB_TOTAL = 4096
STUB_CHUNK = 1024


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        video_storage_path=tmp_path / "pond/video",
        image_storage_path=tmp_path / "pond/image",
        music_storage_path=tmp_path / "pond/music",
        temp_video_path=tmp_path / "bubble/video",
        temp_image_path=tmp_path / "bubble/image",
        temp_music_path=tmp_path / "bubble/music",
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=settings, engine=engine) is True
    storage = get_storage(settings)
    token_provider = get_one_time_token_provider(settings)
    return settings, engine, storage, token_provider


@pytest.fixture
def engine(env):
    return env[1]


@pytest.fixture
def storage(env):
    return env[2]


@pytest.fixture
def token_provider(env):
    return env[3]


class FakeHub:
    """In-process stand-in for :class:`DownloadEventHub` that records events."""

    def __init__(self):
        self.events = []

    async def publish(self, download_id: str, event: dict) -> None:
        self.events.append((download_id, event))

    def for_download(self, download_id: str) -> list[dict]:
        return [event for did, event in self.events if did == download_id]


def seed_parse_task(
    engine, *, task_id=None, title="示例视频", media_type=MediaType.VIDEO,
    metadata=None,
) -> str:
    """Insert a ParseTask row; return its task_id."""
    task_id = task_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url=VIDEO_URL,
                platform="bilibili",
                media_type=media_type,
                title=title,
                format="mp4",
                metadata_=metadata if metadata is not None else {},
            )
        )
    return task_id


def seed_download(
    engine,
    *,
    task_id,
    status=DownloadStatus.PENDING,
    download_id=None,
    retry_count=0,
    progress=0.0,
    **kwargs,
) -> str:
    """Insert a DownloadTask row; return its download_id."""
    download_id = download_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            DownloadTask(
                download_id=download_id,
                task_id=task_id,
                title="示例视频",
                format=kwargs.pop("format", "mp4"),
                quality=kwargs.pop("quality", "1080p"),
                status=status,
                progress=progress,
                retry_count=retry_count,
                **kwargs,
            )
        )
    return download_id


def load_download(engine, download_id) -> DownloadTask:
    with session_scope(engine) as session:
        return session.get(DownloadTask, download_id)


def expected_stub_bytes(download_id: str, title: str, total_bytes: int) -> bytes:
    """The deterministic byte stream the stub downloader writes (its contract)."""
    seed_bytes = hashlib.sha256(
        f"{download_id}:{title or ''}".encode("utf-8")
    ).digest()
    return (seed_bytes * (total_bytes // 32 + 1))[:total_bytes]


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

    def test_complete_event_token_serves_the_file_once(self, engine, storage, token_provider):
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
        with pytest.raises(Exception) as excinfo:
            service.get_file(download_id, token)  # single use
        assert excinfo.value.code == CODE_FILE_TOKEN_INVALID

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
