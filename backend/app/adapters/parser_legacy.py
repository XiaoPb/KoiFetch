"""Legacy parser adapter: parse-video-py fallback for platforms f2 does not cover.

Implements :class:`app.adapters.protocols.ParserAdapter` against the
`parse-video-py` engine (Git install — see backend/requirements.txt). This is
the pre-f2 engine, kept as a fallback for kuaishou/bilibili/xiaohongshu/xigua/
... while :class:`app.adapters.parser_f2.F2ParserAdapter` is the primary
engine for douyin/weibo/tiktok. Remove this module (and the parse-video-py
dependency) once f2 covers those platforms and ``PARSER_LEGACY_FALLBACK=false``.

Behaviour (unchanged from the pre-f2 era):

* Video share URLs resolve through ``parse_video_share_url`` into a
  :class:`parse_video_py.VideoInfo` with real title, cover, video URL, author
  and (for albums) images. No duration, no quality ladder (documented engine
  gaps). 图集/动图 with ``images`` and no ``video_url`` classify as IMAGE with
  the first image's extension; the album lives in ``metadata["images"]``.
* The size hint is a best-effort Content-Length probe (never fatal).
* Errors are typed through :mod:`app.adapters.engine_errors`; the original
  exception is preserved as ``__cause__``.
* ``transport`` is a test seam (None = real network).

Platform routing is the facade's job (``app.adapters.parser_engine._route``);
this adapter keeps its own ``LEGACY_ROUTES`` table (derived from the engine's
own mapping) as a defensive self-check.
"""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlsplit

import httpx

from app.adapters.engine_errors import (
    EngineError,
    UnsupportedPlatformError,
    translate_engine_exception,
)
from app.adapters.engine_utils import extension_of, probe_file_size_mb
from app.adapters.protocols import ParserAdapter
from app.domain import MediaType, ParseCommand, ParseResult
from parse_video_py import parse_video_share_url
from parse_video_py.parser import video_source_info_mapping

__all__ = ["LegacyParserAdapter", "LEGACY_ROUTES", "_route"]

_MESSAGE_UNSUPPORTED_PLATFORM = "平台不支持 / Unsupported platform"

# parse-video-py's VideoSource.value -> KoiFetch canonical platform name.
_CANONICAL_BY_ENGINE_SOURCE = {
    "douyin": "douyin", "kuaishou": "kuaishou", "bilibili": "bilibili",
    "weibo": "weibo", "xigua": "xigua", "redbook": "xiaohongshu",
    "twitter": "twitter", "qqvideo": "qqvideo", "sohu": "sohu",
    "cctv": "cctv", "acfun": "acfun", "huya": "huya", "weishi": "weishi",
    "pipixia": "pipixia", "pipigaoxiao": "pipigaoxiao", "zuiyou": "zuiyou",
    "quanmin": "quanmin", "lishipin": "lishipin", "lvzhou": "lvzhou",
    "meipai": "meipai", "quanminkge": "quanminkge", "sixroom": "sixroom",
    "xinpianchang": "xinpianchang", "haokan": "haokan", "doupai": "doupai",
}

# Host fragment -> canonical platform, derived from the engine's own routing
# table (single source of truth; see :func:`_route` for matching semantics).
LEGACY_ROUTES: list[tuple[str, str]] = []
for _source, _info in video_source_info_mapping.items():
    _canonical = _CANONICAL_BY_ENGINE_SOURCE.get(_source.value)
    if _canonical:
        for _domain in _info["domain_list"]:
            LEGACY_ROUTES.append((_domain, _canonical))


def _route(url: str) -> str | None:
    """Return the canonical platform for a legacy (parse-video-py) URL, or None."""
    host = (urlsplit(url).hostname or "").lower()
    for domain, canonical in LEGACY_ROUTES:
        if host == domain or host.endswith("." + domain):
            return canonical
    return None


class LegacyParserAdapter:
    """Real :class:`ParserAdapter` for platforms f2 does not cover (parse-video-py)."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        proxy: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._proxy = proxy
        self._transport = transport  # test seam; None = real network

    def parse(self, command: ParseCommand) -> list[ParseResult]:
        return [self._parse_one(url) for url in command.urls]

    def _parse_one(self, url: str) -> ParseResult:
        platform = _route(url)
        if platform is None:
            raise UnsupportedPlatformError(_MESSAGE_UNSUPPORTED_PLATFORM)
        return self._parse_video(url, platform)

    def _parse_video(self, url: str, platform: str) -> ParseResult:
        try:
            info = asyncio.run(
                asyncio.wait_for(parse_video_share_url(url), timeout=self._timeout)
            )
            # 图集 / 动图: the engine returns the album (or animated image) in
            # `images` with no video_url — classify honestly as IMAGE and
            # format from the first image's extension (jpg/gif/webp/...).
            is_album = not info.video_url and bool(info.images)
            if is_album:
                media_type = MediaType.IMAGE
                size_url = info.images[0].url
                media_format = extension_of(size_url) or "jpg"
            else:
                media_type = MediaType.VIDEO
                size_url = info.video_url
                media_format = extension_of(size_url) or "mp4"
            return ParseResult(
                task_id=str(uuid.uuid4()),
                url=url,
                media_type=media_type,
                platform=platform,
                title=(info.title or "").strip() or platform,
                cover=info.cover_url or None,
                duration=None,  # engine exposes no duration (documented)
                file_size_mb=probe_file_size_mb(
                    size_url,
                    timeout_seconds=self._timeout,
                    proxy=self._proxy,
                    transport=self._transport,
                ),
                format=media_format,
                available_qualities=[],  # engine exposes no quality ladder
                available_bitrates=[],
                metadata={
                    "engine": "parse-video-py",
                    "video_url": info.video_url,
                    "music_url": info.music_url or None,
                    "author": {
                        "uid": info.author.uid,
                        "name": info.author.name,
                        "avatar": info.author.avatar,
                    },
                    "images": [
                        {"url": img.url, "live_photo_url": img.live_photo_url}
                        for img in info.images
                    ],
                },
                error=None,
            )
        except EngineError:
            raise
        except Exception as exc:
            raise translate_engine_exception(exc, url=url, operation="parse") from exc
