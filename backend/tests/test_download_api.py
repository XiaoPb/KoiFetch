"""Tests for the download HTTP API (Task 9).

Covers the wire contract:

* ``POST /api/download/submit`` — success envelope with
  ``data: {download_id, task_id, status: "pending", created_at}``; unknown
  task → ``400`` ``3001``; duplicate active download → ``409`` ``3002``;
  malformed/missing task_id → ``400`` generic; end-to-end parse → submit.
* ``GET /api/download/progress/{download_id}`` — ``200`` envelope with the
  snapshot fields (status/progress/speed/bytes/remaining_time); unknown
  download → ``400`` ``3001``; malformed id → ``400`` generic.
* ``GET /api/download/file/{download_id}?token=...`` — serves the raw bytes
  with a Content-Disposition header for a completed download; missing/invalid/
  expired/mis-bound token → ``401`` ``5003`` (reuse within the 5-minute window is allowed — playback needs repeated/range requests); not completed → ``400`` ``5002``;
  expired task → ``410`` ``5004``; missing bubble file → ``404`` ``5001``.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.adapters.factory import get_storage
from app.adapters.tokens_jwt import JwtOneTimeTokenProvider
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_EXPIRED,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_FILE_NOT_FOUND,
    CODE_FILE_TOKEN_INVALID,
    CODE_OK,
    CODE_TASK_ALREADY_DOWNLOADING,
    CODE_TASK_NOT_FOUND,
)
from app.domain import DownloadStatus, MediaType
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask, ParseTask
from app.main import create_app

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"

BUBBLE_DATA = b"fake-video-bytes-for-api-tests"
BUBBLE_FILENAME = "2026-01-01_api-shipin_av123.mp4"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", **overrides)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'download-api.db'}",
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
    app = create_app(settings=settings)
    return settings, engine, app


@pytest.fixture
def engine(env):
    return env[1]


@pytest.fixture
def app(env):
    return env[2]


@pytest.fixture
def storage(env):
    # Same roots the app's download service was wired with (tmp dirs).
    return get_storage(env[0])


@pytest.fixture
def client(env):
    return TestClient(env[2])


def seed_parse_task(engine, *, task_id=None, title="示例视频") -> str:
    task_id = task_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url=VIDEO_URL,
                platform="bilibili",
                media_type=MediaType.VIDEO,
                title=title,
                format="mp4",
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
) -> str:
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
            )
        )
    return download_id


def seed_completed_with_file(engine, *, task_id, storage) -> str:
    path = storage.save_bytes(MediaType.VIDEO, BUBBLE_FILENAME, BUBBLE_DATA)
    return seed_download(
        engine,
        task_id=task_id,
        status=DownloadStatus.COMPLETED,
        progress=100.0,
        total_bytes=len(BUBBLE_DATA),
        downloaded_bytes=len(BUBBLE_DATA),
        bubble_path=str(path),
    )


class TestSubmitApi:
    def test_submit_returns_success_envelope(self, client, engine):
        task_id = seed_parse_task(engine)
        response = client.post("/api/download/submit", json={"task_id": task_id})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == CODE_OK
        assert body["message"]
        data = body["data"]
        assert set(data) == {"download_id", "task_id", "status", "created_at"}
        assert data["task_id"] == task_id
        assert data["status"] == "pending"
        assert data["created_at"]
        assert len(data["download_id"]) == 36

    def test_submit_with_format_and_quality(self, client, engine):
        task_id = seed_parse_task(engine)
        response = client.post(
            "/api/download/submit",
            json={"task_id": task_id, "format": "mp4", "quality": "1080p"},
        )
        assert response.status_code == 200
        download_id = response.json()["data"]["download_id"]
        with session_scope(engine) as session:
            row = session.get(DownloadTask, download_id)
        assert row.format == "mp4"
        assert row.quality == "1080p"

    def test_submit_unknown_task_returns_3001(self, client):
        response = client.post(
            "/api/download/submit", json={"task_id": str(uuid.uuid4())}
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == CODE_TASK_NOT_FOUND
        assert body["data"] is None

    def test_submit_duplicate_active_download_returns_3002(self, client, engine):
        task_id = seed_parse_task(engine)
        seed_download(engine, task_id=task_id)
        response = client.post("/api/download/submit", json={"task_id": task_id})
        assert response.status_code == 409
        assert response.json()["code"] == CODE_TASK_ALREADY_DOWNLOADING

    def test_submit_malformed_task_id_returns_generic_400(self, client):
        response = client.post("/api/download/submit", json={"task_id": "nope"})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_submit_missing_task_id_returns_generic_400(self, client):
        response = client.post("/api/download/submit", json={})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_submit_unknown_field_returns_generic_400(self, client):
        # PRD §5.3 fields such as save_to_nas are Task 10 / NAS-API concerns:
        # an unknown body field must be rejected loudly (extra="forbid"),
        # never silently dropped while the client believes it took effect.
        response = client.post(
            "/api/download/submit",
            json={"task_id": str(uuid.uuid4()), "save_to_nas": True},
        )
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_parse_then_submit_end_to_end(self, tmp_path):
        """Real wiring (no overrides): POST /api/parse persists to the settings
        database, then POST /api/download/submit accepts its task_id."""
        settings = make_settings(
            database_url=f"sqlite:///{tmp_path / 'flow.db'}",
            temp_video_path=tmp_path / "bubble/video",
        )
        engine = build_engine(settings.database_url)
        Base.metadata.create_all(engine)
        assert seed.seed_admin(settings=settings, engine=engine) is True

        client = TestClient(create_app(settings=settings))
        parse_body = client.post("/api/parse", json={"urls": [VIDEO_URL]}).json()
        task_id = parse_body["data"]["results"][0]["task_id"]

        response = client.post("/api/download/submit", json={"task_id": task_id})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["task_id"] == task_id
        assert data["status"] == "pending"


class TestProgressApi:
    def test_progress_returns_snapshot(self, client, engine):
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
        response = client.get(f"/api/download/progress/{download_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == CODE_OK
        data = body["data"]
        assert data["download_id"] == download_id
        assert data["status"] == "downloading"
        assert data["progress"] == 25.0
        assert data["speed"] == 250.0
        assert data["downloaded_bytes"] == 250
        assert data["total_bytes"] == 1000
        assert data["remaining_time"] == pytest.approx(3.0)

    def test_progress_unknown_download_returns_3001(self, client):
        response = client.get(f"/api/download/progress/{uuid.uuid4()}")
        assert response.status_code == 400
        assert response.json()["code"] == CODE_TASK_NOT_FOUND

    def test_progress_malformed_download_id_returns_generic_400(self, client):
        response = client.get("/api/download/progress/not-a-uuid")
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST


class TestLatestByTaskApi:
    """GET /api/download/by-task/{task_id} — recovery lookup for the preview UI."""

    def test_returns_newest_download_for_task(self, client, engine):
        task_id = seed_parse_task(engine)
        older = seed_download(
            engine, task_id=task_id, status=DownloadStatus.FAILED, progress=10.0
        )
        newest = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.DOWNLOADING,
            progress=50.0,
            speed=100.0,
            total_bytes=1000,
            downloaded_bytes=500,
        )
        response = client.get(f"/api/download/by-task/{task_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == CODE_OK
        data = body["data"]
        assert data["download_id"] == newest
        assert data["download_id"] != older
        assert data["task_id"] == task_id
        assert data["status"] == "downloading"
        assert data["progress"] == 50.0
        assert data["total_bytes"] == 1000
        assert data["downloaded_bytes"] == 500

    def test_returns_completed_snapshot_for_recovery(self, client, engine, storage):
        # The exact preview-recovery scenario: the file finished server-side
        # and the client re-attaches after a reload.
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        response = client.get(f"/api/download/by-task/{task_id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["download_id"] == download_id
        assert data["status"] == "completed"
        assert data["progress"] == 100.0

    def test_no_download_for_task_returns_3001(self, client, engine):
        task_id = seed_parse_task(engine)
        response = client.get(f"/api/download/by-task/{task_id}")
        assert response.status_code == 400
        assert response.json()["code"] == CODE_TASK_NOT_FOUND

    def test_malformed_task_id_returns_generic_400(self, client):
        response = client.get("/api/download/by-task/not-a-uuid")
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST


class TestFileApi:
    @staticmethod
    def _file_url(download_id, token=None) -> str:
        url = f"/api/download/file/{download_id}"
        if token is not None:
            url += f"?token={token}"
        return url

    def test_file_serves_bytes_with_valid_token(
        self, client, engine, app, storage
    ):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = app.state.download_service.issue_download_token(download_id).token

        response = client.get(self._file_url(download_id, token))
        assert response.status_code == 200
        assert response.content == BUBBLE_DATA
        assert "content-disposition" in response.headers

    def test_file_missing_token_returns_5003(self, client, engine, app, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        response = client.get(self._file_url(download_id))
        assert response.status_code == 401
        assert response.json()["code"] == CODE_FILE_TOKEN_INVALID

    def test_file_garbage_token_returns_5003(self, client, engine, app, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        response = client.get(self._file_url(download_id, "garbage"))
        assert response.status_code == 401
        assert response.json()["code"] == CODE_FILE_TOKEN_INVALID

    def test_file_expired_token_returns_5003(self, client, engine, app, storage, env):
        settings = env[0]
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = JwtOneTimeTokenProvider(
            settings.secret_key, ttl=timedelta(seconds=-5)
        ).issue(download_id=download_id)
        response = client.get(self._file_url(download_id, token))
        assert response.status_code == 401
        assert response.json()["code"] == CODE_FILE_TOKEN_INVALID

    def test_file_reused_token_serves_again_within_expiry(self, client, engine, app, storage):
        # Playback compatibility: the token is short-lived (5 min), NOT
        # single-use — a media player's repeated/range requests must all serve.
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = app.state.download_service.issue_download_token(download_id).token
        first = client.get(self._file_url(download_id, token))
        assert first.status_code == 200
        second = client.get(self._file_url(download_id, token))
        assert second.status_code == 200
        third = client.get(self._file_url(download_id, token))
        assert third.status_code == 200

    def test_file_not_completed_returns_5002(self, client, engine, env):
        settings = env[0]
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        # issue_download_token refuses to mint for a pending task, so forge a
        # token directly with the app's secret key.
        token = JwtOneTimeTokenProvider(settings.secret_key).issue(
            download_id=download_id
        )
        response = client.get(self._file_url(download_id, token))
        assert response.status_code == 400
        assert response.json()["code"] == CODE_FILE_NOT_DOWNLOADED

    def test_file_expired_task_returns_5004(self, client, engine, env):
        settings = env[0]
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, status=DownloadStatus.EXPIRED)
        token = JwtOneTimeTokenProvider(settings.secret_key).issue(
            download_id=download_id
        )
        response = client.get(self._file_url(download_id, token))
        assert response.status_code == 410
        assert response.json()["code"] == CODE_FILE_EXPIRED

    def test_file_missing_bubble_file_returns_5001(self, client, engine, app, storage):
        task_id = seed_parse_task(engine)
        bubble_root = storage.bubble_root(MediaType.VIDEO)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            bubble_path=str(bubble_root / "gone.mp4"),
        )
        token = app.state.download_service.issue_download_token(download_id).token
        response = client.get(self._file_url(download_id, token))
        assert response.status_code == 404
        assert response.json()["code"] == CODE_FILE_NOT_FOUND

    def test_file_unknown_download_returns_3001(self, client):
        response = client.get(self._file_url(str(uuid.uuid4()), "garbage"))
        assert response.status_code == 400
        assert response.json()["code"] == CODE_TASK_NOT_FOUND
