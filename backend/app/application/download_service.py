"""Download use cases (Task 9): submit, progress polling, tokenized file handoff.

Sits in the application layer between the API transport (``app.api.download``)
and persistence/adapters. The worker (Task 11) executes downloads; this service
is the API contract side: it creates pending rows, serves progress snapshots,
and hands a verified bubble file to the file endpoint behind a one-time token.

Design decisions (stable contract for Tasks 10-12):

* **Submit duplicate policy (documented).** ``submit`` refuses to create a row
  when the *same task* already has an **active** download (status
  ``pending``/``downloading``) → ``3002`` (409): one source cannot be queued or
  fetched twice concurrently. A **completed** download blocks only the
  *identical* format+quality variant → ``3003`` (400): the exact media already
  exists and should be fetched via the file endpoint, while the one-to-many
  model (several formats/qualities per parse task) stays intact. ``failed`` and
  ``expired`` downloads never block (the state graph's ``failed -> pending`` /
  ``expired -> pending`` re-download paths map to a fresh row here). The
  check-then-insert is not atomic against two racing submits (no unique
  constraint exists by design); SQLite WAL serializes writes, so a duplicate
  pair can only slip through an exact same-instant race — documented, accepted
  for v1, and worth a partial unique index if it ever matters.
* **One-time token flow.** The client obtains a token from the WebSocket
  ``complete`` event (``download_url``) or by calling ``issue_download_token``
  — never from the file endpoint itself, which *requires* a token. ``get_file``
  validates it (``TokenError`` subclasses all map to the single PRD code
  ``5003``), checks the token targets this download, then records the token's
  ``tid`` claim on the row **atomically** (a conditional ``UPDATE`` that only
  matches when the row holds no token_id or a *different* one), so reusing the
  same token always fails while a fresh issuance is a valid new link. Single
  use is therefore per *issuance*.
* **Error precedence in ``get_file`` (documented).** task missing → ``3001``;
  expired task → ``5004`` (410); not completed → ``5002`` (400); token rules →
  ``5003`` (401); bubble file missing/escaped → ``5001`` (404). Status rules run
  before token rules (they are about the task, not the credential) and the file
  is verified *before* the token is consumed, so a failed attempt never burns
  the link.
* **Containment at resolution and at I/O.** The stored ``bubble_path`` is
  re-derived through :func:`app.domain.paths.build_path` against the live
  bubble root (traversal/corrupt paths → ``5001``), and
  :meth:`app.adapters.protocols.StorageAdapter.exists` re-verifies containment
  right before the API serves — the Task 5 TOCTOU rule applied to serving, not
  just writing.
* **DI over globals.** The constructor takes the one-time token provider and
  downloader (defaulting to ``app.adapters.factory``), an explicit storage
  adapter (``None`` = degraded-storage mode, see :meth:`DownloadService.__init__`),
  and an optional ``engine``; ``create_app`` wires the production instance and
  tests pin a temp database + temp storage roots.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Engine, or_, select, update
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_410_GONE,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.adapters.factory import get_downloader, get_one_time_token_provider
from app.adapters.protocols import (
    DownloaderAdapter,
    OneTimeTokenProvider,
    StorageAdapter,
    TokenError,
)
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_EXPIRED,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_FILE_NOT_FOUND,
    CODE_FILE_TOKEN_INVALID,
    CODE_INTERNAL_ERROR,
    CODE_TASK_ALREADY_COMPLETED,
    CODE_TASK_ALREADY_DOWNLOADING,
    CODE_TASK_NOT_FOUND,
    ApiError,
)
from app.domain import (
    DownloadCommand,
    DownloadProgress,
    DownloadResult,
    DownloadStatus,
    MediaType,
)
from app.domain.paths import PathOutsideRootError, build_path, is_within
from app.infrastructure.database import session_scope
from app.infrastructure.models import DownloadTask, ParseTask

__all__ = ["DownloadService", "DownloadedFile"]

_MESSAGE_TASK_NOT_FOUND = "任务不存在 / Task not found"
_MESSAGE_INVALID_SUBMIT = "请求参数错误 / Invalid request parameters"
_MESSAGE_TASK_ALREADY_DOWNLOADING = "任务已在下载 / Task already downloading"
_MESSAGE_TASK_ALREADY_COMPLETED = "任务已完成 / Task already completed"
_MESSAGE_FILE_NOT_DOWNLOADED = "文件未下载完成 / File not fully downloaded"
_MESSAGE_FILE_TOKEN_INVALID = "Token无效或已过期 / Invalid or expired token"
_MESSAGE_FILE_EXPIRED = "文件已过期 / File expired"
_MESSAGE_FILE_NOT_FOUND = "文件不存在 / File not found"
_MESSAGE_STORAGE_NOT_READY = "存储未就绪 / Storage not ready"


@dataclass(frozen=True)
class DownloadedFile:
    """A resolved, verified bubble file ready for the API to serve.

    ``path`` is absolute and contained in the bubble root (re-verified at
    resolution and at ``exists`` time); ``filename`` is the stored bubble
    basename for the Content-Disposition header.
    """

    path: Path
    media_type: MediaType
    filename: str


class DownloadService:
    """Submit downloads, report progress, and hand off completed files."""

    def __init__(
        self,
        token_provider: OneTimeTokenProvider | None = None,
        storage: StorageAdapter | None = None,
        downloader: DownloaderAdapter | None = None,
        *,
        engine: Engine | None = None,
    ) -> None:
        self._token_provider = (
            token_provider if token_provider is not None else get_one_time_token_provider()
        )
        # ``None`` is meaningful: degraded-storage mode (create_app builds the
        # adapter eagerly and falls back to None when a root cannot be created,
        # so the app boots and /api/health reports degraded). ``get_file`` then
        # fails with a clean storage error instead of a traceback.
        self._storage = storage
        # Held for the worker (Task 11) wiring contract; the API service itself
        # never downloads — it only creates pending rows.
        self._downloader = downloader if downloader is not None else get_downloader()
        self._engine = engine

    def submit(
        self,
        task_id: str,
        format: str | None = None,
        quality: str | None = None,
    ) -> DownloadResult:
        """Create a ``pending`` download row for an existing parse task.

        Raises :class:`ApiError`: ``3001`` (400) unknown task; ``3002`` (409)
        an active download already exists for the task; ``3003`` (400) the
        identical format+quality variant is already completed; generic ``400``
        for a blank selection or malformed task_id.
        """
        try:
            command = DownloadCommand(task_id=task_id, format=format, quality=quality)
        except ValidationError as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_SUBMIT
            ) from exc

        with session_scope(self._engine) as session:
            parse_task = session.get(ParseTask, command.task_id)
            if parse_task is None:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_TASK_NOT_FOUND,
                    _MESSAGE_TASK_NOT_FOUND,
                )
            active = session.scalar(
                select(DownloadTask).where(
                    DownloadTask.task_id == command.task_id,
                    DownloadTask.status.in_(
                        (DownloadStatus.PENDING, DownloadStatus.DOWNLOADING)
                    ),
                )
            )
            if active is not None:
                raise ApiError(
                    HTTP_409_CONFLICT,
                    CODE_TASK_ALREADY_DOWNLOADING,
                    _MESSAGE_TASK_ALREADY_DOWNLOADING,
                )
            completed_variant = session.scalar(
                select(DownloadTask).where(
                    DownloadTask.task_id == command.task_id,
                    DownloadTask.status == DownloadStatus.COMPLETED,
                    DownloadTask.format == command.format,
                    DownloadTask.quality == command.quality,
                )
            )
            if completed_variant is not None:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_TASK_ALREADY_COMPLETED,
                    _MESSAGE_TASK_ALREADY_COMPLETED,
                )

            row = DownloadTask(
                download_id=str(uuid.uuid4()),
                task_id=command.task_id,
                title=parse_task.title,
                format=command.format,
                quality=command.quality,
                status=DownloadStatus.PENDING,
                progress=0.0,
                retry_count=0,
            )
            session.add(row)
            session.flush()  # assign python-side defaults (created_at, ...)
            return _result_from_row(row, media_type=parse_task.media_type)

    def get_progress(self, download_id: str) -> DownloadProgress:
        """Return a live snapshot for a download; ``3001`` (400) when missing.

        ``remaining_time`` is derived from speed while ``downloading``
        (``(total - downloaded) / speed``) and is ``None`` otherwise.
        """
        with session_scope(self._engine) as session:
            row = session.get(DownloadTask, download_id)
            if row is None:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_TASK_NOT_FOUND,
                    _MESSAGE_TASK_NOT_FOUND,
                )
            return _progress_from_row(row)

    def get_file(self, download_id: str, token: str | None) -> DownloadedFile:
        """Validate the one-time token and resolve the completed bubble file.

        Error precedence (see module docstring): ``3001``, ``5004`` (410),
        ``5002`` (400), ``5003`` (401) for any token failure including reuse,
        ``5001`` (404) for a missing/escaped bubble file. The token is consumed
        atomically only after every other check passed.
        """
        if not token or not token.strip():
            raise ApiError(
                HTTP_401_UNAUTHORIZED,
                CODE_FILE_TOKEN_INVALID,
                _MESSAGE_FILE_TOKEN_INVALID,
            )
        if self._storage is None:
            # Degraded-storage mode: the app booted but no storage adapter
            # could be built (see __init__); file serving is unavailable.
            raise ApiError(
                HTTP_500_INTERNAL_SERVER_ERROR,
                CODE_INTERNAL_ERROR,
                _MESSAGE_STORAGE_NOT_READY,
            )
        with session_scope(self._engine) as session:
            row = session.get(DownloadTask, download_id)
            if row is None:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_TASK_NOT_FOUND,
                    _MESSAGE_TASK_NOT_FOUND,
                )
            if row.status == DownloadStatus.EXPIRED:
                raise ApiError(
                    HTTP_410_GONE, CODE_FILE_EXPIRED, _MESSAGE_FILE_EXPIRED
                )
            if row.status != DownloadStatus.COMPLETED:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_FILE_NOT_DOWNLOADED,
                    _MESSAGE_FILE_NOT_DOWNLOADED,
                )
            media_type = row.parse_task.media_type

            try:
                claims = self._token_provider.validate(token)
            except TokenError as exc:
                # Single PRD code for every token failure (invalid AND expired).
                raise ApiError(
                    HTTP_401_UNAUTHORIZED,
                    CODE_FILE_TOKEN_INVALID,
                    _MESSAGE_FILE_TOKEN_INVALID,
                ) from exc
            if claims.download_id != download_id:
                raise ApiError(
                    HTTP_401_UNAUTHORIZED,
                    CODE_FILE_TOKEN_INVALID,
                    _MESSAGE_FILE_TOKEN_INVALID,
                )

            # Resolve + verify the bubble file BEFORE consuming the token, so a
            # failed attempt (e.g. file swept by cleanup) does not burn the link.
            if not row.bubble_path:
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                )
            path = self._resolve_bubble_path(media_type, row.bubble_path)
            if not self._storage.exists(path):
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                )

            # Single use, atomically: the UPDATE only matches when the row has
            # no recorded token_id yet or a *different* one, so the same token
            # can never serve twice (reuse → rowcount 0 → 5003) while a fresh
            # issuance remains a valid new link. Concurrent same-token requests
            # serialize on SQLite's write lock: only the first matches.
            result = session.execute(
                update(DownloadTask)
                .where(
                    DownloadTask.download_id == download_id,
                    or_(
                        DownloadTask.token_id.is_(None),
                        DownloadTask.token_id != claims.token_id,
                    ),
                )
                .values(token_id=claims.token_id, token_expires_at=claims.expires_at)
            )
            if result.rowcount == 0:
                raise ApiError(
                    HTTP_401_UNAUTHORIZED,
                    CODE_FILE_TOKEN_INVALID,
                    _MESSAGE_FILE_TOKEN_INVALID,
                )
            return DownloadedFile(
                path=path,
                media_type=media_type,
                filename=Path(row.bubble_path).name,
            )

    def issue_download_token(self, download_id: str) -> str:
        """Mint a 5-minute one-time token for a completed download.

        This is how clients obtain a download link (the WS ``complete`` event
        calls it); ``3001`` (400) unknown download, ``5002`` (400) not yet
        completed. Single use is enforced later, at :meth:`get_file`.
        """
        with session_scope(self._engine) as session:
            row = session.get(DownloadTask, download_id)
            if row is None:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_TASK_NOT_FOUND,
                    _MESSAGE_TASK_NOT_FOUND,
                )
            if row.status != DownloadStatus.COMPLETED:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_FILE_NOT_DOWNLOADED,
                    _MESSAGE_FILE_NOT_DOWNLOADED,
                )
        return self._token_provider.issue(download_id=download_id)

    # -- internals -------------------------------------------------------------

    def _resolve_bubble_path(self, media_type: MediaType, stored_path: str) -> Path:
        """Re-derive ``stored_path`` inside the live bubble root, refusing escapes.

        Relative stored paths are joined under the root; absolute ones are
        re-derived from their relative form. Any traversal or corruption
        surfaces as ``5001`` (file not found) — the escape itself is never
        disclosed to the client.
        """
        bubble_root = self._storage.bubble_root(media_type)
        raw = Path(stored_path)
        try:
            if not raw.is_absolute():
                return build_path(bubble_root, *raw.parts)
            if not is_within(bubble_root, raw):
                raise PathOutsideRootError(
                    f"stored bubble path escapes the bubble root: {stored_path!r}"
                )
            relative = os.path.relpath(raw, bubble_root)
            return build_path(bubble_root, relative)
        except (ValueError, PathOutsideRootError):
            raise ApiError(
                HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
            ) from None


def _progress_from_row(row: DownloadTask) -> DownloadProgress:
    """Map a row to a domain snapshot, deriving remaining time while downloading."""
    remaining: float | None = None
    if (
        row.status == DownloadStatus.DOWNLOADING
        and row.speed is not None
        and row.speed > 0
        and row.total_bytes is not None
    ):
        remaining_bytes = max(row.total_bytes - (row.downloaded_bytes or 0), 0)
        remaining = remaining_bytes / row.speed
    return DownloadProgress(
        download_id=row.download_id,
        status=row.status,
        progress=row.progress or 0.0,
        speed=row.speed,
        downloaded_bytes=row.downloaded_bytes,
        total_bytes=row.total_bytes,
        remaining_time=remaining,
        error_message=row.error_message,
    )


def _result_from_row(
    row: DownloadTask, *, media_type: MediaType | None = None
) -> DownloadResult:
    """Map a row to the domain :class:`DownloadResult` (media_type from caller)."""
    return DownloadResult(
        download_id=row.download_id,
        task_id=row.task_id,
        title=row.title,
        media_type=media_type,
        format=row.format,
        quality=row.quality,
        status=row.status,
        progress=row.progress or 0.0,
        speed=row.speed,
        total_bytes=row.total_bytes,
        downloaded_bytes=row.downloaded_bytes,
        retry_count=row.retry_count,
        error_message=row.error_message,
        bubble_path=row.bubble_path,
        pond_path=row.pond_path,
        token_expires_at=row.token_expires_at,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )
