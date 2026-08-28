"""Tests for the music search service (Tasks 1, 3) and domain models (Task 1)."""

import uuid

import pytest
from pydantic import ValidationError

from app.application.music_service import MusicService
from app.domain import (
    MusicAlbum,
    MusicArtist,
    MusicCategory,
    MusicPlaylist,
    MusicSearchParams,
    MusicSearchResult,
    MusicSong,
)
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import MusicSongRow, ParseTask


def make_song(title="晴天", artist="周杰伦", album="叶惠美", song_info=None):
    return MusicSong(
        id="pending",  # placeholder — rewritten to the persisted song_id
        title=title,
        artist=artist,
        album=album,
        cover="http://cover/1.jpg",
        duration="04:30",
        bitrate=320,
        ext="mp3",
        source="NeteaseMusicClient",
        song_info=song_info
        or {
            "source": "NeteaseMusicClient",
            "song_name": title,
            "singers": artist,
            "album": album,
            "ext": "mp3",
            "duration_s": 270,
            "protocol": "HTTP",
            "download_url": "http://cdn/1.mp3",
            "download_url_status": {"ok": True},
        },
    )


class FakeAdapter:
    """Deterministic in-memory adapter for service tests."""

    def __init__(self, songs):
        self._songs = songs
        self.calls = []

    def search(self, command: MusicSearchParams) -> MusicSearchResult:
        self.calls.append(command)
        return MusicSearchResult(
            totals={"all": len(self._songs), "song": len(self._songs), "artist": 0, "album": 0, "playlist": 0},
            songs=self._songs,
            artists=[],
            albums=[],
            playlists=[],
            hasMore=False,
        )


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'music.db'}")
    Base.metadata.create_all(engine)
    return engine


def test_music_domain_models_validate():
    song = MusicSong(id="s1", title="晴天", artist="周杰伦", duration="03:30")
    assert song.album == ""
    assert song.play_url is None
    assert song.song_info is None
    artist = MusicArtist(id="a1", name="周杰伦", songCount=2)
    assert artist.fans == 0
    album = MusicAlbum(id="al1", title="叶惠美", artist="周杰伦")
    playlist = MusicPlaylist(id="p1", title="热歌", creator="A")
    result = MusicSearchResult(
        totals={"all": 1, "song": 1, "artist": 1, "album": 1, "playlist": 0},
        songs=[song], artists=[artist], albums=[album], playlists=[playlist],
        hasMore=False,
    )
    assert result.totals["song"] == 1
    assert MusicCategory.ARTIST.value == "artist"
    assert MusicSearchParams(keyword="x", page=1).page == 1


def test_music_search_params_reject_blank_and_page_zero():
    with pytest.raises(ValidationError):
        MusicSearchParams(keyword="  ")
    with pytest.raises(ValidationError):
        MusicSearchParams(keyword="x", page=0)


def test_service_search_persists_songs_and_rewrites_ids(engine):
    service = MusicService(adapter=FakeAdapter([make_song()]), engine=engine)
    result = service.search("晴天", MusicCategory.SONG)
    assert result.songs[0].id != ""
    with session_scope(engine) as session:
        row = session.get(MusicSongRow, result.songs[0].id)
        assert row is not None
        assert row.song_name == "晴天"
        assert row.singers == "周杰伦"
        assert row.song_info["download_url"] == "http://cdn/1.mp3"


def test_service_search_is_idempotent_per_song_key(engine):
    service = MusicService(adapter=FakeAdapter([make_song()]), engine=engine)
    first = service.search("晴天", MusicCategory.SONG).songs[0]
    second = service.search("晴天", MusicCategory.SONG).songs[0]
    assert first.id == second.id
    with session_scope(engine) as session:
        assert len(list(session.query(MusicSongRow).all())) == 1


def test_service_search_builds_proxy_play_url(engine):
    service = MusicService(adapter=FakeAdapter([make_song()]), engine=engine)
    song = service.search("晴天", MusicCategory.SONG).songs[0]
    assert song.play_url.startswith("/api/music/stream?src=")


def test_service_search_derives_artists_albums_and_totals(engine):
    songs = [
        make_song(title="晴天", artist="周杰伦", album="叶惠美"),
        make_song(title="告白气球", artist="周杰伦", album="叶惠美"),
        make_song(title="光年之外", artist="邓紫棋", album="新的心跳"),
    ]
    service = MusicService(adapter=FakeAdapter(songs), engine=engine)
    result = service.search("周", MusicCategory.ALL)
    assert result.totals == {"all": 3, "song": 3, "artist": 2, "album": 2, "playlist": 0}
    assert [a.name for a in result.artists] == ["周杰伦", "邓紫棋"]
    assert result.artists[0].songCount == 2
    assert [a.title for a in result.albums] == ["叶惠美", "新的心跳"]
    assert result.playlists == []
    assert result.hasMore is False


def test_service_search_rejects_blank_keyword(engine):
    service = MusicService(adapter=FakeAdapter([]), engine=engine)
    from app.api.responses import ApiError

    with pytest.raises(ApiError) as exc:
        service.search("   ", MusicCategory.ALL)
    assert exc.value.code == 400


def test_service_import_creates_music_parse_task(engine):
    service = MusicService(adapter=FakeAdapter([make_song()]), engine=engine)
    song = service.search("晴天", MusicCategory.SONG).songs[0]
    task_id = service.import_song(song.id)
    assert task_id
    with session_scope(engine) as session:
        task = session.get(ParseTask, task_id)
        assert task is not None
        assert task.media_type.value == "music"
        assert task.title == "晴天"
        assert task.metadata_["song_info"]["download_url"] == "http://cdn/1.mp3"
        assert task.url.startswith("musicdl://")


def test_service_import_unknown_song_raises(engine):
    service = MusicService(adapter=FakeAdapter([]), engine=engine)
    from app.api.responses import ApiError

    with pytest.raises(ApiError) as exc:
        service.import_song(str(uuid.uuid4()))
    assert exc.value.code == 400
