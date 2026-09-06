"""Map parsed asset selections onto the configured filesystem buckets."""

from app.domain import AssetSelector, MediaType

__all__ = ["storage_media_type"]


def storage_media_type(
    media_type: MediaType, selector: AssetSelector | None
) -> MediaType:
    """Return the configured bucket type for one parsed/downloaded asset."""
    if media_type is not MediaType.LIVE_PHOTO:
        return media_type
    if selector is not None and selector.kind == "live_motion":
        return MediaType.VIDEO
    return MediaType.IMAGE
