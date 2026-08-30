"""Strict, versioned contracts for previewable media resources."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

__all__ = ["LivePhotoPair", "MediaManifest", "MediaResource"]

_FORMAT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,15}$"
_MAX_QUALITY_LENGTH = 64
_MAX_WARNING_LENGTH = 256
_MAX_WARNING_COUNT = 32
_WARNING_TEXT = Annotated[str, Field(min_length=1, max_length=_MAX_WARNING_LENGTH)]
_FORMAT_RE = re.compile(_FORMAT_PATTERN)


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


class MediaResource(BaseModel):
    """One upstream media resource in a private, persisted manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    url: HttpUrl
    format: str = Field(min_length=1, max_length=16, pattern=_FORMAT_PATTERN)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    quality: str | None = Field(
        default=None, min_length=1, max_length=_MAX_QUALITY_LENGTH
    )
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("format")
    @classmethod
    def _format_must_be_a_safe_token(cls, value: str) -> str:
        if not _FORMAT_RE.fullmatch(value):
            raise ValueError("format must be a safe media token")
        return value

    @field_validator("quality")
    @classmethod
    def _quality_must_not_contain_control_characters(
        cls, value: str | None
    ) -> str | None:
        if value is not None and _contains_control_character(value):
            raise ValueError("quality must not contain control characters")
        return value


class LivePhotoPair(BaseModel):
    """A still image and its optional motion resource."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    image: MediaResource
    motion: MediaResource | None = None


class MediaManifest(BaseModel):
    """Versioned, discriminated payload for one previewable media result.

    Exactly one payload collection must be non-empty, and it must be the
    collection selected by ``kind``.  The model stores upstream URLs only;
    public proxy URL projection belongs to the API/application layer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    kind: Literal["video", "image_album", "live_photo"]
    # Tuples make the frozen contract deep-immutable. JSON persistence must use
    # ``model_dump(mode="json")`` (or ``model_dump_json()``), which emits arrays.
    videos: tuple[MediaResource, ...] = Field(default_factory=tuple)
    images: tuple[MediaResource, ...] = Field(default_factory=tuple)
    live_photos: tuple[LivePhotoPair, ...] = Field(default_factory=tuple)
    warnings: tuple[_WARNING_TEXT, ...] = Field(
        default_factory=tuple, max_length=_MAX_WARNING_COUNT
    )

    @field_validator("videos", "images", "live_photos", "warnings", mode="before")
    @classmethod
    def _normalize_json_collections(cls, value: Any) -> Any:
        """Accept JSON/ORM lists while retaining strict tuple contracts.

        Python callers may provide either a tuple (already immutable) or a
        list (the shape produced by JSON decoding). Other iterables are not
        accepted, so sets/generators/strings cannot silently change ordering
        or bypass the tuple contract.
        """
        if isinstance(value, list):
            return tuple(value)
        if isinstance(value, tuple):
            return value
        raise ValueError("manifest collections must be lists or tuples")

    @field_validator("warnings")
    @classmethod
    def _warnings_must_not_contain_control_characters(
        cls, warnings: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(_contains_control_character(warning) for warning in warnings):
            raise ValueError("warnings must not contain control characters")
        return warnings

    @model_validator(mode="after")
    def _validate_payload(self) -> "MediaManifest":
        counts = {
            "video": len(self.videos),
            "image_album": len(self.images),
            "live_photo": len(self.live_photos),
        }
        if counts[self.kind] == 0 or sum(value > 0 for value in counts.values()) != 1:
            raise ValueError("manifest payload does not match kind")
        return self
