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
  ``/``); a *missing* asset is an honest 404, never the SPA. Assets carry an
  ETag and honor ``If-None-Match`` (304).
* ``/api/health`` keeps its readiness envelope; an unknown ``/api/*`` path
  keeps its envelope 404 and is never swallowed by the SPA fallback.
* The download WebSocket is still served (route precedence over the mount).
* Method/safety edges: HEAD gets headers without a body; a non-GET/HEAD method
  on an unknown non-API path keeps the envelope 404 (never the SPA); path
  traversal attempts never leak files outside the dist (the SPA fallback or an
  honest 404, never sibling content); a broken build (dist directory without
  ``index.html``) keeps honest 404s instead of the placeholder.
* Without a build, the app still boots: "/" serves an honest "frontend not
  built" placeholder, the API works, and API 404s stay envelopes.
"""

import logging
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


@pytest.fixture
def caplog_info(caplog):
    """caplog with INFO capture enabled.

    pytest's caplog only captures WARNING+ by default; the frontend startup
    log line is INFO, and the level must be raised *before* ``create_app``
    runs (the app fixture depends on this fixture to guarantee the order).
    """
    caplog.set_level(logging.INFO)
    return caplog


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

    def test_asset_served_with_etag_and_conditional_304(self, client):
        # Starlette's FileResponse emits an ETag from the file stat, so
        # conditional requests still give browsers revalidation even though the
        # no-Nginx serving deliberately sets no Cache-Control (the old Nginx
        # `expires 1y` on /assets is not reproduced — see main.py notes).
        first = client.get("/assets/app.js")
        assert first.status_code == 200
        assert first.text == ASSET_JS
        etag = first.headers.get("etag")
        assert etag

        revalidated = client.get(
            "/assets/app.js", headers={"If-None-Match": etag}
        )
        assert revalidated.status_code == 304
        assert revalidated.content == b""

    def test_head_unknown_path_returns_headers_without_body(self, client):
        # The SPA fallback must honor HEAD semantics: 200 status and headers,
        # never a body (Starlette suppresses the body for HEAD responses).
        for path in ("/", "/login", "/nas"):
            response = client.head(path)
            assert response.status_code == 200
            assert response.content == b""

    def test_non_get_method_unknown_path_keeps_envelope_404(self, client):
        # Only GET/HEAD are candidates for the SPA fallback: a POST to an
        # unknown non-API path is a client error and must keep the envelope
        # 404, not be answered with index.html.
        for method in ("post", "put", "delete"):
            response = getattr(client, method)("/foo")
            assert response.status_code == 404
            assert response.json()["code"] == 404
            assert "KOI_SPA_MARKER" not in response.text

    def test_path_traversal_never_leaks_files_outside_dist(self, client, dist):
        # A sibling "secret" next to the dist must never be served, no matter
        # how the traversal is encoded. The client (httpx) normalizes most dot
        # segments before they reach the app, and StaticFiles rejects what
        # survives — either way the response is the SPA fallback or an honest
        # 404, never sibling content. This pins the boundary so a future
        # fallback refactor cannot silently open the dist to ../.
        secret = dist.parent / "TOP_SECRET.txt"
        secret.write_text("TOP_SECRET_BODY", encoding="utf-8")
        attempts = (
            "/../TOP_SECRET.txt",
            "/%2e%2e/TOP_SECRET.txt",
            "/..%2fTOP_SECRET.txt",
            "/assets/../TOP_SECRET.txt",
            "/%2e%2e%2fTOP_SECRET.txt",
        )
        for path in attempts:
            response = client.get(path)
            assert response.status_code in (200, 404), path
            assert "TOP_SECRET_BODY" not in response.text, path
            assert response.text != secret.read_text(encoding="utf-8"), path

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
        # Route precedence: /ws/download/{id} is registered on the app, so the
        # WebSocket keeps working with a build present (the frontend middleware
        # only ever answers the app's 404s).
        with client.websocket_connect(f"/ws/download/{uuid.uuid4()}") as ws:
            event = ws.receive_json()
        assert event["type"] == "error"
        assert event["data"]["code"] == CODE_TASK_NOT_FOUND


class TestNoDist:
    """The build is missing (fresh clone, tests): the API must not break."""

    @pytest.fixture
    def app(self, tmp_path, caplog_info):
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

    def test_head_placeholder_serves_headers_without_body(self, client):
        # HEAD on the placeholder keeps HEAD semantics: 200 + headers, no body.
        response = client.head("/")
        assert response.status_code == 200
        assert response.content == b""

    def test_missing_build_logs_placeholder_notice(self, client, caplog_info):
        # The placeholder is easy to miss behind a reverse proxy; the startup
        # log line is the operational signal that no build is being served.
        # It fires while the app fixture is being built, i.e. in the pytest
        # *setup* phase — pytest's caplog separates phases, so the setup
        # records must be inspected too (caplog.records alone covers only the
        # test call phase).
        client.get("/")
        records = caplog_info.get_records("setup") + caplog_info.records
        assert any(
            "frontend build not found" in record.getMessage()
            for record in records
        )

    def test_websocket_still_served(self, client):
        with client.websocket_connect(f"/ws/download/{uuid.uuid4()}") as ws:
            event = ws.receive_json()
        assert event["type"] == "error"
        assert event["data"]["code"] == CODE_TASK_NOT_FOUND


class TestBrokenDist:
    """The dist directory exists but has no index.html (broken/corrupt build).

    This is distinct from "no dist at all": StaticFiles is constructed (the
    directory exists) but every lookup 404s, and the SPA fallback must NOT
    pretend a broken build is fine — the honest answer is the app's envelope
    404, never the "frontend not built" placeholder (that would suggest the
    operator just needs to build, when the build is actually present but
    unusable).
    """

    @pytest.fixture
    def app(self, tmp_path):
        broken = tmp_path / "broken-dist"
        broken.mkdir()  # exists, but no index.html
        return make_app(tmp_path, frontend_dist_path=broken)

    @pytest.fixture
    def client(self, app):
        with TestClient(app) as client:
            yield client

    def test_root_keeps_honest_404(self, client):
        response = client.get("/")
        assert response.status_code == 404
        assert response.json()["code"] == 404
        assert "frontend not built" not in response.text

    def test_deep_link_keeps_honest_404(self, client):
        response = client.get("/login")
        assert response.status_code == 404
        assert response.json()["code"] == 404

    def test_api_health_independent_of_broken_dist(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["code"] == CODE_OK
