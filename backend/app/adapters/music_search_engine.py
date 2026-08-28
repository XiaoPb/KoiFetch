"""Real music search adapter: musicdl keyword search (engine mode).

Implements :class:`app.adapters.protocols.MusicSearchAdapter` against
`musicdl` (see ``backend/requirements.txt``): ``MusicClient.search`` fans the
keyword out to the configured source clients in parallel and returns
``{source: [SongInfo, ...]}``.

Behaviour contract:

* **One client per adapter.** ``MusicClient`` construction imports heavy
  modules (curl_cffi, pywidevine, ...) and builds every source client, so the
  adapter builds it once lazily (``client_factory`` is the test seam).
* **stdout is swallowed during search.** musicdl renders a rich progress bar
  to stdout on every search; a server must not spam logs, so the call is
  wrapped in ``contextlib.redirect_stdout``.
* **Dedupe by (song_name, singers) in configured source order.** The same
  track is commonly returned by several sources; the first occurrence wins
  (the sources list order is the priority).
* **Errors.** musicdl swallows per-source failures into empty lists and its
  own timeouts/403s surface as empty results, so the adapter never raises
  for a failed source. ``download_url`` may be absent from search results —
  songs are still returned (``play_url``/downloadability is the service's
  concern); musicdl sources that need a parse round-trip yield a song with a
  ``song_info`` whose ``download_url_status.ok`` is falsy.
* **Dependency.** Importing this module requires ``musicdl``; the factory
  imports it lazily in engine mode only (same rule as the other engines).
"""

from __future__ import annotations

import contextlib
import io
import uuid
from typing import Any, Callable

from musicdl import musicdl as _musicdl

from app.adapters.protocols import MusicSearchAdapter
from app.domain import (
    MusicSearchParams,
    MusicSearchResult,
    MusicSong,
    format_duration,
)

__all__ = ["MusicdlMusicSearchAdapter"]

_MESSAGE_BLANK_TITLE = "未知歌曲"
_MESSAGE_BLANK_ARTIST = "未知歌手"


class MusicdlMusicSearchAdapter:
    """musicdl-backed :class:`MusicSearchAdapter` (engine mode)."""

    def __init__(
        self,
        *,
        music_sources: list[str] | None = None,
        timeout_seconds: float = 15.0,
        size_per_source: int = 5,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._music_sources = list(music_sources or [
            "MiguMusicClient", "NeteaseMusicClient", "QQMusicClient",
            "KuwoMusicClient", "QianqianMusicClient",
        ])
        self._timeout = timeout_seconds
        self._size_per_source = size_per_source
        self._client_factory = client_factory
        self._client_cache: Any | None = None

    def _client(self) -> Any:
        if self._client_cache is None:
            if self._client_factory is not None:
                self._client_cache = self._client_factory()
            else:
                self._client_cache = _musicdl.MusicClient(
                    music_sources=self._music_sources,
                    init_music_clients_cfg={
                        source: {"search_size_per_source": self._size_per_source}
                        for source in self._music_sources
                    },
                    requests_overrides={
                        source: {"timeout": (self._timeout, self._timeout)}
                        for source in self._music_sources
                    },
                )
        return self._client_cache

    def search(self, command: MusicSearchParams) -> MusicSearchResult:
        with contextlib.redirect_stdout(io.StringIO()):
            per_source = self._client().search(command.keyword)
        songs: list[MusicSong] = []
        seen: set[tuple[str, str]] = set()
        for source in self._music_sources:
            for info in per_source.get(source, []):
                key = (info.song_name or "", info.singers or "")
                if key in seen:
                    continue
                seen.add(key)
                songs.append(self._to_song(info))
        return MusicSearchResult(
            totals={"all": len(songs), "song": len(songs), "artist": 0, "album": 0, "playlist": 0},
            songs=songs,
            artists=[],
            albums=[],
            playlists=[],
            hasMore=False,
        )

    @staticmethod
    def _to_song(info: Any) -> MusicSong:
        duration = (
            format_duration(info.duration_s)
            if isinstance(info.duration_s, int) and info.duration_s > 0
            else None
        )
        title = (info.song_name or "").strip() or _MESSAGE_BLANK_TITLE
        artist = (info.singers or "").strip() or _MESSAGE_BLANK_ARTIST
        return MusicSong(
            id=str(uuid.uuid4()),
            title=title,
            artist=artist,
            album=(info.album or "").strip(),
            cover=info.cover_url or None,
            duration=duration,
            play_url=None,  # the service builds the same-origin proxy path
            bitrate=info.bitrate if isinstance(info.bitrate, int) else None,
            ext=(info.ext or "").removeprefix(".") or None,
            source=info.source or None,
            song_info=info.todict(),
        )
