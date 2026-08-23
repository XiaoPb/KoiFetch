"""Preview use cases (Task 8): v1 single-media preview metadata/stream info.

Sits in the application layer between the API transport (``app.api.preview``)
and persistence: it loads a :class:`ParseTask` by id and builds a safe,
metadata-only preview response the frontend can render. v1 deliberately
streams no media bytes — the design defers full preview streaming; this
service returns the persisted parse metadata plus a ``streams`` ladder
(quality ladder for video, bitrate ladder for music, none for images) so the
preview page has everything it needs without touching a file store.

Design decisions (stable contract for Task 9+):

* **Metadata-only and safe.** Nothing here reads or serves file bytes; the
  response is derived entirely from the ORM row and its persisted ``metadata``
  JSON, so a preview can never leak storage paths or stream content.
* **Code 3001 任务不存在.** A well-formed task_id with no row raises
  :class:`~app.api.responses.ApiError` (400/3001); malformed task_ids are
  rejected earlier by the transport (``UuidStr`` path parameter → generic 400).
* **What the row cannot store lives in ``metadata``.** The Task 4 ORM has no
  columns for ``file_size_mb``/``available_qualities``/``available_bitrates`;
  :class:`~app.application.parse_service.ParseService` enriches the metadata
  JSON on persist, and this service reads the ladders back from there.
* **Duration round-trip.** Rows store integer seconds; the response reformats
  to the PRD ``MM:SS`` display form via :func:`app.domain.format_duration`.
* **DI over globals.** The constructor takes an optional ``engine`` (defaults
  to the configured engine); ``create_app`` wires the production instance and
  tests override the API dependency (``app.api.preview.get_preview_service``).
"""

from __future__ import annotations

from sqlalchemy import Engine
from starlette.status import HTTP_400_BAD_REQUEST

from app.api.responses import CODE_TASK_NOT_FOUND, ApiError
from app.domain import MediaType, format_duration
from app.infrastructure.database import session_scope
from app.infrastructure.models import ParseTask

__all__ = ["PreviewService"]

_MESSAGE_TASK_NOT_FOUND = "任务不存在 / Task not found"


class PreviewService:
    """Load a parsed task and build its v1 single-media preview response."""

    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine

    def preview(self, task_id: str) -> dict:
        """Return the preview metadata/stream info for ``task_id``.

        Raises :class:`ApiError` (400/3001) when no such task exists.
        """
        with session_scope(self._engine) as session:
            task = session.get(ParseTask, task_id)
        if task is None:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_TASK_NOT_FOUND, _MESSAGE_TASK_NOT_FOUND
            )
        return _build_preview(task)


def _build_preview(task: ParseTask) -> dict:
    """Map a :class:`ParseTask` row to the v1 preview response payload."""
    metadata = task.metadata_ or {}
    qualities = list(metadata.get("available_qualities") or [])
    bitrates = list(metadata.get("available_bitrates") or [])
    streams: list[dict] = []
    if task.media_type == MediaType.VIDEO:
        # Stream information = the quality ladder (the stub has no real
        # streams; v1 preview is metadata-only by design).
        streams = [{"quality": quality, "format": task.format} for quality in qualities]
    elif task.media_type == MediaType.MUSIC:
        streams = [{"bitrate": bitrate, "format": task.format} for bitrate in bitrates]
    # Images have no stream ladder in v1.

    return {
        "task_id": task.task_id,
        "preview_type": task.media_type.value,
        "url": task.url,
        "platform": task.platform,
        "title": task.title,
        "cover": task.cover_url,
        "duration": (
            format_duration(task.duration) if task.duration is not None else None
        ),
        "format": task.format,
        "file_size_mb": metadata.get("file_size_mb"),
        "available_qualities": qualities,
        "available_bitrates": bitrates,
        "streams": streams,
    }
