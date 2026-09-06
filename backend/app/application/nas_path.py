"""Deterministic pond paths for completed media downloads."""

from __future__ import annotations

import re
from datetime import date, datetime

from app.domain.paths import slugify

_EXTENSION_RE = re.compile(r"^[A-Za-z0-9]+$")
_MEDIA_BUCKETS = {"video", "image", "music"}


def build_media_pond_path(
    *,
    media_bucket: str,
    platform: object | None,
    published_at: object | None,
    author: object | None,
    work_id: object | None,
    title: object | None,
    extension: str,
    task_id: object | None = None,
    downloaded_on: date | datetime | None = None,
    index: int | None = None,
    resource_kind: str | None = None,
) -> str:
    """Build a safe pond-relative path for one completed media resource."""
    if media_bucket not in _MEDIA_BUCKETS:
        raise ValueError(f"unsupported media bucket: {media_bucket!r}")
    extension = extension.strip().lstrip(".").lower()
    if extension and not _EXTENSION_RE.fullmatch(extension):
        raise ValueError("extension must contain only letters and digits")
    if index is not None and index < 0:
        raise ValueError("index must be >= 0")

    platform_slug = _slug_or_fallback(platform, "unknown-platform")
    author_slug = _slug_or_fallback(author, "unknown-author")
    work_slug = _slug_or_fallback(work_id or task_id, "unknown-work")
    title_slug = _slug_or_fallback(title, "untitled")
    published = _format_date(published_at, downloaded_on)

    name_parts = [author_slug, title_slug]
    if resource_kind in {"live_image", "live_motion"}:
        kind_label = "motion" if resource_kind == "live_motion" else "image"
        name_parts.append(f"live-{(index or 0) + 1:04d}-{kind_label}")
    elif index is not None:
        name_parts.append(f"{index + 1:03d}")
    base = "_".join(name_parts)
    # Empty extension → directory path (loose-file package like live_zip)
    filename = base if not extension else f"{base}.{extension}"
    return "/".join((platform_slug, published, author_slug, work_slug, filename))


def _slug_or_fallback(value: object | None, fallback: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return fallback
    return slugify(value, fallback=fallback)


def _format_date(
    published_at: object | None,
    downloaded_on: date | datetime | None,
) -> str:
    if isinstance(published_at, datetime):
        return published_at.date().isoformat()
    if isinstance(published_at, date):
        return published_at.isoformat()
    if isinstance(published_at, str):
        text = published_at.strip()
        if text:
            try:
                return date.fromisoformat(text[:10]).isoformat()
            except ValueError:
                pass
    if isinstance(downloaded_on, datetime):
        return downloaded_on.date().isoformat()
    if isinstance(downloaded_on, date):
        return downloaded_on.isoformat()
    return date.today().isoformat()
