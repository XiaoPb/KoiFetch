"""Domain value objects: parse/download commands and results.

These pydantic models are the transport/domain views of the persistence
records defined in ``app.infrastructure.models`` (Task 4) and the API payloads
specified in the PRD (ParseResult §3.1.4, DownloadTask §3.3.5, submit request
§5.3, progress §5.4). Application services (Tasks 7-10) and the worker (Tasks
11-12) construct, validate and serialize these objects — keep this module the
single domain contract and add new fields here rather than duplicating them in
ORM models or route schemas.

Conventions:

* **Strict models.** ``extra="forbid"`` everywhere so a typo in a field name
  fails fast instead of silently dropping data when crossing layers.
* **UUIDs are validated strings.** The ORM stores UUIDs as ``String(36)``
  (SQLite has no native UUID type), so domain fields are ``str`` validated
  against the canonical UUID format; ``uuid.UUID`` objects are accepted and
  normalized to lowercase strings. Version is not pinned (v1 uses v4, but the
  contract should accept any canonical UUID).
* **Progress bounds.** ``progress`` is a 0-100 float, ``downloaded_bytes`` may
  never exceed ``total_bytes``, and a ``COMPLETED`` snapshot must report
  ``progress == 100``; all enforced by validation.
* **NAS contract modeled, not enforced.** ``DownloadCommand`` carries
  ``save_to_nas``/``nas_path`` because v1 performs NAS saves through a
  separate authenticated API (Task 10); only the required-when-save invariant
  is validated here, not filesystem safety of ``nas_path`` (that is a virtual
  pond path, not a filesystem path).

Field dispositions (deliberately NOT fields here):

* ``download_url`` (PRD §3.3.5) is a per-request construct — the API layer
  builds it with a short-lived token when serving the file. It is not a
  domain/ORM field.
* ``metadata_path`` (the sidecar ``.json`` path) and ``sha256`` (content
  hash) are deferred to v1.1: the Task 4 ORM has no columns for them, so the
  domain contract does not carry them yet. Add them here only when the
  storage adapter (Task 6+) persists them.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, ClassVar
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.enums import DownloadStatus, MediaType

__all__ = [
    "DownloadCommand",
    "DownloadProgress",
    "DownloadResult",
    "ParseCommand",
    "ParseResult",
]

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _normalize_uuid(value: object) -> str:
    """Accept a canonical UUID string or ``uuid.UUID``; return a lowercase str."""
    if isinstance(value, uuid.UUID):
        return str(value).lower()
    if not isinstance(value, str):
        raise ValueError("expected a UUID string")
    normalized = value.strip().lower()
    if not _UUID_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid UUID: {value!r}")
    return normalized


UuidStr = Annotated[str, BeforeValidator(_normalize_uuid)]


class _DownloadStateInvariants(BaseModel):
    """Shared cross-field invariants for download snapshots.

    Both :class:`DownloadProgress` and :class:`DownloadResult` must satisfy:
    ``downloaded_bytes <= total_bytes`` (when both are known) and
    ``status COMPLETED => progress == 100``. The second rule is deliberately
    one-directional: non-completed snapshots may carry any 0-100 progress
    (e.g. a ``downloading`` tick at 100 % just before finalization) so worker
    updates are never rejected mid-flight.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_download_state(self) -> "_DownloadStateInvariants":
        if (
            self.downloaded_bytes is not None
            and self.total_bytes is not None
            and self.downloaded_bytes > self.total_bytes
        ):
            raise ValueError("downloaded_bytes cannot exceed total_bytes")
        if self.status is DownloadStatus.COMPLETED and self.progress < 100.0:
            raise ValueError("a completed download must have progress == 100")
        return self


