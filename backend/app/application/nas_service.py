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
  rejected up front; at most ``_MAX_TARGET_DEPTH`` segments and
  ``_MAX_POND_RELATIVE_LENGTH`` joined characters are allowed (clear 400s
  instead of filesystem-limit OSErrors); containment is additionally enforced
  by ``build_path`` and the adapter's write-time TOCTOU re-check, so the file
  can never land outside the pond root.
* **Collision guard — no silent data loss (documented).** The one-to-many
  model allows several COMPLETED variants per task (3003 only blocks
  *identical* format+quality), and metadata-driven naming gives them the
  SAME pond filename. ``save`` therefore resolves the exact pond target and
  refuses with a stable ``400`` 目标文件已存在 when it already exists, instead
  of letting ``shutil.move`` silently overwrite the earlier variant (POSIX)
  or raising an unhandled ``FileExistsError`` (Windows). A concurrent
  same-target save that wins the race between the pre-check and the move
  surfaces as the same 400 via ``FileExistsError``; the POSIX
  rename-overwrite race is a documented, accepted residual (saves serialize
  on SQLite in one process, so it is a non-issue in practice). To put both
  variants in one directory a caller must choose different target paths —
  a v1 constraint that a later version can relax.
* **Move semantics and ordering.** The bubble file is *moved* through
  :meth:`app.adapters.protocols.StorageAdapter.move_to_pond` (with the
  computed pond-relative target), which re-verifies the source inside the
  bubble root and the target inside the pond root at I/O time (Task 5 TOCTOU
  rule). Move failures map to stable errors: containment escape →
  ``400`` (logged as an integrity event), ``FileExistsError`` (race) → same
  400, ``FileNotFoundError``/adapter source guard → ``5001``, any other
  ``OSError`` → ``9001`` (logged). The move happens first, then the row is
  updated (``pond_path``, ``completed_at``) in the same session; the two are
  not in one transaction — if the DB update failed after a successful move,
  the pond file would exist while the row keeps its old state (accepted for
  v1: the file is present and correct, and a retry would surface ``5001`` on
  the stale bubble path).
* **DI over globals.** The constructor takes an optional storage adapter
  (``None`` = degraded-storage mode, mirroring :class:`DownloadService`) and
  an optional ``engine``; ``create_app`` wires the production instance and
  tests pin a temp database + temp storage roots.
