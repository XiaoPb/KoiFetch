"""Tests for the music HTTP API (Task 4): search / stream / import.

Uses the deterministic stub adapter (default ``music_search_engine``) with a
temp database, so the whole contract is exercised offline.
"""

import pytest
import httpx
import importlib
from fastapi.testclient import TestClient

from app.adapters.safe_upstream import SafeUpstreamClient
from app.api.music import get_music_service
from app.api.responses import CODE_OK
from app.application.music_service import MusicService
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import MusicSongRow
from app.main import create_app

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", **overrides)


@pytest.fixture
def env(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'music-api.db'}",
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
    return settings, engine, create_app(settings=settings)


@pytest.fixture
def client(env):
    return TestClient(env[2])


def test_search_returns_envelope_with_real_shape(client):
    response = client.get("/api/music/search", params={"keyword": "晴天", "category": "song"})
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == CODE_OK
    data = body["data"]
    assert set(data.keys()) == {"totals", "songs", "artists", "albums", "playlists", "hasMore"}
    assert data["hasMore"] is False
    assert data["totals"]["song"] == len(data["songs"]) > 0
    song = data["songs"][0]
    assert set(song.keys()) == {"id", "title", "artist", "album", "cover", "duration", "play_url", "bitrate"}
    # song_info must never leak to the client
    assert "song_info" not in song


def test_search_derived_artist_album_lists(client):
    response = client.get("/api/music/search", params={"keyword": "周杰伦", "category": "all"})
    data = response.json()["data"]
    assert len(data["artists"]) > 0
    assert len(data["albums"]) > 0
    assert data["totals"]["artist"] == len(data["artists"])
    assert data["totals"]["playlist"] == 0


def test_search_empty_for_long_keyword(client):
    response = client.get("/api/music/search", params={"keyword": "一二三四五六七八九十X"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["songs"] == []
    assert data["totals"]["all"] == 0


def test_search_rejects_missing_keyword(client):
    response = client.get("/api/music/search")
    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_search_rejects_invalid_category(client):
    response = client.get("/api/music/search", params={"keyword": "晴天", "category": "nope"})
    assert response.status_code == 400


def test_import_then_submit_round_trip(client):
    found = client.get("/api/music/search", params={"keyword": "晴天", "category": "song"}).json()["data"]
    song_id = found["songs"][0]["id"]
    imported = client.post("/api/music/import", json={"song_id": song_id})
    assert imported.status_code == 200
    task_id = imported.json()["data"]["task_id"]
    submitted = client.post("/api/download/submit", json={"task_id": task_id})
    assert submitted.status_code == 200
    assert submitted.json()["data"]["status"] == "pending"


def test_import_unknown_song_returns_400(client):
    response = client.post("/api/music/import", json={"song_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_legacy_src_stream_route_is_not_available(client):
    response = client.get("/api/music/stream", params={"src": "http://127.0.0.1/secret"})
    assert response.status_code == 404
    assert response.json()["code"] == 404


def test_song_stream_loads_persisted_url_and_forwards_range(env):
    settings, engine, app = env
    song_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with session_scope(engine) as session:
        session.add(
            MusicSongRow(
                song_id=song_id,
                song_key="a" * 32,
                source="NeteaseMusicClient",
                song_name="晴天",
                singers="周杰伦",
                song_info={"download_url": "https://cdn.example/song.mp3"},
            )
        )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "Mozilla/5.0 (KoiFetch/0.1)"
        assert request.headers["range"] == "bytes=0-2"
        return httpx.Response(
            206,
            headers={"content-type": "audio/mpeg", "content-range": "bytes 0-2/3"},
            content=b"abc",
        )

    upstream = SafeUpstreamClient(
        resolver=lambda host, port: ["93.184.216.34"],
        transport=httpx.MockTransport(handler),
    )
    app.dependency_overrides[get_music_service] = lambda: MusicService(
        engine=engine, upstream=upstream
    )
    response = TestClient(app).get(
        f"/api/music/{song_id}/stream", headers={"Range": "bytes=0-2"}
    )
    assert response.status_code == 206
    assert response.content == b"abc"


def test_song_stream_unknown_id_returns_api_error_without_fetch(env):
    settings, engine, app = env
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, content=b"should-not-fetch")

    upstream = SafeUpstreamClient(
        resolver=lambda host, port: ["93.184.216.34"],
        transport=httpx.MockTransport(handler),
    )
    app.dependency_overrides[get_music_service] = lambda: MusicService(
        engine=engine, upstream=upstream
    )
    response = TestClient(app).get("/api/music/not-a-song/stream")
    assert response.status_code == 400
    assert response.json()["code"] == 400
    assert calls == []


def test_song_stream_rejects_non_playable_persisted_row_without_fetch(env):
    settings, engine, app = env
    song_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    with session_scope(engine) as session:
        session.add(
            MusicSongRow(
                song_id=song_id,
                song_key="b" * 32,
                source="NeteaseMusicClient",
                song_name="playlist",
                singers="artist",
                song_info={
                    "protocol": "HLS",
                    "download_url": "https://cdn.example/playlist.m3u8",
                },
            )
        )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, content=b"must-not-fetch")

    upstream = SafeUpstreamClient(
        resolver=lambda host, port: ["93.184.216.34"],
        transport=httpx.MockTransport(handler),
    )
    app.dependency_overrides[get_music_service] = lambda: MusicService(
        engine=engine, upstream=upstream
    )
    response = TestClient(app).get(f"/api/music/{song_id}/stream")
    assert response.status_code == 400
    assert response.json()["code"] == 400
    assert calls == []


def test_create_app_shares_configured_upstream_client(env, monkeypatch):
    main_module = importlib.import_module("app.main")
    created = []

    class FakeSafeUpstreamClient:
        def __init__(self, **kwargs):
            created.append(self)
            self.kwargs = kwargs

    monkeypatch.setattr(main_module, "SafeUpstreamClient", FakeSafeUpstreamClient)
    settings, _, _ = env
    settings.engine_proxy = "http://proxy.example:8080"
    app = main_module.create_app(settings=settings)

    assert len(created) == 1
    assert created[0].kwargs["proxy"] == "http://proxy.example:8080"
    assert app.state.music_service._upstream is created[0]
    assert app.state.preview_service._upstream is created[0]


def test_hot_keywords_endpoint(client):
    response = client.get("/api/music/hot")
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["keywords"], list) and len(data["keywords"]) > 0
    assert "周杰伦" in data["keywords"]
