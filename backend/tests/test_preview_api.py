"""Tests for the preview API (Task 8): ``GET /api/preview/{task_id}``.

Covers the v1 single-media preview contract: an existing task returns ``200``
with the ``{code, message, data}`` envelope and metadata/stream information
(preview_type derived from media_type, title/platform/duration/format/
file_size_mb/cover from the row, qualities/bitrates from the persisted
metadata JSON, and a ``streams`` ladder for video/music); a well-formed but
unknown task_id → ``400`` code ``3001``; a malformed task_id → ``400`` generic;
and the end-to-end flow parse → preview (the preview of a just-parsed task
reflects what the parse endpoint persisted).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.preview import get_preview_service
from app.api.responses import CODE_BAD_REQUEST, CODE_OK, CODE_TASK_NOT_FOUND
from app.application.preview_service import PreviewService
from app.domain import MediaType
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import ParseTask
from app.main import create_app

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"

VIDEO_TASK_ID = "11111111-1111-1111-1111-111111111111"
IMAGE_TASK_ID = "22222222-2222-2222-2222-222222222222"
MUSIC_TASK_ID = "33333333-3333-3333-3333-333333333333"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


def _seed_task(engine, *, task_id, url, media_type, format, title="t", duration=323,
               metadata=None) -> None:
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url=url,
                platform="bilibili" if media_type is MediaType.VIDEO else "example",
                media_type=media_type,
                title=title,
                cover_url="https://cdn.example.com/cover.jpg",
                duration=duration,
                format=format,
                metadata_=metadata or {},
            )
        )


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'preview-api.db'}")
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=make_settings(), engine=engine) is True
    return engine


@pytest.fixture
def client(engine):
    app = create_app(settings=make_settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine
    )
    return TestClient(app)


class TestPreviewMetadata:
    def test_video_task_returns_preview_metadata_and_streams(self, client, engine):
        _seed_task(
            engine,
            task_id=VIDEO_TASK_ID,
            url=VIDEO_URL,
            media_type=MediaType.VIDEO,
            format="mp4",
            title="av123",
            metadata={
                "stub": True,
                "file_size_mb": 12.5,
                "available_qualities": ["1080p", "720p", "480p"],
                "available_bitrates": [],
            },
        )
        response = client.get(f"/api/preview/{VIDEO_TASK_ID}")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == CODE_OK

        data = body["data"]
        assert data["task_id"] == VIDEO_TASK_ID
        assert data["preview_type"] == "video"
        assert data["url"] == VIDEO_URL
        assert data["platform"] == "bilibili"
        assert data["title"] == "av123"
        assert data["cover"] == "https://cdn.example.com/cover.jpg"
        assert data["duration"] == "05:23"  # 323s formatted back to MM:SS
        assert data["format"] == "mp4"
        assert data["file_size_mb"] == 12.5
        assert data["available_qualities"] == ["1080p", "720p", "480p"]
        assert data["available_bitrates"] == []
        assert data["streams"] == [
            {"quality": "1080p", "bitrate": None, "format": "mp4"},
            {"quality": "720p", "bitrate": None, "format": "mp4"},
            {"quality": "480p", "bitrate": None, "format": "mp4"},
        ]

    def test_music_task_returns_bitrate_streams(self, client, engine):
        _seed_task(
            engine,
            task_id=MUSIC_TASK_ID,
            url="https://music.example.com/song/hello.mp3",
            media_type=MediaType.MUSIC,
            format="mp3",
            title="hello",
            metadata={
                "file_size_mb": 3.2,
                "available_qualities": [],
                "available_bitrates": ["320kbps", "FLAC"],
            },
        )
        response = client.get(f"/api/preview/{MUSIC_TASK_ID}")
        data = response.json()["data"]
        assert data["preview_type"] == "music"
        assert data["duration"] == "05:23"
        assert data["streams"] == [
            {"quality": None, "bitrate": "320kbps", "format": "mp3"},
            {"quality": None, "bitrate": "FLAC", "format": "mp3"},
        ]

    def test_image_task_has_no_streams(self, client, engine):
        _seed_task(
            engine,
            task_id=IMAGE_TASK_ID,
            url="https://www.xiaohongshu.com/photo/cover.jpg",
            media_type=MediaType.IMAGE,
            format="jpg",
            title="cover",
            metadata={
                "file_size_mb": 0.8,
                "available_qualities": [],
                "available_bitrates": [],
            },
        )
        response = client.get(f"/api/preview/{IMAGE_TASK_ID}")
        data = response.json()["data"]
        assert data["preview_type"] == "image"
        assert data["streams"] == []

    def test_missing_task_returns_400_code_3001(self, client):
        response = client.get(f"/api/preview/{uuid.uuid4()}")
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == CODE_TASK_NOT_FOUND
        assert body["data"] is None
        assert body["message"]

    def test_malformed_task_id_returns_400(self, client):
        response = client.get("/api/preview/not-a-uuid")
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST


class TestParseThenPreview:
    def test_parsed_task_can_be_previewed(self, tmp_path):
        """End-to-end on the real wiring (no dependency overrides): POST
        /api/parse persists to the settings database and GET /api/preview
        serves the row — exercising both create_app service bindings."""
        settings = make_settings(database_url=f"sqlite:///{tmp_path / 'flow.db'}")
        engine = build_engine(settings.database_url)
        Base.metadata.create_all(engine)
        assert seed.seed_admin(settings=settings, engine=engine) is True

        client = TestClient(create_app(settings=settings))
        parse_body = client.post("/api/parse", json={"urls": [VIDEO_URL]}).json()
        task_id = parse_body["data"]["results"][0]["task_id"]

        response = client.get(f"/api/preview/{task_id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["preview_type"] == "video"
        assert data["platform"] == "bilibili"
        assert data["format"] == "mp4"
        assert data["available_qualities"] == ["1080p", "720p", "480p"]
        assert len(data["streams"]) == 3
