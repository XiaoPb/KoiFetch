"""Task 3 wire contracts for private media manifests and resource proxies."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.parse import get_parse_service
from app.api.preview import get_preview_service
from app.application.parse_service import ParseService
from app.application.preview_service import PreviewService
from app.domain import LivePhotoPair, MediaManifest, MediaResource, MediaType, ParseResult
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import ParseTask
from app.infrastructure.config import Settings
from app.main import create_app


SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"
TASK_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SOURCE_URL = "https://v.douyin.com/live"


def settings(**overrides):
    return Settings(
        admin_password=PASSWORD,
        secret_key=SECRET,
        cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        **overrides,
    )


def live_manifest():
    return MediaManifest(
        kind="live_photo",
        live_photos=(
            LivePhotoPair(
                image=MediaResource(url="https://cdn.example/a.jpg", format="jpg"),
                motion=MediaResource(url="https://cdn.example/a.mp4", format="mp4"),
            ),
        ),
        warnings=("warning",),
    )


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'manifest-api.db'}")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine):
    app = create_app(settings=settings())

    class Parser:
        def parse(self, command):
            return [
                ParseResult(
                    task_id=TASK_ID,
                    url=command.urls[0],
                    media_type=MediaType.LIVE_PHOTO,
                    platform="douyin",
                    title="live",
                    cover="https://cdn.example/cover.jpg",
                    format="jpg",
                    metadata={"manifest": live_manifest().model_dump(mode="json")},
                )
            ]

    app.dependency_overrides[get_parse_service] = lambda: ParseService(
        parser=Parser(), engine=engine
    )
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(engine=engine)
    return TestClient(app)


def test_parse_serializes_only_safe_live_photo_routes(client):
    result = client.post("/api/parse", json={"urls": [SOURCE_URL]}).json()["data"]["results"][0]
    assert result["manifest"]["kind"] == "live_photo"
    assert result["manifest"]["live_photos"] == [
        {
            "image_url": f"/api/preview/{TASK_ID}/resources/live/0/image",
            "motion_url": f"/api/preview/{TASK_ID}/resources/live/0/motion",
        }
    ]
    assert "https://cdn.example" not in json.dumps(result["manifest"])


def test_preview_serializes_only_safe_live_photo_routes(client, engine):
    client.post("/api/parse", json={"urls": [SOURCE_URL]})
    data = client.get(f"/api/preview/{TASK_ID}").json()["data"]
    assert data["manifest"]["live_photos"][0]["image_url"].endswith(
        "/resources/live/0/image"
    )
    assert "https://cdn.example" not in json.dumps(data["manifest"])


def test_live_resource_proxy_forwards_range_and_content_headers(engine):
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=TASK_ID,
                url=SOURCE_URL,
                platform="douyin",
                media_type=MediaType.LIVE_PHOTO,
                title="live",
                format="jpg",
                metadata_={"manifest": live_manifest().model_dump(mode="json")},
            )
        )

    def handler(request: httpx.Request):
        assert request.method == "GET"
        assert request.headers["range"] == "bytes=0-2"
        return httpx.Response(
            206,
            headers={
                "content-type": "image/jpeg",
                "content-range": "bytes 0-2/3",
                "etag": "abc",
            },
            content=b"jpg",
        )

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, transport=httpx.MockTransport(handler)
    )
    response = TestClient(app).get(
        f"/api/preview/{TASK_ID}/resources/live/0/image",
        headers={"Range": "bytes=0-2"},
    )
    assert response.status_code == 206
    assert response.content == b"jpg"
    assert response.headers["content-range"] == "bytes 0-2/3"
    assert response.headers["etag"] == "abc"


def test_missing_motion_is_stable_not_found(engine):
    manifest = MediaManifest(
        kind="live_photo",
        live_photos=(LivePhotoPair(image=MediaResource(url="https://cdn.example/a.jpg", format="jpg")),),
    )
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=TASK_ID,
                url=SOURCE_URL,
                platform="douyin",
                media_type=MediaType.LIVE_PHOTO,
                title="live",
                format="jpg",
                metadata_={"manifest": manifest.model_dump(mode="json")},
            )
        )
    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(engine=engine)
    response = TestClient(app).get(
        f"/api/preview/{TASK_ID}/resources/live/0/motion"
    )
    assert response.status_code == 404
    assert response.json()["code"] == 404


def test_head_resource_uses_upstream_head_and_returns_headers(engine):
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=TASK_ID,
                url=SOURCE_URL,
                platform="douyin",
                media_type=MediaType.LIVE_PHOTO,
                title="live",
                format="jpg",
                metadata_={"manifest": live_manifest().model_dump(mode="json")},
            )
        )

    def handler(request: httpx.Request):
        assert request.method == "HEAD"
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg", "content-length": "3", "etag": "abc"},
        )

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, transport=httpx.MockTransport(handler)
    )
    response = TestClient(app).head(
        f"/api/preview/{TASK_ID}/resources/live/0/image"
    )
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "3"
    assert response.headers["etag"] == "abc"
