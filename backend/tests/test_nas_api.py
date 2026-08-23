"""Tests for the NAS HTTP API (Task 10).

Covers the wire contract for ``POST /api/nas/save``:

* The endpoint is admin-only: missing/malformed Authorization header → 401
  (2001/2003); a valid token (obtained through ``POST /api/auth/login``) is
  authorized — v1 has a single admin, so 2002 (权限不足) is reserved for a
  future role system and never fires today.
* Success — ``200`` envelope ``{code: 0, message, data: {nas_path, file_size,
  saved_at}}`` with the bubble file actually moved into the pond.
* Errors — unknown download → 400 ``3001``; not completed → 400 ``5002``;
  missing bubble file → 404 ``5001``; invalid target path (``..``, blank) →
  400 generic; malformed ``download_id`` / unknown body field → 400 generic.
* Browsing stays out of v1: there is no ``GET /api/nas/list`` (404 envelope).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.adapters.factory import get_storage
from app.adapters.tokens_jwt import JwtAccessTokenProvider
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_FILE_NOT_FOUND,
    CODE_INVALID_TOKEN,
    CODE_OK,
    CODE_TASK_NOT_FOUND,
    CODE_UNAUTHORIZED,
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

BUBBLE_DATA = b"fake-video-bytes-for-nas-api-tests"
BUBBLE_FILENAME = "2026-01-01_api-shipin_av123.mp4"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'nas-api.db'}",
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
    # Same roots the app's nas service was wired with (tmp dirs).
    return get_storage(env[0])


@pytest.fixture
def client(env):
    return TestClient(env[2])


def login(client) -> str:
    """Log in as the seeded admin; return the bearer token."""
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["data"]["token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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
    progress=0.0,
    bubble_path=None,
) -> str:
    download_id = download_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            DownloadTask(
                download_id=download_id,
                task_id=task_id,
                title="示例视频",
                format="mp4",
                status=status,
                progress=progress,
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
        bubble_path=str(path),
    )


class TestAuth:
    def test_save_requires_login(self, client):
        response = client.post("/api/nas/save", json={"download_id": str(uuid.uuid4()), "target_path": "/视频"})
        assert response.status_code == 401
        body = response.json()
        assert body["code"] == CODE_UNAUTHORIZED
        assert body["data"] is None

    def test_save_rejects_invalid_token(self, client):
        response = client.post(
            "/api/nas/save",
            json={"download_id": str(uuid.uuid4()), "target_path": "/视频"},
            headers=auth_headers("not-a-jwt"),
        )
        assert response.status_code == 401
        assert response.json()["code"] == CODE_INVALID_TOKEN

    def test_save_rejects_forged_token(self, client):
        forged = JwtAccessTokenProvider(
            "another-secret-key-0123456789abcdef0123"
        ).issue(user_id=1, username="admin")
        response = client.post(
            "/api/nas/save",
            json={"download_id": str(uuid.uuid4()), "target_path": "/视频"},
            headers=auth_headers(forged),
        )
        assert response.status_code == 401
        assert response.json()["code"] == CODE_INVALID_TOKEN


class TestSaveApi:
    def test_save_returns_success_envelope(self, client, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = login(client)

        response = client.post(
            "/api/nas/save",
            json={"download_id": download_id, "target_path": "/视频/抖音"},
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == CODE_OK
        assert "文件已存入NAS" in body["message"]
        data = body["data"]
        assert set(data) == {"nas_path", "file_size", "saved_at"}
        assert data["nas_path"] == "/视频/抖音/2026-01-01_api-shipin_av123.mp4"
        assert data["file_size"] == len(BUBBLE_DATA)
        assert data["saved_at"]

    def test_save_moves_file_end_to_end(self, client, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = login(client)

        response = client.post(
            "/api/nas/save",
            json={"download_id": download_id, "target_path": "/视频/抖音"},
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        pond = storage.pond_root(MediaType.VIDEO) / "视频" / "抖音" / BUBBLE_FILENAME
        assert pond.is_file()
        assert pond.read_bytes() == BUBBLE_DATA
        assert not (storage.bubble_root(MediaType.VIDEO) / BUBBLE_FILENAME).exists()

    def test_save_persists_pond_path(self, client, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = login(client)

        client.post(
            "/api/nas/save",
            json={"download_id": download_id, "target_path": "/视频/抖音"},
            headers=auth_headers(token),
        )
        with session_scope(engine) as session:
            row = session.get(DownloadTask, download_id)
        assert row.pond_path == "视频/抖音/2026-01-01_api-shipin_av123.mp4"
        assert row.completed_at is not None

    def test_save_unknown_download_returns_3001(self, client):
        token = login(client)
        response = client.post(
            "/api/nas/save",
            json={"download_id": str(uuid.uuid4()), "target_path": "/视频"},
            headers=auth_headers(token),
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == CODE_TASK_NOT_FOUND
        assert body["data"] is None

    def test_save_not_completed_returns_5002(self, client, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, status=DownloadStatus.PENDING)
        token = login(client)
        response = client.post(
            "/api/nas/save",
            json={"download_id": download_id, "target_path": "/视频"},
            headers=auth_headers(token),
        )
        assert response.status_code == 400
        assert response.json()["code"] == CODE_FILE_NOT_DOWNLOADED

    def test_save_missing_bubble_file_returns_5001(self, client, engine, storage):
        task_id = seed_parse_task(engine)
        bubble_root = storage.bubble_root(MediaType.VIDEO)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            bubble_path=str(bubble_root / "gone.mp4"),
        )
        token = login(client)
        response = client.post(
            "/api/nas/save",
            json={"download_id": download_id, "target_path": "/视频"},
            headers=auth_headers(token),
        )
        assert response.status_code == 404
        assert response.json()["code"] == CODE_FILE_NOT_FOUND

    def test_save_bad_target_returns_400(self, client, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = login(client)
        for target in ("..", "a/../b", "C:\\evil", "视频\\抖音", "   "):
            response = client.post(
                "/api/nas/save",
                json={"download_id": download_id, "target_path": target},
                headers=auth_headers(token),
            )
            assert response.status_code == 400, target
            assert response.json()["code"] == CODE_BAD_REQUEST, target

    def test_save_malformed_download_id_returns_400(self, client):
        token = login(client)
        response = client.post(
            "/api/nas/save",
            json={"download_id": "not-a-uuid", "target_path": "/视频"},
            headers=auth_headers(token),
        )
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_save_missing_target_path_returns_400(self, client, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = login(client)
        for payload in (
            {"download_id": download_id},
            {"download_id": download_id, "target_path": ""},
        ):
            response = client.post("/api/nas/save", json=payload, headers=auth_headers(token))
            assert response.status_code == 400, payload
            assert response.json()["code"] == CODE_BAD_REQUEST, payload

    def test_save_unknown_body_field_returns_400(self, client, engine, storage):
        task_id = seed_parse_task(engine)
        download_id = seed_completed_with_file(engine, task_id=task_id, storage=storage)
        token = login(client)
        response = client.post(
            "/api/nas/save",
            json={"download_id": download_id, "target_path": "/视频", "rename": "x"},
            headers=auth_headers(token),
        )
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST


class TestNoBrowsing:
    """v1 keeps NAS browsing and destructive operations out of scope."""

    def test_no_nas_list_endpoint(self, client):
        response = client.get("/api/nas/list")
        assert response.status_code == 404
        assert set(response.json()) == {"code", "message", "data"}
