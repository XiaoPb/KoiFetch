"""Download transport (Task 9): submit, progress, tokenized file, WebSocket.

This router is deliberately thin — the use cases live in
:class:`app.application.download_service.DownloadService`. Three HTTP
endpoints plus one WebSocket:

* ``POST /api/download/submit`` — create a ``pending`` download for a parsed
  task (``data: {download_id, task_id, status, created_at}``); errors per the
  service (3001 / 3002 / 3003 / generic 400).
* ``GET /api/download/progress/{download_id}`` — the current snapshot
  (status/progress/speed/bytes/remaining_time); unknown id → ``3001``.
* ``GET /api/download/file/{download_id}?token=...`` — serve the completed
  bubble file behind a five-minute one-time token via
  :class:`fastapi.responses.FileResponse` (raw bytes + Content-Disposition;
  error envelopes only on failure). A missing ``token`` is itself a ``5003``.
* ``WS /ws/download/{download_id}`` — on connect, one structured snapshot
  event (``progress`` / ``complete`` / ``error``), then a live feed: events
  published to the app's :class:`app.application.download_events.DownloadEventHub`
  (the channel Task 11's worker drives) are forwarded verbatim.

Structured WS events (PRD §5.5), documented contract:

* ``{"type": "progress", "data": {download_id, status, progress, speed,
  downloaded_bytes, total_bytes, remaining_time}}`` — pending or downloading.
* ``{"type": "complete", "data": {..., "download_url":
  "/api/download/file/{id}?token=..."}}`` — completed; the URL carries a fresh
  one-time token minted at send time.
* ``{"type": "error", "data": {..., "error_message"}}`` — failed/expired, or
  a protocol failure with ``{"code", "message"}`` (unknown download → 3001;
  malformed download_id → generic 400) followed by a close.

The worker does not exist yet (Task 11): the snapshot is read straight from
the DB, so the WS contract works today and gains live updates when the worker
starts publishing. A connection whose client vanishes is cleaned up on the
next publish (send failure); the snapshot/subscription handoff has a tiny
window where an in-flight event may be missed — the client reconciles via the
progress endpoint.

DI hook (override in tests via ``app.dependency_overrides``):

* :func:`get_download_service` — the app-wired download service (``app.state``).

``create_app`` populates ``app.state.download_service`` and
``app.state.download_event_hub``; handlers must only be mounted on an app
built by ``create_app`` (or one that sets the same state).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_TASK_NOT_FOUND,
    ApiError,
    ok,
)
from app.application.download_events import DownloadEventHub
from app.application.download_service import DownloadService
from app.domain import DownloadProgress, DownloadStatus
from app.domain.models import UuidStr

__all__ = [
    "ProgressData",
    "ProgressResponse",
    "SubmitData",
    "SubmitRequest",
    "SubmitResponse",
    "get_download_service",
    "router",
    "ws_router",
]

# HTTP endpoints live under /api/download/*; the WebSocket is at /ws (mounted
# by create_app with no prefix).
router = APIRouter(prefix="/download", tags=["download"])
ws_router = APIRouter(tags=["download"])

_MESSAGE_SUBMIT_OK = "提交下载成功 / Download submitted"
_MESSAGE_PROGRESS_OK = "获取进度成功 / Progress loaded"
_MESSAGE_INVALID_DOWNLOAD_ID = "下载ID格式无效 / Invalid download id"

# Stable close codes for the WS (RFC 6455): normal completion vs. policy
# violation (malformed id).
_WS_CLOSE_OK = 1000
_WS_CLOSE_POLICY = 1008


class SubmitRequest(BaseModel):
    """A download submission: the parsed task plus optional selections.

    ``task_id`` is validated as a canonical UUID here (malformed → generic
    400); blank ``format``/``quality`` are rejected before the service sees
    them. The PRD's ``bitrate``/``save_to_nas``/``nas_path`` fields are v1.1 /
    Task 10 concerns and deliberately absent from the wire schema.
    """

    task_id: UuidStr
    format: str | None = Field(default=None, max_length=64)
    quality: str | None = Field(default=None, max_length=64)

    @field_validator("format", "quality", mode="before")
    @classmethod
    def _strip_selections(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class SubmitData(BaseModel):
    """The payload of a successful submit response."""

    download_id: str
    task_id: str
    status: str
    created_at: str  # ISO-8601 (UTC)


class SubmitResponse(BaseModel):
    """The unified envelope for ``POST /api/download/submit`` success."""

    code: int
    message: str
    data: SubmitData | None = None


class ProgressData(BaseModel):
    """The payload of a progress snapshot (PRD §5.4)."""

    download_id: str
    status: str
    progress: float
    speed: float | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    remaining_time: float | None = None  # seconds
    error_message: str | None = None


class ProgressResponse(BaseModel):
    """The unified envelope for ``GET /api/download/progress/{id}`` success."""

    code: int
    message: str
    data: ProgressData | None = None


def get_download_service(request: Request) -> DownloadService:
    """DI hook: the app-wired download service (override in tests)."""
    return request.app.state.download_service


@router.post("/submit", response_model=SubmitResponse)
def submit_download(
    body: SubmitRequest,
    service: Annotated[DownloadService, Depends(get_download_service)],
) -> dict:
    """Create a pending download for an already-parsed task.

    Success: ``200`` with ``{download_id, task_id, status: "pending",
    created_at}``. Unknown task → ``400`` ``3001``; active duplicate → ``409``
    ``3002``; completed identical variant → ``400`` ``3003``.
    """
    result = service.submit(body.task_id, format=body.format, quality=body.quality)
    return ok(
        data={
            "download_id": result.download_id,
            "task_id": result.task_id,
            "status": result.status.value,
            "created_at": result.created_at.isoformat() if result.created_at else None,
        },
        message=_MESSAGE_SUBMIT_OK,
    )


@router.get("/progress/{download_id}", response_model=ProgressResponse)
def download_progress(
    download_id: UuidStr,
    service: Annotated[DownloadService, Depends(get_download_service)],
) -> dict:
    """Return the current snapshot for a download.

    Success: ``200`` with the snapshot fields; unknown download → ``400``
    ``3001``; malformed id → ``400`` generic.
    """
    snapshot = service.get_progress(download_id)
    return ok(data=_serialize_progress(snapshot), message=_MESSAGE_PROGRESS_OK)


@router.get("/file/{download_id}")
def download_file(
    download_id: UuidStr,
    service: Annotated[DownloadService, Depends(get_download_service)],
    token: str | None = Query(default=None),
) -> FileResponse:
    """Serve a completed bubble file behind a five-minute one-time token.

    Success: the raw file bytes with a ``Content-Disposition`` attachment
    header (extension-derived media type). Failure is always an envelope:
    ``3001``/``5002``/``5003``/``5004``/``5001`` per the service's documented
    precedence. The file is served via :class:`FileResponse` — a contained,
    streamed read — after the service's containment re-checks.
    """
    file = service.get_file(download_id, token)
    return FileResponse(
        path=file.path,
        filename=file.filename,
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@ws_router.websocket("/ws/download/{download_id}")
async def download_ws(websocket: WebSocket, download_id: str) -> None:
    """Stream structured download events for one download.

    On connect: validate the id, send a DB-backed snapshot event, then forward
    every event the app's event hub publishes for this download (the worker's
    channel). The handler never raises after ``accept`` — protocol problems
    (unknown/malformed id) become an ``error`` event followed by a close.
    """
    await websocket.accept()
    service: DownloadService = websocket.app.state.download_service
    hub: DownloadEventHub = websocket.app.state.download_event_hub

    if not _is_canonical_uuid(download_id):
        await websocket.send_json(
            {
                "type": "error",
                "data": {"code": CODE_BAD_REQUEST, "message": _MESSAGE_INVALID_DOWNLOAD_ID},
            }
        )
        await websocket.close(code=_WS_CLOSE_POLICY)
        return

    try:
        snapshot = service.get_progress(download_id)
    except ApiError as exc:
        if exc.code != CODE_TASK_NOT_FOUND:
            raise
        await websocket.send_json(
            {"type": "error", "data": {"code": exc.code, "message": exc.message}}
        )
        await websocket.close(code=_WS_CLOSE_OK)
        return

    await websocket.send_json(_event_for(snapshot, service))

    queue = await hub.subscribe(download_id)
    try:
        while True:
            event = await queue.get()
            try:
                await websocket.send_json(event)
            except (WebSocketDisconnect, RuntimeError):
                break  # client gone; the finally below releases the subscription
    finally:
        await hub.unsubscribe(download_id, queue)


def _is_canonical_uuid(value: str) -> bool:
    """True for the canonical lowercase UUID form the domain ``UuidStr`` uses."""
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return str(parsed) == value.strip().lower()


def _event_for(progress: DownloadProgress, service: DownloadService) -> dict:
    """Build the structured snapshot event for a task state (PRD §5.5).

    ``progress`` for pending/downloading, ``complete`` (with a fresh one-time
    ``download_url``) for completed, ``error`` (with the row's error message)
    for failed/expired.
    """
    data = {
        "download_id": progress.download_id,
        "status": progress.status.value,
        "progress": progress.progress,
        "speed": progress.speed,
        "downloaded_bytes": progress.downloaded_bytes,
        "total_bytes": progress.total_bytes,
        "remaining_time": progress.remaining_time,
    }
    if progress.status is DownloadStatus.COMPLETED:
        token = service.issue_download_token(progress.download_id)
        data["download_url"] = (
            f"/api/download/file/{progress.download_id}?token={token}"
        )
        return {"type": "complete", "data": data}
    if progress.status in (DownloadStatus.FAILED, DownloadStatus.EXPIRED):
        data["error_message"] = progress.error_message
        return {"type": "error", "data": data}
    return {"type": "progress", "data": data}


def _serialize_progress(progress: DownloadProgress) -> dict:
    """Map a domain snapshot to the HTTP progress payload."""
    return {
        "download_id": progress.download_id,
        "status": progress.status.value,
        "progress": progress.progress,
        "speed": progress.speed,
        "downloaded_bytes": progress.downloaded_bytes,
        "total_bytes": progress.total_bytes,
        "remaining_time": progress.remaining_time,
        "error_message": progress.error_message,
    }
