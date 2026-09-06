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
  bubble file behind a five-minute short-lived token via
  :class:`fastapi.responses.FileResponse` (raw bytes + Content-Disposition;
  error envelopes only on failure). A missing ``token`` is itself a ``5003``.
* ``WS /ws/download/{download_id}`` — on connect, one structured snapshot
  event (``progress`` / ``complete`` / ``error``), then a live feed: events
  published to the app's :class:`app.application.download_events.DownloadEventHub`
  (the channel Task 11's worker drives) are forwarded verbatim.

Structured WS events (PRD §5.5), documented contract:

* ``{"type": "progress", "data": {download_id, status, progress, speed,
  downloaded_bytes, total_bytes, remaining_time}}`` — pending or downloading.
  ``remaining_time`` stays numeric seconds end-to-end; the PRD's display form
  (约 2分钟) is the frontend's formatting concern.
* ``{"type": "complete", "data": {..., "download_url":
  "/api/download/file/{id}?token=...", "token_expire_at": "<ISO-8601>"}}`` —
  completed; the URL carries a fresh short-lived reusable token minted at send
  time and ``token_expire_at`` its 5-minute validity. The field is named ``download_url``
  (the plan's wording) — the PRD wavers between ``download_url`` and
  ``file_url``; keep ``download_url``.
* ``{"type": "error", "data": {code, message, ...state}}`` — one uniform error
  shape. Task-state errors carry the state fields plus a code/message:
  ``failed`` → code 5002 (文件未下载完成 — the file is not available because the
  download failed), ``expired`` → code 5004 (文件已过期). Protocol failures
  carry just ``{code, message}`` (unknown download → 3001; malformed
  download_id → generic 400) and are followed by a close.

The worker does not exist yet (Task 11): the snapshot is read straight from
the DB, so the WS contract works today and gains live updates when the worker
starts publishing. The handler races its event queue against the socket's
receive, so a client that disconnects while idle (no event in flight) releases
its hub subscription immediately — verified against a real uvicorn server, not
just the TestClient (which tears the connection down with the app). The
snapshot/subscription handoff still has a tiny window where an in-flight event
may be missed — the client reconciles via the progress endpoint.

DI hook (override in tests via ``app.dependency_overrides``):

* :func:`get_download_service` — the app-wired download service (``app.state``).

``create_app`` populates ``app.state.download_service`` and
``app.state.download_event_hub``; handlers must only be mounted on an app
built by ``create_app`` (or one that sets the same state).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_FILE_EXPIRED,
    CODE_FILE_NOT_DOWNLOADED,
    CODE_TASK_NOT_FOUND,
    ApiError,
    ok,
)
from app.application.download_events import DownloadEventHub
from app.application.download_service import DownloadService
from app.application.transfer_service import TransferService
from app.domain import (
    DownloadProgress,
    DownloadStatus,
    AssetSelector,
    PrepareRequest,
    PreparedTransfer,
)
from app.domain.models import UuidStr

__all__ = [
    "ProgressData",
    "ProgressResponse",
    "SubmitData",
    "SubmitRequest",
    "SubmitResponse",
    "PrepareResponse",
    "get_download_service",
    "get_transfer_service",
    "router",
    "ws_router",
]

# HTTP endpoints live under /api/download/*; the WebSocket is at /ws (mounted
# by create_app with no prefix).
router = APIRouter(prefix="/download", tags=["download"])
ws_router = APIRouter(tags=["download"])

_MESSAGE_SUBMIT_OK = "提交下载成功 / Download submitted"
_MESSAGE_PROGRESS_OK = "获取进度成功 / Progress loaded"
_MESSAGE_TASK_NOT_FOUND = "任务不存在 / Task not found"
_MESSAGE_INVALID_DOWNLOAD_ID = "下载ID格式无效 / Invalid download id"
_MESSAGE_FILE_EXPIRED = "文件已过期 / File expired"
_MESSAGE_FILE_NOT_DOWNLOADED = "文件未下载完成 / File not fully downloaded"

# Stable close codes for the WS (RFC 6455): normal completion vs. policy
# violation (malformed id).
_WS_CLOSE_OK = 1000
_WS_CLOSE_POLICY = 1008


class SubmitRequest(BaseModel):
    """A download submission: the parsed task plus optional selections.

    ``task_id`` is validated as a canonical UUID here (malformed → generic
    400); blank ``format``/``quality`` are rejected before the service sees
    them. ``extra="forbid"`` (the project convention, like the domain models):
    an unknown body field — e.g. the PRD §5.3 ``save_to_nas`` flag, which is
    Task 10's NAS API concern — is rejected with a generic 400 rather than
    silently dropped, so a client never believes a field took effect when it
    did not.
    """

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    download_id: str
    task_id: str
    status: str
    created_at: str  # ISO-8601 (UTC)


class SubmitResponse(BaseModel):
    """The unified envelope for ``POST /api/download/submit`` success."""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SubmitData | None = None


class PrepareResponse(BaseModel):
    """The unified envelope for ``POST /api/download/prepare``."""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: PreparedTransfer | None = None


class ProgressData(BaseModel):
    """The payload of a progress snapshot (PRD §5.4)."""

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: ProgressData | None = None


class ByTaskData(ProgressData):
    """A progress snapshot plus the task it belongs to (recovery lookup).

    ``GET /api/download/by-task/{task_id}`` lets the frontend re-attach to a
    download after a page reload (the session-local download list is empty
    then): it returns the NEWEST download row for a task so the client can
    resume reconciliation / refresh its file link.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str


class ByTaskResponse(BaseModel):
    """The unified envelope for ``GET /api/download/by-task/{task_id}``."""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: ByTaskData | None = None


def get_download_service(request: Request) -> DownloadService:
    """DI hook: the app-wired download service (override in tests)."""
    return request.app.state.download_service


def get_transfer_service(request: Request) -> TransferService:
    """DI hook for transfer preparation (override in tests)."""
    return request.app.state.transfer_service


@router.post("/prepare", response_model=PrepareResponse)
def prepare_download(
    body: PrepareRequest,
    service: Annotated[TransferService, Depends(get_transfer_service)],
) -> dict:
    """Choose a same-origin direct route or create one staged download row."""
    result = service.prepare(
        body.task_id,
        body.asset,
        force_staged=body.force_staged,
    )
    return ok(
        data=result.model_dump(mode="json"),
        message="传输准备成功 / Transfer prepared",
    )


@router.get("/direct/{task_id}")
def direct_download(
    task_id: str,
    request: Request,
    service: Annotated[TransferService, Depends(get_transfer_service)],
    kind: str = Query(min_length=1),
    index: int = Query(default=0, ge=0),
    package: str | None = Query(default=None),
) -> StreamingResponse:
    """Stream a prepared single asset directly to the caller's device."""
    try:
        selector = AssetSelector(kind=kind, index=index, package=package)
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, CODE_BAD_REQUEST, "媒体资源无效 / Invalid media asset") from exc
    stream, filename = service.stream_direct(task_id, selector, request.headers.get("range"))

    def iterator():
        try:
            yield from stream.chunks
        finally:
            stream.close()

    return StreamingResponse(
        iterator(),
        status_code=stream.status_code,
        headers={**stream.headers, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        media_type=stream.content_type,
    )


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


@router.get("/by-task/{task_id}", response_model=ByTaskResponse)
def latest_by_task(
    task_id: UuidStr,
    service: Annotated[DownloadService, Depends(get_download_service)],
) -> dict:
    """Return the newest download snapshot for a parsed task.

    Success: ``200`` with the snapshot fields plus ``task_id``; no download
    exists for the task → ``400`` ``3001``. Used by the preview UI to
    re-attach to a download after a page reload (the session-local download
    list is empty then) and by the worker wiring to locate a task's row.
    """
    result = service.get_latest_by_task(task_id)
    if result is None:
        raise ApiError(
            CODE_BAD_REQUEST, CODE_TASK_NOT_FOUND, _MESSAGE_TASK_NOT_FOUND
        )
    remaining: float | None = None
    if (
        result.status is DownloadStatus.DOWNLOADING
        and result.speed is not None
        and result.speed > 0
        and result.total_bytes is not None
    ):
        remaining = max(
            result.total_bytes - (result.downloaded_bytes or 0), 0
        ) / result.speed
    return ok(
        data={
            "download_id": result.download_id,
            "task_id": result.task_id,
            "status": result.status.value,
            "progress": result.progress,
            "speed": result.speed,
            "downloaded_bytes": result.downloaded_bytes,
            "total_bytes": result.total_bytes,
            "remaining_time": remaining,
            "error_message": result.error_message,
        },
        message=_MESSAGE_PROGRESS_OK,
    )


@router.get("/file/{download_id}")
def download_file(
    download_id: UuidStr,
    service: Annotated[DownloadService, Depends(get_download_service)],
    token: str | None = Query(default=None),
) -> FileResponse:
    """Serve a completed bubble file behind a five-minute short-lived token.

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
    channel). The event queue is raced against the socket's receive, so a
    client that disconnects while idle releases its subscription at once. The
    handler never raises after ``accept`` — protocol problems (unknown/
    malformed id) become an ``error`` event followed by a close.
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
            # Race the event queue against the socket: if the client
            # disconnects while we are idle (no event in flight), the receive
            # task finishes with a disconnect and we release the subscription
            # immediately instead of waiting for the next publish to fail.
            get_task = asyncio.create_task(queue.get())
            recv_task = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                {get_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if recv_task in done:
                try:
                    recv_task.result()
                except (WebSocketDisconnect, RuntimeError):
                    break  # client gone; release the subscription
                # else: a client-sent message — the v1 protocol is server →
                # client only, so it is ignored.
            if get_task in done:
                event = get_task.result()
                try:
                    await websocket.send_json(event)
                except (WebSocketDisconnect, RuntimeError):
                    break  # send failed; the finally below unsubscribes
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

    ``progress`` for pending/downloading; ``complete`` for completed (with a
    fresh short-lived reusable ``download_url`` and its ``token_expire_at``);
    ``error``
    for failed/expired — one uniform error shape carrying ``code``/``message``
    plus the state fields (failed → 5002, expired → 5004).
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
        issued = service.issue_download_token(progress.download_id)
        data["download_url"] = (
            f"/api/download/file/{progress.download_id}?token={issued.token}"
        )
        data["token_expire_at"] = issued.expires_at.isoformat()
        return {"type": "complete", "data": data}
    if progress.status is DownloadStatus.EXPIRED:
        data["code"] = CODE_FILE_EXPIRED
        data["message"] = _MESSAGE_FILE_EXPIRED
        return {"type": "error", "data": data}
    if progress.status is DownloadStatus.FAILED:
        data["code"] = CODE_FILE_NOT_DOWNLOADED
        data["message"] = _MESSAGE_FILE_NOT_DOWNLOADED
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
