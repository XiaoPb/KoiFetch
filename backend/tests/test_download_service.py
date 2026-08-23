"""Service-layer tests for the download use cases (Task 9).

Covers :class:`app.application.download_service.DownloadService`:

* ``submit`` — a fresh ``pending`` row is persisted (uuid4 download_id, title
  copied from the parse task, zeroed counters); unknown task → ``3001`` (400);
  the duplicate policy (documented): an active (pending/downloading) download
  for the same task → ``3002`` (409), a completed download with the *identical*
  format+quality variant → ``3003`` (400); failed/expired downloads never
  block; blank selections → generic 400.
* ``get_progress`` — a snapshot with ``remaining_time`` computed from speed
  while downloading; unknown download → ``3001``.
* ``get_file`` — token rules: missing/invalid/expired/reused/mis-targeted
  token → ``5003`` (401); single-use enforced atomically via ``token_id``
  (same token twice → 5003, a fresh token still works); status rules: not
  completed → ``5002``, expired → ``5004`` (410); the bubble file must exist
  inside the bubble root (missing/absent path → ``5001`` (404), traversal
  attempt → ``5001``).
* ``issue_download_token`` — mints a 5-minute one-time token for a completed
  download; unknown → ``3001``; not completed → ``5002``.
"""

import threading
import uuid
from datetime import timedelta

import pytest
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_410_GONE,
)

from app.adapters.factory import get_one_time_token_provider, get_storage
from app.adapters.tokens_jwt import JwtOneTimeTokenProvider
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_EXPIRED,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_FILE_NOT_FOUND,
    CODE_FILE_TOKEN_INVALID,
    CODE_TASK_ALREADY_COMPLETED,
    CODE_TASK_ALREADY_DOWNLOADING,
    CODE_TASK_NOT_FOUND,
    ApiError,
)
from app.application.download_service import DownloadService
from app.domain import DownloadStatus, MediaType
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask, ParseTask

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"

BUBBLE_DATA = b"fake-video-bytes-for-tests"
BUBBLE_FILENAME = "2026-01-01_shili-shipin_av123.mp4"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'download-service.db'}",
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
    service = DownloadService(
        token_provider=get_one_time_token_provider(settings),
        storage=get_storage(settings),
        engine=engine,
    )
    return settings, engine, service


@pytest.fixture
def engine(env):
    return env[1]


@pytest.fixture
def service(env):
    return env[2]


@pytest.fixture
def storage(env):
    settings = env[0]
    return get_storage(settings)


def seed_parse_task(
    engine, *, task_id=None, title="示例视频", media_type=MediaType.VIDEO,
    format="mp4",
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
                format=format,
                metadata_={},
            )
        )
    return task_id


def seed_download(
    engine,
    *,
    task_id,
    download_id=None,
    status=DownloadStatus.PENDING,
    format=None,
    quality=None,
    progress=0.0,
    speed=None,
    total_bytes=None,
    downloaded_bytes=None,
    bubble_path=None,
    error_message=None,
) -> str:
    """Insert a DownloadTask row; return its download_id."""
    download_id = download_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            DownloadTask(
                download_id=download_id,
                task_id=task_id,
                title="示例视频",
                format=format,
                quality=quality,
                status=status,
                progress=progress,
                speed=speed,
                total_bytes=total_bytes,
                downloaded_bytes=downloaded_bytes,
                bubble_path=str(bubble_path) if bubble_path is not None else None,
                error_message=error_message,
            )
        )
    return download_id


def seed_completed_with_file(engine, storage, *, task_id, **kwargs) -> str:
    """A completed download whose bubble file really exists; return download_id."""
    path = storage.save_bytes(MediaType.VIDEO, BUBBLE_FILENAME, BUBBLE_DATA)
    return seed_download(
        engine,
        task_id=task_id,
        status=DownloadStatus.COMPLETED,
        progress=100.0,
        total_bytes=len(BUBBLE_DATA),
        downloaded_bytes=len(BUBBLE_DATA),
        bubble_path=str(path),
        **kwargs,
    )


