"""Preview transport: ``GET /api/preview/{task_id}`` v1 single-media metadata.

This router is deliberately thin — the use case lives in
:class:`app.application.preview_service.PreviewService`. The handler validates
the path parameter (the domain ``UuidStr`` — a malformed task_id becomes a
generic 400 via the request-validation handler), delegates, and renders the
envelope; a well-formed but unknown task_id raises the PRD ``3001`` 任务不存在
envelope from the service.

DI hook (override in tests via ``app.dependency_overrides``):

* :func:`get_preview_service` — the app-wired preview service (``app.state``).

``create_app`` populates ``app.state.preview_service``; handlers must only be
mounted on an app built by ``create_app`` (or one that sets the same state).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.api.responses import ok
from app.application.preview_service import PreviewService
from app.domain.models import UuidStr

__all__ = [
    "PreviewData",
    "PreviewResponse",
    "StreamInfo",
    "get_preview_service",
    "router",
]

router = APIRouter(prefix="/preview", tags=["preview"])

_MESSAGE_PREVIEW_OK = "获取预览成功 / Preview loaded"


class StreamInfo(BaseModel):
    """One v1 stream representation: quality for video, bitrate for music."""

    quality: str | None = None
    bitrate: str | None = None
    format: str | None = None


class PreviewData(BaseModel):
    """The v1 single-media preview payload (metadata + stream information).

    ``preview_type`` mirrors the media type (video/image/music) so the
    frontend can render the right preview widget without re-deriving it.
    """

    task_id: str
    preview_type: str
    url: str
    platform: str
    title: str | None = None
    cover: str | None = None
    duration: str | None = None  # "MM:SS"
    format: str | None = None
    file_size_mb: float | None = None
    available_qualities: list[str] = Field(default_factory=list)
    available_bitrates: list[str] = Field(default_factory=list)
    streams: list[StreamInfo] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    """The unified envelope for ``GET /api/preview/{task_id}`` success."""

    code: int
    message: str
    data: PreviewData | None = None


def get_preview_service(request: Request) -> PreviewService:
    """DI hook: the app-wired preview service (override in tests)."""
    return request.app.state.preview_service


@router.get("/{task_id}", response_model=PreviewResponse)
def preview(
    task_id: UuidStr,
    service: Annotated[PreviewService, Depends(get_preview_service)],
) -> dict:
    """Return v1 single-media preview metadata/stream information for a task.

    Success: ``200`` with ``{code, message, data: {preview_type, ...}}``.
    Missing task → ``400`` code ``3001`` (任务不存在); malformed task_id →
    ``400`` generic.
    """
    return ok(data=service.preview(task_id), message=_MESSAGE_PREVIEW_OK)


@router.get("/{task_id}/stream")
def stream_media(
    task_id: UuidStr,
    request: Request,
    service: Annotated[PreviewService, Depends(get_preview_service)],
) -> StreamingResponse:
    """Proxy the task's recorded video URL for inline playback.

    Same-origin byte stream: the platform URL stays server-side, the engine
    UA is sent upstream, and the client's ``Range`` header is forwarded so
    seeking works (206 responses pass through). Success is raw bytes (NOT the
    envelope — the response is media, matching the file endpoint's
    convention); failures use the shared ApiError envelope (3001/400).
    """
    stream = service.stream_video(task_id, request.headers.get("range"))

    def iterator():
        try:
            for chunk in stream.chunks:
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        iterator(),
        status_code=stream.status_code,
        headers=stream.headers,
        media_type=None,
    )


@router.get("/{task_id}/images/{index}")
def image_file(
    task_id: UuidStr,
    index: int,
    service: Annotated[PreviewService, Depends(get_preview_service)],
) -> Response:
    """Serve one album image as an attachment (download-current).

    Raw bytes with a ``Content-Disposition: attachment`` header; failures use
    the shared ApiError envelope (3001/400).
    """
    body, content_type, filename = service.image_bytes(task_id, index)
    return Response(
        content=body,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{task_id}/images.zip")
def album_zip(
    task_id: UuidStr,
    service: Annotated[PreviewService, Depends(get_preview_service)],
) -> Response:
    """Serve the whole album as a ZIP attachment (download-all)."""
    body, filename = service.album_zip(task_id)
    return Response(
        content=body,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
