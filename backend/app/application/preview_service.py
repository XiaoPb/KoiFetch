"""Preview use cases (Task 8): single-media preview metadata + media proxying.

Sits in the application layer between the API transport (``app.api.preview``)
and persistence: it loads a :class:`ParseTask` by id and builds a safe
preview response the frontend can render. The metadata preview (``preview``)
is derived entirely from the ORM row and its persisted ``metadata`` JSON —
no file store is touched — and exposes a ``streams`` ladder (quality for
video, bitrate for music, none for images). Task 2+ added real media
serving on top: ``stream_video`` proxies the task's recorded ``video_url``
(same-origin, Range passthrough), and ``image_bytes``/``album_zip`` serve
album images individually or as a ZIP attachment.

Design decisions (stable contract for Task 9+):

* **Metadata preview is safe and store-free.** The ``preview`` response is
  derived entirely from the ORM row and its persisted ``metadata`` JSON, so
  it can never leak storage paths; the media-proxy methods (Task 2+) re-fetch
  the engine-persisted URLs server-side, never local files.
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
import logging
import re
import zipfile
from dataclasses import dataclass
from typing import Callable, Iterator

import httpx
from sqlalchemy import Engine
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from app.adapters.safe_upstream import (
    SafeUpstreamClient,
    UnsafeUpstreamUrl,
    UpstreamProtocolError,
    UpstreamTooLarge,
)
from app.api.responses import CODE_BAD_REQUEST, CODE_TASK_NOT_FOUND, ApiError
from app.application.media_manifest import (
    ManifestError,
    load_manifest,
    public_cover,
    public_manifest,
)
from app.domain import MediaManifest, MediaResource, MediaType, format_duration
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
_MESSAGE_MANIFEST_MISSING = "媒体清单不存在 / Media manifest is missing"
_MESSAGE_MANIFEST_INVALID = "媒体清单无效 / Invalid media manifest"
_MESSAGE_RESOURCE_KIND = "媒体资源类型无效 / Invalid media resource kind"
_MESSAGE_RESOURCE_INDEX = "媒体资源序号无效 / Invalid media resource index"
_MESSAGE_RESOURCE_SIDE = "Live Photo 资源类型无效 / Invalid Live Photo resource side"
_MESSAGE_RESOURCE_MISSING = "媒体资源不存在 / Media resource not found"

_UA = {"User-Agent": "Mozilla/5.0 (KoiFetch/0.1)"}
_MAX_IMAGE_BYTES = 20 * 1024 * 1024    # 20 MB per image
_MAX_ALBUM_BYTES = 200 * 1024 * 1024   # 200 MB total
_MAX_PROXY_BYTES = 200 * 1024 * 1024   # SafeUpstreamClient default cap

_EXT_UNSAFE = re.compile(r"[^a-z0-9]+")
_MEDIA_CACHE_CONTROL = "private, no-store"

logger = logging.getLogger(__name__)


def _extension_of(url: str) -> str:
    """Lowercase alnum extension of the last path segment (default ``jpg``)."""
    last = url.split("?", 1)[0].rsplit("/", 1)[-1]
    dot = last.rfind(".")
    if dot == -1 or not last[dot + 1 :]:
        return "jpg"
    cleaned = _EXT_UNSAFE.sub("", last[dot + 1 :].lower())
    return cleaned or "jpg"


def _image_content_type(ext: str) -> str:
    return {
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }.get(ext, "image/jpeg")


def _content_type_for_format(media_format: str) -> str:
    normalized = media_format.lower()
    if normalized in {"jpg", "jpeg"}:
        return "image/jpeg"
    if normalized == "png":
        return "image/png"
    if normalized == "gif":
        return "image/gif"
    if normalized == "webp":
        return "image/webp"
    if normalized in {"mp4", "m4v"}:
        return "video/mp4"
    if normalized == "webm":
        return "video/webm"
    return "application/octet-stream"


def _safe_media_headers(source: dict[str, str], content_type: str) -> dict[str, str]:
    """Keep only response headers useful for media playback and caching."""
    allowed = {
        "accept-ranges",
        "cache-control",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
    }
    headers = {
        name.title(): value
        for name, value in source.items()
        if name.lower() in allowed and isinstance(value, str)
    }
    headers["Content-Type"] = content_type
    # Manifest resources may contain signed URLs; never let an upstream
    # public/cache directive make a same-origin response shareable.
    headers["Cache-Control"] = _MEDIA_CACHE_CONTROL
    return headers


def _slug(text: str) -> str:
    """ASCII-safe filename slug (header safety); falls back to ``album``."""
    slug = _EXT_UNSAFE.sub("-", (text or "").lower()).strip("-")[:40]
    return slug or "album"


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
        upstream: SafeUpstreamClient | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._engine = engine
        # ``transport`` remains a deterministic test seam for existing tests;
        # production callers inject the shared SafeUpstreamClient instead.
        if upstream is not None:
            self._upstream = upstream
        elif transport is not None:
            self._upstream = SafeUpstreamClient(
                resolver=lambda host, port: ["93.184.216.34"],
                transport=transport,
            )
        else:
            self._upstream = SafeUpstreamClient()
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

    def stream_resource(
        self,
        task_id: str,
        kind: str,
        index: int,
        *,
        side: str | None = None,
        range_header: str | None = None,
    ) -> MediaStream:
        """Proxy one validated manifest resource without exposing its URL."""
        resource = self._resolve_resource(task_id, kind, index, side)
        try:
            stream = self._upstream.stream(
                str(resource.url),
                range_header=range_header,
                max_bytes=(
                    _MAX_IMAGE_BYTES
                    if kind == "image" or side == "image"
                    else _MAX_PROXY_BYTES
                ),
            )
            if stream.status_code >= 400:
                stream.close()
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
                )
            content_type = stream.content_type or _content_type_for_format(
                resource.format
            )
            headers = _safe_media_headers(stream.headers, content_type)
            return MediaStream(
                status_code=stream.status_code,
                content_type=content_type,
                headers=headers,
                chunks=stream.chunks,
                close=stream.close,
            )
        except ApiError:
            raise
        except (UnsafeUpstreamUrl, UpstreamTooLarge, UpstreamProtocolError) as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc
        except Exception:
            logger.exception(
                "unexpected media proxy failure task_id=%s kind=%s index=%s side=%s",
                task_id,
                kind,
                index,
                side,
            )
            raise

    def head_resource(
        self,
        task_id: str,
        kind: str,
        index: int,
        *,
        side: str | None = None,
        range_header: str | None = None,
    ) -> tuple[int, dict[str, str]]:
        """Return safe upstream headers for a resource HEAD request."""
        resource = self._resolve_resource(task_id, kind, index, side)
        try:
            upstream_headers = dict(_UA)
            if range_header is not None:
                upstream_headers["Range"] = range_header
            response = self._upstream.head(
                str(resource.url),
                headers=upstream_headers,
                max_bytes=(
                    _MAX_IMAGE_BYTES
                    if kind == "image" or side == "image"
                    else _MAX_PROXY_BYTES
                ),
            )
            if response.status_code >= 400:
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
                )
            content_type = response.headers.get(
                "content-type"
            ) or _content_type_for_format(resource.format)
            return response.status_code, _safe_media_headers(
                dict(response.headers), content_type
            )
        except ApiError:
            raise
        except (UnsafeUpstreamUrl, UpstreamTooLarge, UpstreamProtocolError) as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc
        except Exception:
            logger.exception(
                "unexpected media HEAD failure task_id=%s kind=%s index=%s side=%s",
                task_id,
                kind,
                index,
                side,
            )
            raise

    def _resolve_resource(
        self, task_id: str, kind: str, index: int, side: str | None
    ) -> MediaResource:
        task = self._load_task(task_id)
        try:
            manifest = load_manifest(task)
        except ManifestError as exc:
            message = (
                str(exc)
                if str(exc) in {_MESSAGE_MANIFEST_MISSING, _MESSAGE_MANIFEST_INVALID}
                else _MESSAGE_MANIFEST_INVALID
            )
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, message) from exc
        expected_type = {
            "video": MediaType.VIDEO,
            "image_album": MediaType.IMAGE,
            "live_photo": MediaType.LIVE_PHOTO,
        }[manifest.kind]
        if task.media_type != expected_type:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_MANIFEST_INVALID
            )
        if kind not in {"video", "image", "live"}:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_RESOURCE_KIND
            )
        if not isinstance(index, int) or index < 0:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_RESOURCE_INDEX
            )
        if kind == "video":
            if (
                manifest.kind != "video"
                or index >= len(manifest.videos)
                or side is not None
            ):
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_RESOURCE_INDEX
                )
            return manifest.videos[index]
        if kind == "image":
            if (
                manifest.kind != "image_album"
                or index >= len(manifest.images)
                or side is not None
            ):
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_RESOURCE_INDEX
                )
            return manifest.images[index]
        if manifest.kind != "live_photo" or index >= len(manifest.live_photos):
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_RESOURCE_INDEX)
        if side == "image":
            return manifest.live_photos[index].image
        if side == "motion":
            motion = manifest.live_photos[index].motion
            if motion is None:
                raise ApiError(
                    HTTP_404_NOT_FOUND,
                    HTTP_404_NOT_FOUND,
                    _MESSAGE_RESOURCE_MISSING,
                )
            return motion
        raise ApiError(
            HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_RESOURCE_SIDE
        )

    def stream_video(self, task_id: str, range_header: str | None) -> MediaStream:
        """Proxy the task's recorded video URL with Range passthrough.

        Raises :class:`ApiError`: ``3001`` unknown task; ``400`` for a
        non-video task, a task with no ``video_url`` (stub-era rows), an HLS
        upstream (playlist proxying is out of scope), or an upstream HTTP or
        transport failure. The returned :class:`MediaStream` yields raw bytes;
        the caller owns ``close``.
        """
        url = self._video_url(task_id)
        try:
            stream = self._upstream.stream(url, range_header=range_header)
            if stream.status_code >= 400:
                stream.close()
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
                )
            content_type = stream.content_type or "application/octet-stream"
            # Lowercase a copy for HLS detection only; the original casing is
            # kept for the passthrough Content-Type header.
            if (
                "mpegurl" in content_type.lower()
                or url.split("?", 1)[0].lower().endswith(".m3u8")
            ):
                stream.close()
                raise ApiError(
                    HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_HLS_UNSUPPORTED
                )
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Type": content_type,
                "Cache-Control": _MEDIA_CACHE_CONTROL,
            }
            for source_name, output_name in (
                ("content-length", "Content-Length"),
                ("content-range", "Content-Range"),
            ):
                if source_name in stream.headers:
                    headers[output_name] = stream.headers[source_name]
            return MediaStream(
                status_code=stream.status_code,
                content_type=content_type,
                headers=headers,
                chunks=stream.chunks,
                close=stream.close,
            )
        except ApiError:
            raise
        except (UnsafeUpstreamUrl, UpstreamTooLarge, UpstreamProtocolError) as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc
        except Exception:
            logger.exception("unexpected legacy media proxy failure task_id=%s", task_id)
            raise

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

    def image_bytes(self, task_id: str, index: int) -> tuple[bytes, str, str]:
        """Fetch one album image: ``(body, content_type, attachment_filename)``.

        ``index`` is validated against the persisted album; ``3001`` unknown
        task, ``400`` non-image task / no images / out-of-range index /
        oversized image / upstream failure.
        """
        urls = self._album_urls(task_id)
        if not 0 <= index < len(urls):
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_IMAGE_INDEX
            )
        url = urls[index]
        body = self._fetch_bytes(url)
        if len(body) > _MAX_IMAGE_BYTES:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_IMAGE_TOO_LARGE
            )
        ext = _extension_of(url)
        return body, _image_content_type(ext), f"image-{index + 1:04d}.{ext}"

    def album_zip(self, task_id: str) -> tuple[bytes, str]:
        """Bundle every album image into an in-memory ZIP.

        Returns ``(zip_bytes, attachment_filename)``. Size guard: any single
        image over ``_MAX_IMAGE_BYTES`` or a total over ``_MAX_ALBUM_BYTES``
        aborts with a typed ``400`` (never a half-built archive — the buffer
        is only returned after all entries are written).
        """
        urls, title = self._album(task_id)
        buffer = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, url in enumerate(urls):
                body = self._fetch_bytes(url)
                if len(body) > _MAX_IMAGE_BYTES or total + len(body) > _MAX_ALBUM_BYTES:
                    raise ApiError(
                        HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_ALBUM_TOO_LARGE
                    )
                total += len(body)
                archive.writestr(f"image-{index + 1:04d}.{_extension_of(url)}", body)
        slug = _slug(title)
        return buffer.getvalue(), f"{slug}-{len(urls)}-images.zip"

    def _album_urls(self, task_id: str) -> list[str]:
        return self._album(task_id)[0]

    def _album(self, task_id: str) -> tuple[list[str], str]:
        """The task's album image URLs plus its title, or a typed error."""
        task = self._load_task(task_id)
        if task.media_type != MediaType.IMAGE:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_NOT_IMAGE
            )
        images = (task.metadata_ or {}).get("images") or []
        urls = [
            img["url"]
            for img in images
            if isinstance(img, dict) and isinstance(img.get("url"), str)
        ]
        if not urls:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_NO_IMAGES
            )
        return urls, task.title

    def _fetch_bytes(self, url: str) -> bytes:
        try:
            response = self._upstream.open(url, headers=_UA)
            response.raise_for_status()
            return response.content
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_UPSTREAM
            ) from exc


def _build_preview(task: ParseTask) -> dict:
    """Map a :class:`ParseTask` row to the v1 preview response payload."""
    metadata = task.metadata_
    if not isinstance(metadata, dict):
        raise ApiError(
            HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_MANIFEST_INVALID
        )
    manifest = None
    if metadata.get("manifest") is not None:
        try:
            manifest = load_manifest(task)
        except ManifestError as exc:
            message = (
                str(exc)
                if str(exc) in {_MESSAGE_MANIFEST_MISSING, _MESSAGE_MANIFEST_INVALID}
                else _MESSAGE_MANIFEST_INVALID
            )
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, message) from exc
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

    payload = {
        "task_id": task.task_id,
        "preview_type": task.media_type.value,
        "url": task.url,
        "platform": task.platform,
        "title": task.title,
        "cover": public_cover(task.task_id, manifest) if manifest else None,
        "duration": (
            format_duration(task.duration) if task.duration is not None else None
        ),
        "format": task.format,
        "file_size_mb": metadata.get("file_size_mb"),
        "available_qualities": qualities,
        "available_bitrates": bitrates,
        "streams": streams,
    }
    if manifest is not None:
        payload["manifest"] = public_manifest(task.task_id, manifest)
    return payload
