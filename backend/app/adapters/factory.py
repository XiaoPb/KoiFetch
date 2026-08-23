"""Adapter selection: wire settings to the stub/local adapter implementations.

Tasks 7-12 must obtain adapters through these functions instead of
constructing implementations directly, so a future swap (real platform
engines, S3/NAS storage, different token formats) only touches this module and
the implementations — never the services or API handlers.

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
    DownloaderAdapter,
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
    "get_one_time_token_provider",
    "get_parser",
    "get_storage",
]


def get_parser(settings: Settings | None = None) -> ParserAdapter:
    """Return the stub parser (deterministic, offline)."""
    return StubParserAdapter()


def get_downloader(settings: Settings | None = None) -> DownloaderAdapter:
    """Return the stub downloader, throttled by the configured speed limit.

    ``settings.download_speed_limit`` (MB/s; 0 = unlimited) maps to a
    per-chunk delay inside the stub, so the worker's progress speed is
    observable end-to-end (the setting has no other consumer in v1).
    """
    settings = settings or get_settings()
    return StubDownloaderAdapter(
        speed_limit_mb_s=float(settings.download_speed_limit)
    )


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
