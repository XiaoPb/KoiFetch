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
from app.api.parse import get_parse_service
from app.api.responses import (
    CODE_BAD_REQUEST,
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
}


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


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
