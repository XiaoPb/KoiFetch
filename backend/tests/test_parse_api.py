"""Tests for the parse API (Task 8): ``POST /api/parse`` envelope + PRD shape.

Covers the wire contract: success returns ``200`` with the ``{code, message,
data}`` envelope where ``data = {results, failed}`` and each result carries the
PRD ParseResult keys (task_id, url, type, platform, title, cover, duration,
file_size_mb, format, available_qualities, available_bitrates); domain
validation failures surface as the PRD codes (empty/blank → 1001, malformed →
1002, batch over 50 → generic 400); structural body problems (missing field,
non-list, malformed JSON) → generic 400; and ``create_app`` binds the parse
service to its own settings database so a POST persists there (no overrides).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.factory import get_parser
from app.adapters.parser_stub import StubParserAdapter
from app.api.parse import get_parse_service
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_COOKIE_ERROR,
    CODE_OK,
    CODE_URL_EMPTY,
    CODE_URL_INVALID,
)
from app.application.parse_service import ParseService
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import ParseTask
from app.main import create_app

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"
MUSIC_URL = "https://music.example.com/song/hello.mp3"
BAD_URL = "https://bad.example.com/video/x"


class _FlakyParser:
    """A parser that fails for one specific URL, delegating the rest to the stub."""

    def __init__(self, bad_url: str) -> None:
        self._bad_url = bad_url
        self._delegate = StubParserAdapter()

    def parse(self, command):
        if command.urls[0] == self._bad_url:
            raise ValueError("platform engine unavailable")
        return self._delegate.parse(command)

PRD_RESULT_KEYS = {
    "task_id",
    "url",
    "type",
    "platform",
    "title",
    "cover",
    "duration",
    "file_size_mb",
    "format",
    "available_qualities",
    "available_bitrates",
    "manifest",
}


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", **overrides)


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'parse-api.db'}")
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=make_settings(), engine=engine) is True
    return engine


@pytest.fixture
def client(engine):
    app = create_app(settings=make_settings())
    app.dependency_overrides[get_parse_service] = lambda: ParseService(
        parser=get_parser(), engine=engine
    )
    return TestClient(app)


class TestParseSuccess:
    def test_success_returns_envelope_with_results_and_failed(self, client):
        response = client.post("/api/parse", json={"urls": [VIDEO_URL]})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == CODE_OK
        assert body["message"]

        data = body["data"]
        assert set(data) == {"results", "failed"}
        assert data["failed"] == []

    def test_result_matches_prd_shape(self, client):
        response = client.post("/api/parse", json={"urls": [VIDEO_URL]})
        result = response.json()["data"]["results"][0]
        assert set(result) == PRD_RESULT_KEYS
        assert result["type"] == "video"
        assert result["url"] == VIDEO_URL
        assert result["platform"] == "bilibili"
        assert result["title"]
        assert result["duration"]
        assert result["file_size_mb"]
        assert result["format"] == "mp4"
        assert result["available_qualities"] == ["1080p", "720p", "480p"]
        assert result["manifest"] is None  # stub metadata carries no manifest

    def test_multiple_urls_return_ordered_results(self, client):
        response = client.post("/api/parse", json={"urls": [VIDEO_URL, MUSIC_URL]})
        results = response.json()["data"]["results"]
        assert [r["url"] for r in results] == [VIDEO_URL, MUSIC_URL]
        assert results[1]["type"] == "music"

    def test_image_url_reports_type_image(self, client):
        response = client.post(
            "/api/parse", json={"urls": ["https://www.xiaohongshu.com/photo/cover.jpg"]}
        )
        assert response.json()["data"]["results"][0]["type"] == "image"


class TestParseValidation:
    def test_empty_urls_returns_400_code_1001(self, client):
        response = client.post("/api/parse", json={"urls": []})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_URL_EMPTY
        assert response.json()["data"] is None

    def test_blank_entry_returns_400_code_1001(self, client):
        response = client.post("/api/parse", json={"urls": [""]})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_URL_EMPTY

    def test_invalid_url_returns_400_code_1002(self, client):
        response = client.post("/api/parse", json={"urls": ["not-a-url"]})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_URL_INVALID

    def test_too_many_urls_returns_400(self, client):
        # Rejected at the wire level (ParseRequest.max_length=50) before the
        # domain count rule; same visible envelope code 400.
        urls = [f"https://example.com/video/{i}" for i in range(51)]
        response = client.post("/api/parse", json={"urls": urls})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_missing_urls_field_returns_400(self, client):
        response = client.post("/api/parse", json={})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_urls_not_a_list_returns_400(self, client):
        response = client.post("/api/parse", json={"urls": "not-a-list"})
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_malformed_json_body_returns_400(self, client):
        response = client.post(
            "/api/parse",
            content="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert set(response.json()) == {"code", "message", "data"}

    def test_error_envelope_shape(self, client):
        response = client.post("/api/parse", json={"urls": []})
        assert set(response.json()) == {"code", "message", "data"}
        assert response.json()["message"]


class TestPartialFailure:
    """Per-URL parser failures surface in the response ``failed`` list."""

    def test_failed_urls_surface_in_response_failed_list(self, engine):
        app = create_app(settings=make_settings())
        app.dependency_overrides[get_parse_service] = lambda: ParseService(
            parser=_FlakyParser(BAD_URL), engine=engine
        )
        client = TestClient(app)

        response = client.post(
            "/api/parse", json={"urls": [VIDEO_URL, BAD_URL, MUSIC_URL]}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert [r["url"] for r in data["results"]] == [VIDEO_URL, MUSIC_URL]
        assert len(data["failed"]) == 1
        failure = data["failed"][0]
        assert failure["url"] == BAD_URL
        # Sanitized: stable message + exception class name; raw exception text
        # is never echoed to the client.
        assert "Parse failed" in failure["error"]
        assert "(ValueError)" in failure["error"]
        assert "platform engine unavailable" not in failure["error"]
        # Untagged failures carry no PRD code — the additive key stays null.
        assert failure["code"] is None

    def test_all_urls_failed_still_returns_200_with_empty_results(self, engine):
        app = create_app(settings=make_settings())
        app.dependency_overrides[get_parse_service] = lambda: ParseService(
            parser=_FlakyParser(VIDEO_URL), engine=engine
        )
        client = TestClient(app)

        response = client.post("/api/parse", json={"urls": [VIDEO_URL]})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["results"] == []
        assert len(data["failed"]) == 1

    def test_unsupported_platform_failure_serializes_code_1003(self, engine):
        from app.adapters.engine_errors import UnsupportedPlatformError
        from app.api.responses import CODE_PLATFORM_UNSUPPORTED

        class _UnsupportedParser:
            def __init__(self, bad_url: str) -> None:
                self._bad_url = bad_url
                self._delegate = StubParserAdapter()

            def parse(self, command):
                if command.urls[0] == self._bad_url:
                    raise UnsupportedPlatformError("平台不支持 / Unsupported platform")
                return self._delegate.parse(command)

        app = create_app(settings=make_settings())
        app.dependency_overrides[get_parse_service] = lambda: ParseService(
            parser=_UnsupportedParser(BAD_URL), engine=engine
        )
        client = TestClient(app)

        response = client.post("/api/parse", json={"urls": [VIDEO_URL, BAD_URL]})
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["results"]) == 1
        assert data["failed"] == [
            {
                "url": BAD_URL,
                "error": "平台不支持 / Unsupported platform",
                "code": CODE_PLATFORM_UNSUPPORTED,
            }
        ]

    def test_cookie_error_failure_serializes_code_1006(self, engine):
        from app.adapters.engine_errors import CookieInvalidError

        douyin_url = "https://v.douyin.com/abc/"

        class _CookieRejectingParser:
            def __init__(self, bad_url: str) -> None:
                self._bad_url = bad_url
                self._delegate = StubParserAdapter()

            def parse(self, command):
                if command.urls[0] == self._bad_url:
                    raise CookieInvalidError(
                        "Cookie 无效或已过期，请重新设置 / Cookie invalid or expired — please update it"
                    )
                return self._delegate.parse(command)

        app = create_app(settings=make_settings())
        app.dependency_overrides[get_parse_service] = lambda: ParseService(
            parser=_CookieRejectingParser(douyin_url), engine=engine
        )
        client = TestClient(app)

        response = client.post("/api/parse", json={"urls": [douyin_url]})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["results"] == []
        assert len(data["failed"]) == 1
        failure = data["failed"][0]
        assert failure["url"] == douyin_url
        assert "Cookie 无效或已过期" in failure["error"]
        assert failure["code"] == CODE_COOKIE_ERROR


class TestParsePersistence:
    def test_parse_persists_to_settings_database(self, tmp_path):
        """No dependency overrides: create_app's own wiring must persist to the
        database its settings object points at (not the process singleton)."""
        settings = make_settings(database_url=f"sqlite:///{tmp_path / 'wired.db'}")
        engine = build_engine(settings.database_url)
        Base.metadata.create_all(engine)
        assert seed.seed_admin(settings=settings, engine=engine) is True

        client = TestClient(create_app(settings=settings))
        response = client.post("/api/parse", json={"urls": [VIDEO_URL]})
        assert response.status_code == 200

        with session_scope(engine) as session:
            stored = list(session.scalars(select(ParseTask)))
        assert len(stored) == 1
        assert stored[0].url == VIDEO_URL
        assert stored[0].media_type.value == "video"


class TestParseMediaUrls:
    """Engine-style private metadata never crosses the API boundary."""

    def test_video_result_does_not_expose_video_url(self, engine):
        from app.domain import MediaType, ParseResult
        import uuid as _uuid

        class _EngineStyleParser:
            def parse(self, command):
                return [
                    ParseResult(
                        task_id=str(_uuid.uuid4()),
                        url=command.urls[0],
                        media_type=MediaType.VIDEO,
                        platform="douyin",
                        title="clip",
                        format="mp4",
                        metadata={"video_url": "https://cdn.example.com/v.mp4"},
                    )
                ]

        app = create_app(settings=make_settings())
        app.dependency_overrides[get_parse_service] = lambda: ParseService(
            parser=_EngineStyleParser(), engine=engine
        )
        response = TestClient(app).post(
            "/api/parse", json={"urls": ["https://v.douyin.com/abc/"]}
        )
        assert response.status_code == 200
        result = response.json()["data"]["results"][0]
        assert "video_url" not in result
        assert "https://cdn.example.com" not in str(result)
        assert result["manifest"] is None

    def test_image_result_does_not_expose_album_images(self, engine):
        from app.domain import MediaType, ParseResult
        import uuid as _uuid

        class _AlbumParser:
            def parse(self, command):
                return [
                    ParseResult(
                        task_id=str(_uuid.uuid4()),
                        url=command.urls[0],
                        media_type=MediaType.IMAGE,
                        platform="xiaohongshu",
                        title="album",
                        format="jpg",
                        metadata={
                            "images": [
                                {"url": "https://cdn.example.com/1.jpg"},
                                {"url": "https://cdn.example.com/2.jpg"},
                            ]
                        },
                    )
                ]

        app = create_app(settings=make_settings())
        app.dependency_overrides[get_parse_service] = lambda: ParseService(
            parser=_AlbumParser(), engine=engine
        )
        response = TestClient(app).post(
            "/api/parse", json={"urls": ["https://www.xiaohongshu.com/explore/abc"]}
        )
        assert response.status_code == 200
        result = response.json()["data"]["results"][0]
        assert "images" not in result
        assert "https://cdn.example.com" not in str(result)
        assert result["manifest"] is None
