"""Adapter protocols: the ports of the ports-and-adapters architecture.

API handlers and application services depend on these interfaces, never on
third-party engines or filesystem details. Each protocol has a stub/local
implementation behind it today (parser, downloader, storage, tokens); real
platform engines (engine mode, see ``app.adapters.factory``), an S3/NAS
store, or different token formats can be swapped in without touching the
API contracts.

Design decisions (documented once, relied on by Tasks 7-12):

* **Synchronous adapters.** :class:`ParserAdapter` and
  :class:`DownloaderAdapter` are sync. FastAPI runs sync ``def`` endpoints in
  its threadpool, and the stub adapters are CPU/local-I/O bound, so sync keeps
  the contracts simple. A future engine that is naturally async can expose a
  sync facade or this layer can gain async variants when a caller needs them.
* **Progress by callback.** The worker (Task 11) needs to persist byte-level
  progress as it arrives; :class:`DownloaderAdapter.download` therefore
  accepts an optional ``progress_callback`` invoked with domain
  :class:`~app.domain.models.DownloadProgress` snapshots. The callback must
  not raise (adapter behaviour is undefined if it does) and must be cheap —
  the worker's DB update is the obvious implementation.
* **Path safety belongs to the storage adapter.** :class:`StorageAdapter`
  methods take safe filenames/components and build absolute, contained paths
  through the domain ``build_path`` helper, re-verifying containment at
  open/write time (TOCTOU note, Task 5). Traversal attempts raise
  :class:`app.domain.paths.PathOutsideRootError`.
* **Tokens: two protocols, stateless validate.** JWT access tokens (24h) and
  one-time file tokens (5 min) are separate concerns with separate callers
  (auth service vs. download-file API), so they are two protocols. ``validate``
  is *pure*: it never marks a token used. Single-use enforcement for one-time
  tokens is the caller's job (Task 9 records the returned ``token_id`` before
  serving the file); the adapter stays stateless so it can be shared and
  scaled freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from app.domain import (
    DownloadCommand,
    DownloadProgress,
    DownloadResult,
    MediaType,
    MusicSearchParams,
    MusicSearchResult,
    ParseCommand,
    ParseResult,
)

__all__ = [
    "AccessTokenClaims",
    "AccessTokenProvider",
    "CookieProvider",
    "DownloadRequest",
    "DownloaderAdapter",
    "InvalidTokenError",
    "MusicSearchAdapter",
    "OneTimeTokenClaims",
    "OneTimeTokenProvider",
    "ParserAdapter",
    "ProgressCallback",
    "StorageAdapter",
    "TokenError",
    "TokenExpiredError",
]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@runtime_checkable
class ParserAdapter(Protocol):
    """Parse one or more source URLs into media metadata.

    Synchronous (see module docstring). Implementations must be deterministic
    for the same input where sensible and must never require network access
    for URLs they cannot actually fetch — the stub derives everything from the
    URL itself.
    """

    def parse(self, command: ParseCommand) -> list[ParseResult]:
        """Return one :class:`ParseResult` per input URL (same order)."""
        ...


# ---------------------------------------------------------------------------
# Cookie provider (f2 parser)
# ---------------------------------------------------------------------------


@runtime_checkable
class CookieProvider(Protocol):
    """Read the configured cookie string for a platform (None when unset).

    Implemented by
    :class:`app.application.cookie_service.PlatformCookieService` (DB-backed)
    so the f2 parser can resolve per-platform cookies without knowing where
    they are stored. Only the read side is part of the protocol — writing is a
    service concern exposed through the cookies API.
    """

    def get(self, platform: str) -> str | None:
        """Return the stored cookie for ``platform`` (None when unset)."""
        ...


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------


ProgressCallback = Callable[[DownloadProgress], None]
"""Signature of a per-chunk progress hook: receives a domain progress snapshot.

