"""Persistence and public projection helpers for media manifests.

Manifest resources contain signed/private upstream URLs.  This module is the
single boundary where those URLs are read from persisted metadata and replaced
with same-origin, index-addressed proxy routes for API responses.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.domain import MediaManifest

__all__ = ["ManifestError", "load_manifest", "public_manifest", "serialize_manifest"]

_MESSAGE_MANIFEST_MISSING = "媒体清单不存在 / Media manifest is missing"
_MESSAGE_MANIFEST_INVALID = "媒体清单无效 / Invalid media manifest"


class ManifestError(ValueError):
    """A persisted manifest is absent or fails its strict domain contract."""


def serialize_manifest(metadata: dict[str, Any]) -> dict[str, Any]:
    """Normalize a manifest value to JSON-safe persisted data.

    Parsers may hand the service either a domain model or an already decoded
    JSON dictionary.  Validation here prevents malformed private resources
    from being persisted as if they were usable manifests.
    """
    payload = metadata.get("manifest")
    if payload is None:
        return metadata
    try:
        manifest = (
            payload
            if isinstance(payload, MediaManifest)
            else MediaManifest.model_validate(payload)
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise ManifestError from exc
    metadata["manifest"] = manifest.model_dump(mode="json")
    return metadata


def load_manifest(task: Any) -> MediaManifest:
    """Load and strictly validate ``task.metadata_['manifest']``.

    Missing and corrupt legacy rows intentionally do not fall back to the
    compatibility ``video_url``/``images`` fields: the public resource API
    must have one validated manifest source and a stable error contract.
    """
    metadata = getattr(task, "metadata_", None)
    payload = metadata.get("manifest") if isinstance(metadata, dict) else None
    if payload is None:
        raise ManifestError(_MESSAGE_MANIFEST_MISSING)
    try:
        return MediaManifest.model_validate(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ManifestError(_MESSAGE_MANIFEST_INVALID) from exc


def public_manifest(task_id: str, manifest: MediaManifest) -> dict[str, Any]:
    """Project a private manifest to same-origin resource-index routes."""
    base = f"/api/preview/{task_id}/resources"
    if manifest.kind == "video":
        return {
            "kind": "video",
            "videos": [
                {
                    "url": f"{base}/video/{index}",
                    "format": item.format,
                    "quality": item.quality,
                }
                for index, item in enumerate(manifest.videos)
            ],
        }
    if manifest.kind == "image_album":
        return {
            "kind": "image_album",
            "images": [
                {"url": f"{base}/image/{index}", "format": item.format}
                for index, item in enumerate(manifest.images)
            ],
        }
    return {
        "kind": "live_photo",
        "live_photos": [
            {
                "image_url": f"{base}/live/{index}/image",
                "motion_url": (
                    f"{base}/live/{index}/motion" if pair.motion is not None else None
                ),
            }
            for index, pair in enumerate(manifest.live_photos)
        ],
        "warnings": list(manifest.warnings),
    }
