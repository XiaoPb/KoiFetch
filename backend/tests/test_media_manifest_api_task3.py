"""Task 3 wire contracts for private media manifests and resource proxies."""

import json
import logging
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.parse import get_parse_service
from app.api.preview import get_preview_service
from app.application.parse_service import ParseService
from app.application.preview_service import PreviewService
from app.adapters.safe_upstream import UpstreamStream
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
    assert result["cover"] == (
        f"/api/preview/{TASK_ID}/resources/live/0/image"
    )
    assert "https://cdn.example" not in json.dumps(result)


def test_preview_serializes_only_safe_live_photo_routes(client, engine):
    client.post("/api/parse", json={"urls": [SOURCE_URL]})
    data = client.get(f"/api/preview/{TASK_ID}").json()["data"]
    assert data["manifest"]["live_photos"][0]["image_url"].endswith(
        "/resources/live/0/image"
    )
    assert "https://cdn.example" not in json.dumps(data["manifest"])
    assert data["cover"] == f"/api/preview/{TASK_ID}/resources/live/0/image"
    assert "https://cdn.example" not in json.dumps(data)


def test_preview_rejects_corrupt_manifest_with_stable_business_error(engine):
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=TASK_ID,
                url=SOURCE_URL,
                platform="douyin",
                media_type=MediaType.LIVE_PHOTO,
                title="live",
                format="jpg",
                cover_url="https://cdn.example/cover.jpg",
                metadata_={"manifest": {"kind": "live_photo", "live_photos": []}},
            )
        )
    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(engine=engine)
    response = TestClient(app).get(f"/api/preview/{TASK_ID}")
    assert response.status_code == 400
    assert response.json()["code"] == 400
    assert response.json()["data"] is None


def test_preview_rejects_non_dict_metadata_with_stable_business_error(engine):
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=TASK_ID,
                url=SOURCE_URL,
                platform="douyin",
                media_type=MediaType.LIVE_PHOTO,
                title="live",
                format="jpg",
                metadata_=["not", "metadata"],
            )
        )
    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(engine=engine)
    response = TestClient(app).get(f"/api/preview/{TASK_ID}")
    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_resource_get_and_head_use_the_same_private_cache_policy(engine):
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

    methods = []

    def handler(request: httpx.Request):
        methods.append(request.method)
        return httpx.Response(
            200,
            headers={
                "content-type": "image/jpeg",
                "content-length": "3",
                "cache-control": "public, max-age=3600",
            },
            content=b"jpg" if request.method == "GET" else b"",
        )

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, transport=httpx.MockTransport(handler)
    )
    client = TestClient(app)
    get_response = client.get(f"/api/preview/{TASK_ID}/resources/live/0/image")
    head_response = client.head(f"/api/preview/{TASK_ID}/resources/live/0/image")
    assert methods == ["GET", "HEAD"]
    assert get_response.headers["cache-control"] == "private, no-store"
    assert head_response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "path",
    [
        "resources/video/0",
        "resources/image/0",
        "resources/live/0/bad-side",
        "resources/live/-1/image",
    ],
)
def test_resource_selector_rejects_cross_kind_and_invalid_selectors(engine, path):
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
    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(engine=engine)
    response = TestClient(app).get(f"/api/preview/{TASK_ID}/{path}")
    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_unexpected_upstream_proxy_error_is_not_sanitized_as_client_error(engine):
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

    class BrokenUpstream:
        def stream(self, url, **kwargs):
            raise RuntimeError("bug with https://cdn.example/private-token")

    service = PreviewService(engine=engine, upstream=BrokenUpstream())
    with pytest.raises(RuntimeError):
        service.stream_resource(TASK_ID, "live", 0, side="image")


def test_unexpected_proxy_error_uses_generic_9001_envelope_without_url(engine):
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

    class BrokenUpstream:
        def stream(self, url, **kwargs):
            raise RuntimeError("bug with https://cdn.example/private-token")

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, upstream=BrokenUpstream()
    )
    response = TestClient(app, raise_server_exceptions=False).get(
        f"/api/preview/{TASK_ID}/resources/live/0/image"
    )
    assert response.status_code == 500
    assert response.json()["code"] == 9001
    assert "cdn.example" not in response.text


def test_unexpected_proxy_logging_is_sanitized_and_uses_fixed_internal_error(
    engine, caplog
):
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

    class BrokenUpstream:
        def stream(self, url, **kwargs):
            raise RuntimeError("bug with https://cdn.example/private-token")

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, upstream=BrokenUpstream()
    )
    caplog.set_level(logging.ERROR)
    response = TestClient(app, raise_server_exceptions=False).get(
        f"/api/preview/{TASK_ID}/resources/live/0/image"
    )
    assert response.status_code == 500
    assert response.json()["code"] == 9001
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "cdn.example" not in log_text
    assert "private-token" not in log_text
    assert "bug with" not in log_text
    assert "RuntimeError" in log_text


