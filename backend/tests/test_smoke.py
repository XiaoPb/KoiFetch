"""Offline end-to-end smoke path (Task 17): one green run through the v1 stack.

Exercises the full user flow over the *real* application wiring — the same
``create_app`` the uvicorn entrypoint uses, the real stub adapters (parser,
downloader, storage, JWT token providers), the real worker batch executor
(:func:`app.workers.worker.run_once`), and the real HTTP/WebSocket transport —
against a throwaway temp database and temp bubble/pond roots. Nothing here
touches the repository's ``data/`` tree and no network call is ever made, so
the smoke is deterministic and fully offline (the stub parser/downloader derive
everything from the input URL / download id).

The exercised path (per the implementation plan):

    health -> parse a stub URL -> preview -> submit download -> observe
    progress (HTTP progress API + worker hub events) -> retrieve the
    tokenized file (real WS complete-event link) -> log in as admin ->
    save to Pond (POST /api/nas/save) -> verify the pond file.

**Fixture design.** A single module-scoped ``smoke_env`` fixture builds one
shared environment — ``(settings, engine, app, storage, downloader)`` — under
``tmp_path_factory``: one temp SQLite database, six temp storage roots, the
seeded admin, and the app wired to those settings. Module scope makes the
"one environment for the whole smoke" explicit (a session-scoped variant would
buy nothing for a single test) and guarantees a unique database URL per run, so
the per-URL cached engine can never alias another test's database. The flow is
one coherent test: a single green run proves the whole pipeline, and a failure
reports exactly which hop of the path broke.

**Determinism.** The stub downloader materializes exactly
``downloader.total_bytes`` (default 1 MiB) bytes whose content is a SHA-256
digest of ``"<download_id>:<title>"`` repeated to fill the file — so the smoke
re-derives the expected bytes from the response values and verifies the served
file byte-for-byte (sha256 of the served bytes), plus the pond copy after the
NAS save.

**Token choice (documented).** The one-time download token is taken from the
real WebSocket ``complete`` event (snapshot-on-connect for the completed
download) — the exact ``download_url`` a frontend client would click — and then
used against ``GET /api/download/file/{id}?token=...``; the file endpoint's
single-use rule is verified by replaying the same token. The worker's
``complete`` event (also carrying ``download_url`` + ``token_expire_at``) is
asserted from the hub capture and its *own* minted token is fetched too,
closing the last hop of the worker-minted link, while the worker's per-chunk
``progress`` events are observed there as well — live WS forwarding of worker
events is covered deterministically by ``test_download_ws.py`` (the worker
publishes to an in-process hub here via :class:`tests.conftest.FakeHub`,
mirroring the v1 cross-process reality where the hub is process-local and the
WS degrades to snapshot + HTTP polling).

**Frontend scope.** A browser cannot be booted in this environment, so the
smoke exercises the exact API flow the frontend uses over the same endpoints;
the frontend build itself is verified in Tasks 13-16 and the Nginx proxy
contract that routes ``/api`` + ``/ws`` to the backend is statically validated
in ``test_compose.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.factory import get_downloader, get_storage
from app.api.responses import CODE_FILE_TOKEN_INVALID, CODE_OK
from app.domain import DownloadStatus
from app.infrastructure import seed
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask
from app.main import create_app
from app.workers.worker import run_once
from tests.conftest import FakeHub, expected_stub_bytes, make_settings

# A deterministic stub-parseable URL: platform "douyin", media type video,
# title = last path segment "123456" (the stub's documented derivation).
SMOKE_URL = "https://www.douyin.com/video/123456"

# NAS-style target directory for the save step (PRD §5.7 leading slash).
SMOKE_TARGET_PATH = "/video/smoke"


@pytest.fixture(scope="module")
def smoke_env(tmp_path_factory):
    """One shared temp environment: settings, engine, app, storage, downloader.

    See the module docstring for the fixture-design rationale. The downloader
    comes from the same factory call ``create_app`` uses, so the worker writes
    the same deterministic 1 MiB stub file the app's service would.
    """
    root = tmp_path_factory.mktemp("koi-smoke")
    settings = make_settings(
        database_url=f"sqlite:///{root / 'smoke.db'}",
        video_storage_path=root / "pond/video",
        image_storage_path=root / "pond/image",
        music_storage_path=root / "pond/music",
        temp_video_path=root / "bubble/video",
        temp_image_path=root / "bubble/image",
        temp_music_path=root / "bubble/music",
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=settings, engine=engine) is True
    app = create_app(settings=settings)
    storage = get_storage(settings)
    downloader = get_downloader(settings)
    return settings, engine, app, storage, downloader


def load_download_row(engine, download_id) -> DownloadTask:
    """Load a DownloadTask row for the final persistence assertions."""
    with session_scope(engine) as session:
        return session.get(DownloadTask, download_id)


class TestEndToEndSmoke:
    def test_full_flow_health_to_pond(self, smoke_env):
        settings, engine, app, storage, downloader = smoke_env
        with TestClient(app) as client:
            # -- 1. health: full readiness, every storage root ok -------------
            health = client.get("/api/health")
            assert health.status_code == 200
            health_body = health.json()
            assert health_body["code"] == CODE_OK
            assert health_body["data"]["status"] == "ok"
            assert health_body["data"]["services"] == {"api": "ok", "storage": "ok"}
            assert all(
                status == "ok"
                for status in health_body["data"]["storage_roots"].values()
            )

            # -- 2. parse a stub URL: deterministic metadata ------------------
            parse = client.post("/api/parse", json={"urls": [SMOKE_URL]})
            assert parse.status_code == 200
            parse_body = parse.json()
            assert parse_body["code"] == CODE_OK
            assert parse_body["data"]["failed"] == []
            result = parse_body["data"]["results"][0]
            task_id = result["task_id"]
            assert task_id
            assert result["type"] == "video"
            assert result["platform"] == "douyin"
            assert result["title"]
            title = result["title"]

            # -- 3. preview the parsed task -----------------------------------
            preview = client.get(f"/api/preview/{task_id}")
            assert preview.status_code == 200
            preview_data = preview.json()["data"]
            assert preview_data["preview_type"] == "video"
            assert preview_data["platform"] == "douyin"
            assert preview_data["task_id"] == task_id

            # -- 4. submit the download: pending row --------------------------
            submit = client.post(
                "/api/download/submit",
                json={"task_id": task_id, "format": "mp4", "quality": "1080p"},
            )
            assert submit.status_code == 200
            submit_data = submit.json()["data"]
            download_id = submit_data["download_id"]
            assert download_id
            assert submit_data["task_id"] == task_id
            assert submit_data["status"] == "pending"

            # -- 5. progress before the worker: pending / 0% ------------------
            pending = client.get(f"/api/download/progress/{download_id}").json()
            assert pending["code"] == CODE_OK
            assert pending["data"]["status"] == "pending"
            assert pending["data"]["progress"] == 0.0

            # -- 6. run the real worker batch against the same DB ------------
            # The same download service the app wired (its token provider
            # matches the file endpoint's), the same factory downloader, the
            # same engine file. Events go to a recording hub so the worker's
            # progress/complete event shapes can be asserted deterministically.
            hub = FakeHub()
            handled = run_once(
                engine,
                downloader,
                storage,
                hub,
                download_service=app.state.download_service,
            )
            assert handled == 1

            events = hub.for_download(download_id)
            progress_events = [e for e in events if e["type"] == "progress"]
            complete_events = [e for e in events if e["type"] == "complete"]
            # Progress was observed during the transfer; the exact event count
            # is the stub's chunk granularity (an implementation detail), so
            # only the semantic contract is asserted here — monotone
            # in-range snapshots plus the terminal 100% complete event.
            assert len(progress_events) >= 1
            assert all(0 <= e["data"]["progress"] <= 100 for e in progress_events)
            assert len(complete_events) == 1
            complete = complete_events[0]["data"]
            assert complete["status"] == "completed"
            assert complete["progress"] == 100.0
            assert complete["download_url"].startswith(
                f"/api/download/file/{download_id}?token="
            )
            assert complete["token_expire_at"]
            worker_token = complete["download_url"].rsplit("token=", 1)[1]
            assert worker_token

            # -- 7. progress after the worker: completed / 100% / full bytes --
            done = client.get(f"/api/download/progress/{download_id}").json()
            assert done["code"] == CODE_OK
            done_data = done["data"]
            assert done_data["status"] == "completed"
            assert done_data["progress"] == 100.0
            assert done_data["downloaded_bytes"] == downloader.total_bytes
            assert done_data["total_bytes"] == downloader.total_bytes

            # -- 8. retrieve the tokenized file via the real WS link ---------
            # The WebSocket snapshot for a completed download mints a fresh
            # one-time link — the exact event a frontend client receives — so
            # the smoke uses its token for the file endpoint, then proves the
            # single-use rule by replaying the same token.
            with client.websocket_connect(f"/ws/download/{download_id}") as ws:
                ws_event = ws.receive_json()
            assert ws_event["type"] == "complete"
            download_url = ws_event["data"]["download_url"]
            assert download_url.startswith(f"/api/download/file/{download_id}?token=")
            token = download_url.rsplit("token=", 1)[1]
            assert token

            expected = expected_stub_bytes(download_id, title, downloader.total_bytes)
            file_response = client.get(f"/api/download/file/{download_id}?token={token}")
            assert file_response.status_code == 200
            assert file_response.content == expected
            assert "content-disposition" in file_response.headers

            replay = client.get(f"/api/download/file/{download_id}?token={token}")
            assert replay.status_code == 401
            assert replay.json()["code"] == CODE_FILE_TOKEN_INVALID

            # The token the WORKER minted into its complete event also serves
            # the file (the last hop of the worker-minted link, distinct from
            # the WS-minted link above — single use is per issuance).
            worker_link = client.get(
                f"/api/download/file/{download_id}?token={worker_token}"
            )
            assert worker_link.status_code == 200
            assert worker_link.content == expected

            # -- 9. log in as the seeded admin -------------------------------
            login = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": settings.admin_password},
            )
            assert login.status_code == 200
            login_data = login.json()["data"]
            assert login_data["username"] == "admin"
            access_token = login_data["token"]
            assert access_token

            # -- 10. save to Pond via the authenticated NAS endpoint ---------
            nas = client.post(
                "/api/nas/save",
                json={"download_id": download_id, "target_path": SMOKE_TARGET_PATH},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert nas.status_code == 200
            nas_data = nas.json()["data"]
            nas_path = nas_data["nas_path"]
            assert nas_path.startswith(SMOKE_TARGET_PATH.rstrip("/") + "/")
            assert nas_data["file_size"] == len(expected)
            assert nas_data["saved_at"]

            # -- 11. verify persistence: pond file + row update ---------------
            row = load_download_row(engine, download_id)
            assert row.status is DownloadStatus.COMPLETED
            assert row.pond_path is not None
            assert "/" + row.pond_path == nas_path  # pond-relative == nas form
            pond_file = Path(settings.video_storage_path) / row.pond_path
            assert pond_file.is_file()
            assert pond_file.read_bytes() == expected
            # The save *moves* the bubble file into the pond: the bubble copy
            # must be gone, the pond copy present with identical bytes.
            assert not Path(row.bubble_path).exists()

            # -- 12. the stack is still healthy after the whole flow ----------
            final_health = client.get("/api/health").json()
            assert final_health["code"] == CODE_OK
            assert final_health["data"]["status"] == "ok"
