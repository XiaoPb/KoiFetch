"""Tests for the music search service (Tasks 1, 3) and domain models (Task 1)."""

import pytest
from pydantic import ValidationError

from app.domain import (
    MusicAlbum,
    MusicArtist,
    MusicCategory,
    MusicPlaylist,
    MusicSearchParams,
    MusicSearchResult,
    MusicSong,
)


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
        songs=[song],
        artists=[artist],
        albums=[album],
        playlists=[playlist],
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
