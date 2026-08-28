"""Adapter selection: wire settings to the stub/local or engine implementations.

Tasks 7-12 obtain adapters through these functions instead of constructing
implementations directly, so swapping stub ↔ real engines (and, in the future,
S3/NAS storage or different token formats) only touches this module and the
implementations — never the services or API handlers. The factory switches
stub ↔ real engines by settings; engine modules are imported lazily so stub
mode never requires the engine packages.

Each function takes an optional :class:`app.infrastructure.config.Settings`;
when omitted, the process-wide ``get_settings()`` singleton is used. Functions
are deliberately **not** cached: storage roots resolve against the process CWD
at construction time (see ``storage_local.resolve_storage_root``), and tests
need fresh adapters per fixture. The FastAPI app constructs what it needs once
at startup and injects it.
"""

from __future__ import annotations

from app.adapters.downloader_stub import StubDownloaderAdapter
from app.adapters.parser_stub import StubParserAdapter
from app.adapters.protocols import (
    AccessTokenProvider,
    CookieProvider,
    DownloaderAdapter,
    MusicSearchAdapter,
    OneTimeTokenProvider,
    ParserAdapter,
    StorageAdapter,
)
from app.adapters.storage_local import LocalStorageAdapter
from app.adapters.tokens_jwt import (
    JwtAccessTokenProvider,
    JwtOneTimeTokenProvider,
)
from app.infrastructure.config import Settings, get_settings

__all__ = [
    "get_access_token_provider",
    "get_downloader",
    "get_music_search",
    "get_one_time_token_provider",
    "get_parser",
    "get_storage",
]


def get_parser(
    settings: Settings | None = None,
    cookie_provider: CookieProvider | None = None,
) -> ParserAdapter:
    """Return the parser for ``settings.parser_engine``.

    ``"stub"`` (default) → the deterministic offline :class:`StubParserAdapter`;
    ``"engine"`` → the routing facade :class:`EngineParserAdapter` (f2 for
    douyin/weibo/tiktok plus the parse-video-py fallback, lazily imported so
    the app boots and the non-engine tests run without the engines installed —
    engine mode fails loudly at factory time if they are missing).
    ``cookie_provider`` (optional) is forwarded to the f2 adapter only.
    """
    settings = settings or get_settings()
    if settings.parser_engine == "engine":
        from app.adapters.parser_engine import EngineParserAdapter

        return EngineParserAdapter(
            timeout_seconds=settings.engine_timeout_seconds,
            proxy=settings.engine_proxy,
            cookie_provider=cookie_provider,
            enable_legacy_fallback=settings.parser_legacy_fallback,
        )
    return StubParserAdapter()


def get_downloader(settings: Settings | None = None) -> DownloaderAdapter:
    """Return the downloader for ``settings.downloader_engine``.

    ``"stub"`` (default) → the throttled stub downloader (the configured speed
    limit maps to a per-chunk delay so progress speed stays observable);
    ``"engine"`` → the real :class:`EngineDownloaderAdapter` (lazily imported,
    same boot-without-engines property as :func:`get_parser`).
    """
    settings = settings or get_settings()
    if settings.downloader_engine == "engine":
        from app.adapters.downloader_engine import EngineDownloaderAdapter

        return EngineDownloaderAdapter(
            timeout_seconds=settings.engine_timeout_seconds,
            download_timeout_seconds=settings.engine_download_timeout_seconds,
            proxy=settings.engine_proxy,
            music_sources=settings.musicdl_sources,
        )
    return StubDownloaderAdapter(
        speed_limit_mb_s=float(settings.download_speed_limit)
    )


def get_music_search(settings: Settings | None = None) -> MusicSearchAdapter:
    """Return the music-search adapter for ``settings.music_search_engine``.

    ``"stub"`` (default) → the deterministic offline stub; ``"engine"`` →
    the musicdl-backed :class:`MusicdlMusicSearchAdapter` (lazily imported so
    the app boots and the non-engine tests run without engine packages).
    """
    settings = settings or get_settings()
    if settings.music_search_engine == "engine":
        from app.adapters.music_search_engine import MusicdlMusicSearchAdapter

        return MusicdlMusicSearchAdapter(
            music_sources=settings.musicdl_sources,
            timeout_seconds=settings.engine_timeout_seconds,
        )
    from app.adapters.music_search_stub import MusicSearchStubAdapter

    return MusicSearchStubAdapter()


def get_storage(settings: Settings | None = None) -> StorageAdapter:
    """Return the local bubble/pond storage adapter for the configured roots.

    Relative roots resolve against the process CWD at construction time (see
    ``storage_local.resolve_storage_root``) — the documented resolution rule.
    """
    settings = settings or get_settings()
    return LocalStorageAdapter(
        pond_video=settings.video_storage_path,
        pond_image=settings.image_storage_path,
        pond_music=settings.music_storage_path,
        bubble_video=settings.temp_video_path,
        bubble_image=settings.temp_image_path,
        bubble_music=settings.temp_music_path,
    )


def get_access_token_provider(
    settings: Settings | None = None,
) -> AccessTokenProvider:
    """Return the 24h JWT access-token provider (auth service, Task 7)."""
    settings = settings or get_settings()
    return JwtAccessTokenProvider(settings.secret_key)


def get_one_time_token_provider(
    settings: Settings | None = None,
) -> OneTimeTokenProvider:
    """Return the 5-minute one-time download-token provider (Task 9)."""
    settings = settings or get_settings()
    return JwtOneTimeTokenProvider(settings.secret_key)
