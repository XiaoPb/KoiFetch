"""Prepare one parsed asset for a direct or staged transfer.

The service is deliberately a preparation boundary: direct responses contain
only an opaque same-origin route and staged responses contain only the
existing download task identifier.  Persisted manifest URLs never cross this
boundary.  Staged selectors are persisted as a strict compatibility seam for
the worker's exact-resource and package execution path.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote, urlencode, urlsplit

from pydantic import ValidationError
from sqlalchemy import Engine
from starlette.status import HTTP_400_BAD_REQUEST

from app.api.responses import CODE_BAD_REQUEST, CODE_TASK_NOT_FOUND, ApiError
from app.adapters.safe_upstream import SafeUpstreamClient, UpstreamStream
from app.application.download_service import DownloadService
from app.application.media_manifest import (
    ManifestError,
    load_manifest,
    validate_manifest_media_type,
)
from app.domain import (
    AssetSelector,
    DirectTransfer,
    MediaManifest,
    MediaResource,
    MediaType,
    PreparedTransfer,
    StagedTransfer,
)
from app.domain.paths import safe_attachment_filename
from app.infrastructure.database import session_scope
from app.infrastructure.models import ParseTask

__all__ = ["TransferService"]

_MESSAGE_ASSET_INVALID = "媒体资源无效 / Invalid media asset"
_MESSAGE_MANIFEST_MISSING = "媒体清单不存在 / Media manifest is missing"
_MESSAGE_MANIFEST_INVALID = "媒体清单无效 / Invalid media manifest"
_MESSAGE_TASK_NOT_FOUND = "任务不存在 / Task not found"


class TransferService:
    """Resolve a selected manifest resource and choose direct or staged."""

    def __init__(
        self,
        download_service: DownloadService,
        *,
        engine: Engine | None = None,
        upstream: SafeUpstreamClient | None = None,
    ) -> None:
        self._download_service = download_service
        self._engine = engine
        self._upstream = upstream or SafeUpstreamClient()

    def stream_direct(
        self,
        task_id: str,
        selector: AssetSelector,
        range_header: str | None = None,
    ) -> tuple[UpstreamStream, str]:
        """Stream one non-package resource through the SSRF-safe adapter."""
        with session_scope(self._engine) as session:
            task = session.get(ParseTask, task_id)
        if task is None:
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_TASK_NOT_FOUND, _MESSAGE_TASK_NOT_FOUND)
        if selector.package is not None:
            raise self._asset_error()
        if task.media_type == MediaType.MUSIC:
            # Music imports persist musicdl ``song_info`` rather than a
            # MediaManifest. ``prepare()`` already vetted this asset as
            # direct-eligible (HTTP protocol, non-streaming), so mirror that
            # lookup here — otherwise prepare returns a direct URL that
            # stream_direct then refuses to serve (HTTP 400).
            if selector.kind != "music" or selector.index != 0:
                raise self._asset_error()
            metadata = task.metadata_ if isinstance(task.metadata_, dict) else {}
            song_info = metadata.get("song_info") if isinstance(metadata, dict) else None
            if not isinstance(song_info, dict):
                raise self._asset_error()
            resource = _music_resource(task, song_info)
            if resource is None:
                raise self._asset_error()
        else:
            resource = self._resolve_resource(task.media_type, self._load_manifest(task), selector)
        if _is_streaming(resource):
            raise self._asset_error()
        try:
            return self._upstream.stream(str(resource.url), range_header=range_header), safe_attachment_filename(task.title, resource.format)
        except Exception as exc:
            if isinstance(exc, ApiError):
                raise
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_ASSET_INVALID) from exc

    def prepare(
        self,
        task_id: str,
        selector: AssetSelector,
        force_staged: bool = False,
    ) -> PreparedTransfer:
        """Prepare an exact selected resource without exposing its upstream URL."""
        try:
            selected = (
                selector
                if isinstance(selector, AssetSelector)
                else AssetSelector.model_validate(selector)
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_ASSET_INVALID
            ) from exc

        with session_scope(self._engine) as session:
            task = session.get(ParseTask, task_id)
        if task is None:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_TASK_NOT_FOUND, _MESSAGE_TASK_NOT_FOUND
            )

        if task.media_type == MediaType.MUSIC:
            # Music imports persist musicdl ``song_info`` rather than a
            # MediaManifest.  The existing engine downloader consumes that
            # private metadata from the task.  A plain HTTP song can use the
            # same-origin direct route; other protocols remain staged.
            if selected.kind != "music" or selected.index != 0 or selected.package is not None:
                raise self._asset_error()
            metadata = task.metadata_ if isinstance(task.metadata_, dict) else {}
            song_info = metadata.get("song_info") if isinstance(metadata, dict) else None
            if not isinstance(song_info, dict):
                raise self._asset_error()
            protocol = song_info.get("protocol", "HTTP")
            if not isinstance(protocol, str):
                protocol = ""
            protocol = protocol.upper()
            if protocol == "HTTP":
                resource = _music_resource(task, song_info)
                if resource is None:
                    raise self._asset_error()
                if not force_staged and not _is_streaming(resource):
                    return DirectTransfer(
                        url=_direct_url(task_id, selected),
                        filename=safe_attachment_filename(task.title, resource.format),
                    )
            fmt = task.format or (
                song_info.get("ext") if isinstance(song_info, dict) else None
            )
            return self._stage(task_id, fmt, None, selected)

        if selected.kind == "music":
            raise self._asset_error()
        manifest = self._load_manifest(task)
        resource = self._resolve_resource(task.media_type, manifest, selected)
        if not force_staged and selected.package is None and not _is_streaming(resource):
            return DirectTransfer(
                url=_direct_url(task_id, selected),
                filename=safe_attachment_filename(task.title, resource.format),
            )

        # The selector is persisted with the staged row.  Package semantics
        # are represented by the canonical zip format and are executed by the
        # worker, which resolves the strict manifest again.
        if selected.package is not None:
            return self._stage(task_id, "zip", None, selected)
        return self._stage(task_id, resource.format, resource.quality, selected)

    def _load_manifest(self, task: ParseTask) -> MediaManifest:
        try:
            return validate_manifest_media_type(load_manifest(task), task.media_type)
        except ManifestError as exc:
            # Only stable business messages are allowed out of persistence;
            # validation details may contain untrusted metadata.
            message = str(exc)
            if message not in {_MESSAGE_MANIFEST_MISSING, _MESSAGE_MANIFEST_INVALID}:
                message = _MESSAGE_MANIFEST_INVALID
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, message) from None

    def _resolve_resource(
        self,
        media_type: MediaType,
        manifest: MediaManifest,
        selector: AssetSelector,
    ) -> MediaResource:
        expected = {
            MediaType.VIDEO: ("video", "video"),
            MediaType.IMAGE: ("image_album", "image"),
            MediaType.LIVE_PHOTO: ("live_photo", "live_image"),
        }
        expected_manifest, expected_selector = expected[media_type]
        selector_matches = selector.kind == expected_selector or (
            media_type is MediaType.LIVE_PHOTO and selector.kind == "live_motion"
        )
        if manifest.kind != expected_manifest or not selector_matches:
            raise self._asset_error()
        try:
            if selector.kind == "video":
                return manifest.videos[selector.index]
            if selector.kind == "image":
                return manifest.images[selector.index]
            pair = manifest.live_photos[selector.index]
            if selector.kind == "live_motion":
                if pair.motion is None:
                    raise self._asset_error()
                return pair.motion
            return pair.image
        except IndexError as exc:
            raise self._asset_error() from exc

    def _stage(
        self,
        task_id: str,
        format: str | None,
        quality: str | None,
        selector: AssetSelector,
    ) -> StagedTransfer:
        result = self._download_service.submit(
            task_id,
            format=format,
            quality=quality,
            selector=selector,
        )
        return StagedTransfer(download_id=result.download_id, status=result.status)

    @staticmethod
    def _asset_error() -> ApiError:
        return ApiError(
            HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_ASSET_INVALID
        )


def _is_streaming(resource: MediaResource) -> bool:
    """Reject HLS/DASH by URL path extension, independent of query/case."""
    suffix = PurePosixPath(urlsplit(str(resource.url)).path).suffix.casefold()
    return suffix in {".m3u8", ".mpd"}


def _direct_url(task_id: str, selector: AssetSelector) -> str:
    query: list[tuple[str, str]] = [
        ("kind", selector.kind),
        ("index", str(selector.index)),
    ]
    if selector.package is not None:
        query.append(("package", selector.package))
    return f"/api/download/direct/{quote(task_id, safe='')}?{urlencode(query)}"


def _music_resource(task: ParseTask, song_info: dict) -> MediaResource | None:
    """Validate the plain-HTTP music URL without exposing it to callers."""
    url = song_info.get("download_url")
    if not isinstance(url, str):
        return None
    ext = song_info.get("ext") or task.format
    if not isinstance(ext, str) or not ext.strip():
        suffix = PurePosixPath(urlsplit(url).path).suffix.lstrip(".")
        ext = suffix or "mp3"
    try:
        return MediaResource(url=url, format=ext.strip())
    except (TypeError, ValueError, ValidationError):
        return None
