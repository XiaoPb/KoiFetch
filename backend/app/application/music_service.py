"""Music use cases (P0): search persistence, import, same-origin stream proxy.

Sits in the application layer between the API transport (``app.api.music``)
and the music-search adapter / persistence:

* **search** validates the keyword, delegates to the
  :class:`~app.adapters.protocols.MusicSearchAdapter`, persists every returned
  song into ``music_songs`` (idempotent content-hash upsert that rewrites
  ``song.id`` to the stable row id), derives artists/albums from the songs,
  builds the same-origin ``play_url`` proxy path for playable songs, and
  returns the wire-shaped :class:`~app.domain.music.MusicSearchResult`.
* **import_song** turns a persisted song into a MUSIC :class:`ParseTask` whose
  ``metadata_`` carries ``{"engine": "musicdl", "song_info": <SongInfo dict>}``
  — the exact contract the engine downloader's music branch consumes
  (``app.adapters.downloader_engine``), so the existing submit/progress/
  download-center/NAS pipeline works for music unchanged.
* **stream_song** loads a persisted song and proxies its upstream media URL
  server-side (``Range`` passthrough), so callers cannot choose an arbitrary
  upstream URL.

Design decisions (stable contract for Tasks 4-16):

* **Honest totals.** musicdl has no pagination: ``totals.song`` is the
  deduped song count, ``totals.artist``/``totals.album`` are derived counts,
  ``totals.playlist`` is always 0 (no playlist search), ``hasMore`` is always
  ``False`` and ``page`` is contract-only.
* **Stable ids via content hash.** ``song_key = md5(source|title|artist|
  album)``; the unique constraint makes re-searches idempotent and P2
  playlists/queue can reference ``song.id`` safely.
* **play_url = best-effort proxy path.** Only plain-HTTP songs with a string
  ``download_url`` get one (HLS cannot be proxied — segment rewriting is out
  of scope, matching the preview proxy); the player surfaces failures.
* **Import is a fresh task per call.** No dedup across imports (a user may
  legitimately import twice); the download service's 3002/3003 rules apply
  per task as usual.
* **DI over globals.** The constructor takes optional adapter/upstream/engine
  dependencies; ``create_app`` wires one shared safe upstream client into the
  music and preview services, while tests can inject deterministic adapters.
"""

from __future__ import annotations

import hashlib
import uuid
from urllib.parse import quote

from sqlalchemy import Engine, select
from starlette.status import HTTP_400_BAD_REQUEST

from app.adapters.factory import get_music_search
from app.adapters.protocols import MusicSearchAdapter
from app.adapters.safe_upstream import (
    SafeUpstreamClient,
    UnsafeUpstreamUrl,
    UpstreamProtocolError,
    UpstreamTooLarge,
    UpstreamStream,
)
from app.api.responses import CODE_BAD_REQUEST, ApiError
from app.domain import MediaType, MusicAlbum, MusicArtist, MusicCategory, MusicSearchParams, MusicSearchResult, MusicSong
from app.infrastructure.database import session_scope
from app.infrastructure.models import MusicSongRow, ParseTask

__all__ = ["MusicService"]

_MESSAGE_KEYWORD_EMPTY = "搜索关键词为空 / Search keyword is empty"
_MESSAGE_SONG_NOT_FOUND = "歌曲不存在 / Song not found"
_MESSAGE_INVALID_STREAM_URL = "播放地址无效 / Invalid stream URL"
_MESSAGE_UPSTREAM = "上游媒体获取失败 / Upstream media fetch failed"


