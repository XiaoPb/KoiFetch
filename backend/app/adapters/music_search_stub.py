"""Deterministic offline music search stub (default ``music_search_engine``).

Mirrors the frontend mock's contract so stub mode is demoable and API tests
run without network: data derives from the keyword via a hash-seeded PRNG,
keywords longer than 10 characters return no results (deterministic empty
state), and every song carries a minimal ``song_info`` dict the download
pipeline can persist. ``play_url`` is left ``None`` (the service builds proxy
paths only for playable song_info; the mini player then simulates).
"""

from __future__ import annotations

import hashlib
import uuid

from app.adapters.protocols import MusicSearchAdapter
from app.domain import (
    MusicSearchParams,
    MusicSearchResult,
    MusicSong,
)

__all__ = ["MusicSearchStubAdapter"]

SONG_TITLES = ["晴天", "七里香", "稻香", "夜曲", "告白气球", "光年之外", "泡沫"]
ALBUM_WORDS = ["精选", "合集", "现场", "翻唱", "经典", "原声"]


def _hash_seed(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def _rand(seed: int):
    """mulberry32-style deterministic PRNG (same idea as the frontend mock)."""
    a = seed
    while True:
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + ((t ^ (t >> 7)) * (t | 61))) & 0xFFFFFFFF
        yield ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


def _format_duration(total_seconds: int) -> str:
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


class MusicSearchStubAdapter:
    """Keyword-derived deterministic :class:`MusicSearchAdapter`."""

    def __init__(self, count: int = 20) -> None:
        self._count = count

    def search(self, command: MusicSearchParams) -> MusicSearchResult:
        keyword = command.keyword.strip()
        if len(keyword) > 10:
            return MusicSearchResult(
                totals={"all": 0, "song": 0, "artist": 0, "album": 0, "playlist": 0},
                songs=[], artists=[], albums=[], playlists=[], hasMore=False,
            )
        rng = _rand(_hash_seed(keyword))
        songs = []
        for index in range(self._count):
            title = SONG_TITLES[index % len(SONG_TITLES)]
            if index > 0:
                title = f"{title} {index + 1}"
            artist = keyword if index == 0 else f"{keyword} {index + 1}"
            album = f"{keyword}{ALBUM_WORDS[index % len(ALBUM_WORDS)]}"
            duration_s = 120 + int(next(rng) * 240)
            songs.append(
                MusicSong(
                    id=str(uuid.uuid4()),
                    title=title,
                    artist=artist,
                    album=album,
                    cover=None,
                    duration=_format_duration(duration_s),
                    play_url=None,
                    bitrate=128 + int(next(rng) * 192),
                    ext="mp3",
                    source="stub",
                    song_info={
                        "source": "stub",
                        "song_name": title,
                        "singers": artist,
                        "album": album,
                        "ext": "mp3",
                        "duration_s": duration_s,
                        "protocol": "HTTP",
                        "download_url": None,
                        "download_url_status": {},
                    },
                )
            )
        return MusicSearchResult(
            totals={"all": len(songs), "song": len(songs), "artist": 0, "album": 0, "playlist": 0},
            songs=songs, artists=[], albums=[], playlists=[], hasMore=False,
        )
