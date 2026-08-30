"""Tests for the download WebSocket (Task 9): ``WS /ws/download/{download_id}``.

Contract (documented in ``app.api.download``):

* On connect the server sends one structured snapshot event — ``progress``
  for pending/downloading, ``complete`` for completed (with a short-lived
  ``download_url`` and ``token_expire_at``), ``error`` for failed/expired
  (uniform shape: ``code`` + ``message`` + state fields).
* Unknown download_id → an ``error`` event with ``code 3001`` then close.
* Malformed download_id → an ``error`` event with ``code 400`` then close.
* While connected, events published to the in-process event hub (the source
  Task 11's worker drives) are forwarded verbatim.
* A client that disconnects while the handler is idle releases its hub
  subscription server-side — asserted against a real uvicorn server, because
  the TestClient tears the app down with the connection and would mask an
  idle-subscription leak.
"""

import json
import socket
import threading
import time
import uuid

import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.sync.client import connect as websocket_connect

from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_EXPIRED,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_TASK_NOT_FOUND,
)
from app.application.download_events import DownloadEventHub
from app.domain import DownloadStatus, MediaType
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask, ParseTask
from app.main import create_app

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", **overrides)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'download-ws.db'}",
        video_storage_path=tmp_path / "pond/video",
        image_storage_path=tmp_path / "pond/image",
        music_storage_path=tmp_path / "pond/music",
        temp_video_path=tmp_path / "bubble/video",
        temp_image_path=tmp_path / "bubble/image",
        temp_music_path=tmp_path / "bubble/music",
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=settings, engine=engine) is True
    app = create_app(settings=settings)
    # A fresh hub per app keeps tests isolated from each other's subscriptions.
    app.state.download_event_hub = DownloadEventHub()
    return settings, engine, app


@pytest.fixture
def engine(env):
    return env[1]


@pytest.fixture
def app(env):
    return env[2]


@pytest.fixture
def client(env):
    # Enter the TestClient context so the anyio portal exists (needed to
    # publish hub events from the test thread via ``client.portal.call``).
    with TestClient(env[2]) as client:
        yield client


def seed_parse_task(engine, *, task_id=None) -> str:
    task_id = task_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url=VIDEO_URL,
                platform="bilibili",
                media_type=MediaType.VIDEO,
                title="示例视频",
                format="mp4",
                metadata_={},
            )
        )
    return task_id


def seed_download(engine, *, task_id, status=DownloadStatus.PENDING, **kwargs) -> str:
    download_id = kwargs.pop("download_id", None) or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            DownloadTask(
                download_id=download_id,
                task_id=task_id,
                title="示例视频",
                status=status,
                progress=100.0 if status is DownloadStatus.COMPLETED else 0.0,
                error_message=kwargs.pop("error_message", None),
                **kwargs,
            )
        )
    return download_id


EVENT_KEYS = {"download_id", "status", "progress", "speed", "downloaded_bytes",
              "total_bytes", "remaining_time"}


class TestSnapshotOnConnect:
    def test_pending_task_sends_progress_snapshot(self, client, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        with client.websocket_connect(f"/ws/download/{download_id}") as ws:
            event = ws.receive_json()
        assert set(event) == {"type", "data"}
        assert event["type"] == "progress"
        data = event["data"]
        assert set(data) == EVENT_KEYS
        assert data["download_id"] == download_id
        assert data["status"] == "pending"
        assert data["progress"] == 0.0

    def test_completed_task_sends_complete_event_with_download_url(
        self, client, engine
    ):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine, task_id=task_id, status=DownloadStatus.COMPLETED
        )
        with client.websocket_connect(f"/ws/download/{download_id}") as ws:
            event = ws.receive_json()
        assert event["type"] == "complete"
        data = event["data"]
        assert data["status"] == "completed"
        assert data["progress"] == 100.0
        url = data["download_url"]
        assert url.startswith(f"/api/download/file/{download_id}?token=")
        # The link's 5-minute validity window is surfaced explicitly.
        assert data["token_expire_at"]

    def test_failed_task_sends_error_event(self, client, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(
            engine,
            task_id=task_id,
            status=DownloadStatus.FAILED,
            error_message="download failed",
        )
        with client.websocket_connect(f"/ws/download/{download_id}") as ws:
            event = ws.receive_json()
        assert event["type"] == "error"
        data = event["data"]
        assert data["status"] == "failed"
        assert data["code"] == CODE_FILE_NOT_DOWNLOADED
        assert data["message"]
        assert data["error_message"] == "download failed"

    def test_expired_task_sends_error_event(self, client, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id, status=DownloadStatus.EXPIRED)
        with client.websocket_connect(f"/ws/download/{download_id}") as ws:
            event = ws.receive_json()
        assert event["type"] == "error"
        data = event["data"]
        assert data["status"] == "expired"
        assert data["code"] == CODE_FILE_EXPIRED
        assert data["message"]

    def test_unknown_download_sends_error_event_then_closes(self, client):
        with client.websocket_connect(f"/ws/download/{uuid.uuid4()}") as ws:
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["data"]["code"] == CODE_TASK_NOT_FOUND
            assert event["data"]["message"]
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

    def test_malformed_download_id_sends_error_event_then_closes(self, client):
        with client.websocket_connect("/ws/download/not-a-uuid") as ws:
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["data"]["code"] == CODE_BAD_REQUEST
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


class TestLiveEvents:
    def test_hub_events_are_forwarded_verbatim(self, client, app, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = app.state.download_event_hub
        event = {
            "type": "progress",
            "data": {
                "download_id": download_id,
                "status": "downloading",
                "progress": 42.0,
                "speed": 100.0,
                "downloaded_bytes": 42,
                "total_bytes": 100,
                "remaining_time": 0.58,
            },
        }
        with client.websocket_connect(f"/ws/download/{download_id}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "progress"
            client.portal.call(hub.publish, download_id, event)
            live = ws.receive_json()
        assert live == event

    def test_close_cleanly_releases_subscription(self, client, app, engine):
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = app.state.download_event_hub
        with client.websocket_connect(f"/ws/download/{download_id}") as ws:
            assert ws.receive_json()["type"] == "progress"
        # After the client closed, no subscribers may remain for this id. The
        # server notices the disconnect asynchronously, so poll briefly.
        deadline = time.monotonic() + 3
        while hub.subscriber_count(download_id) > 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert hub.subscriber_count(download_id) == 0


class TestDisconnectDetection:
    """Server-side disconnect detection under a REAL uvicorn server.

    The TestClient cancels the app task together with the connection, which
    would mask an idle-subscription leak (a client that connects to a
    terminal download, closes, and is never heard from again). With a real
    server the handler must notice the close by itself and release the hub
    subscription even though no further event is ever published.
    """

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_idle_disconnect_releases_subscription(self, env):
        settings, engine, app = env
        task_id = seed_parse_task(engine)
        download_id = seed_download(engine, task_id=task_id)
        hub = app.state.download_event_hub
        assert hub.subscriber_count(download_id) == 0

        port = self._free_port()
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error"
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started

            with websocket_connect(
                f"ws://127.0.0.1:{port}/ws/download/{download_id}"
            ) as ws:
                event = json.loads(ws.recv())
                assert event["type"] == "progress"
                assert hub.subscriber_count(download_id) == 1

            # The client closed while the handler was idle (nothing was ever
            # published): the handler must notice and unsubscribe on its own.
            deadline = time.monotonic() + 5
            while hub.subscriber_count(download_id) > 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert hub.subscriber_count(download_id) == 0
        finally:
            server.should_exit = True
            thread.join(timeout=10)