@pytest.mark.parametrize("task_type, manifest_kind", [(MediaType.IMAGE, "video"), (MediaType.VIDEO, "image_album")])
def test_preview_rejects_manifest_kind_task_type_mismatch(engine, task_type, manifest_kind):
    manifest = MediaManifest(
        kind=manifest_kind,
        videos=(
            MediaResource(url="https://cdn.example/v.mp4", format="mp4"),
        )
        if manifest_kind == "video"
        else (),
        images=(
            MediaResource(url="https://cdn.example/a.jpg", format="jpg"),
        )
        if manifest_kind == "image_album"
        else (),
    )
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=TASK_ID,
                url=SOURCE_URL,
                platform="douyin",
                media_type=task_type,
                title="mismatch",
                format="jpg",
                metadata_={"manifest": manifest.model_dump(mode="json")},
            )
        )
    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(engine=engine)
    client = TestClient(app)
    preview_response = client.get(f"/api/preview/{TASK_ID}")
    assert preview_response.status_code == 400
    assert preview_response.json()["code"] == 400
    resource_kind = "video" if manifest_kind == "video" else "image"
    resource_response = client.get(
        f"/api/preview/{TASK_ID}/resources/{resource_kind}/0"
    )
    assert resource_response.status_code == 400
    assert resource_response.json()["code"] == 400


def test_parse_rejects_manifest_kind_task_type_mismatch(engine):
    mismatched = MediaManifest(
        kind="video",
        videos=(MediaResource(url="https://cdn.example/v.mp4", format="mp4"),),
    )

    class Parser:
        def parse(self, command):
            return [
                ParseResult(
                    task_id=str(uuid.uuid4()),
                    url=command.urls[0],
                    media_type=MediaType.LIVE_PHOTO,
                    platform="douyin",
                    title="mismatch",
                    format="jpg",
                    metadata={"manifest": mismatched.model_dump(mode="json")},
                )
            ]

    app = create_app(settings=settings())
    app.dependency_overrides[get_parse_service] = lambda: ParseService(
        parser=Parser(), engine=engine
    )
    response = TestClient(app).post("/api/parse", json={"urls": [SOURCE_URL]})
    assert response.status_code == 200
    assert response.json()["data"]["results"] == []
    assert response.json()["data"]["failed"][0]["error"].startswith("解析失败")


def test_resource_stream_closes_upstream_when_response_is_consumed(engine):
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
    closed = []

    class ClosableUpstream:
        def stream(self, url, **kwargs):
            return UpstreamStream(
                status_code=200,
                content_type="image/jpeg",
                headers={"content-length": "3"},
                chunks=iter([b"jpg"]),
                close=lambda: closed.append(True),
            )

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, upstream=ClosableUpstream()
    )
    response = TestClient(app).get(f"/api/preview/{TASK_ID}/resources/live/0/image")
    assert response.status_code == 200
    assert response.content == b"jpg"
    assert closed == [True]


def test_resource_stream_chunk_error_is_sanitized_and_closes_once(engine, caplog):
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
    close_calls = []

    def chunks():
        yield b"prefix"
        raise RuntimeError("bug https://cdn.example/private-token")

    class BrokenUpstream:
        def stream(self, url, **kwargs):
            return UpstreamStream(
                status_code=200,
                content_type="image/jpeg",
                headers={},
                chunks=chunks(),
                close=lambda: close_calls.append(True),
            )

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, upstream=BrokenUpstream()
    )
    caplog.set_level(logging.ERROR)
    response = TestClient(app, raise_server_exceptions=False).get(
        f"/api/preview/{TASK_ID}/resources/live/0/image"
    )

    assert response.status_code == 200
    assert close_calls == [True]
    assert "cdn.example" not in caplog.text
    assert "private-token" not in caplog.text
    assert "bug https" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert "Traceback (most recent call last)" not in caplog.text


def test_resource_stream_close_error_is_sanitized_and_closes_once(engine, caplog):
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
    close_calls = []

    def close():
        close_calls.append(True)
        raise RuntimeError("bug https://cdn.example/private-token")

    class BrokenUpstream:
        def stream(self, url, **kwargs):
            return UpstreamStream(
                status_code=200,
                content_type="image/jpeg",
                headers={},
                chunks=iter([b"jpg"]),
                close=close,
            )

    app = create_app(settings=settings())
    app.dependency_overrides[get_preview_service] = lambda: PreviewService(
        engine=engine, upstream=BrokenUpstream()
    )
    caplog.set_level(logging.ERROR)
    response = TestClient(app, raise_server_exceptions=False).get(
        f"/api/preview/{TASK_ID}/resources/live/0/image"
    )

    assert response.status_code == 200
    assert close_calls == [True]
    assert "cdn.example" not in caplog.text
    assert "private-token" not in caplog.text
    assert "bug https" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert "Traceback (most recent call last)" not in caplog.text


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
