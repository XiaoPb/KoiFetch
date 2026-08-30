"""Music transport (P0): search, stream proxy, import.

This router is deliberately thin — the use cases live in
:class:`app.application.music_service.MusicService`. Three endpoints:

* ``GET /api/music/search?keyword=&category=&page=`` — the wire-shaped search
  result (``data: {totals, songs, artists, albums, playlists, hasMore}``).
  ``songs[].play_url`` is a same-origin proxy path (never a platform URL);
  ``song_info`` is intentionally absent from the schema. Blank keyword /
  invalid category / page < 1 → generic 400 envelope.
* ``GET /api/music/{song_id}/stream`` — same-origin byte proxy for playback;
  the persisted song row supplies the upstream URL (Range passthrough, engine
  UA; raw bytes like the preview stream endpoint).
* ``POST /api/music/import {song_id}`` — creates a MUSIC ParseTask from a
  persisted song; returns ``data: {task_id}``. Unknown song → generic 400.
  The frontend then submits the download through the existing pipeline.

DI hook (override in tests): :func:`get_music_service` (``app.state``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.responses import ok
from app.application.music_service import MusicService
from app.domain import MusicCategory

__all__ = [
    "get_music_service",
    "router",
]

router = APIRouter(prefix="/music", tags=["music"])

_MESSAGE_SEARCH_OK = "搜索成功 / Search successful"
_MESSAGE_IMPORT_OK = "导入成功 / Song imported"


class SongData(BaseModel):
    """Wire song shape — deliberately omits the server-internal ``song_info``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    artist: str
    album: str = ""
    cover: str | None = None
    duration: str | None = None
    play_url: str | None = None
    lyric: str | None = None
    bitrate: int | None = None


class ArtistData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    avatar: str | None = None
    fans: int = 0
    songCount: int = 0


class AlbumData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    artist: str
    cover: str | None = None
    songCount: int = 0


class PlaylistData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    creator: str
    cover: str | None = None
    songCount: int = 0


class MusicSearchData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totals: dict[str, int]
    songs: list[SongData]
    artists: list[ArtistData]
    albums: list[AlbumData]
    playlists: list[PlaylistData]
    hasMore: bool = False


class MusicSearchResponse(BaseModel):
    code: int
    message: str
    data: MusicSearchData | None = None


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_id: str = Field(min_length=1)


class ImportData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str


class ImportResponse(BaseModel):
    code: int
    message: str
    data: ImportData | None = None


class HotKeywordsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str]


class HotKeywordsResponse(BaseModel):
    code: int
    message: str
    data: HotKeywordsData | None = None


def get_music_service(request: Request) -> MusicService:
    """DI hook: the app-wired music service (override in tests)."""
    return request.app.state.music_service


def _serialize_search(result) -> dict:
    return {
        "totals": result.totals,
        "songs": [song.model_dump(exclude={"song_info", "source", "ext"}) for song in result.songs],
        "artists": [artist.model_dump() for artist in result.artists],
        "albums": [album.model_dump() for album in result.albums],
        "playlists": [playlist.model_dump() for playlist in result.playlists],
        "hasMore": result.hasMore,
    }


@router.get("/search", response_model=MusicSearchResponse)
def music_search(
    service: Annotated[MusicService, Depends(get_music_service)],
    keyword: str = Query(min_length=1),
    category: MusicCategory = MusicCategory.ALL,
    page: int = Query(default=1, ge=1),
) -> dict:
    """Search one category; returns the wire-shaped result (see module docstring)."""
    result = service.search(keyword, category, page)
    return ok(data=_serialize_search(result), message=_MESSAGE_SEARCH_OK)


@router.get("/{song_id}/stream")
def music_stream(
    song_id: str,
    request: Request,
    service: Annotated[MusicService, Depends(get_music_service)],
) -> StreamingResponse:
    """Same-origin byte proxy for persisted music playback."""
    stream = service.stream_song(song_id, request.headers.get("range"))

    def iterator():
        try:
            yield from stream.chunks
        finally:
            stream.close()

    return StreamingResponse(
        iterator(),
        status_code=stream.status_code,
        headers=stream.headers,
        media_type=stream.content_type,
    )


@router.post("/import", response_model=ImportResponse)
def music_import(
    body: ImportRequest,
    service: Annotated[MusicService, Depends(get_music_service)],
) -> dict:
    """Create a MUSIC ParseTask for a persisted song (download closure)."""
    task_id = service.import_song(body.song_id)
    return ok(data={"task_id": task_id}, message=_MESSAGE_IMPORT_OK)


@router.get("/hot", response_model=HotKeywordsResponse)
def music_hot(request: Request) -> dict:
    """Return the configured hot-search keywords (Settings.hot_keywords)."""
    keywords = list(request.app.state.settings.hot_keywords)
    return ok(data={"keywords": keywords}, message="ok")
