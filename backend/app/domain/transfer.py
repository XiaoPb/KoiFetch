"""Contracts for preparing an asset for direct or staged transfer.

These value objects deliberately describe the hand-off between the download
application service and its API caller.  They do not perform a download or
authorize a path; the service remains responsible for those operations.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, TypeAlias
from urllib.parse import unquote, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.enums import DownloadStatus

__all__ = [
    "AssetSelector",
    "DirectTransfer",
    "PrepareRequest",
    "PreparedTransfer",
    "StagedTransfer",
]


AssetKind = Literal["video", "image", "live_image", "live_motion", "music"]
AssetPackage = Literal["album_zip", "live_zip"]

# IDs are used in URLs and storage lookups, so keep the alphabet deliberately
# small.  128 characters leaves room for UUIDs and task-specific prefixes
# while putting a firm upper bound on request size.
SafeId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]

_MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_PERCENT_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_MAX_URL_LENGTH = 2048
_MAX_FILENAME_LENGTH = 255
_WINDOWS_INVALID_FILENAME = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_RESERVED_DEVICE_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    "clock$",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _decode_url_component(value: str) -> str:
    """Decode a URL component while rejecting malformed/hidden escapes."""
    current = value
    for _ in range(8):
        if _MALFORMED_PERCENT_ESCAPE.search(current):
            raise ValueError("URL contains a malformed percent escape")
        if "%" not in current:
            return current
        decoded = unquote(current)
        if decoded == current:
            return current
        current = decoded
    # Generated routes are ASCII, so an escape chain that survives this bound
    # is not a useful URL and should not be allowed to hide path semantics.
    raise ValueError("URL contains too many nested percent escapes")


def _has_encoded_separator(value: str) -> bool:
    """Detect slash/backslash escapes at any bounded decoding depth."""
    current = value
    for _ in range(8):
        if _PERCENT_SEPARATOR.search(current):
            return True
        if _MALFORMED_PERCENT_ESCAPE.search(current) or "%" not in current:
            return False
        decoded = unquote(current)
        if decoded == current:
            return False
        current = decoded
    return True


class AssetSelector(BaseModel):
    """Select one parsed asset or one of the supported package forms.

    ``album_zip`` has canonical index ``0`` because it represents the whole
    image album, while ``live_zip`` canonically selects a complete
    ``live_image`` pair.  A ``live_motion`` selector remains valid as a
    single asset but cannot be packaged on its own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AssetKind
    index: StrictInt = Field(default=0, ge=0)
    package: AssetPackage | None = None

    @model_validator(mode="after")
    def _validate_package_combination(self) -> "AssetSelector":
        if self.package == "album_zip":
            if self.kind != "image":
                raise ValueError("album_zip is only valid for image assets")
            if self.index != 0:
                raise ValueError("album_zip must use the canonical index 0")
        elif self.package == "live_zip":
            if self.kind != "live_image":
                raise ValueError("live_zip is only valid for live_image assets")
            if self.index != 0:
                raise ValueError("live_zip must use the canonical index 0")
        return self


class DirectTransfer(BaseModel):
    """A transfer that can be fetched immediately from an API route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["direct"] = "direct"
    url: StrictStr = Field(min_length=1, max_length=_MAX_URL_LENGTH)
    filename: StrictStr = Field(min_length=1, max_length=_MAX_FILENAME_LENGTH)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("direct transfer URL must not contain whitespace or controls")
        if "\\" in value:
            raise ValueError("direct transfer URL must not contain backslashes")

        parts = urlsplit(value)
        # A query is allowed for same-origin short-lived token parameters;
        # fragments are client-only and are not part of an API route.
        if parts.scheme or parts.netloc or parts.fragment:
            raise ValueError("direct transfer URL must be a same-origin API path")
        if not parts.path.startswith("/api/"):
            raise ValueError("direct transfer URL must start with /api/")

        if _has_encoded_separator(parts.path) or _has_encoded_separator(parts.query):
            raise ValueError("direct transfer URL must not contain encoded separators")
        decoded_path = _decode_url_component(parts.path)
        decoded_query = _decode_url_component(parts.query)
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded_path):
            raise ValueError("direct transfer URL must not contain encoded controls")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded_query):
            raise ValueError("direct transfer URL must not contain encoded controls")
        if any(segment in {".", ".."} for segment in decoded_path.split("/")):
            raise ValueError("direct transfer URL must not contain traversal segments")
        if "\\" in decoded_path:
            raise ValueError("direct transfer URL must not contain backslashes")
        if "/" in decoded_query or "\\" in decoded_query:
            raise ValueError("direct transfer URL query must not contain separators")
        if any(token in {".", ".."} for token in re.split(r"[&=/?#]", decoded_query)):
            raise ValueError("direct transfer URL query must not contain traversal segments")
        return value

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        if value.strip() in {"", ".", ".."}:
            raise ValueError("attachment filename must be a non-empty name")
        if _WINDOWS_INVALID_FILENAME.search(value):
            raise ValueError(
                "attachment filename must not contain Windows-invalid characters"
            )
        if value.endswith((".", " ")):
            raise ValueError("attachment filename must not end with a dot or space")
        basename = value.split(".", 1)[0].rstrip(" .").casefold()
        if basename in _RESERVED_DEVICE_BASENAMES:
            raise ValueError("attachment filename uses a reserved device basename")
        return value


class StagedTransfer(BaseModel):
    """A transfer whose download is identified by an active task ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["staged"] = "staged"
    download_id: SafeId
    status: DownloadStatus

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, value: object) -> DownloadStatus:
        if isinstance(value, DownloadStatus):
            status = value
        elif type(value) is str:
            try:
                status = DownloadStatus(value)
            except ValueError as exc:
                raise ValueError("status must be a DownloadStatus value") from exc
        else:
            raise ValueError("status must be a DownloadStatus or exact string value")

        value = status
        if value in {DownloadStatus.FAILED, DownloadStatus.EXPIRED}:
            raise ValueError("staged transfer status must be active or completed")
        return value


PreparedTransfer: TypeAlias = Annotated[
    DirectTransfer | StagedTransfer,
    Field(discriminator="mode"),
]


class PrepareRequest(BaseModel):
    """Request to prepare one selected asset for transfer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: SafeId
    asset: AssetSelector
    force_staged: StrictBool = False
