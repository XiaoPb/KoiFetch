"""Tests for the musicdl-backed music search adapter (engine mode).

The adapter never touches the network in tests: a fake musicdl client (a
callable with a ``search`` method) is injected via ``client_factory``, so the
mapping/dedupe/error-contract logic is exercised offline.
"""

from __future__ import annotations

from app.adapters.music_search_engine import MusicdlMusicSearchAdapter
from app.domain import MusicCategory, MusicSearchParams, MusicSearchResult


class FakeSongInfo:
    """Minimal duck-type of musicdl's SongInfo with the fields we read."""

    def __init__(self, **kwargs):
        self.song_name = kwargs.get("song_name")
        self.singers = kwargs.get("singers")
        self.album = kwargs.get("album")
        self.cover_url = kwargs.get("cover_url")
        self.duration_s = kwargs.get("duration_s")
        self.bitrate = kwargs.get("bitrate")
        self.ext = kwargs.get("ext")
        self.source = kwargs.get("source")
        self.protocol = kwargs.get("protocol", "HTTP")
        self.download_url = kwargs.get("download_url")
        self.download_url_status = kwargs.get("download_url_status") or {}
        self.default_download_headers = {}

    def todict(self):
        return {k: v for k, v in self.__dict__.items()}


class FakeMusicClient:
    """Fake musicdl.MusicClient: returns per-source SongInfo lists."""

    def __init__(self, per_source):
        self._per_source = per_source
        self.searched = []

    def search(self, keyword):
        self.searched.append(keyword)
        return self._per_source


def make_adapter(per_source):
    client = FakeMusicClient(per_source)
    adapter = MusicdlMusicSearchAdapter(
        music_sources=["NeteaseMusicClient", "QQMusicClient"],
        client_factory=lambda: client,
    )
    return adapter, client


SONG_A = dict(
    song_name="晴天", singers="周杰伦", album="叶惠美", cover_url="http://c/1.jpg",
    duration_s=270, bitrate=320, ext="mp3", source="NeteaseMusicClient",
    download_url="http://m1/1.mp3", download_url_status={"ok": True},
)
SONG_B = dict(
    song_name="晴天", singers="周杰伦", album="叶惠美", source="QQMusicClient",
    download_url="http://m2/1.mp3", download_url_status={"ok": True},
)


def test_search_maps_songinfo_fields():
    adapter, client = make_adapter({"NeteaseMusicClient": [FakeSongInfo(**SONG_A)]})
    result = adapter.search(MusicSearchParams(keyword="晴天"))
    assert isinstance(result, MusicSearchResult)
    assert client.searched == ["晴天"]
    assert len(result.songs) == 1
    song = result.songs[0]
    assert song.title == "晴天"
    assert song.artist == "周杰伦"
    assert song.album == "叶惠美"
    assert song.cover == "http://c/1.jpg"
    assert song.duration == "04:30"
    assert song.bitrate == 320
    assert song.source == "NeteaseMusicClient"
    assert song.play_url is None  # the service builds the proxy path
    assert song.song_info["download_url"] == "http://m1/1.mp3"


def test_search_dedupes_identical_songs_keeping_first_source_order():
    adapter, _ = make_adapter(
        {
            "NeteaseMusicClient": [FakeSongInfo(**SONG_A)],
            "QQMusicClient": [FakeSongInfo(**SONG_B)],
        }
    )
    result = adapter.search(MusicSearchParams(keyword="晴天"))
    assert len(result.songs) == 1
    assert result.songs[0].source == "NeteaseMusicClient"


def test_search_swallows_missing_sources_and_blank_names():
    adapter, _ = make_adapter({"NeteaseMusicClient": [FakeSongInfo(**SONG_A)]})
    result = adapter.search(MusicSearchParams(keyword="晴天"))
    assert len(result.songs) == 1


def test_search_falls_back_for_blank_title():
    adapter, _ = make_adapter(
        {"NeteaseMusicClient": [FakeSongInfo(song_name="", singers="", source="NeteaseMusicClient")]}
    )
    result = adapter.search(MusicSearchParams(keyword="晴天"))
    assert result.songs[0].title == "未知歌曲"
    assert result.songs[0].artist == "未知歌手"
