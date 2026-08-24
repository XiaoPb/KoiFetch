"""Tests for the backend serving the built frontend (Task 18, no-Nginx).

v1 removes the container-internal Nginx: the FastAPI backend serves the Vite
build (``settings.frontend_dist_path``, default ``frontend/dist``) at "/" with
a client-side-routing fallback, so ``/api`` and ``/ws`` are same-origin and
need no reverse proxy. These tests pin that contract with a **synthetic dist**
(a fake index.html + asset): a real ``npm run build`` cannot run in this
sandbox (the esbuild spawn is blocked), and the static-serving logic — route
precedence, SPA fallback, API-404 preservation, conditional mount — is exactly
what the backend owns.

Contract asserted here:

* ``GET /`` serves ``index.html`` from the dist.
* Client-side routes (``/login``, ``/nas``, deep links) fall back to
  ``index.html``.
* Hashed assets are served at their absolute root paths (the Vite ``base`` is
  ``/``); a *missing* asset is an honest 404, never the SPA.
* ``/api/health`` keeps its readiness envelope; an unknown ``/api/*`` path
  keeps its envelope 404 and is never swallowed by the SPA fallback.
* The download WebSocket is still served (route precedence over the mount).
* Without a build, the app still boots: "/" serves an honest "frontend not
  built" placeholder, the API works, and API 404s stay envelopes.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.responses import CODE_OK, CODE_TASK_NOT_FOUND
from app.infrastructure import seed
from app.infrastructure.database import Base, build_engine
from app.main import create_app
from tests.conftest import make_settings

INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Koi Fetch</title></head>
<body><div id="root">KOI_SPA_MARKER</div></body></html>
"""
ASSET_JS = "console.log('KOI_ASSET_MARKER');"


@pytest.fixture
def dist(tmp_path):
    """A fake Vite build: index.html plus one hashed asset under /assets."""
    dist_dir = tmp_path / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist_dir / "assets" / "app.js").write_text(ASSET_JS, encoding="utf-8")
    return dist_dir


def make_app_settings(tmp_path, **overrides):
    """Settings for a hermetic static-serving app (temp DB + storage roots)."""
    return make_settings(
        database_url=f"sqlite:///{tmp_path / 'static.db'}",
        video_storage_path=tmp_path / "pond/video",
        image_storage_path=tmp_path / "pond/image",
        music_storage_path=tmp_path / "pond/music",
        temp_video_path=tmp_path / "bubble/video",
        temp_image_path=tmp_path / "bubble/image",
        temp_music_path=tmp_path / "bubble/music",
        **overrides,
    )


def make_app(tmp_path, **overrides):
    """Build the app exactly like the other suites: seeded schema + create_app."""
    settings = make_app_settings(tmp_path, **overrides)
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=settings, engine=engine) is True
    return create_app(settings=settings)


class TestSpaServing:
    @pytest.fixture
    def app(self, tmp_path, dist):
        return make_app(tmp_path, frontend_dist_path=dist)

    @pytest.fixture
    def client(self, app):
        with TestClient(app) as client:
            yield client

    def test_root_serves_index_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "KOI_SPA_MARKER" in response.text

    def test_deep_links_fall_back_to_index_html(self, client):
        # Client-side routes: the built app has no such files, so the SPA
        # fallback must serve index.html (browser refresh on a route).
        for path in ("/login", "/nas", "/download/abc-123"):
            response = client.get(path)
            assert response.status_code == 200
            assert "KOI_SPA_MARKER" in response.text

    def test_hashed_asset_served_at_absolute_root_path(self, client):
        # Vite's build base is "/", so index.html references /assets/... and
        # the backend must serve them at that exact path.
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        assert response.text == ASSET_JS

    def test_missing_asset_is_honest_404_not_spa_fallback(self, client):
        response = client.get("/assets/old-hash.js")
        assert response.status_code == 404
        assert "KOI_SPA_MARKER" not in response.text

    def test_api_health_still_returns_envelope(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["code"] == CODE_OK
        assert "KOI_SPA_MARKER" not in response.text

    def test_unknown_api_path_keeps_envelope_404(self, client):
        # The SPA fallback must never swallow API 404s: an unknown /api/* path
        # keeps the envelope (body code mirrors the HTTP status).
        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == 404
        assert "KOI_SPA_MARKER" not in response.text

    def test_websocket_still_served(self, client):
        # Route precedence: /ws/download/{id} is registered before the "/"
        # static mount, so the WebSocket keeps working with a build present.
        with client.websocket_connect(f"/ws/download/{uuid.uuid4()}") as ws:
            event = ws.receive_json()
        assert event["type"] == "error"
        assert event["data"]["code"] == CODE_TASK_NOT_FOUND


class TestNoDist:
    """The build is missing (fresh clone, tests): the API must not break."""

    @pytest.fixture
    def app(self, tmp_path):
        missing = tmp_path / "no-such-dist"
        assert not missing.exists()
        return make_app(tmp_path, frontend_dist_path=missing)

    @pytest.fixture
    def client(self, app):
        with TestClient(app) as client:
            yield client

    def test_root_serves_honest_placeholder(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "frontend not built" in response.text

    def test_deep_links_also_get_placeholder(self, client):
        response = client.get("/login")
        assert response.status_code == 200
        assert "frontend not built" in response.text

    def test_api_health_independent_of_dist(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["code"] == CODE_OK

    def test_unknown_api_path_keeps_envelope_404(self, client):
        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        assert response.json()["code"] == 404
        assert "frontend not built" not in response.text

    def test_websocket_still_served(self, client):
        with client.websocket_connect(f"/ws/download/{uuid.uuid4()}") as ws:
            event = ws.receive_json()
        assert event["type"] == "error"
        assert event["data"]["code"] == CODE_TASK_NOT_FOUND
