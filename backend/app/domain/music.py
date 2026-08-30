"""Music search domain models (music feature, P0).

These pydantic models are the wire contract for the music search feature —
they mirror ``frontend/src/types/music.ts`` field-for-field (same names, same
shapes: ``totals`` per category, ``duration`` as ``"MM:SS"``, ``play_url``
nullable) so the frontend HTTP source is a passthrough with no mapping layer.

Conventions (same as ``app.domain.models``):

* **Strict models.** ``extra="forbid"`` so a field-name typo fails fast when
  crossing layers.
* **``song_info`` is server-internal.** :class:`MusicSong.song_info` carries
  the musicdl ``SongInfo``-compatible dict (the download pipeline's input).
  It is NEVER serialized to the client — the API schema
  (``app.api.music.SongData``) omits it and the router builds payloads
  manually, so platform URLs stay server-side.
* **``id`` is the persisted song id.** ``MusicSong.id`` equals the
  ``music_songs.song_id`` row (a UUID) assigned by
  :class:`app.application.music_service.MusicService`; the frontend uses it
  for playback/download identity.
* **Honest totals/pagination.** musicdl returns everything in one call and
  has no pagination, so ``hasMore`` is always ``False`` and ``page`` is
  accepted for contract compatibility only.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MusicAlbum",
    "MusicArtist",
    "MusicCategory",
    "MusicPlaylist",
    "MusicSearchParams",
    "MusicSearchResult",
    "MusicSong",
]

MUSIC_CATEGORIES: tuple[str, ...] = ("all", "song", "artist", "album", "playlist")


class MusicCategory(str, enum.Enum):
    """Search categories; values match the frontend's ``MusicCategory`` union."""

    ALL = "all"
    SONG = "song"
    ARTIST = "artist"
    ALBUM = "album"
    PLAYLIST = "playlist"


class MusicSong(BaseModel):
    """One search result song (frontend ``MusicSong`` wire shape + extras)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    artist: str = Field(min_length=1)
    album: str = Field(default="")
    cover: str | None = None
    duration: str | None = Field(default=None, min_length=1)  # "MM:SS"
    play_url: str | None = None  # same-origin proxy path (service-built)
    lyric: str | None = None  # raw LRC/plain lyrics from musicdl, never an upstream URL
    bitrate: int | None = None  # kbps; sorting proxy for 热度 (P2)
    ext: str | None = None
    source: str | None = None  # musicdl source client name
    # Server-internal musicdl SongInfo dict (persisted for the download
    # pipeline). Never serialized: the API schema omits it.
    song_info: dict[str, Any] | None = None


class MusicArtist(BaseModel):
    """An artist derived from the search results (song.singers)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    avatar: str | None = None
    fans: int = 0  # musicdl exposes no fan counts; 0 = unknown, UI hides
    songCount: int = 0


class MusicAlbum(BaseModel):
    """An album derived from the search results (song.album)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    artist: str = Field(min_length=1)
    cover: str | None = None
    songCount: int = 0


class MusicPlaylist(BaseModel):
    """A playlist entity. musicdl has no playlist search, so the backend
    always returns an empty list; the model exists for contract symmetry."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    creator: str = Field(min_length=1)
    cover: str | None = None
    songCount: int = 0


class MusicSearchParams(BaseModel):
    """One search command (validated at the service boundary)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    keyword: str = Field(min_length=1)
    category: MusicCategory = MusicCategory.ALL
    page: int = Field(default=1, ge=1)


class MusicSearchResult(BaseModel):
    """The search response payload (frontend ``MusicSearchResult`` shape)."""

    model_config = ConfigDict(extra="forbid")

    totals: dict[str, int]
    songs: list[MusicSong]
    artists: list[MusicArtist]
    albums: list[MusicAlbum]
    playlists: list[MusicPlaylist]
    hasMore: bool = False
