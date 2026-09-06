"""NAS transport (Task 10): the authenticated save endpoint.

This router is deliberately thin — the use case lives in
:class:`app.application.nas_service.NasService`. v1's NAS surface is a single
operation:

* ``POST /api/nas/save`` — move a completed download's bubble file into the
  pond at a generated platform/date/author/work path. Protected by :func:`require_admin`
  (``app.api.auth``); success returns the PRD §5.7 envelope
  ``{code: 0, message, data: {nas_path, file_size, saved_at}}``.

**Authorization model (documented).** The endpoint is gated by
:func:`require_admin`, which accepts *any* valid access token — v1 has exactly
one administrator, so a valid token IS authorization. The PRD code ``2002``
(权限不足 / Forbidden, HTTP 403) is therefore reserved for a future role
system and never fires today: missing/malformed header → ``2001``, invalid
token → ``2003``, expired token → ``2004`` (all 401). If a role check
(``username == "admin"``) is ever added, raise ``2002`` there — the envelope
and handler need no other change.

**Browsing and destructive operations are out of v1.** There is no
``/api/nas/list`` and no delete/rename/move endpoint; the pond layout is
produced by the save operation itself and consumed later (Task 11+ worker
cleanup, future NAS browsing).

DI hook (override in tests via ``app.dependency_overrides``):

* :func:`get_nas_service` — the app-wired NAS service (``app.state``).

``create_app`` populates ``app.state.nas_service``; handlers must only be
mounted on an app built by ``create_app`` (or one that sets the same state).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.adapters.protocols import AccessTokenClaims
from app.api.auth import require_admin
from app.api.responses import ok
from app.application.nas_service import NasService
from app.domain.models import UuidStr

__all__ = [
    "NasSaveData",
    "NasSaveRequest",
    "NasSaveResponse",
    "get_nas_service",
    "router",
]

router = APIRouter(prefix="/nas", tags=["nas"])

_MESSAGE_SAVE_OK = "文件已存入NAS / File saved to NAS"


class NasSaveRequest(BaseModel):
    """A NAS save request containing the completed download.

    ``target_path`` remains optional for old clients. New clients omit it and
    receive the generated platform/date/author/work path; when supplied, the
    service retains the legacy target behavior for compatibility.
    ``extra="forbid"`` (the project convention): an unknown body field — e.g.
    a v1.1 ``rename``/``overwrite`` flag — is rejected with a generic 400
    rather than silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    download_id: UuidStr
    target_path: str | None = Field(default=None, max_length=1024)

    @field_validator("target_path", mode="before")
    @classmethod
    def _strip_target_path(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("target_path must not be blank")
            return value
        return value


class NasSaveData(BaseModel):
    """The payload of a successful NAS save (PRD §5.7)."""

    model_config = ConfigDict(extra="forbid")

    nas_path: str  # NAS-style logical path, e.g. "/视频/抖音/x.mp4"
    file_size: int  # bytes
    saved_at: str  # ISO-8601 with timezone (UTC)


class NasSaveResponse(BaseModel):
    """The unified envelope for ``POST /api/nas/save`` success."""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: NasSaveData | None = None


def get_nas_service(request: Request) -> NasService:
    """DI hook: the app-wired NAS service (override in tests)."""
    return request.app.state.nas_service


@router.post("/save", response_model=NasSaveResponse)
def nas_save(
    body: NasSaveRequest,
    service: Annotated[NasService, Depends(get_nas_service)],
    claims: Annotated[AccessTokenClaims, Depends(require_admin)],
) -> dict:
    """Move a completed download's file into its generated pond path.

    Success: ``200`` with ``{nas_path, file_size, saved_at}``. Errors per the
    service: ``3001`` unknown download;
    ``5002`` not completed; ``5001`` missing bubble file; ``9001`` degraded
    storage. ``claims`` is the authorization gate itself (v1 single admin).
    """
    result = service.save(body.download_id, body.target_path)
    return ok(
        data={
            "nas_path": result.nas_path,
            "file_size": result.file_size,
            "saved_at": result.saved_at.isoformat(),
        },
        message=_MESSAGE_SAVE_OK,
    )
