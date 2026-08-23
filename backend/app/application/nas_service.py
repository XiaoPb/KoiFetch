"""NAS save use case (Task 10): move a completed download's file to the pond.

Sits in the application layer between the API transport (``app.api.nas``) and
the storage adapter / persistence. v1's NAS surface is exactly one operation:
``POST /api/nas/save`` moves a completed download's bubble file into the
permanent pond at a caller-chosen NAS-style directory. Full browsing and
destructive file operations are deliberately out of scope for v1 (no list /
delete / rename — the PRD's "keep browsing and destructive operations out").

Design decisions (stable contract for Tasks 11-12 and the frontend):

* **Error precedence (documented).** ``save`` validates the request first —
  an invalid ``target_path`` is a generic ``400`` and short-circuits before
  any lookup (a malformed request reveals nothing about the task). Then:
  degraded storage (``storage is None``) → ``9001`` (500); unknown download →
  ``3001`` (400); any non-COMPLETED status — pending/downloading/failed and
  *expired* alike — → ``5002`` (400): the PRD's NAS error set has a single
  "not downloaded" code and an expired task's file is not saveable either;
  missing/escaping bubble path → ``5001`` (404).
* **Pond layout rule (documented).** ``target_path`` is a NAS-style *logical*
  path relative to the pond root of the task's media type. A PRD §5.7 leading
  ``/`` (e.g. ``"/视频/抖音"``) is stripped; every remaining non-empty segment
  is slugified via :func:`app.domain.paths.slugify` (CJK-preserving, hostile
  characters neutralized) and joined under the pond root:
  ``pond_root/<seg1>/<seg2>/…/<filename>``. The filename is either a fresh
  PRD-style name via :func:`app.domain.paths.safe_media_filename` when the
  parse task's ``metadata_`` carries the required inputs (video/music:
  ``published_at`` + ``source_id``, optional ``artist``; image: ``index``),
  with the title taken from the download row (fallback: the parse title) and
  the extension from the bubble file — or, when that metadata is absent
  (stub-era rows), the bubble file's basename, which the worker already named
  per the PRD pattern. The stored ``pond_path`` is the pond-relative path
  (e.g. ``"视频/抖音/2026-01-01_x.mp4"``); the response ``nas_path`` is the
  NAS-style form with a leading slash. ``target_path`` segments that are
  ``.``/``..``, blank, backslash-separated or drive-letter-prefixed are
  rejected up front; containment is additionally enforced by ``build_path``
  and the adapter's write-time TOCTOU re-check, so the file can never land
  outside the pond root.
* **Move semantics and ordering.** The bubble file is *moved* through
  :meth:`app.adapters.protocols.StorageAdapter.move_to_pond` (with the
  computed pond-relative target), which re-verifies the source inside the
  bubble root and the target inside the pond root at I/O time (Task 5 TOCTOU
  rule). The move happens first, then the row is updated (``pond_path``,
  ``completed_at``) in the same session; the two are not in one transaction —
  if the DB update failed after a successful move, the pond file would exist
  while the row keeps its old state (accepted for v1: the file is present and
  correct, and a retry would surface ``5001`` on the stale bubble path).
* **DI over globals.** The constructor takes an optional storage adapter
  (``None`` = degraded-storage mode, mirroring :class:`DownloadService`) and
  an optional ``engine``; ``create_app`` wires the production instance and
  tests pin a temp database + temp storage roots.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.adapters.protocols import StorageAdapter
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_FILE_NOT_FOUND,
    CODE_INTERNAL_ERROR,
    CODE_TASK_NOT_FOUND,
    ApiError,
)
from app.domain import DownloadStatus, MediaType
from app.domain.paths import (
    PathOutsideRootError,
    build_path,
    is_within,
    safe_media_filename,
    slugify,
)
from app.infrastructure.database import session_scope
from app.infrastructure.models import DownloadTask, ParseTask

__all__ = ["NasSaveResult", "NasService"]

_MESSAGE_TASK_NOT_FOUND = "任务不存在 / Task not found"
_MESSAGE_FILE_NOT_DOWNLOADED = "文件未下载完成 / File not fully downloaded"
_MESSAGE_FILE_NOT_FOUND = "文件不存在 / File not found"
_MESSAGE_INVALID_TARGET = "目标路径无效 / Invalid target path"
_MESSAGE_STORAGE_NOT_READY = "存储未就绪 / Storage not ready"

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class NasSaveResult:
    """Outcome of a NAS save.

    ``nas_path`` is the NAS-style logical path (leading slash, e.g.
    ``"/视频/抖音/2026-01-01_x.mp4"``) — the product-facing value, never a
    filesystem path; ``file_size`` is the moved file's byte count; ``saved_at``
    is the aware-UTC save timestamp (also written to the row's
    ``completed_at``).
    """

    nas_path: str
    file_size: int
    saved_at: datetime


class NasService:
    """Move a completed download's bubble file into the pond (NAS save)."""

    def __init__(
        self,
        storage: StorageAdapter | None = None,
        *,
        engine: Engine | None = None,
    ) -> None:
        # ``None`` is meaningful: degraded-storage mode (create_app falls back
        # to None when a root cannot be created); save then fails with a clean
        # storage error instead of a traceback. Mirrors DownloadService: the
        # storage adapter is never defaulted behind the caller's back.
        self._storage = storage
        self._engine = engine

    def save(self, download_id: str, target_path: str) -> NasSaveResult:
        """Move a completed download's bubble file to ``target_path`` in the pond.

        Raises :class:`ApiError`: generic ``400`` for an invalid target path;
        ``9001`` (500) in degraded-storage mode; ``3001`` (400) unknown
        download; ``5002`` (400) task not completed; ``5001`` (404) bubble
        file missing or escaping its root. Returns the NAS-style destination
        plus file size and save time.
        """
        segments = self._parse_target_path(target_path)
        if self._storage is None:
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
            if row.status != DownloadStatus.COMPLETED:
                raise ApiError(
                    HTTP_400_BAD_REQUEST,
                    CODE_FILE_NOT_DOWNLOADED,
                    _MESSAGE_FILE_NOT_DOWNLOADED,
                )

            parse_task = row.parse_task
            media_type = parse_task.media_type

            if not row.bubble_path:
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                )
            bubble = self._resolve_bubble_path(media_type, row.bubble_path)
            if not self._storage.exists(bubble):
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                )

            filename = self._pond_filename(row, parse_task, bubble.name)
            pond_relative = "/".join([*segments, filename])
            try:
                pond_path = self._storage.move_to_pond(
                    media_type, bubble, target=pond_relative
                )
            except PathOutsideRootError as exc:
                # Defense in depth: slugified segments cannot escape, but a
                # swapped root/symlink at move time must surface as a clean
                # 400, never a traceback.
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_TARGET
                ) from exc
            except ValueError as exc:
                # The source vanished (or is no longer a file) between the
                # exists() check and the move — same "file not found" contract.
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                ) from exc

            saved_at = datetime.now(timezone.utc)
            row.pond_path = pond_relative
            row.completed_at = saved_at
            return NasSaveResult(
                nas_path="/" + pond_relative,
                file_size=pond_path.stat().st_size,
                saved_at=saved_at,
            )

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _parse_target_path(target_path: str) -> list[str]:
        """Validate a NAS-style target path; return its slugified segments.

        Accepts a PRD §5.7 leading ``/`` (stripped — the path is always
        relative to the pond root). Rejects blank values, ``.``/``..``
        segments, backslash separators and drive-letter prefixes with a
        generic ``400``. Every remaining segment is slugified (CJK-preserving,
        hostile characters neutralized), so containment is guaranteed before
        any filesystem access.
        """
        if not isinstance(target_path, str) or not target_path.strip():
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_TARGET
            )
        text = target_path.strip()
        if "\\" in text or _DRIVE_LETTER.match(text):
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_TARGET
            )
        text = text.lstrip("/")  # the PRD's NAS-style leading slash
        segments = [segment for segment in text.split("/") if segment]
        if not segments:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_TARGET
            )
        if any(segment in (".", "..") for segment in segments):
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_TARGET
            )
        return [slugify(segment) for segment in segments]

    @staticmethod
    def _pond_filename(
        row: DownloadTask, parse_task: ParseTask, bubble_basename: str
    ) -> str:
        """Pick the pond filename for a moved file (see the module docstring).

        A fresh :func:`safe_media_filename` name when the parse task's
        metadata carries the required inputs; the bubble basename (already a
        PRD-style safe name) otherwise — including when the extension cannot
        be derived.
        """
        ext = _extension_of(bubble_basename)
        if not ext:
            return bubble_basename
        title = row.title or parse_task.title or "untitled"
        metadata = parse_task.metadata_ or {}
        media_type = parse_task.media_type
        try:
            if media_type is MediaType.IMAGE:
                index = metadata.get("index")
                if index is None:
                    raise ValueError("image pond filename needs metadata index")
                return safe_media_filename(
                    media_type, title=title, ext=ext, index=int(index)
                )
            published_at = metadata.get("published_at")
            source_id = metadata.get("source_id")
            if not published_at or not source_id:
                raise ValueError(
                    "video/music pond filename needs metadata published_at + source_id"
                )
            return safe_media_filename(
                media_type,
                published_at=published_at,
                title=title,
                artist=metadata.get("artist"),
                source_id=source_id,
                ext=ext,
            )
        except (ValueError, TypeError):
            # Missing/ill-formed metadata or an undecodable index → keep the
            # worker's PRD-style bubble basename.
            return bubble_basename

    def _resolve_bubble_path(self, media_type: MediaType, stored_path: str) -> Path:
        """Re-derive ``stored_path`` inside the live bubble root, refusing escapes.

        Mirrors :meth:`app.application.download_service.DownloadService._resolve_bubble_path`
        (the two services share the same bubble-path contract); relative stored
        paths are joined under the root, absolute ones re-derived from their
        relative form. Any traversal or corruption surfaces as ``5001``.
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


def _extension_of(basename: str) -> str:
    """The extension (lowercase) of a basename, or ``""`` when absent."""
    name = Path(basename).name
    dot = name.rfind(".")
    if dot == -1 or dot == len(name) - 1:
        return ""
    return name[dot + 1 :].lower()
