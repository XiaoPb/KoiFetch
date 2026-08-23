"""Service-layer tests for the NAS save use case (Task 10).

Covers :class:`app.application.nas_service.NasService.save`:

* The happy path — a completed download's real bubble file is *moved* into
  the pond under the requested NAS-style target directory (bubble copy gone,
  pond file present with the same bytes), the row's ``pond_path``/
  ``completed_at`` are updated, and the result carries ``nas_path`` (NAS-style
  leading slash), ``file_size`` and an aware ``saved_at``.
* Target-path mapping (documented layout rule): each non-empty segment is
  slugified (CJK-preserving), a PRD-style leading ``/`` is stripped, and the
  filename is either a fresh ``safe_media_filename`` name when the parse
  task's metadata carries the required inputs (video/music ``published_at`` +
  ``source_id``, image ``index``) or the bubble file's basename otherwise.
* Media-type routing: video/image/music land in their own pond buckets.
* Errors: unknown download → ``3001`` (400); any non-COMPLETED status
  (pending/downloading/failed/expired) → ``5002`` (400); missing/escaped
  bubble file → ``5001`` (404); invalid target path (blank, ``..``, backslash,
  drive-letter) → generic 400; degraded storage (no adapter) → ``9001`` (500).
"""

import uuid
from datetime import datetime, timezone

import pytest
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.adapters.factory import get_storage
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_FILE_NOT_FOUND,
    CODE_INTERNAL_ERROR,
    CODE_TASK_NOT_FOUND,
    ApiError,
)
from app.application.nas_service import NasService
from app.domain import DownloadStatus, MediaType
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask, ParseTask

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"
MUSIC_URL = "https://music.example.com/song/456"
IMAGE_URL = "https://photo.example.com/pic/789"

BUBBLE_DATA = b"fake-video-bytes-for-nas-tests"
BUBBLE_FILENAME = "2026-01-01_shili-shipin_av123.mp4"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'nas-service.db'}",
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
    service = NasService(storage=get_storage(settings), engine=engine)
    return settings, engine, service


@pytest.fixture
def engine(env):
    return env[1]


@pytest.fixture
def service(env):
    return env[2]


@pytest.fixture
def storage(env):
    return get_storage(env[0])


def seed_parse_task(
    engine,
    *,
    task_id=None,
    title="示例视频",
    media_type=MediaType.VIDEO,
    format="mp4",
    metadata=None,
) -> str:
    """Insert a ParseTask row (metadata defaults to {}); return its task_id."""
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
                metadata_=metadata or {},
            )
        )
    return task_id


def seed_download(
    engine,
    *,
    task_id,
    download_id=None,
    title="示例视频",
    status=DownloadStatus.PENDING,
    progress=0.0,
    bubble_path=None,
) -> str:
    download_id = download_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            DownloadTask(
                download_id=download_id,
                task_id=task_id,
                title=title,
                format="mp4",
                status=status,
                progress=progress,
                bubble_path=str(bubble_path) if bubble_path is not None else None,
            )
        )
    return download_id


def seed_completed_with_file(
    engine, storage, *, task_id, media_type=MediaType.VIDEO, filename=BUBBLE_FILENAME,
    data=BUBBLE_DATA, title="示例视频", **kwargs,
) -> str:
    """A completed download whose bubble file really exists; return download_id."""
    path = storage.save_bytes(media_type, filename, data)
    return seed_download(
        engine,
        task_id=task_id,
        title=title,
        status=DownloadStatus.COMPLETED,
        progress=100.0,
        bubble_path=str(path),
        **kwargs,
    )


def api_error(exc: Exception) -> ApiError:
    assert isinstance(exc, ApiError)
    return exc


