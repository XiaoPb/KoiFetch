"""Tests for the FastAPI entrypoint, the readiness endpoint, and the
container readiness probe.

Covers: the ``GET /api/health`` contract (``{code, message, data}`` envelope,
service + storage readiness), storage-root directory creation, degraded mode
when a root cannot be created, CORS wiring, the module-level ``app`` that
uvicorn imports, and the ``app.health`` probe that the Compose backend
healthcheck runs — the probe must fail whenever the health body does not
report full readiness, so ``depends_on: service_healthy`` reflects storage
readiness, not just HTTP 200.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import health as health_module
from app.health import is_ready, main, probe
from app.infrastructure.config import Settings
from app.main import APP_TITLE, STORAGE_ROOT_FIELDS, create_app

# STORAGE_ROOT_FIELDS is imported from app.main so the per-root reporting
# contract cannot drift between the endpoint and its tests.


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Settings with storage roots under ``tmp_path`` so tests never touch repo data."""
    values = {field: tmp_path / field for field in STORAGE_ROOT_FIELDS}
    values.update(overrides)
    return Settings(admin_password="pw", secret_key="sk", **values)


class _FakeResponse:
    """Minimal file-like stand-in for urllib's HTTPResponse."""

    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


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


class TestReadinessProbe:
    """The Compose backend healthcheck runs ``python -m app.health``.

    ``/api/health`` always returns HTTP 200 and reports readiness in the body
    (``code``), so the probe must fail on anything but ``code == 0`` — that is
    what makes ``depends_on: service_healthy`` reflect storage readiness too.
    """

    def test_ready_body_accepted(self):
        assert is_ready({"code": 0, "message": "ok"})

    def test_degraded_body_rejected(self):
        assert not is_ready({"code": 1, "message": "storage not ready"})

    def test_missing_or_malformed_body_rejected(self):
        assert not is_ready({})
        assert not is_ready({"code": "0"})  # wrong type
        assert not is_ready("not a dict")

    def test_probe_ok_when_ready(self, monkeypatch):
        monkeypatch.setattr(
            health_module, "urlopen", lambda url, timeout=3: _FakeResponse('{"code": 0}')
        )
        assert probe() is True

    def test_probe_fails_on_degraded_body(self, monkeypatch):
        monkeypatch.setattr(
            health_module, "urlopen", lambda url, timeout=3: _FakeResponse('{"code": 1}')
        )
        assert probe() is False

    def test_probe_fails_on_connection_error(self, monkeypatch):
        def boom(url, timeout=3):
            raise OSError("connection refused")

        monkeypatch.setattr(health_module, "urlopen", boom)
        assert probe() is False

    def test_main_exit_code_follows_readiness(self, monkeypatch):
        monkeypatch.setattr(
            health_module, "urlopen", lambda url, timeout=3: _FakeResponse('{"code": 0}')
        )
        assert main() == 0
        monkeypatch.setattr(
            health_module, "urlopen", lambda url, timeout=3: _FakeResponse('{"code": 1}')
        )
        assert main() == 1