class MusicService:
    """Search/import/stream use cases for the music feature."""

    def __init__(
        self,
        adapter: MusicSearchAdapter | None = None,
        *,
        engine: Engine | None = None,
        upstream: SafeUpstreamClient | None = None,
    ) -> None:
        self._adapter = adapter if adapter is not None else get_music_search()
        self._engine = engine
        self._upstream = upstream or SafeUpstreamClient()

    # -- search ----------------------------------------------------------

    def search(
        self,
        keyword: str,
        category: MusicCategory,
        page: int = 1,
    ) -> MusicSearchResult:
        """Search, persist songs, and shape the wire result."""
        keyword = (keyword or "").strip()
        if not keyword:
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_KEYWORD_EMPTY)
        raw = self._adapter.search(
            MusicSearchParams(keyword=keyword, category=category, page=page)
        )
        songs = [self._persist_song(song) for song in raw.songs]
        artists = _derive_artists(songs)
        albums = _derive_albums(songs)
        totals = {
            "all": len(songs),
            "song": len(songs),
            "artist": len(artists),
            "album": len(albums),
            "playlist": 0,
        }
        return MusicSearchResult(
            totals=totals,
            songs=songs,
            artists=artists,
            albums=albums,
            playlists=[],
            hasMore=False,
        )

    def _persist_song(self, song: MusicSong) -> MusicSong:
        """Upsert the song by content hash; return it with the stable row id
        and the proxy ``play_url``."""
        song_info = song.song_info or {}
        song_key = hashlib.md5(
            "|".join(
                [str(song_info.get("source") or ""), song.title, song.artist, song.album]
            ).encode("utf-8")
        ).hexdigest()
        with session_scope(self._engine) as session:
            row = session.scalar(select(MusicSongRow).where(MusicSongRow.song_key == song_key))
            if row is None:
                row = MusicSongRow(
                    song_key=song_key,
                    source=str(song_info.get("source") or ""),
                    song_name=song.title,
                    singers=song.artist,
                    album=song.album or None,
                    cover_url=song.cover,
                    duration_s=_parse_duration_seconds(song.duration),
                    ext=song.ext,
                    song_info=song_info,
                )
                session.add(row)
                session.flush()
            else:
                # Freshest metadata wins; safe — the download pipeline reads
                # song_info from the task row, never from this table.
                row.song_name = song.title
                row.singers = song.artist
                row.album = song.album or None
                row.cover_url = song.cover
                row.duration_s = _parse_duration_seconds(song.duration)
                row.ext = song.ext
                row.song_info = song_info
            song_id = row.song_id
        return song.model_copy(
            update={"id": song_id, "play_url": _play_proxy_url(song_id, song_info)}
        )

    # -- import (download closure) ---------------------------------------

    def import_song(self, song_id: str) -> str:
        """Create a MUSIC ParseTask for a persisted song; return its task_id.

        The task's ``metadata_`` carries the musicdl ``song_info`` dict the
        engine downloader consumes; the frontend then submits the download
        through the existing pipeline (``downloadsStore.submit``).
        """
        with session_scope(self._engine) as session:
            row = session.get(MusicSongRow, song_id)
            if row is None:
                raise ApiError(HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_SONG_NOT_FOUND)
            task = ParseTask(
                task_id=str(uuid.uuid4()),
                url=f"musicdl://{row.source}/{song_id}",
                platform=row.source or "music",
                media_type=MediaType.MUSIC,
                title=row.song_name,
                cover_url=row.cover_url,
                duration=row.duration_s,
                format=row.ext,
                metadata_={"engine": "musicdl", "song_info": row.song_info},
            )
            session.add(task)
            session.flush()
            return task.task_id

    # -- stream proxy ----------------------------------------------------

    def stream_song(self, song_id: str, range_header: str | None) -> UpstreamStream:
        """Proxy the persisted playable URL for ``song_id``.

        The URL is always loaded from the database row. A caller-provided URL
        is not accepted anywhere in this use case.
        """
        with session_scope(self._engine) as session:
            row = session.get(MusicSongRow, song_id)
            if row is None:
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_SONG_NOT_FOUND
                )
            song_info = row.song_info or {}
            url = song_info.get("download_url")
        if _playable_url(song_info) is None:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_STREAM_URL
            )
        try:
            stream = self._upstream.stream(url, range_header=range_header)
            if stream.status_code >= 400:
                stream.close()
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
                )
            return stream
        except ApiError:
            raise
        except (UnsafeUpstreamUrl, UpstreamTooLarge, UpstreamProtocolError) as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc
        except Exception as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc


def _play_proxy_url(song_id: str, song_info: dict) -> str | None:
    """Same-origin proxy path for a playable song, else ``None``.

    Playable = plain-HTTP protocol with a string http(s) ``download_url``.
    ``download_url_status.ok`` is deliberately NOT required here (some musicdl
    sources leave it empty on search); the player surfaces real failures.
    """
    if _playable_url(song_info) is None:
        return None
    return f"/api/music/{quote(song_id, safe='')}/stream"


def _playable_url(song_info: dict) -> str | None:
    """Return a persisted HTTP playable URL, otherwise ``None``."""
    protocol = str(song_info.get("protocol") or "HTTP").upper()
    url = song_info.get("download_url")
    if protocol != "HTTP" or not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    return url


def _parse_duration_seconds(duration: str | None) -> int | None:
    """Best-effort ``"MM:SS"`` → seconds (None when malformed)."""
    if not duration or ":" not in duration:
        return None
    parts = duration.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _derive_artists(songs: list[MusicSong]) -> list[MusicArtist]:
    """Distinct artists from songs (song.artist), source order, with counts."""
    by_name: dict[str, list[MusicSong]] = {}
    for song in songs:
        by_name.setdefault(song.artist, []).append(song)
    return [
        MusicArtist(
            id=f"artist:{_stable_id(name)}",
            name=name,
            avatar=songs[0].cover,
            fans=0,
            songCount=len(group),
        )
        for name, group in by_name.items()
    ]


def _derive_albums(songs: list[MusicSong]) -> list[MusicAlbum]:
    """Distinct albums from songs (song.album), skipping blanks."""
    by_title: dict[str, list[MusicSong]] = {}
    for song in songs:
        if not song.album:
            continue
        by_title.setdefault(song.album, []).append(song)
    return [
        MusicAlbum(
            id=f"album:{_stable_id(title)}",
            title=title,
            artist=group[0].artist,
            cover=group[0].cover,
            songCount=len(group),
        )
        for title, group in by_title.items()
    ]


def _stable_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
