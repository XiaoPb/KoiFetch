"""Worker runtime (Task 11): atomic claiming and download execution.

The worker is the *execution* side of the download pipeline: it polls for
``pending`` tasks, atomically claims them (``pending -> downloading``), drives
the downloader adapter with a progress callback that persists byte-level
progress and publishes WebSocket events through the event hub, and records
either completion (bubble file written, short-lived file token issued) or failure
(retry budget, capped at :data:`MAX_RETRIES`).

Design decisions (stable contract for Tasks 12+):

* **Atomic claim on SQLite.** :func:`claim_pending_tasks` runs a single
  conditional ``UPDATE ... WHERE status = 'pending' ... RETURNING`` — the
  subquery picks the oldest ``limit`` pending rows, the outer guard only
  flips rows that are *still* pending, and ``RETURNING`` hands back exactly
  the ids this statement claimed. SQLite serializes writers, so two workers
  claiming at the same instant each claim a disjoint batch (the second one's
  subquery no longer sees the first one's rows). The ``pending -> downloading``
  edge of the domain graph is therefore enforced atomically at the SQL level
  (equivalent to ``transition`` + row lock).
* **The worker NEVER touches ``downloading`` rows it did not claim.** Recovery
  from a crashed worker is Task 12's cleanup pass (stale ``downloading`` →
  ``expired`` → re-download). Every terminal write here re-checks that the row
  is still ``DOWNLOADING`` and uses the domain :func:`transition` for state
  moves, so a row that left ``downloading`` mid-flight (e.g. swept to
  ``expired`` by cleanup) is left alone.
* **Sequential batch processing.** :func:`run_once` claims up to
  ``max_concurrent`` tasks and processes them one at a time in the calling
  thread. ``MAX_CONCURRENT`` is a **per-process** in-flight cap: ``N`` worker
  processes can therefore have up to ``N * max_concurrent`` tasks downloading
  at once (the claims themselves remain disjoint — SQLite serializes writers).
  A thread pool is deliberately avoided: SQLite writes stay single-connection
  (no session-per-thread juggling), the stub downloader is I/O-tiny, and
  progress callbacks are naturally ordered. A parallel engine can move the
  per-task execution to threads later behind the same interface.
* **Retry policy (documented): re-queue while under the cap, no backoff.** On
  failure the worker increments ``retry_count`` exactly once, then re-queues
  the task (``failed -> pending`` via the domain graph) while
  ``retry_count < MAX_RETRIES`` — so a task gets at most
  :data:`MAX_RETRIES` download attempts total. At the cap it stays ``failed``
  with ``error_message`` set. ``failed -> pending`` is always legal in the
  domain graph; the cap is this worker's policy layered on top. Retries are
  **immediate**: a re-queued row is eligible for the very next poll round —
  v1 deliberately has no backoff (the plan specifies only the cap); a delay
  can be layered onto the re-queue later if transient failures become common.
  Only the *terminal* failure publishes a WS ``error`` event; automatic
  retries are silent (the client reconciles via
  ``GET /api/download/progress/{id}``).
* **Progress persistence per callback.** The stub fires one callback per
  64 KiB chunk, so writing the row on every callback is cheap and keeps the
  HTTP progress endpoint live; a real engine can throttle on its own. The
  callback must not raise (adapter protocol), so DB/publish hiccups are logged
  and skipped — progress is lossy by design (see ``download_events.py``) and
  the terminal update below always reconciles the row.
* **Events via the in-process hub.** ``_publish`` runs
  ``await hub.publish(...)`` on a single per-thread event loop created lazily
  and reused across calls (never a fresh loop per progress chunk). The hub
  delivers to *this process's* WebSocket subscribers; a cross-process worker
  would need a different transport behind the same ``publish`` signature
  (documented in ``download_events.py``). The event payloads mirror the shapes
  ``app.api.download`` documents (``progress`` / ``complete`` / ``error``),
  with the ``complete`` event's ``download_url`` minted by
  :meth:`DownloadService.issue_download_token`. Terminal events are published
  only after the row update is committed, so a publish failure can never
  roll back a recorded completion/failure.
* **The downloader writes the bubble file.** ``DownloadRequest.target_path``
  is resolved here through the storage adapter (a contained absolute bubble
  path); the adapter writes bytes there and returns a ``COMPLETED``
  :class:`DownloadResult`. The request also carries ``source_url`` and the
  parse task's ``metadata_`` JSON so engine downloaders can resolve the media
  URL / song info the parser found; engine errors carry stable client
  messages by contract (see ``app.adapters.engine_errors``). The worker
  verifies the file exists, then records the path on the row. A failed
  attempt removes its partial target (best-effort) so a re-queue starts
  clean.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import zipfile

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import selectinload

from app.adapters.protocols import (
    DownloadRequest,
    DownloaderAdapter,
    OneTimeTokenProvider,
    StorageAdapter,
)
from app.adapters.engine_errors import EngineDownloadError
from app.api.responses import CODE_FILE_NOT_DOWNLOADED
from app.application.download_events import DownloadEventHub
from app.application.download_service import DownloadService, IssuedDownloadToken
from app.domain import (
    DownloadCommand,
    DownloadProgress,
    DownloadResult,
    DownloadStatus,
    AssetSelector,
    MediaType,
    transition,
)
from app.application.media_manifest import (
    ManifestError,
    load_manifest,
    validate_manifest_media_type,
)
from app.application.preview_service import _MAX_ALBUM_BYTES, _MAX_IMAGE_BYTES
from app.domain.paths import slugify
from app.infrastructure.database import session_scope
from app.infrastructure.models import DownloadTask

__all__ = [
    "MAX_RETRIES",
    "claim_pending_tasks",
    "run_once",
]

logger = logging.getLogger(__name__)

# Retry budget: a download gets at most this many total attempts (the first
# attempt plus MAX_RETRIES - 1 re-queues). The domain graph's failed -> pending
# edge is always legal; this constant is the worker's cap on top of it.
MAX_RETRIES = 3
_MAX_PACKAGE_MEMBER_BYTES = _MAX_IMAGE_BYTES
_MAX_PACKAGE_TOTAL_BYTES = _MAX_ALBUM_BYTES
_MAX_PACKAGE_MEMBERS = 128

# The WS error event mirrors app.api.download's documented failed shape
# (code 5002). Kept in sync by test_worker_retry.py's event-shape assertions.
_MESSAGE_FILE_NOT_DOWNLOADED = "文件未下载完成 / File not fully downloaded"

# Extension fallback per media type when the download row carries no format.
_DEFAULT_EXT_BY_TYPE = {
    MediaType.VIDEO: "mp4",
    MediaType.MUSIC: "mp3",
    MediaType.IMAGE: "jpg",
}

# Everything that is not a lowercase alphanumeric is stripped from an
# extension (mirrors domain.paths._clean_extension's charset).
_EXT_UNSAFE = re.compile(r"[^a-z0-9]+")


def claim_pending_tasks(engine: Engine, limit: int) -> list[str]:
    """Atomically claim up to ``limit`` pending tasks; return their download ids.

    A single conditional ``UPDATE ... RETURNING`` flips ``pending`` rows to
    ``downloading`` (oldest first) and returns exactly the ids this statement
    claimed. Rows in any other status — including ``downloading`` rows claimed
    by another worker or left stale by a crash — are never matched.
    """
    if limit < 1:
        return []
    candidate_ids = (
        select(DownloadTask.download_id)
        .where(DownloadTask.status == DownloadStatus.PENDING)
        .order_by(DownloadTask.created_at, DownloadTask.download_id)
        .limit(limit)
    )
    with session_scope(engine) as session:
        result = session.execute(
            update(DownloadTask)
            .where(
                DownloadTask.status == DownloadStatus.PENDING,
                DownloadTask.download_id.in_(candidate_ids),
            )
            .values(status=DownloadStatus.DOWNLOADING)
            .returning(DownloadTask.download_id)
        )
        return list(result.scalars())


def run_once(
    engine: Engine,
    downloader: DownloaderAdapter,
    storage: StorageAdapter,
    event_hub: DownloadEventHub,
    *,
    max_concurrent: int = 1,
    download_service: DownloadService | None = None,
    token_provider: OneTimeTokenProvider | None = None,
    max_retries: int = MAX_RETRIES,
) -> int:
    """Claim and execute one batch of pending downloads; return tasks handled.

    Claims up to ``max_concurrent`` tasks (the documented in-flight cap) and
    processes them sequentially (see the module docstring). ``download_service``
    is reused when given (production wires one in ``app.workers.main``);
    otherwise a service is built from ``token_provider``/``storage``/
    ``downloader`` on the same engine.
    """
    service = download_service or DownloadService(
        token_provider=token_provider,
        storage=storage,
        downloader=downloader,
        engine=engine,
    )
    claimed = claim_pending_tasks(engine, limit=max_concurrent)
    for download_id in claimed:
        try:
            _execute_download(
                engine, downloader, storage, event_hub, service,
                download_id, max_retries,
            )
        except Exception:  # one bad task must not stall the batch
            logger.exception("unexpected error handling download %s", download_id)
    return len(claimed)


def _execute_download(
    engine: Engine,
    downloader: DownloaderAdapter,
    storage: StorageAdapter,
    event_hub: DownloadEventHub,
    service: DownloadService,
    download_id: str,
    max_retries: int,
) -> None:
    """Download one claimed task: build the request, run it, record the outcome."""
    with session_scope(engine) as session:
        row = session.scalars(
            select(DownloadTask)
            .options(selectinload(DownloadTask.parse_task))
            .where(DownloadTask.download_id == download_id)
        ).one_or_none()
        if row is None:
            logger.warning("claimed download %s vanished; skipping", download_id)
            return
        if row.status is not DownloadStatus.DOWNLOADING:
            logger.warning(
                "download %s left downloading (status=%s); skipping",
                download_id, row.status,
            )
            return
        media_type = row.parse_task.media_type
        try:
            selector = _selector_from_row(row)
        except Exception as exc:
            fallback_type = (
                MediaType.IMAGE if media_type is MediaType.LIVE_PHOTO else media_type
            )
            target = storage.resolve_bubble(
                fallback_type, _bubble_filename(row, fallback_type)
            )
            _record_failure(engine, event_hub, download_id, exc, max_retries, target)
            return
        storage_type = _storage_media_type(media_type, selector)
        target = storage.resolve_bubble(storage_type, _bubble_filename(row, storage_type))
        title = row.title
        source_url = row.parse_task.url
        try:
            command = DownloadCommand(
                task_id=row.task_id,
                format=row.format,
                quality=row.quality,
                asset_selector=selector,
            )
            effective_type, metadata = _metadata_for_selector(
                row.parse_task, selector, media_type
            )
        except Exception as exc:
            _record_failure(engine, event_hub, download_id, exc, max_retries, target)
            return

    request = DownloadRequest(
        command=command,
        download_id=download_id,
        target_path=target,
        title=title,
        media_type=effective_type,
        source_url=source_url,
        metadata=metadata,
        progress_callback=_progress_callback_for(engine, event_hub, download_id),
    )
    try:
        if selector is not None and selector.package is not None:
            result = _download_package(
                downloader,
                request,
                row.parse_task,
                selector,
                target,
            )
        else:
            result = downloader.download(request)
    except Exception as exc:  # the adapter raises on failure
        logger.warning("download %s failed: %s", download_id, exc)
        _record_failure(engine, event_hub, download_id, exc, max_retries, target)
        return
    if not target.is_file():
        # The adapter reported COMPLETED but left no file: treat as a failure
        # (the protocol contract is that the file exists at target_path).
        error = RuntimeError(f"downloader reported success but no file at {target}")
        logger.warning("download %s: %s", download_id, error)
        _record_failure(engine, event_hub, download_id, error, max_retries, target)
        return
    _record_completion(engine, service, event_hub, download_id, result, target)


def _selector_from_row(row: DownloadTask) -> AssetSelector | None:
    """Strictly reconstruct a persisted selector; never log its raw payload."""
    if row.asset_selector is None:
        return None
    try:
        return AssetSelector.model_validate(row.asset_selector)
    except Exception as exc:
        raise EngineDownloadError("下载失败 / Download failed") from exc


def _metadata_for_selector(
    parse_task, selector: AssetSelector | None, media_type: MediaType
) -> tuple[MediaType, dict]:
    """Resolve a persisted selector to exact downloader metadata."""
    metadata = dict(parse_task.metadata_ or {})
    if selector is None or selector.package is not None or selector.kind == "music":
        return media_type, metadata
    try:
        manifest = validate_manifest_media_type(load_manifest(parse_task), media_type)
        resource = _resource_for_selector(manifest, selector, media_type)
    except (ManifestError, IndexError, TypeError, ValueError) as exc:
        raise EngineDownloadError("下载失败 / Download failed") from exc
    if selector.kind in {"video", "live_motion"}:
        metadata["video_url"] = str(resource.url)
        return MediaType.VIDEO, metadata
    metadata["images"] = [{"url": str(resource.url)}]
    metadata.pop("video_url", None)
    return MediaType.IMAGE, metadata


def _storage_media_type(media_type: MediaType, selector: AssetSelector | None) -> MediaType:
    """Map live-photo selections to the configured image/video storage roots."""
    if selector is None:
        return media_type
    if selector.kind == "live_motion":
        return MediaType.VIDEO
    if selector.kind == "live_image" or selector.package == "live_zip":
        return MediaType.IMAGE
    return media_type


def _resource_for_selector(manifest, selector: AssetSelector, media_type: MediaType):
    expected_kind = {
        MediaType.VIDEO: "video",
        MediaType.IMAGE: "image",
        MediaType.LIVE_PHOTO: {"live_image", "live_motion"},
    }[media_type]
    if manifest.kind != {
        MediaType.VIDEO: "video",
        MediaType.IMAGE: "image_album",
        MediaType.LIVE_PHOTO: "live_photo",
    }[media_type]:
        raise ValueError("manifest kind mismatch")
    if selector.kind == "video":
        if expected_kind != "video":
            raise ValueError("selector kind mismatch")
        return manifest.videos[selector.index]
    if selector.kind == "image":
        if expected_kind != "image":
            raise ValueError("selector kind mismatch")
        return manifest.images[selector.index]
    if selector.kind not in {"live_image", "live_motion"} or not isinstance(expected_kind, set):
        raise ValueError("selector kind mismatch")
    pair = manifest.live_photos[selector.index]
    if selector.kind == "live_motion":
        if pair.motion is None:
            raise ValueError("live motion is missing")
        return pair.motion
    return pair.image


def _package_members(parse_task, selector: AssetSelector) -> list[tuple[str, object, MediaType]]:
    """Resolve package entries in deterministic manifest order."""
    try:
        manifest = validate_manifest_media_type(load_manifest(parse_task), parse_task.media_type)
        if selector.package == "album_zip":
            if selector.kind != "image":
                raise ValueError("package selector mismatch")
            return [
                (f"image-{index:04d}.{resource.format}", resource, MediaType.IMAGE)
                for index, resource in enumerate(manifest.images, 1)
            ]
        if selector.package == "live_zip":
            if selector.kind != "live_image":
                raise ValueError("package selector mismatch")
            members = []
            for index, pair in enumerate(manifest.live_photos, 1):
                members.append((f"live-{index:04d}-image.{pair.image.format}", pair.image, MediaType.IMAGE))
                if pair.motion is not None:
                    members.append((f"live-{index:04d}-motion.{pair.motion.format}", pair.motion, MediaType.VIDEO))
            return members
    except (ManifestError, IndexError, TypeError, ValueError) as exc:
        raise EngineDownloadError("下载失败 / Download failed") from exc
    raise EngineDownloadError("下载失败 / Download failed")


def _download_package(
    downloader: DownloaderAdapter,
    request: DownloadRequest,
    parse_task,
    selector: AssetSelector,
    target: Path,
) -> DownloadResult:
    """Fetch package members and atomically materialize one deterministic ZIP."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{request.download_id[:8]}-", dir=target.parent))
    zip_path = temp_dir / "package.zip"
    try:
        members = _package_members(parse_task, selector)
        if len(members) > _MAX_PACKAGE_MEMBERS:
            raise EngineDownloadError("下载失败 / Download failed")
        names = [name for name, _, _ in members]
        if len(names) != len(set(names)) or any(
            Path(name).name != name or name in {".", ".."} for name in names
        ):
            raise EngineDownloadError("下载失败 / Download failed")
        total_written = 0
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, resource, effective_type in members:
                member_path = temp_dir / name
                command = request.command.model_copy(
                    update={
                        "format": resource.format,
                        "quality": resource.quality,
                    }
                )
                metadata = dict(request.metadata)
                if effective_type is MediaType.VIDEO:
                    metadata["video_url"] = str(resource.url)
                    metadata.pop("images", None)
                else:
                    metadata["images"] = [{"url": str(resource.url)}]
                    metadata.pop("video_url", None)

                def guard_progress(progress: DownloadProgress) -> None:
                    downloaded = progress.downloaded_bytes
                    if downloaded is not None and (
                        downloaded > _MAX_PACKAGE_MEMBER_BYTES
                        or downloaded > _MAX_PACKAGE_TOTAL_BYTES - total_written
                    ):
                        raise EngineDownloadError("下载失败 / Download failed")

                member_request = DownloadRequest(
                    command=command,
                    download_id=request.download_id,
                    target_path=member_path,
                    title=request.title,
                    media_type=effective_type,
                    source_url=request.source_url,
                    metadata=metadata,
                    progress_callback=guard_progress,
                )
                downloader.download(member_request)
                if not member_path.is_file():
                    raise EngineDownloadError("下载失败 / Download failed")
                member_size = member_path.stat().st_size
                if (
                    member_size > _MAX_PACKAGE_MEMBER_BYTES
                    or total_written + member_size > _MAX_PACKAGE_TOTAL_BYTES
                ):
                    raise EngineDownloadError("下载失败 / Download failed")
                total_written += member_size
                archive.write(member_path, arcname=name)
        os.replace(zip_path, target)
        size = target.stat().st_size
        return DownloadResult(
            download_id=request.download_id,
            task_id=request.command.task_id,
            title=request.title,
            media_type=request.media_type,
            format="zip",
            quality=None,
            status=DownloadStatus.COMPLETED,
            progress=100.0,
            speed=None,
            total_bytes=size,
            downloaded_bytes=size,
            retry_count=0,
            error_message=None,
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _progress_callback_for(
    engine: Engine, event_hub: DownloadEventHub, download_id: str
) -> Callable[[DownloadProgress], None]:
    """Return the progress callback the downloader invokes per chunk.

    Persists the snapshot to the row and publishes a WS ``progress`` event.
    Must never raise (adapter protocol): DB/publish hiccups are logged and
    skipped — progress is lossy by design and the terminal update reconciles.
    """

    def callback(progress: DownloadProgress) -> None:
        try:
            with session_scope(engine) as session:
                row = session.get(DownloadTask, progress.download_id)
                if row is None:
                    logger.warning(
                        "progress for unknown download %s; skipping",
                        progress.download_id,
                    )
                    return
                row.progress = progress.progress
                row.speed = progress.speed
                row.downloaded_bytes = progress.downloaded_bytes
                row.total_bytes = progress.total_bytes
        except Exception:  # see docstring: a progress hiccup must not abort the transfer
            logger.exception(
                "failed to persist progress for %s", progress.download_id
            )
            return
        try:
            _publish(event_hub, download_id, _progress_event(progress))
        except Exception:  # see docstring: progress is lossy by design
            logger.exception("failed to publish progress for %s", download_id)

    return callback


def _record_completion(
    engine: Engine,
    service: DownloadService,
    event_hub: DownloadEventHub,
    download_id: str,
    result: DownloadResult,
    target: Path,
) -> None:
    """Mark the row completed, then publish the ``complete`` event with a token."""
    with session_scope(engine) as session:
        row = session.get(DownloadTask, download_id)
        if row is None:
            return
        if row.status is not DownloadStatus.DOWNLOADING:
            logger.warning(
                "download %s left downloading before completion (status=%s); "
                "abandoning", download_id, row.status,
            )
            return
        row.status = transition(row.status, DownloadStatus.COMPLETED)
        row.progress = 100.0
        row.speed = result.speed
        row.total_bytes = result.total_bytes
        row.downloaded_bytes = result.downloaded_bytes
        row.bubble_path = str(target)
        row.completed_at = datetime.now(timezone.utc)
        row.error_message = None
    issued = service.issue_download_token(download_id)
    _publish(event_hub, download_id, _complete_event(result, issued))


def _record_failure(
    engine: Engine,
    event_hub: DownloadEventHub,
    download_id: str,
    error: Exception,
    max_retries: int,
    target: Path | None,
) -> None:
    """Record one failed attempt: increment the budget, re-queue or fail.

    Re-queues (``failed -> pending`` via the domain graph) while
    ``retry_count < max_retries``, resetting progress/bytes and clearing
    ``error_message`` for a fresh attempt; at the cap the row stays ``failed``
    with ``error_message`` set. The WS ``error`` event for a terminal failure
    is published *after* the row update commits — a publish failure must never
    roll back the recorded state (same rule as :func:`_record_completion`).
    The partial target file is removed best-effort so a re-queue starts clean.

    Message contract: engine adapters raise typed ``EngineError`` subclasses
    (``app.adapters.engine_errors``) whose ``str()`` is a stable bilingual
    user-facing message, so ``error_message = str(error)`` is already
    client-safe for real engines. Untyped exceptions (genuine bugs) still
    forward raw exception text; those are sanitized upstream where possible
    rather than here.

    v1 decision (documented): the row's ``error_message`` feeds the terminal
    WS ``error`` event (see :func:`_failed_event`) and the progress endpoint,
    so whatever an adapter raises ends up client-visible — which is why
    engine adapters are contractually required to raise mapped error types.
    """
    failure_event: dict | None = None
    with session_scope(engine) as session:
        row = session.get(DownloadTask, download_id)
        if row is None:
            return
        if row.status is not DownloadStatus.DOWNLOADING:
            # Not ours to fail (e.g. cleanup swept it to expired mid-flight):
            # leave the row alone — the domain graph forbids downloading ->
            # pending and recovery is Task 12's job.
            logger.warning(
                "download %s left downloading before failure handling (status=%s); "
                "leaving untouched", download_id, row.status,
            )
            return
        row.retry_count += 1
        row.error_message = str(error)
        if row.retry_count >= max_retries:
            row.status = transition(row.status, DownloadStatus.FAILED)
            failure_event = _failed_event(row)
        else:
            row.status = transition(row.status, DownloadStatus.FAILED)
            row.status = transition(DownloadStatus.FAILED, DownloadStatus.PENDING)
            row.progress = 0.0
            row.speed = None
            row.downloaded_bytes = None
            row.total_bytes = None
            # A re-queued pending row returns clean: the previous attempt's
            # error is not shown as a stale error on the progress endpoint.
            row.error_message = None
    if failure_event is not None:
        _publish(event_hub, download_id, failure_event)
    if target is not None:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove partial target %s", target, exc_info=True)


# ---------------------------------------------------------------------------
# Bubble filename + WS event builders
# ---------------------------------------------------------------------------


def _bubble_filename(row: DownloadTask, media_type: MediaType) -> str:
    """A safe, deterministic bubble basename for a download row.

    ``<title-slug>_<download-id-prefix>.<ext>`` — the download id suffix keeps
    concurrent variants of the same task (different format/quality) from
    colliding on one bubble path, and every component is slugified so hostile
    titles/formats cannot escape the bubble root.
    """
    title = row.title or "untitled"
    return f"{slugify(title)}_{row.download_id[:8]}.{_extension_for(row, media_type)}"


def _extension_for(row: DownloadTask, media_type: MediaType) -> str:
    """A sanitized file extension for a download row.

    Takes the segment after the last dot of the row's format (so ``mp4.webm``
    yields ``webm``, not the slugified ``mp4-webm``), restricted to lowercase
    alphanumerics; falls back to the media type's default when the format is
    blank or yields nothing usable. An unknown media type raises a clear
    ``ValueError`` instead of a bare ``KeyError``.
    """
    ext = (row.format or "").strip().lstrip(".")
    if ext:
        cleaned = _EXT_UNSAFE.sub("", ext.rsplit(".", 1)[-1].lower())
        if cleaned:
            return cleaned
    try:
        return _DEFAULT_EXT_BY_TYPE[media_type]
    except KeyError:
        raise ValueError(f"no default extension for media type {media_type!r}") from None


def _progress_event(progress: DownloadProgress) -> dict:
    """The WS ``progress`` event, mirroring app.api.download's documented shape."""
    remaining: float | None = None
    if (
        progress.speed
        and progress.speed > 0
        and progress.total_bytes is not None
    ):
        remaining = max(
            progress.total_bytes - (progress.downloaded_bytes or 0), 0
        ) / progress.speed
    return {
        "type": "progress",
        "data": {
            "download_id": progress.download_id,
            "status": progress.status.value,
            "progress": progress.progress,
            "speed": progress.speed,
            "downloaded_bytes": progress.downloaded_bytes,
            "total_bytes": progress.total_bytes,
            "remaining_time": remaining,
        },
    }


def _complete_event(result: DownloadResult, issued: IssuedDownloadToken) -> dict:
    """The WS ``complete`` event with a fresh reusable download link."""
    return {
        "type": "complete",
        "data": {
            "download_id": result.download_id,
            "status": DownloadStatus.COMPLETED.value,
            "progress": result.progress,
            "speed": result.speed,
            "downloaded_bytes": result.downloaded_bytes,
            "total_bytes": result.total_bytes,
            "remaining_time": None,
            "download_url": (
                f"/api/download/file/{result.download_id}?token={issued.token}"
            ),
            "token_expire_at": issued.expires_at.isoformat(),
        },
    }


def _failed_event(row: DownloadTask) -> dict:
    """The WS ``error`` event for a terminal failure (mirrors app.api.download)."""
    return {
        "type": "error",
        "data": {
            "code": CODE_FILE_NOT_DOWNLOADED,
            "message": _MESSAGE_FILE_NOT_DOWNLOADED,
            "download_id": row.download_id,
            "status": DownloadStatus.FAILED.value,
            "progress": row.progress or 0.0,
            "speed": row.speed,
            "downloaded_bytes": row.downloaded_bytes,
            "total_bytes": row.total_bytes,
            "remaining_time": None,
            "error_message": row.error_message,
        },
    }


# One reusable event loop per thread (the worker thread creates it lazily on
# the first publish and reuses it for every subsequent one — progress chunks
# must not spin up a fresh loop each time). `run_until_complete` may be called
# repeatedly on a non-running loop; the worker's sync polling thread never has
# a running loop of its own. The hub delivers thread-safely to each
# subscriber's own loop, so the loop here is only a transport shim.
_loop_local = threading.local()


def _publish(event_hub: DownloadEventHub, download_id: str, event: dict) -> None:
    """Publish a hub event from the sync worker thread on a reused loop."""
    loop = getattr(_loop_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _loop_local.loop = loop
    loop.run_until_complete(event_hub.publish(download_id, event))
