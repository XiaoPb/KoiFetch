"""Stub parser adapter: deterministic, offline parsing for any URL.

Implements :class:`app.adapters.protocols.ParserAdapter`. No network access is
ever performed — everything is derived from the URL itself, so the complete
parse → download → store workflow can be exercised before real platform
engines exist. Real engines (yt-dlp wrappers etc.) can be added later behind
the same protocol.

Determinism contract:

* All fields except ``task_id`` are pure functions of the input URL:
  parsing the same URL twice yields identical title/media type/platform/
  duration/size/options.
* ``task_id`` is a fresh ``uuid4`` per result — it is the persistence
  identity of the task row (Task 8 creates one row per result), so it must
  NOT be derived from the URL or re-parses would collide on the primary key.

Derivation rules (documented so later engines can mimic or deviate):

* **media type** — checked in order: a recognized file extension in the last
  path segment (``.mp4``/``.mov``/``.mkv``/``.webm``/``.avi`` → video,
  ``.mp3``/``.flac``/``.wav``/``.m4a``/``.ogg``/``.aac`` → music,
  ``.jpg``/``.jpeg``/``.png``/``.gif``/``.webp``/``.bmp`` → image); then a
  URL keyword in any path segment (``video``/``watch``/… → video,
  ``music``/``audio``/… → music, ``photo``/``image``/… → image); finally the
  documented default **video**.
* **platform** — a small mapping of known hosts to canonical names
  (``www.bilibili.com`` → ``bilibili``, ``v.douyin.com`` → ``douyin``, …);
  unknown hosts fall back to the hostname without a leading ``www.``.
* **title** — the last path segment, percent-decoded, its recognized
  extension stripped, separator runs (``-``/``_``/``.``) collapsed to spaces;
  falls back to the hostname when the segment is empty. CJK is preserved.
* **duration / file_size_mb** — stable pseudo-random values in plausible
  ranges (1-10 min, 1-500 MB) derived from a SHA-256 digest of the URL.
* **format / qualities / bitrates** — fixed per media type
  (video: ``mp4`` + 1080p/720p/480p; music: ``mp3`` + 320kbps/FLAC;
  image: ``jpg``, no quality ladder).

Language neutrality: stub titles are whatever the URL contains — no invented
Chinese or English words, so the stub never imposes a language on the product.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from urllib.parse import unquote, urlsplit

from app.adapters.protocols import ParserAdapter
from app.domain import MediaType, ParseCommand, ParseResult
from app.domain.formats import format_duration

__all__ = ["StubParserAdapter"]

# Extension → media type (checked before keywords, on the last path segment).
_VIDEO_EXTS = frozenset({"mp4", "mov", "mkv", "webm", "avi", "m4v"})
_MUSIC_EXTS = frozenset({"mp3", "flac", "wav", "m4a", "ogg", "aac"})
_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp"})
_EXT_TO_TYPE = {
    **{ext: MediaType.VIDEO for ext in _VIDEO_EXTS},
    **{ext: MediaType.MUSIC for ext in _MUSIC_EXTS},
    **{ext: MediaType.IMAGE for ext in _IMAGE_EXTS},
}

# URL keyword → media type (checked on any path segment, after extensions).
_KEYWORD_TO_TYPE = {
    **{word: MediaType.VIDEO for word in ("video", "videos", "watch", "clip", "clips")},
    **{word: MediaType.MUSIC for word in ("music", "audio", "song", "songs", "track", "album")},
    **{word: MediaType.IMAGE for word in ("image", "images", "photo", "photos", "picture", "pic", "pics")},
}

# Known hosts → canonical platform name (PRD vocabulary); fallback keeps the
# hostname without a leading "www.".
_PLATFORM_MAP = {
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "bilibili.com": "bilibili",
    "www.bilibili.com": "bilibili",
    "douyin.com": "douyin",
    "www.douyin.com": "douyin",
    "v.douyin.com": "douyin",
    "xiaohongshu.com": "xiaohongshu",
    "www.xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
    "weibo.com": "weibo",
    "www.weibo.com": "weibo",
    "ixigua.com": "ixigua",
    "www.ixigua.com": "ixigua",
    "kuaishou.com": "kuaishou",
    "www.kuaishou.com": "kuaishou",
}

_SEPARATOR_RUN = re.compile(r"[-_.]+")
_WHITESPACE_RUN = re.compile(r"\s+")

# Fixed per-type option ladders (deterministic by design).
_FORMAT_BY_TYPE = {
    MediaType.VIDEO: "mp4",
    MediaType.MUSIC: "mp3",
    MediaType.IMAGE: "jpg",
}
_QUALITIES_BY_TYPE = {
    MediaType.VIDEO: ["1080p", "720p", "480p"],
    MediaType.MUSIC: [],
    MediaType.IMAGE: [],
}
_BITRATES_BY_TYPE = {
    MediaType.VIDEO: [],
    MediaType.MUSIC: ["320kbps", "FLAC"],
    MediaType.IMAGE: [],
}


class StubParserAdapter:
    """Deterministic, offline :class:`ParserAdapter` implementation."""

    def parse(self, command: ParseCommand) -> list[ParseResult]:
        return [self._parse_url(url) for url in command.urls]

    # -- internals ---------------------------------------------------------

    def _parse_url(self, url: str) -> ParseResult:
        parts = urlsplit(url)
        path_segments = [seg for seg in parts.path.split("/") if seg]
        seed = int.from_bytes(hashlib.sha256(url.encode("utf-8")).digest(), "big")

        media_type = self._detect_media_type(path_segments)
        platform = self._detect_platform(parts.hostname or "")
        title = self._derive_title(path_segments, platform)
        duration = format_duration(60 + (seed % 540))  # 1-10 minutes
        file_size_mb = round(1.0 + ((seed >> 8) % 4990) / 10.0, 1)  # 1.0-500.0

        return ParseResult(
            task_id=str(uuid.uuid4()),
            url=url,
            media_type=media_type,
            platform=platform,
            title=title,
            cover=None,  # the stub never fabricates cover URLs (no network)
            duration=duration,
            file_size_mb=file_size_mb,
            format=_FORMAT_BY_TYPE[media_type],
            available_qualities=_QUALITIES_BY_TYPE[media_type],
            available_bitrates=_BITRATES_BY_TYPE[media_type],
            metadata={"stub": True},
            error=None,
        )

    @staticmethod
    def _detect_media_type(path_segments: list[str]) -> MediaType:
        if path_segments:
            last = path_segments[-1].lower()
            dot = last.rfind(".")
            if dot != -1:
                ext = last[dot + 1 :]
                if ext in _EXT_TO_TYPE:
                    return _EXT_TO_TYPE[ext]
        for segment in path_segments:
            if segment.lower() in _KEYWORD_TO_TYPE:
                return _KEYWORD_TO_TYPE[segment.lower()]
        return MediaType.VIDEO  # documented default

    @staticmethod
    def _detect_platform(hostname: str) -> str:
        host = hostname.lower()
        if host in _PLATFORM_MAP:
            return _PLATFORM_MAP[host]
        return host.removeprefix("www.") or "unknown"

    @staticmethod
    def _derive_title(path_segments: list[str], platform: str) -> str:
        if path_segments:
            segment = unquote(path_segments[-1])
            # A bare directory keyword (e.g. "/videos/") carries no title;
            # fall back to the platform name instead of "videos".
            if segment.lower() in _KEYWORD_TO_TYPE and len(path_segments) == 1:
                return platform
            dot = segment.rfind(".")
            if dot != -1 and segment[dot + 1 :].lower() in _EXT_TO_TYPE:
                segment = segment[:dot]
            title = _SEPARATOR_RUN.sub(" ", segment).strip()
            title = _WHITESPACE_RUN.sub(" ", title).strip()
            if title:
                return title
        return platform  # empty segment → hostname/platform
