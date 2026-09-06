"""Engine parser adapter: per-URL routing across f2 and the legacy engine.

This module is the engine-mode :class:`ParserAdapter` entry point. It routes
each URL to one of two protocol-compliant adapters:

* :class:`app.adapters.parser_f2.F2ParserAdapter` — douyin/weibo/tiktok via
  the f2 library (primary engine; cookie-aware).
* :class:`app.adapters.parser_legacy.LegacyParserAdapter` — parse-video-py
  fallback for the platforms f2 does not cover (kuaishou/bilibili/
  xiaohongshu/xigua/...), gated by ``enable_legacy_fallback`` so the legacy
  engine can be removed once f2 covers them (set
  ``PARSER_LEGACY_FALLBACK=false`` and delete parser_legacy.py + the
  parse-video-py dependency). Coverage is the engine's own domain list
  (e.g. ``www.bilibili.com``/``m.bilibili.com``/``b23.tv``,
  ``v.kuaishou.com``); hosts outside it (e.g. bare ``bilibili.com``,
  ``www.kuaishou.com``) route as 平台不支持 — pre-existing engine-derived
  behavior.

Routing precedence: f2 → legacy → music. Music-platform URLs (musicdl is a
search-based engine, not URL-based) are rejected with
:class:`UnsupportedPlatformError` (parse-service maps it to code 1003); the
playlist→tasks product decision remains a documented v1.1 follow-up. The
precedence keeps a host that both the legacy and music tables might cover
(e.g. a future 全民K歌 vs QQ-music overlap) on the video path.

Both adapters are constructed lazily on first use so the facade imports
nothing heavy at module load beyond what engine mode already requires.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from app.adapters.engine_errors import UnsupportedPlatformError
from app.adapters.parser_f2 import F2ParserAdapter, _route as f2_route
from app.adapters.parser_legacy import (
    LegacyParserAdapter,
    _route as _legacy_route,
)
from app.adapters.protocols import CookieProvider
from app.domain import ParseCommand, ParseResult

__all__ = ["EngineParserAdapter"]

_MESSAGE_UNSUPPORTED_PLATFORM = "平台不支持 / Unsupported platform"
_MESSAGE_MUSIC_UNSUPPORTED = (
    "该平台暂不支持链接解析（音乐引擎为搜索型，歌单解析为v1.1） / "
    "Music URL parsing unsupported in v1 (musicdl is a search-based engine)"
)
_MESSAGE_LEGACY_DISABLED = (
    "该平台的解析引擎已停用 / The parsing engine for this platform is disabled"
)

# Music platforms (musicdl's supported sources). Checked AFTER the legacy
# video routes so a host both the legacy and music tables might cover in the
# future stays on the video path.
_MUSIC_ROUTES: list[tuple[str, str]] = [
    ("music.163.com", "netease_music"), ("163cn.tv", "netease_music"),
    ("y.qq.com", "qq_music"), ("i.y.qq.com", "qq_music"),
    ("kugou.com", "kugou"), ("kuwo.cn", "kuwo"),
    ("music.migu.cn", "migu"), ("m.music.migu.cn", "migu"),
    ("music.taihe.com", "qianqian"), ("qianqian.com", "qianqian"),
]


def _route(url: str) -> str | None:
    """Return the engine for ``url``: ``"f2"`` | ``"legacy"`` | ``"musicdl"``.

    Matches the hostname exactly or as a ``.``-suffixed subdomain, so
    unrelated hosts can never false-positive into a platform route. ``"f2"``
    wins over ``"legacy"`` over ``"musicdl"``. The f2 check consumes
    :func:`app.adapters.parser_f2._route` directly (single source of truth for
    the f2 host table — the legacy table also contains douyin/weibo hosts, so
    the tables must not be re-derived independently and drift).
    """
    host = (urlsplit(url).hostname or "").lower()
    if f2_route(url) is not None:
        return "f2"
    if _legacy_route(url) is not None:
        return "legacy"
    for domain, _platform in _MUSIC_ROUTES:
        if host == domain or host.endswith("." + domain):
            return "musicdl"
    return None


class EngineParserAdapter:
    """Real :class:`ParserAdapter`: routes each URL to f2 or the legacy engine."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        proxy: str | None = None,
        cookie_provider: CookieProvider | None = None,
        enable_legacy_fallback: bool = True,
        transport: httpx.BaseTransport | None = None,
        disable_bark: bool = True,
    ) -> None:
        self._timeout = timeout_seconds
        self._proxy = proxy
        self._cookie_provider = cookie_provider
        self._enable_legacy_fallback = enable_legacy_fallback
        self._transport = transport  # test seam; None = real network
        self._disable_bark = disable_bark
        self._f2_adapter: F2ParserAdapter | None = None
        self._legacy_adapter: LegacyParserAdapter | None = None

    def parse(self, command: ParseCommand) -> list[ParseResult]:
        return [self._parse_one(url) for url in command.urls]

    def _parse_one(self, url: str) -> ParseResult:
        routed = _route(url)
        if routed is None:
            raise UnsupportedPlatformError(_MESSAGE_UNSUPPORTED_PLATFORM)
        if routed == "musicdl":
            raise UnsupportedPlatformError(_MESSAGE_MUSIC_UNSUPPORTED)
        if routed == "f2":
            return self._f2().parse(ParseCommand(urls=[url]))[0]
        if not self._enable_legacy_fallback:
            raise UnsupportedPlatformError(_MESSAGE_LEGACY_DISABLED)
        return self._legacy().parse(ParseCommand(urls=[url]))[0]

    # -- lazy inner adapters -----------------------------------------------

    def _f2(self) -> F2ParserAdapter:
        if self._f2_adapter is None:
            self._f2_adapter = F2ParserAdapter(
                timeout_seconds=self._timeout,
                proxy=self._proxy,
                cookie_provider=self._cookie_provider,
                transport=self._transport,
                disable_bark=self._disable_bark,
            )
        return self._f2_adapter

    def _legacy(self) -> LegacyParserAdapter:
        if self._legacy_adapter is None:
            self._legacy_adapter = LegacyParserAdapter(
                timeout_seconds=self._timeout,
                proxy=self._proxy,
                transport=self._transport,
            )
        return self._legacy_adapter