class TestSaveHappyPath:
    def test_save_moves_bubble_file_into_pond_target(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)

        result = service.save(download_id, "/视频/抖音")

        bubble = storage.bubble_root(MediaType.VIDEO) / BUBBLE_FILENAME
        assert not bubble.exists()  # moved, not copied
        pond = storage.pond_root(MediaType.VIDEO) / "视频" / "抖音" / BUBBLE_FILENAME
        assert pond.is_file()
        assert pond.read_bytes() == BUBBLE_DATA
        assert result.nas_path == "/视频/抖音/2026-01-01_shili-shipin_av123.mp4"
        assert result.file_size == len(BUBBLE_DATA)
        assert result.saved_at.tzinfo is not None

    def test_save_updates_pond_path_and_completed_at(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)

        result = service.save(download_id, "/视频/抖音")

        with session_scope(engine) as session:
            row = session.get(DownloadTask, download_id)
        # pond_path is stored pond-relative (resolvable against the live root).
        assert row.pond_path == "视频/抖音/2026-01-01_shili-shipin_av123.mp4"
        assert row.completed_at == result.saved_at
        # The bubble_path column keeps its historical value (the file moved).
        assert row.bubble_path is not None

    def test_save_without_leading_slash_is_equivalent(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        result = service.save(download_id, "视频/抖音")
        assert result.nas_path == "/视频/抖音/2026-01-01_shili-shipin_av123.mp4"

    def test_save_slugifies_target_segments(self, service, engine, storage):
        # The target path is a NAS-style *logical* path: every segment is
        # slugified (CJK preserved, separator runs collapsed) before landing.
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        result = service.save(download_id, "视频 合集/抖音/2026年 1月")
        assert (
            result.nas_path
            == "/视频-合集/抖音/2026年-1月/2026-01-01_shili-shipin_av123.mp4"
        )
        assert (
            storage.pond_root(MediaType.VIDEO) / "视频-合集" / "抖音" / "2026年-1月"
            / BUBBLE_FILENAME
        ).is_file()

    def test_save_routes_by_media_type(self, service, engine, storage):
        # Each media type lands in its own pond bucket (video/image/music).
        video_task = seed_parse_task(engine, media_type=MediaType.VIDEO)
        image_task = seed_parse_task(
            engine, media_type=MediaType.IMAGE, title="示例图片",
            metadata={"index": 3},
        )
        music_task = seed_parse_task(
            engine, media_type=MediaType.MUSIC, title="示例音乐",
            metadata={"published_at": "2026-02-02", "source_id": "song456"},
        )
        video_id = seed_completed_with_file(
            engine, storage, task_id=video_task, filename="clip.mp4"
        )
        image_id = seed_completed_with_file(
            engine, storage, task_id=image_task, media_type=MediaType.IMAGE,
            filename="pic.jpg", data=b"img-data", title="示例图片",
        )
        music_id = seed_completed_with_file(
            engine, storage, task_id=music_task, media_type=MediaType.MUSIC,
            filename="song.mp3", data=b"audio-data", title="示例音乐",
        )

        video = service.save(video_id, "/测试")
        image = service.save(image_id, "/测试")
        music = service.save(music_id, "/测试")

        assert video.nas_path == "/测试/clip.mp4"
        assert image.nas_path == "/测试/003_示例图片.jpg"
        assert music.nas_path == "/测试/2026-02-02_示例音乐_song456.mp3"
        assert (storage.pond_root(MediaType.VIDEO) / "测试" / "clip.mp4").is_file()
        assert (storage.pond_root(MediaType.IMAGE) / "测试" / "003_示例图片.jpg").is_file()
        assert (
            storage.pond_root(MediaType.MUSIC) / "测试" / "2026-02-02_示例音乐_song456.mp3"
        ).is_file()
        assert video.file_size == len(BUBBLE_DATA)
        assert image.file_size == 8
        assert music.file_size == 10

    def test_save_metadata_driven_video_filename(self, service, engine, storage):
        task_id = seed_parse_task(
            engine, title="示例视频", metadata={"published_at": "2026-01-01", "source_id": "av123"}
        )
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        result = service.save(download_id, "/视频/抖音")
        assert result.nas_path == "/视频/抖音/2026-01-01_示例视频_av123.mp4"

    def test_save_falls_back_to_bubble_basename_without_metadata(
        self, service, engine, storage
    ):
        # Stub-era rows carry no published_at/source_id/index: the bubble
        # basename (already a PRD-style safe name) is kept unchanged.
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        result = service.save(download_id, "/视频/抖音")
        assert result.nas_path == f"/视频/抖音/{BUBBLE_FILENAME}"

    def test_save_saved_at_is_close_to_now(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        before = datetime.now(timezone.utc)
        result = service.save(download_id, "/视频/抖音")
        after = datetime.now(timezone.utc)
        assert before <= result.saved_at <= after


class TestSaveErrors:
    def test_save_unknown_download_raises_3001(self, service):
        with pytest.raises(ApiError) as excinfo:
            service.save(str(uuid.uuid4()), "/视频/抖音")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_TASK_NOT_FOUND

    @pytest.mark.parametrize(
        "status", [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING,
                   DownloadStatus.FAILED, DownloadStatus.EXPIRED]
    )
    def test_save_not_completed_raises_5002(self, service, engine, status):
        # Any non-COMPLETED status — including expired — means the file is not
        # available to save (single PRD code 5002 for the NAS save).
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, status=status)
        with pytest.raises(ApiError) as excinfo:
            service.save(download_id, "/视频/抖音")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_FILE_NOT_DOWNLOADED

    def test_save_missing_bubble_file_raises_5001(self, service, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            bubble_path=str(storage.bubble_root(MediaType.VIDEO) / "gone.mp4"),
        )
        with pytest.raises(ApiError) as excinfo:
            service.save(download_id, "/视频/抖音")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_404_NOT_FOUND
        assert exc.code == CODE_FILE_NOT_FOUND

    def test_save_completed_row_without_bubble_path_raises_5001(
        self, service, engine
    ):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine, task_id=task_id, status=DownloadStatus.COMPLETED, progress=100.0
        )
        with pytest.raises(ApiError) as excinfo:
            service.save(download_id, "/视频/抖音")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_404_NOT_FOUND
        assert exc.code == CODE_FILE_NOT_FOUND

    def test_save_bubble_path_outside_root_raises_5001(
        self, service, engine, tmp_path
    ):
        # A hostile/corrupt bubble_path that escapes the bubble root is
        # "file not found" — it is never moved anywhere.
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
        with pytest.raises(ApiError) as excinfo:
            service.save(download_id, "/视频/抖音")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_404_NOT_FOUND
        assert exc.code == CODE_FILE_NOT_FOUND

    @pytest.mark.parametrize(
        "target",
        [
            "",
            "   ",
            "/",
            "..",
            "/..",
            "a/../b",
            "./a",
            "a/./b",
            "C:\\evil",
            "C:/evil",
            "视频\\抖音",
        ],
    )
    def test_save_invalid_target_path_raises_generic_400(
        self, service, engine, storage, target
    ):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        with pytest.raises(ApiError) as excinfo:
            service.save(download_id, target)
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_400_BAD_REQUEST
        assert exc.code == CODE_BAD_REQUEST

    def test_save_invalid_target_does_not_move_file(
        self, service, engine, storage
    ):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, storage, task_id=task_id)
        with pytest.raises(ApiError):
            service.save(download_id, "../evil")
        bubble = storage.bubble_root(MediaType.VIDEO) / BUBBLE_FILENAME
        assert bubble.is_file()  # untouched by the rejected request
        with session_scope(engine) as session:
            row = session.get(DownloadTask, download_id)
        assert row.pond_path is None

    def test_save_without_storage_raises_9001(self, env):
        # Degraded-storage mode: the app booted without an adapter; NAS save
        # must fail with a clean 500 envelope, not a traceback.
        settings, engine, _ = env
        service = NasService(storage=None, engine=engine)
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine, task_id=task_id, status=DownloadStatus.COMPLETED, progress=100.0
        )
        with pytest.raises(ApiError) as excinfo:
            service.save(download_id, "/视频/抖音")
        exc = api_error(excinfo.value)
        assert exc.http_status == HTTP_500_INTERNAL_SERVER_ERROR
        assert exc.code == CODE_INTERNAL_ERROR

    def test_save_unknown_download_with_bad_target_prefers_target_400(
        self, service
    ):
        # Request validation (target_path) short-circuits before any DB lookup.
        with pytest.raises(ApiError) as excinfo:
            service.save(str(uuid.uuid4()), "../evil")
        exc = api_error(excinfo.value)
        assert exc.code == CODE_BAD_REQUEST
