"""Adapter implementations for the parser, downloader, storage, and tokens.

This package is the *adapters* half of the ports-and-adapters architecture:
``protocols.py`` defines the ports (interfaces services depend on), and the
other modules provide the concrete implementations — stub parser/downloader
(deterministic, offline, so the whole workflow runs before real platform
engines exist), local bubble/pond storage, and JWT token providers. ``factory``
wires settings to implementations; services should use it instead of
constructing adapters directly.

Public surface (import from ``app.adapters``):

* Protocols — :class:`ParserAdapter`, :class:`DownloaderAdapter`,
  :class:`StorageAdapter`, :class:`AccessTokenProvider`,
  :class:`OneTimeTokenProvider` plus their claim/value types and the
  :class:`TokenError` hierarchy.
* Stubs — :class:`StubParserAdapter`, :class:`StubDownloaderAdapter`,
  :class:`LocalStorageAdapter`, :class:`JwtAccessTokenProvider`,
  :class:`JwtOneTimeTokenProvider`.
* Selection — :func:`get_parser`, :func:`get_downloader`,
  :func:`get_storage`, :func:`get_access_token_provider`,
  :func:`get_one_time_token_provider`.
"""

from app.adapters.downloader_stub import StubDownloaderAdapter
from app.adapters.factory import (
    get_access_token_provider,
    get_downloader,
    get_one_time_token_provider,
    get_parser,
    get_storage,
)
from app.adapters.parser_stub import StubParserAdapter
from app.adapters.protocols import (
    AccessTokenClaims,
    AccessTokenProvider,
    DownloadRequest,
    DownloaderAdapter,
    InvalidTokenError,
    OneTimeTokenClaims,
    OneTimeTokenProvider,
    ParserAdapter,
    ProgressCallback,
    StorageAdapter,
    TokenError,
    TokenExpiredError,
)
from app.adapters.storage_local import LocalStorageAdapter, resolve_storage_root
from app.adapters.tokens_jwt import (
    JwtAccessTokenProvider,
    JwtOneTimeTokenProvider,
)

__all__ = [
    "AccessTokenClaims",
    "AccessTokenProvider",
    "DownloadRequest",
    "DownloaderAdapter",
    "InvalidTokenError",
    "JwtAccessTokenProvider",
    "JwtOneTimeTokenProvider",
    "LocalStorageAdapter",
    "OneTimeTokenClaims",
    "OneTimeTokenProvider",
    "ParserAdapter",
    "ProgressCallback",
    "StorageAdapter",
    "StubDownloaderAdapter",
    "StubParserAdapter",
    "TokenError",
    "TokenExpiredError",
    "get_access_token_provider",
    "get_downloader",
    "get_one_time_token_provider",
    "get_parser",
    "get_storage",
    "resolve_storage_root",
]