"""

from __future__ import annotations

import logging
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
from app.application.stored_paths import resolve_bubble_path
from app.application.nas_path import build_media_pond_path
from app.application.storage_types import storage_media_type
from app.domain import AssetSelector, DownloadStatus, MediaType
from app.domain.paths import PathOutsideRootError, safe_media_filename, slugify
from app.infrastructure.database import session_scope
from app.infrastructure.models import DownloadTask, ParseTask

__all__ = ["NasSaveResult", "NasService"]

logger = logging.getLogger(__name__)

# Parse "live-0001-image.webp" / "live-0001-motion.mp4" → (index, kind)
_LIVE_MEMBER_RE = re.compile(r"live-(\d+)-(image|motion)\.")


def _parse_live_member(name: str) -> tuple[int, str] | None:
    """Parse a loose-file member name into (0-based index, resource_kind)."""
    m = _LIVE_MEMBER_RE.match(name)
    if m:
        return int(m.group(1)) - 1, f"live_{m.group(2)}"
    return None


_MESSAGE_TASK_NOT_FOUND = "任务不存在 / Task not found"
_MESSAGE_FILE_NOT_DOWNLOADED = "文件未下载完成 / File not fully downloaded"
_MESSAGE_FILE_NOT_FOUND = "文件不存在 / File not found"
_MESSAGE_INVALID_TARGET = "目标路径无效 / Invalid target path"
_MESSAGE_TARGET_EXISTS = "目标文件已存在 / Target file already exists"
_MESSAGE_TARGET_TOO_DEEP = "目标路径层级过深 / Target path too deep"
_MESSAGE_TARGET_TOO_LONG = "目标路径过长 / Target path too long"
_MESSAGE_STORAGE_NOT_READY = "存储未就绪 / Storage not ready"
_MESSAGE_STORAGE_ERROR = "存储操作失败 / Storage operation failed"

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
_MAX_TARGET_DEPTH = 16
_MAX_POND_RELATIVE_LENGTH = 260


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

    def save(self, download_id: str, target_path: str | None = None) -> NasSaveResult:
        """Move a completed download's bubble file to its generated pond path.

        Raises :class:`ApiError`: generic ``400`` for an invalid target path;
        ``9001`` (500) in degraded-storage mode; ``3001`` (400) unknown
        download; ``5002`` (400) task not completed; ``5001`` (404) bubble
        file missing or escaping its root. Returns the NAS-style destination
        plus file size and save time.
        """
        legacy_segments = (
            self._parse_target_path(target_path) if target_path is not None else None
        )
        if legacy_segments is not None and len(legacy_segments) > _MAX_TARGET_DEPTH:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_TARGET_TOO_DEEP
            )
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
            try:
                selector = (
                    AssetSelector.model_validate(row.asset_selector)
                    if row.asset_selector is not None
                    else None
                )
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                ) from exc
            storage_type = storage_media_type(media_type, selector)

            if not row.bubble_path:
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                )
            bubble = resolve_bubble_path(self._storage, storage_type, row.bubble_path)
            if not self._storage.exists(bubble):
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                )

            if legacy_segments is None:
                pond_relative = self._pond_relative_path(
                    row, parse_task, bubble.name, selector
                )
            else:
                pond_relative = "/".join(
                    [*legacy_segments, self._pond_filename(row, parse_task, bubble.name)]
                )
            if len(pond_relative) > _MAX_POND_RELATIVE_LENGTH:
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_TARGET_TOO_LONG
                )

            # Loose-file package (live_zip): bubble is a directory of images
            # and motion videos. Move each member individually to its own
            # pond path so the pond mirrors the video layout (no extra
            # directory nesting — files go directly under work_id/).
            if bubble.is_dir():
                members = sorted(f for f in bubble.iterdir() if f.is_file())
                if not members:
                    raise ApiError(
                        HTTP_404_NOT_FOUND,
                        CODE_FILE_NOT_FOUND,
                        _MESSAGE_FILE_NOT_FOUND,
                    )
                first_relative: str | None = None
                total_size = 0
                skipped = 0
                for member in members:
                    parsed = _parse_live_member(member.name)
                    if parsed is not None:
                        m_index, m_kind = parsed
                        m_selector = AssetSelector(kind=m_kind, index=m_index)
                    else:
                        m_selector = selector
                    # Each member lands in its own bucket: images → image,
                    # motion videos → video.
                    m_storage_type = storage_media_type(media_type, m_selector)
                    m_relative = self._pond_relative_path(
                        row, parse_task, member.name, m_selector
                    )
                    if len(m_relative) > _MAX_POND_RELATIVE_LENGTH:
                        continue
                    m_target = self._storage.resolve_pond(
                        m_storage_type, *m_relative.split("/")
                    )
                    if self._storage.exists(m_target):
                        skipped += 1
                        continue
                    try:
                        m_pond = self._storage.move_to_pond(
                            m_storage_type, member, target=m_relative
                        )
                    except (FileExistsError, PathOutsideRootError):
                        skipped += 1
                        continue
                    if first_relative is None:
                        first_relative = m_relative
                    total_size += m_pond.stat().st_size
                if first_relative is None:
                    # All members already existed in pond
                    raise ApiError(
                        HTTP_400_BAD_REQUEST,
                        CODE_BAD_REQUEST,
                        _MESSAGE_TARGET_EXISTS,
                    )
                saved_at = datetime.now(timezone.utc)
                row.pond_path = first_relative
                row.completed_at = saved_at
                # Best-effort: remove the now-empty bubble directory
                try:
                    bubble.rmdir()
                except OSError:
                    pass
                return NasSaveResult(
                    nas_path="/" + first_relative,
                    file_size=total_size,
                    saved_at=saved_at,
                )

            # --- single-file path (original logic) ---

            # Collision guard (data-loss prevention): the one-to-many model
            # allows several COMPLETED variants per task, and metadata-driven
            # naming gives them the SAME pond filename — shutil.move would
            # silently overwrite on POSIX. Refuse with a stable 400 instead;
            # the move below still re-checks containment (TOCTOU). A
            # concurrent same-target save that wins the race between this
            # pre-check and the move surfaces as the same 400 via
            # FileExistsError (Windows); the POSIX rename-overwrite race is
            # accepted and documented (SQLite-serialized saves in one process
            # make it a non-issue in practice).
            target_path_abs = self._storage.resolve_pond(
                storage_type, *pond_relative.split("/")
            )
            if self._storage.exists(target_path_abs):
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_TARGET_EXISTS
                )

            try:
                pond_path = self._storage.move_to_pond(
                    storage_type, bubble, target=pond_relative
                )
            except PathOutsideRootError as exc:
                # Integrity event: slugified segments cannot escape, but a
                # swapped root/symlink at move time must be logged and surface
                # as a clean 400, never a traceback.
                logger.warning(
                    "nas save containment failure for download %s target %r: %s",
                    download_id, pond_relative, exc,
                )
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_INVALID_TARGET
                ) from exc
            except FileExistsError as exc:
                # A concurrent same-target save won the race after the
                # pre-check — same stable "already exists" contract.
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_TARGET_EXISTS
                ) from exc
            except FileNotFoundError as exc:
                # The source vanished (or is no longer a file) between the
                # exists() check and the move — same "file not found" contract.
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                ) from exc
            except ValueError as exc:
                # The adapter's own source guard (``is_file``) fired — the
                # path is no longer a regular file. Same 5001 contract.
                raise ApiError(
                    HTTP_404_NOT_FOUND, CODE_FILE_NOT_FOUND, _MESSAGE_FILE_NOT_FOUND
                ) from exc
            except OSError as exc:
                # Any other storage failure (permissions, disk, path length)
                # is a genuine server error: log it, return a stable 500.
                logger.exception(
                    "nas save storage failure for download %s target %r",
                    download_id, pond_relative,
                )
                raise ApiError(
                    HTTP_500_INTERNAL_SERVER_ERROR,
                    CODE_INTERNAL_ERROR,
                    _MESSAGE_STORAGE_ERROR,
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
    def _pond_relative_path(
        row: DownloadTask,
        parse_task: ParseTask,
        bubble_basename: str,
        selector: AssetSelector | None,
    ) -> str:
        """Build the generated platform/date/author/work-relative path."""
        ext = _extension_of(bubble_basename)
        # Empty extension → loose-file directory (e.g. live_zip package);
        # still build the full pond path so it lands under platform/date/author.
        title = row.title or parse_task.title or "untitled"
        metadata = parse_task.metadata_ if isinstance(parse_task.metadata_, dict) else {}
        author_data = metadata.get("author")
        author = author_data.get("name") if isinstance(author_data, dict) else author_data
        index = None
        resource_kind = None
        if selector is not None:
            resource_kind = selector.kind
            if selector.kind in {"image", "live_image"}:
                index = selector.index
        if index is None:
            try:
                index = int(metadata["index"]) if "index" in metadata else None
            except (TypeError, ValueError):
                index = None
        return build_media_pond_path(
            media_bucket=storage_media_type(parse_task.media_type, selector).value,
            platform=parse_task.platform,
            published_at=metadata.get("published_at"),
            author=author,
            work_id=metadata.get("source_id"),
            task_id=parse_task.task_id,
            title=title,
            extension=ext,
            index=index,
            resource_kind=resource_kind,
        )

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
        # A drive-letter prefix anywhere (e.g. "/C:/evil", "a/C:/b") is an
        # absolute-path form and is rejected per the documented rule — not
        # just at the start of the whole string.
        if any(_DRIVE_LETTER.match(segment) for segment in segments):
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
        media_type = parse_task.media_type
        try:
            metadata = parse_task.metadata_ or {}
            if not isinstance(metadata, dict):
                # The metadata JSON column is unconstrained; a corrupt
                # (non-dict) value is treated exactly like absent metadata —
                # the fallback below, never an AttributeError → 9001.
                raise ValueError("parse task metadata must be a dict")
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


def _extension_of(basename: str) -> str:
    """The extension (lowercase) of a basename, or ``""`` when absent."""
    name = Path(basename).name
    dot = name.rfind(".")
    if dot == -1 or dot == len(name) - 1:
        return ""
    return name[dot + 1 :].lower()