class ParseCommand(BaseModel):
    """A parse request: one or more source URLs (batch parse, PRD §5.1).

    The domain rule set is: non-empty list, 1..``MAX_URLS`` entries, each an
    absolute ``http``/``https`` URL without whitespace or control characters.
    HTTP-level concerns (rate limiting, payload size at the wire) stay in the
    API layer; the count limit here is a business rule.
    """

    model_config = ConfigDict(extra="forbid")

    MAX_URLS: ClassVar[int] = 50

    urls: list[str]

    @field_validator("urls")
    @classmethod
    def _validate_urls(cls, urls: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in urls:
            url = raw.strip()
            if not url:
                raise ValueError("URLs must not contain empty entries")
            if any(ch.isspace() for ch in url):
                raise ValueError(f"URL must not contain whitespace: {url!r}")
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
                raise ValueError(f"URL must not contain control characters: {url!r}")
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(
                    f"invalid URL (must be an absolute http/https URL): {url!r}"
                )
            cleaned.append(url)
        if not cleaned:
            raise ValueError("at least one URL is required")
        if len(cleaned) > cls.MAX_URLS:
            raise ValueError(f"at most {cls.MAX_URLS} URLs per parse request")
        return cleaned


class ParseResult(BaseModel):
    """Parsed metadata for one source URL (PRD §3.1.4).

    ``media_type`` is the domain enum (:class:`app.domain.enums.MediaType`);
    v1 covers video/image/music — ``live_photo`` is not in the enum yet and is
    rejected (extend the enum when live-photo parsing lands). ``duration`` is
    the PRD display form (``"03:20"``); the ORM stores seconds, so services
    convert. ``metadata`` carries extra platform-specific detail.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: UuidStr
    url: str
    media_type: MediaType
    platform: str = Field(min_length=1)
    title: str = Field(min_length=1)
    cover: str | None = Field(default=None, min_length=1)
    duration: str | None = Field(default=None, min_length=1)  # "MM:SS"
    file_size_mb: float | None = Field(default=None, ge=0.0)
    format: str | None = Field(default=None, min_length=1)
    available_qualities: list[str] = Field(default_factory=list)
    available_bitrates: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("platform", "title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DownloadCommand(BaseModel):
    """Submit a download for an already-parsed task (PRD §5.3).

    ``format``/``quality``/``bitrate`` select among the parsed options; v1 NAS
    saving goes through a separate authenticated API, but the fields are part
    of the contract now so the payload shape is stable.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: UuidStr
    format: str | None = None
    quality: str | None = None
    bitrate: str | None = None
    save_to_nas: bool = False
    nas_path: str | None = None

    @field_validator("format", "quality", "bitrate")
    @classmethod
    def _selection_not_blank(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("selection must not be blank when provided")
        return value

    @field_validator("nas_path")
    @classmethod
    def _strip_nas_path(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("nas_path must not be blank")
        return value

    @model_validator(mode="after")
    def _require_nas_path_when_saving(self) -> "DownloadCommand":
        if self.save_to_nas and self.nas_path is None:
            raise ValueError("nas_path is required when save_to_nas is true")
        return self


class DownloadProgress(_DownloadStateInvariants):
    """A real-time progress snapshot for one download (PRD §5.4 / WebSocket).

    ``progress`` is a 0-100 float, ``speed`` bytes/sec; ``downloaded_bytes``
    may not exceed ``total_bytes`` (either may be unknown until the worker
    reports them). ``remaining_time`` stays numeric seconds here — the PRD's
    display form ("约 2分钟", §5.4) is the frontend's formatting concern.
    """

    download_id: UuidStr
    status: DownloadStatus
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    speed: float | None = Field(default=None, ge=0.0)
    downloaded_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    remaining_time: float | None = Field(default=None, ge=0.0)
    error_message: str | None = None


class DownloadResult(_DownloadStateInvariants):
    """A persisted download-task snapshot (mirrors the ``DownloadTask`` ORM
    row, Task 4; also the PRD §3.3.5 DownloadTask shape).

    ``media_type`` is not stored on the ORM row (it derives from the parse
    task) but is included for the frontend; services fill it from the joined
    parse task. Paths are the stored bubble/pond paths — safe path building is
    the domain ``paths`` module's job. ``download_url`` is intentionally
    absent: it is a per-request, token-bearing construct built by the API
    layer, and ``metadata_path``/``sha256`` are deferred to v1.1 (see the
    module docstring).
    """

    download_id: UuidStr
    task_id: UuidStr
    title: str | None = None
    media_type: MediaType | None = None
    format: str | None = None
    quality: str | None = None
    status: DownloadStatus = DownloadStatus.PENDING
    progress: float = Field(default=0.0, ge=0.0, le=100.0)
    speed: float | None = Field(default=None, ge=0.0)
    total_bytes: int | None = Field(default=None, ge=0)
    downloaded_bytes: int | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    error_message: str | None = None
    bubble_path: str | None = None
    pond_path: str | None = None
    token_expires_at: datetime | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
