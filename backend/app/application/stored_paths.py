"""Shared resolution of stored storage paths for the application services.

``DownloadService.get_file`` and ``NasService.save`` both re-derive a stored
bubble path against the *live* bubble root before touching the filesystem.
The rule is identical for both, so it lives here once instead of drifting in
two copies: relative stored paths are joined under the root, absolute ones are
re-derived from their relative form, and any traversal or corruption is
reported as ``ApiError`` 5001 (file not found) — the escape itself is never
disclosed to the client. Keep this module free of per-service concerns so the
bubble-path contract cannot drift.
"""

from __future__ import annotations

import os
from pathlib import Path

from starlette.status import HTTP_404_NOT_FOUND

from app.adapters.protocols import StorageAdapter
from app.api.responses import CODE_FILE_NOT_FOUND, ApiError
from app.domain import MediaType
from app.domain.paths import PathOutsideRootError, build_path, is_within

__all__ = ["resolve_bubble_path"]

_MESSAGE_FILE_NOT_FOUND = "文件不存在 / File not found"


def resolve_bubble_path(
    storage: StorageAdapter, media_type: MediaType, stored_path: str
) -> Path:
    """Re-derive ``stored_path`` inside the live bubble root, refusing escapes.

    Raises :class:`app.api.responses.ApiError` ``5001`` (404) for a
    missing/corrupt/traversal path. Containment is validated here at build
    time only; I/O operations re-verify it (the Task 5 TOCTOU rule).
    """
    bubble_root = storage.bubble_root(media_type)
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
