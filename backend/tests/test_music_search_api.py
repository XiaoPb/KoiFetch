"""Tests for the music HTTP API (Task 4): search / stream / import.

Uses the deterministic stub adapter (default ``music_search_engine``) with a
temp database, so the whole contract is exercised offline.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.responses import CODE_OK
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine
from app.main import create_app

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


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


def test_stream_proxies_an_upstream_url(client):
    # The upstream is fetched server-side; a malformed src is a 400 envelope,
    # proving the guard (a real fetch would need network — not exercised here).
    response = client.get("/api/music/stream", params={"src": "not-a-url"})
    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_hot_keywords_endpoint(client):
    response = client.get("/api/music/hot")
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["keywords"], list) and len(data["keywords"]) > 0
    assert "周杰伦" in data["keywords"]