def api_error(exc: Exception) -> ApiError:
    assert isinstance(exc, ApiError)
    return exc


class TestSubmit:
    def test_submit_creates_pending_row(self, service, engine):
        task_id = seed_parse_task(engine)
        result = service.submit(task_id)
        assert result.download_id and len(result.download_id) == 36
        assert uuid.UUID(result.download_id).version == 4
        assert result.task_id == task_id
        assert result.status is DownloadStatus.PENDING
        assert result.progress == 0.0
        assert result.created_at is not None

        with session_scope(engine) as session:
            row = session.get(DownloadTask, result.download_id)
        assert row is not None
        assert row.status is DownloadStatus.PENDING
        assert row.progress == 0.0
        assert row.retry_count == 0
        assert row.title == "示例视频"  # copied from the parse task

    def test_submit_persists_format_and_quality(self, service, engine):
        task_id = seed_parse_task(engine)
        result = service.submit(task_id, format="mp4", quality="1080p")
        with session_scope(engine) as session:
            row = session.get(DownloadTask, result.download_id)
        assert row.format == "mp4"
        assert row.quality == "1080p"

    def test_submit_unknown_task_raises_3001(self, service):
        with pytest.raises(ApiError) as excinfo:
            service.submit(str(uuid.uuid4()))
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_TASK_NOT_FOUND

    def test_submit_blank_format_rejected_as_generic_400(self, service, engine):
        task_id = seed_parse_task(engine)
        with pytest.raises(ApiError) as excinfo:
            service.submit(task_id, format="   ")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_BAD_REQUEST

    def test_submit_malformed_task_id_rejected_as_generic_400(self, service):
        with pytest.raises(ApiError) as excinfo:
            service.submit("not-a-uuid")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_BAD_REQUEST

    def test_submit_duplicate_pending_download_raises_3002(self, service, engine):
        task_id = seed_parse_task(engine)
        seed_download(engine, task_id=task_id, status=DownloadStatus.PENDING)
        with pytest.raises(ApiError) as excinfo:
            service.submit(task_id)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_409_CONFLICT
        assert exc.code == CODE_TASK_ALREADY_DOWNLOADING

    def test_submit_duplicate_downloading_download_raises_3002(self, service, engine):
        task_id = seed_parse_task(engine)
        seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.DOWNLOADING,
            progress=50.0,
        )
        with pytest.raises(ApiError) as excinfo:
            service.submit(task_id)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_409_CONFLICT
        assert exc.code == CODE_TASK_ALREADY_DOWNLOADING

    def test_submit_completed_same_variant_raises_3003(self, service, engine):
        task_id = seed_parse_task(engine)
        seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            format="mp4",
            quality="1080p",
            progress=100.0,
        )
        with pytest.raises(ApiError) as excinfo:
            service.submit(task_id, format="mp4", quality="1080p")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_TASK_ALREADY_COMPLETED

    def test_submit_completed_different_variant_is_allowed(self, service, engine):
        task_id = seed_parse_task(engine)
        seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            format="mp4",
            quality="1080p",
            progress=100.0,
        )
        result = service.submit(task_id, format="mp4", quality="720p")
        assert result.status is DownloadStatus.PENDING

    def test_submit_after_failed_download_is_allowed(self, service, engine):
        task_id = seed_parse_task(engine)
        seed_download(engine, task_id=task_id, status=DownloadStatus.FAILED)
        result = service.submit(task_id)
        assert result.status is DownloadStatus.PENDING

    def test_submit_after_expired_download_is_allowed(self, service, engine):
        task_id = seed_parse_task(engine)
        seed_download(engine, task_id=task_id, status=DownloadStatus.EXPIRED)
        result = service.submit(task_id)
        assert result.status is DownloadStatus.PENDING


