"""Stable parser-result to database mapping.

Parser adapters return the canonical domain :class:`ParseResult`. This module
is the persistence boundary: it selects the fields that belong in the stable
``parse_tasks`` schema and keeps parser-specific details in JSON metadata. The
SQLAlchemy model is constructed only from :class:`PersistedParseTask`, so a
parser response change does not become an ORM change by accident.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.application.media_manifest import serialize_manifest
from app.domain import MediaType, ParseResult, parse_duration
from app.infrastructure.models import ParseTask

logger = logging.getLogger(__name__)

_BACKGROUND_MUSIC_KEYS = (
    "background_music_urls",
    "background_music_url",
    "music_urls",
    "music_url",
)

__all__ = [
    "PersistedParseTask",
    "to_parse_task",
    "to_persisted_parse_task",
]


@dataclass(frozen=True)
class PersistedParseTask:
    """Stable database-facing representation of one parsed URL."""

    task_id: str
    url: str
    platform: str
    media_type: MediaType
    title: str
    cover_url: str | None
    duration: int | None
    format: str | None
    metadata: dict[str, Any]


def to_persisted_parse_task(result: ParseResult) -> PersistedParseTask:
    """Convert a domain parse result into the stable persistence contract."""
    metadata: dict[str, Any] = dict(result.metadata)
    metadata = serialize_manifest(metadata, media_type=result.media_type)
    metadata["file_size_mb"] = result.file_size_mb
    metadata["available_qualities"] = list(result.available_qualities)
    metadata["available_bitrates"] = list(result.available_bitrates)
    _log_media_links(result.url, metadata)
    return PersistedParseTask(
        task_id=result.task_id,
        url=result.url,
        platform=result.platform,
        media_type=result.media_type,
        title=result.title,
        cover_url=result.cover,
        duration=parse_duration(result.duration) if result.duration else None,
        format=result.format,
        metadata=metadata,
    )


def _safe_log_url(value: Any) -> str | None:
    """Return a URL suitable for logs without query-string credentials."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme and parts.netloc:
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        pass
    return value.split("?", 1)[0].split("#", 1)[0]


def _safe_log_urls(values: Any) -> list[str]:
    """Normalize scalar/list metadata URLs and redact query strings."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    urls: list[str] = []
    for value in values:
        safe_url = _safe_log_url(value)
        if safe_url is not None:
            urls.append(safe_url)
    return urls


def _manifest_media_links(metadata: dict[str, Any]) -> dict[str, list[str]]:
    """Extract categorized links from the serialized canonical manifest."""
    links = {
        "video_links": [],
        "image_links": [],
        "live_photo_image_links": [],
        "live_photo_motion_links": [],
    }
    manifest = metadata.get("manifest")
    if not isinstance(manifest, dict):
        return links
    kind = manifest.get("kind")
    if kind == "video":
        links["video_links"] = _safe_log_urls(
            [
                item.get("url")
                for item in manifest.get("videos", [])
                if isinstance(item, dict)
            ]
        )
    elif kind == "image_album":
        links["image_links"] = _safe_log_urls(
            [
                item.get("url")
                for item in manifest.get("images", [])
                if isinstance(item, dict)
            ]
        )
    elif kind == "live_photo":
        for pair in manifest.get("live_photos", []):
            if not isinstance(pair, dict):
                continue
            image = pair.get("image")
            motion = pair.get("motion")
            if isinstance(image, dict):
                links["live_photo_image_links"].extend(
                    _safe_log_urls(image.get("url"))
                )
            if isinstance(motion, dict):
                links["live_photo_motion_links"].extend(
                    _safe_log_urls(motion.get("url"))
                )
    return links


def _log_media_links(source_url: str, metadata: dict[str, Any]) -> None:
    """Log parsed resources immediately before the persistence conversion."""
    links = _manifest_media_links(metadata)
    music_links: list[str] = []
    for key in _BACKGROUND_MUSIC_KEYS:
        music_links.extend(_safe_log_urls(metadata.get(key)))
    source = _safe_log_url(source_url) or "<invalid-url>"
    logger.info(
        "Parsed media resources before persistence source_url=%s "
        "video_links=%s image_links=%s live_photo_image_links=%s "
        "live_photo_motion_links=%s background_music_links=%s",
        source,
        links["video_links"],
        links["image_links"],
        links["live_photo_image_links"],
        links["live_photo_motion_links"],
        music_links,
    )


def to_parse_task(record: PersistedParseTask) -> ParseTask:
    """Convert the stable persistence contract into the SQLAlchemy row."""
    return ParseTask(
        task_id=record.task_id,
        url=record.url,
        platform=record.platform,
        media_type=record.media_type,
        title=record.title,
        cover_url=record.cover_url,
        duration=record.duration,
        format=record.format,
        metadata_=record.metadata,
    )
