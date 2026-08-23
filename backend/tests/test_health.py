"""Tests for the FastAPI entrypoint and the readiness endpoint.

Covers: the ``GET /api/health`` contract (``{code, message, data}`` envelope,
service + storage readiness), storage-root directory creation, degraded mode
when a root cannot be created, CORS wiring, and the module-level ``app`` that
uvicorn imports.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infrastructure.config import Settings
from app.main import APP_TITLE, create_app

# Storage root field names on Settings, in contract order. The health endpoint
# reports one status per root.
STORAGE_ROOT_FIELDS = [
    "video_storage_path",
    "image_storage_path",
    "music_storage_path",
    "temp_video_path",
    "temp_image_path",
    "temp_music_path",
]


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Settings with storage roots under ``tmp_path`` so tests never touch repo data."""
    values = {field: tmp_path / field for field in STORAGE_ROOT_FIELDS}
    values.update(overrides)
    return Settings(admin_password="pw", secret_key="sk", **values)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(settings=make_settings(tmp_path)))


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_ready_shape_uses_code_message_data_envelope(self, client):
        body = client.get("/api/health").json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == 0
        assert body["message"] == "ok"

    def test_ready_reports_service_and_storage_ok(self, client):
        body = client.get("/api/health").json()
        assert body["data"]["status"] == "ok"
        assert body["data"]["services"] == {"api": "ok", "storage": "ok"}
        for field in STORAGE_ROOT_FIELDS:
            assert body["data"]["storage_roots"][field] == "ok"

    def test_health_creates_missing_storage_dirs(self, tmp_path):
        client = TestClient(create_app(settings=make_settings(tmp_path)))
        client.get("/api/health")
        for field in STORAGE_ROOT_FIELDS:
            assert (tmp_path / field).is_dir()

    def test_health_degrades_when_storage_uncreatable(self, tmp_path):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a directory", encoding="utf-8")
        settings = make_settings(tmp_path, video_storage_path=blocker / "sub")
        body = TestClient(create_app(settings=settings)).get("/api/health").json()

        assert body["code"] == 1
        assert body["message"] == "storage not ready"
        assert body["data"]["status"] == "degraded"
        assert body["data"]["services"] == {"api": "ok", "storage": "degraded"}
        assert body["data"]["storage_roots"]["video_storage_path"] == "error"
        # Other roots remain healthy.
        assert body["data"]["storage_roots"]["image_storage_path"] == "ok"


class TestCors:
    def test_allowed_origin_echoed_in_response(self, tmp_path):
        settings = make_settings(tmp_path, cors_origins=["http://localhost:5173"])
        client = TestClient(create_app(settings=settings))
        response = client.get(
            "/api/health", headers={"Origin": "http://localhost:5173"}
        )
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_disallowed_origin_gets_no_cors_header(self, tmp_path):
        settings = make_settings(tmp_path, cors_origins=["http://localhost:5173"])
        client = TestClient(create_app(settings=settings))
        response = client.get(
            "/api/health", headers={"Origin": "http://evil.example"}
        )
        assert "access-control-allow-origin" not in response.headers


class TestAppEntrypoint:
    def test_module_app_is_fastapi_instance(self):
        # The module-level ``app`` is what uvicorn imports
        # (uvicorn app.main:app --app-dir backend). Conftest provides safe
        # secret env defaults so importing the module succeeds.
        from app.main import app

        assert isinstance(app, FastAPI)
        assert app.title == APP_TITLE

    def test_create_app_accepts_explicit_settings(self, tmp_path):
        app = create_app(settings=make_settings(tmp_path))
        assert isinstance(app, FastAPI)