class TestGetProgress:
    def test_pending_row_returns_snapshot(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        snapshot = service.get_progress(download_id)
        assert snapshot.download_id == download_id
        assert snapshot.status is DownloadStatus.PENDING
        assert snapshot.progress == 0.0
        assert snapshot.remaining_time is None

    def test_unknown_download_raises_3001(self, service):
        with pytest.raises(ApiError) as excinfo:
            service.get_progress(str(uuid.uuid4()))
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_TASK_NOT_FOUND

    def test_remaining_time_computed_from_speed_while_downloading(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.DOWNLOADING,
            progress=25.0,
            speed=250.0,
            total_bytes=1000,
            downloaded_bytes=250,
        )
        snapshot = service.get_progress(download_id)
        assert snapshot.status is DownloadStatus.DOWNLOADING
        assert snapshot.remaining_time == pytest.approx(3.0)

    def test_no_remaining_time_without_speed(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.DOWNLOADING,
            progress=25.0,
            total_bytes=1000,
            downloaded_bytes=250,
        )
        assert service.get_progress(download_id).remaining_time is None

    def test_error_message_surfaces(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.FAILED,
            error_message="download failed",
        )
        assert service.get_progress(download_id).error_message == "download failed"


class TestGetFile:
    def test_completed_download_returns_resolved_file(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        token = service.issue_download_token(download_id).token
        file = service.get_file(download_id, token)
        assert file.path.is_file()
        assert file.path.read_bytes() == BUBBLE_DATA
        assert file.filename == BUBBLE_FILENAME

    def test_unknown_download_raises_3001(self, service):
        with pytest.raises(ApiError) as excinfo:
            service.get_file(str(uuid.uuid4()), "any-token")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_TASK_NOT_FOUND

    def test_pending_download_raises_5002(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, "any-token")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_FILE_NOT_DOWNLOADED

    def test_downloading_download_raises_5002(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine, task_id=task_id, status=DownloadStatus.DOWNLOADING
        )
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, "any-token")
        exc = api_error(excinfo.value)
        assert exc.code == CODE_FILE_NOT_DOWNLOADED

    def test_failed_download_raises_5002(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, status=DownloadStatus.FAILED)
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, "any-token")
        exc = api_error(excinfo.value)
        assert exc.code == CODE_FILE_NOT_DOWNLOADED

    def test_expired_download_raises_5004_gone(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, status=DownloadStatus.EXPIRED)
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, "any-token")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_410_GONE
        assert exc.code == CODE_FILE_EXPIRED

    def test_missing_token_raises_5003(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        for bad in (None, "", "   "):
            with pytest.raises(ApiError) as excinfo:
                service.get_file(download_id, bad)
            exc = api_error(excinfo.value)
            assert exc.http_status == HTTP_401_UNAUTHORIZED
            assert exc.code == CODE_FILE_TOKEN_INVALID

    def test_garbage_token_raises_5003(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, "definitely-not-a-jwt")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_401_UNAUTHORIZED
        assert exc.code == CODE_FILE_TOKEN_INVALID

    def test_expired_token_raises_5003(self, service, engine, storage, env):
        settings = env[0]
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        expired_provider = JwtOneTimeTokenProvider(
            settings.secret_key, ttl=timedelta(seconds=-5)
        )
        token = expired_provider.issue(download_id=download_id)
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, token)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_401_UNAUTHORIZED
        assert exc.code == CODE_FILE_TOKEN_INVALID

    def test_token_for_other_download_raises_5003(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        other = str(uuid.uuid4())
        # Re-issue with a different download_id target (same secret).
        provider = get_one_time_token_provider(make_settings())
        wrong = provider.issue(download_id=other)
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, wrong)
        exc = api_error(excinfo.value)
        assert exc.code == CODE_FILE_TOKEN_INVALID

    def test_token_single_use_first_ok_second_5003(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        token = service.issue_download_token(download_id).token
        assert service.get_file(download_id, token).path.is_file()
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, token)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_401_UNAUTHORIZED
        assert exc.code == CODE_FILE_TOKEN_INVALID

    def test_fresh_token_after_use_still_works(self, service, engine, storage):
        # Single-use is per token issuance (token_id), not per download: a
        # fresh one-time token is a new link and must still serve the file.
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        first = service.issue_download_token(download_id).token
        assert service.get_file(download_id, first).path.is_file()
        second = service.issue_download_token(download_id).token
        assert service.get_file(download_id, second).path.is_file()

    def test_concurrent_same_token_serves_exactly_once(self, service, engine, storage):
        # Two simultaneous get_file calls with the SAME token: the atomic
        # claim must let exactly one serve and reject the other with 5003
        # (SQLite serializes the writes; only the first UPDATE matches).
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        token = service.issue_download_token(download_id).token
        barrier = threading.Barrier(2)
        served: list[bool] = []
        rejected: list[int] = []

        def attempt() -> None:
            barrier.wait()
            try:
                service.get_file(download_id, token)
                served.append(True)
            except ApiError as exc:
                assert exc.code == CODE_FILE_TOKEN_INVALID
                rejected.append(exc.code)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(served) == 1
        assert len(rejected) == 1

    def test_missing_bubble_file_raises_5001(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            bubble_path=str(storage.bubble_root(MediaType.VIDEO) / "gone.mp4"),
        )
        token = service.issue_download_token(download_id).token
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, token)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_404_NOT_FOUND
        assert exc.code == CODE_FILE_NOT_FOUND

    def test_completed_row_without_bubble_path_raises_5001(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine, task_id=task_id, status=DownloadStatus.COMPLETED, progress=100.0
        )
        token = service.issue_download_token(download_id).token
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, token)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_404_NOT_FOUND
        assert exc.code == CODE_FILE_NOT_FOUND

    def test_bubble_path_outside_root_raises_5001(self, service, engine, tmp_path):
        # A hostile/corrupt bubble_path that escapes the bubble root must be
        # treated as "file not found", never served.
        task_id = seed_parse_task(engine)
        outside = tmp_path / "evil.mp4"
        outside.write_bytes(b"evil")
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            bubble_path=str(outside),
        )
        token = service.issue_download_token(download_id).token
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, token)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_404_NOT_FOUND
        assert exc.code == CODE_FILE_NOT_FOUND

    def test_get_file_without_storage_raises_9001(self, env):
        # Degraded-storage mode (create_app falls back to storage=None when a
        # root cannot be created): file serving must fail with a clean 500
        # envelope instead of a traceback.
        settings, engine, _ = env
        service = DownloadService(
            token_provider=get_one_time_token_provider(settings),
            storage=None,
            engine=engine,
        )
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine, task_id=task_id, status=DownloadStatus.COMPLETED, progress=100.0
        )
        token = service.issue_download_token(download_id).token
        with pytest.raises(ApiError) as excinfo:
            service.get_file(download_id, token)
        exc = api_error(excinfo.value)
        assert exc.http_status == 500
        assert exc.code == 9001


class TestIssueDownloadToken:
    def test_issue_for_completed_download_returns_valid_token(
        self, service, engine, storage
    ):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        issued = service.issue_download_token(download_id)
        provider = get_one_time_token_provider(make_settings())
        claims = provider.validate(issued.token)
        assert claims.download_id == download_id
        assert claims.token_id
        # The issuer reports the same expiry the token itself carries, so the
        # WS complete event can surface the 5-minute link validity.
        assert issued.expires_at == claims.expires_at

    def test_issue_unknown_download_raises_3001(self, service):
        with pytest.raises(ApiError) as excinfo:
            service.issue_download_token(str(uuid.uuid4()))
        exc = api_error(excinfo.value)
        assert exc.code == CODE_TASK_NOT_FOUND

    def test_issue_pending_download_raises_5002(self, service, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        with pytest.raises(ApiError) as excinfo:
            service.issue_download_token(download_id)
        exc = api_error(excinfo.value)
        assert exc.code == CODE_FILE_NOT_DOWNLOADED
