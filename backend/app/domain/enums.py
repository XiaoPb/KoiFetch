"""Domain enums shared by the persistence and application layers.

These are the stable vocabulary for the product domain: the media kinds Koi
Fetch can parse/download and the lifecycle states of a download task. They live
in the domain layer so later tasks (state transitions, value objects, API
schemas) extend them here rather than duplicating enums inside ORM models or
routes.

Both enums subclass ``str`` so their members are directly JSON-serializable
and compare cleanly against plain strings (e.g. ``"video" == MediaType.VIDEO``).

Task 5 (path-safety / state-transition work) will extend this module with
transition rules and value objects; do NOT add state-transition logic here yet.
"""

from __future__ import annotations

import enum

__all__ = ["MediaType", "DownloadStatus"]


class MediaType(str, enum.Enum):
    """The kind of media a parse/download task operates on.

    Values match the configured storage roots (pond/bubble video, image,
    music) so a media type maps 1:1 to a storage bucket.
    """

    VIDEO = "video"
    IMAGE = "image"
    LIVE_PHOTO = "live_photo"
    MUSIC = "music"


class DownloadStatus(str, enum.Enum):
    """Lifecycle state of a download task (persisted as the enum value).

    Transitions between these states are validated by the download service
    (Task 5+); the ORM model only stores and defaults the value.
    """

    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
