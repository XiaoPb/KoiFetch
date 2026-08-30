"""Strict, versioned contracts for previewable media resources."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

__all__ = ["LivePhotoPair", "MediaManifest", "MediaResource"]


class MediaResource(BaseModel):
    """One upstream media resource in a private, persisted manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    url: HttpUrl
    format: str = Field(min_length=1, max_length=16)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    quality: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("format")
    @classmethod
    def _format_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("format must not be blank")
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
    videos: list[MediaResource] = Field(default_factory=list)
    images: list[MediaResource] = Field(default_factory=list)
    live_photos: list[LivePhotoPair] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

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
