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
* **Same-origin video proxy (Task 2).** ``stream_video`` re-fetches the
  persisted ``metadata["video_url"]`` server-side (engine UA, Range
  passthrough, redirects followed) so xgplayer/flv.js get a same-origin,
  CORS-free, referer-free byte stream; the platform URL never reaches the
  browser. The upstream httpx client is owned by the caller via
  :class:`MediaStream.close`.
* **HLS is rejected by design.** m3u8 playlists are not proxied (segment
  rewriting is out of scope); the frontend falls back to the direct URL.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Callable, Iterator

import httpx
from sqlalchemy import Engine
from starlette.status import HTTP_400_BAD_REQUEST

from app.api.responses import CODE_BAD_REQUEST, CODE_TASK_NOT_FOUND, ApiError
from app.domain import MediaType, format_duration
from app.infrastructure.database import session_scope
from app.infrastructure.models import ParseTask

__all__ = ["MediaStream", "PreviewService"]

_MESSAGE_TASK_NOT_FOUND = "任务不存在 / Task not found"
_MESSAGE_NOT_VIDEO = "该任务不是视频 / Task is not a video"
_MESSAGE_NOT_IMAGE = "该任务不是图片 / Task is not an image"
_MESSAGE_NO_MEDIA_URL = "该任务没有可播放的媒体 / No playable media for this task"
_MESSAGE_NO_IMAGES = "该任务没有图片 / Task has no images"
_MESSAGE_IMAGE_INDEX = "图片序号无效 / Invalid image index"
_MESSAGE_UPSTREAM = "上游媒体获取失败 / Upstream media fetch failed"
_MESSAGE_HLS_UNSUPPORTED = "HLS流暂不支持代理播放 / HLS streams are not supported by the proxy"
_MESSAGE_IMAGE_TOO_LARGE = "图片过大，无法下载 / Image too large to download"
_MESSAGE_ALBUM_TOO_LARGE = "图集过大，无法打包 / Album too large to pack"

_UA = {"User-Agent": "Mozilla/5.0 (KoiFetch/0.1)"}
_STREAM_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_CHUNK_SIZE = 64 * 1024
_MAX_IMAGE_BYTES = 20 * 1024 * 1024    # 20 MB per image
_MAX_ALBUM_BYTES = 200 * 1024 * 1024   # 200 MB total


@dataclass(frozen=True)
class MediaStream:
    """A proxied upstream media response (video passthrough).

    ``chunks`` yields the upstream body; the caller MUST call ``close`` when
    finished (it releases the upstream httpx client). ``headers`` carries the
    passthrough metadata the transport re-emits (Content-Type is also
    duplicated in ``content_type`` for convenience).
    """

    status_code: int
    content_type: str
    headers: dict[str, str]
    chunks: Iterator[bytes]
    close: Callable[[], None]


class PreviewService:
    """Load a parsed task and build its v1 single-media preview response."""

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        transport: httpx.BaseTransport | None = None,
        proxy: str | None = None,
    ) -> None:
        self._engine = engine
        # Test seam (mirrors EngineDownloaderAdapter): None = real network.
        self._transport = transport
        self._proxy = proxy

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

    def stream_video(self, task_id: str, range_header: str | None) -> MediaStream:
        """Proxy the task's recorded video URL with Range passthrough.

        Raises :class:`ApiError`: ``3001`` unknown task; ``400`` for a
        non-video task, a task with no ``video_url`` (stub-era rows), an HLS
        upstream (playlist proxying is out of scope), or an upstream HTTP or
        transport failure. The returned :class:`MediaStream` yields raw bytes;
        the caller owns ``close``.
        """
        url = self._video_url(task_id)
        kwargs: dict = {"timeout": _STREAM_TIMEOUT, "follow_redirects": True}
        if self._proxy:
            kwargs["proxy"] = self._proxy
        if self._transport is not None:
            kwargs["transport"] = self._transport
        request_headers = dict(_UA)
        if range_header:
            request_headers["Range"] = range_header

        client = httpx.Client(**kwargs)
        try:
            response = client.send(
                client.build_request("GET", url, headers=request_headers),
                stream=True,
            )
            if response.is_error:
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
                )
            content_type = response.headers.get(
                "content-type", "application/octet-stream"
            )
            # Lowercase a copy for HLS detection only; the original casing is
            # kept for the passthrough Content-Type header.
            if (
                "mpegurl" in content_type.lower()
                or url.split("?", 1)[0].lower().endswith(".m3u8")
            ):
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_HLS_UNSUPPORTED
                )
            headers = {"Accept-Ranges": "bytes", "Content-Type": content_type}
            if response.headers.get("content-length") is not None:
                headers["Content-Length"] = response.headers["content-length"]
            if response.headers.get("content-range") is not None:
                headers["Content-Range"] = response.headers["content-range"]
            return MediaStream(
                status_code=response.status_code,
                content_type=content_type,
                headers=headers,
                chunks=response.iter_bytes(chunk_size=_CHUNK_SIZE),
                close=lambda: (response.close(), client.close()),
            )
        except ApiError:
            client.close()
            raise
        except httpx.RequestError as exc:
            client.close()
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc

    def _video_url(self, task_id: str) -> str:
        """The task's playable video URL, or a typed :class:`ApiError`."""
        task = self._load_task(task_id)
        if task.media_type != MediaType.VIDEO:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_NOT_VIDEO
            )
        url = (task.metadata_ or {}).get("video_url")
        if not isinstance(url, str) or not url:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_NO_MEDIA_URL
            )
        return url

    def _load_task(self, task_id: str) -> ParseTask:
        with session_scope(self._engine) as session:
            task = session.get(ParseTask, task_id)
        if task is None:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_TASK_NOT_FOUND, _MESSAGE_TASK_NOT_FOUND
            )
        return task


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
