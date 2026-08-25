"""Real parser adapter: platform routing + parse-video-py (Task 5).

Implements :class:`app.adapters.protocols.ParserAdapter` against the
`parse-video-py` engine (Git install — see backend/requirements.txt). The
engine resolves video share URLs (douyin/kuaishou/bilibili/xiaohongshu/weibo/
xigua/...) into a :class:`parse_video_py.VideoInfo` with real title, cover,
video URL, author and (for albums) images.

Honesty contract (verified against the engine source, 2026-08-25):

* **No duration, no quality ladder.** ``VideoInfo`` exposes
  ``video_url``/``cover_url``/``title``/``music_url``/``author``/``images``
  only, so the result reports ``duration=None`` and empty quality/bitrate
  lists (both optional in :class:`app.domain.models.ParseResult`), and
  ``file_size_mb`` comes from a best-effort Content-Length probe of the
  video URL (never fatal — ``None`` on failure).
* **musicdl is search-based, not URL-based** (verified: its only URL entry is
  ``parseplaylist`` for playlist URLs, and ``#/song?id=`` URLs return empty).
  Music-platform URLs are therefore rejected with
  :class:`UnsupportedPlatformError` (parse-service maps it to code 1003);
  the playlist→tasks product decision is a documented v1.1 follow-up.
* **Sync facade over an async engine.** ``parse_video_share_url`` is async;
  the protocol is sync and runs in FastAPI's threadpool, so each URL is
  awaited with ``asyncio.run`` (no shared loop across URLs).
* **Errors are typed.** httpx/timeout/engine failures are translated to the
  :mod:`app.adapters.engine_errors` hierarchy with stable bilingual messages;
  the original exception is preserved as ``__cause__``.
* **``transport`` is a test seam.** Production passes ``None`` (real
  network); tests inject ``httpx.MockTransport`` to exercise the size probe
  offline.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from urllib.parse import urlsplit

import httpx

from app.adapters.engine_errors import (
    EngineError,
    EngineParseError,
    UnsupportedPlatformError,
    translate_engine_exception,
)
from app.adapters.protocols import ParserAdapter
from app.domain import MediaType, ParseCommand, ParseResult
from parse_video_py import parse_video_share_url
from parse_video_py.parser import video_source_info_mapping

__all__ = ["EngineParserAdapter"]

_MESSAGE_UNSUPPORTED_PLATFORM = "平台不支持 / Unsupported platform"
_MESSAGE_MUSIC_UNSUPPORTED = (
    "该平台暂不支持链接解析（音乐引擎为搜索型，歌单解析为v1.1） / "
    "Music URL parsing unsupported in v1 (musicdl is a search-based engine)"
)

# parse-video-py's VideoSource.value -> KoiFetch canonical platform name.
# Most values already match (DouYin.value == "douyin"); only divergences are
# mapped (RedBook -> xiaohongshu to keep the stub-era vocabulary stable).
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
# table (single source of truth; mirrored matching: substring-in-URL).
_VIDEO_ROUTES: list[tuple[str, str]] = []
for _source, _info in video_source_info_mapping.items():
    _canonical = _CANONICAL_BY_ENGINE_SOURCE.get(_source.value)
    if _canonical:
        for _domain in _info["domain_list"]:
            _VIDEO_ROUTES.append((_domain, _canonical))

# Music platforms (musicdl's supported sources). Checked AFTER video routes so
# a host both engines know (kg.qq.com: 全民K歌 video vs QQ music) stays video.
_MUSIC_ROUTES: list[tuple[str, str]] = [
    ("music.163.com", "netease_music"), ("163cn.tv", "netease_music"),
    ("y.qq.com", "qq_music"), ("i.y.qq.com", "qq_music"),
    ("kugou.com", "kugou"), ("kuwo.cn", "kuwo"),
    ("music.migu.cn", "migu"), ("m.music.migu.cn", "migu"),
    ("music.taihe.com", "qianqian"), ("qianqian.com", "qianqian"),
]

_UA = {"User-Agent": "Mozilla/5.0 (KoiFetch/0.1)"}
_EXT_UNSAFE = re.compile(r"[^a-z0-9]+")


def _route(url: str) -> tuple[str, str] | None:
    """Return ``(engine, canonical_platform)`` for a URL, or ``None``."""
    for host, canonical in _VIDEO_ROUTES:
        if host in url:
            return "parse-video-py", canonical
    for host, canonical in _MUSIC_ROUTES:
        if host in url:
            return "musicdl", canonical
    return None


def _extension_of(url: str) -> str | None:
    """Lowercase alnum extension from the last path segment ('' when none)."""
    last = urlsplit(url).path.rsplit("/", 1)[-1]
    dot = last.rfind(".")
    if dot == -1 or not last[dot + 1 :]:
        return None
    cleaned = _EXT_UNSAFE.sub("", last[dot + 1 :].lower())
    return cleaned or None


class EngineParserAdapter:
    """Real :class:`ParserAdapter`: routes by platform and parses via parse-video-py."""

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
        routed = _route(url)
        if routed is None:
            raise UnsupportedPlatformError(_MESSAGE_UNSUPPORTED_PLATFORM)
        engine, platform = routed
        if engine == "musicdl":
            raise UnsupportedPlatformError(_MESSAGE_MUSIC_UNSUPPORTED)
        return self._parse_video(url, platform)

    def _parse_video(self, url: str, platform: str) -> ParseResult:
        try:
            info = asyncio.run(parse_video_share_url(url))
        except EngineError:
            raise
        except Exception as exc:
            raise translate_engine_exception(exc, url=url, operation="parse") from exc

        return ParseResult(
            task_id=str(uuid.uuid4()),
            url=url,
            media_type=MediaType.VIDEO,
            platform=platform,
            title=(info.title or "").strip() or platform,
            cover=info.cover_url or None,
            duration=None,  # engine exposes no duration (documented)
            file_size_mb=self._probe_file_size_mb(info.video_url),
            format=_extension_of(info.video_url) or "mp4",
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

    def _probe_file_size_mb(self, video_url: str) -> float | None:
        """Best-effort Content-Length of the media URL (GET, headers only).

        CDNs often reject HEAD or omit length; any failure yields ``None`` —
        the size is optional metadata, never a parse failure.
        """
        if not video_url:
            return None
        kwargs: dict = {"timeout": self._timeout, "follow_redirects": True}
        if self._proxy:
            kwargs["proxy"] = self._proxy
        if self._transport is not None:
            kwargs["transport"] = self._transport
        try:
            with httpx.Client(**kwargs) as client:
                with client.stream("GET", video_url, headers=_UA) as response:
                    length = response.headers.get("content-length")
            if length and length.isdigit():
                return round(int(length) / 1_000_000, 1)
        except Exception:  # probe is best-effort by contract
            pass
        return None