The callback must not raise. ``speed`` is bytes/second for the interval since
the previous callback (or since start for the first one).
"""


@dataclass(frozen=True)
class DownloadRequest:
    """Everything the worker knows when it asks an adapter to fetch a file.

    ``command`` carries the parsed task id and format/quality selections;
    ``download_id`` is the worker's persisted row id (the adapter cannot invent
    it — the row already exists when the worker starts); ``target_path`` is the
    destination the worker resolved through the storage adapter (usually a
    bubble path); ``title``/``media_type`` are carried for convenience so
    adapters can name/derive content without another lookup;
    ``source_url``/``metadata`` are the parse context the real engines need
    (the media URL / song info the parser resolved).
    """

    command: DownloadCommand
    download_id: str
    target_path: Path
    title: str | None = None
    media_type: MediaType | None = None
    source_url: str | None = None
    """The original parse-task URL, carried so engine downloaders can
    re-resolve media when the metadata does not carry it (and for
    diagnostics). The stub downloader ignores it."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Engine-produced parse metadata (the persisted ``ParseTask.metadata_``
    JSON): the engine downloader reads ``video_url`` (video) or ``song_info``
    (music) from here. The stub downloader ignores it."""

    progress_callback: ProgressCallback | None = None


@runtime_checkable
class DownloaderAdapter(Protocol):
    """Fetch media for one download task, reporting incremental progress.

    Implementations write bytes to ``request.target_path`` (creating parent
    directories), call ``request.progress_callback`` (if set) with
    :class:`DownloadProgress` snapshots as chunks land, and return a final
    :class:`DownloadResult` snapshot (status ``COMPLETED`` on success). On
    failure they raise; the worker (Task 11) records the failure state.
    """

    def download(self, request: DownloadRequest) -> DownloadResult:
        """Download one file; returns the final task snapshot."""
        ...


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageAdapter(Protocol):
    """Bubble (temporary) and pond (permanent) media storage.

    All returned paths are absolute and guaranteed contained in the relevant
    root. Callers hand this adapter safe names/components (e.g. produced by
    ``safe_media_filename``); the adapter re-verifies containment via
    ``build_path`` at build *and* I/O time, raising
    :class:`app.domain.paths.PathOutsideRootError` on any escape. A future
    S3/NAS implementation implements the same operations.
    """

    # --- roots ----------------------------------------------------------
    def bubble_root(self, media_type: MediaType) -> Path: ...

    def pond_root(self, media_type: MediaType) -> Path: ...

    # --- path resolution (no I/O) ----------------------------------------
    def resolve_bubble(self, media_type: MediaType, *components: str) -> Path: ...

    def resolve_pond(self, media_type: MediaType, *components: str) -> Path: ...

    # --- persistence ------------------------------------------------------
    def save_file(
        self,
        media_type: MediaType,
        source: Path,
        filename: str,
        *,
        to_pond: bool = False,
    ) -> Path:
        """Move ``source`` into storage under ``filename``; return stored path."""
        ...

    def save_bytes(
        self,
        media_type: MediaType,
        filename: str,
        data: bytes,
        *,
        to_pond: bool = False,
    ) -> Path:
        """Write ``data`` into storage under ``filename``; return stored path."""
        ...

    def move_to_pond(
        self,
        media_type: MediaType,
        bubble_path: Path,
        *,
        target: str | None = None,
    ) -> Path:
        """Move a bubble file to the pond root; return the pond path.

        ``target`` (Task 10 NAS save) optionally names a pond-relative
        destination (directories + filename); when omitted, the bubble's
        relative path is mirrored under the pond root. The adapter validates
        the target and re-verifies containment at move time.
        """
        ...

    def read_bytes(self, stored_path: Path) -> bytes:
        """Return the bytes of a stored file (bubble or pond)."""
        ...

    def exists(self, stored_path: Path) -> bool:
        """Return whether the path is a stored file inside a configured root.

        Predicate semantics: a path outside every configured root returns
        ``False`` (no exception), while :meth:`read_bytes` and :meth:`delete`
        raise :class:`app.domain.paths.PathOutsideRootError` for the same
        input. ``exists`` performs no containment re-check beyond the
        containment predicate itself.
        """
        ...

    def delete(self, stored_path: Path) -> None:
        """Delete a stored file; raise ``FileNotFoundError`` when absent."""
        ...

    # --- listing (preview / NAS APIs, Tasks 8/10) -------------------------
    def list_files(self, media_type: MediaType, *, pond: bool = False) -> list[Path]:
        """Return sorted absolute paths of stored files in one bucket."""
        ...


# ---------------------------------------------------------------------------
# Music search
# ---------------------------------------------------------------------------


@runtime_checkable
class MusicSearchAdapter(Protocol):
    """Keyword search over music platforms (musicdl-backed in engine mode).

    Implementations return at most one page's worth of songs — musicdl
    returns everything in a single call and v1 does not paginate (the
    service documents ``hasMore: False``). Per-source failures must never
    raise: musicdl swallows them into empty lists, and a deterministic stub
    never fails.
    """

    def search(self, command: MusicSearchParams) -> MusicSearchResult:
        """Return songs for ``command.keyword`` (artists/albums/playlists
        empty — the service derives them from the songs)."""
        ...


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """Base class for token failures surfaced to callers (never pyjwt types)."""


class TokenExpiredError(TokenError):
    """The token is well-formed but past its expiry."""


class InvalidTokenError(TokenError):
    """The token is malformed, tampered, mis-signed, or missing required claims."""


@dataclass(frozen=True)
class AccessTokenClaims:
    """Decoded, validated access-token claims."""

    user_id: int
    username: str
    issued_at: datetime
    expires_at: datetime


@runtime_checkable
class AccessTokenProvider(Protocol):
    """Issue/validate the 24h JWT bearer token used by the auth service."""

    def issue(self, *, user_id: int, username: str) -> str:
        """Create a signed access token for a user."""
        ...

    def validate(self, token: str) -> AccessTokenClaims:
        """Validate and decode; raise :class:`TokenError` on any failure."""
        ...


@dataclass(frozen=True)
class OneTimeTokenClaims:
    """Decoded, validated one-time download-token claims.

    ``token_id`` uniquely identifies this issuance; the download-file API
    (Task 9) records it to enforce single use.
    """

    token_id: str
    download_id: str
    issued_at: datetime
    expires_at: datetime


@runtime_checkable
class OneTimeTokenProvider(Protocol):
    """Issue/validate 5-minute single-use download tokens.

    ``validate`` is stateless (see module docstring): single-use enforcement
    is the caller's job via the returned ``token_id``.
    """

    def issue(self, *, download_id: str) -> str:
        """Create a signed one-time token for a download."""
        ...

    def validate(self, token: str) -> OneTimeTokenClaims:
        """Validate and decode; raise :class:`TokenError` on any failure."""
        ...
