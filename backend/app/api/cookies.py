"""Cookie configuration API: admin-managed per-platform cookies for the f2 parser.

Transport for :class:`app.application.cookie_service.PlatformCookieService`:

* ``GET /api/cookies`` — public entries for every configured platform
  (``platform`` / ``configured`` / ``updated_at``). The cookie VALUE is never
  returned: the frontend only needs to know whether a platform is configured
  and when it was last updated.
* ``PUT /api/cookies/{platform}`` — upsert a cookie (body ``{cookie}``).
* ``DELETE /api/cookies/{platform}`` — clear a stored cookie (idempotent).

All routes require the admin bearer token (:func:`app.api.auth.require_admin`).
Platform names are validated for shape only (``[a-z0-9_-]{1,64}``) and kept
backend-agnostic — the frontend owns the current f2 platform list.

DI hook: :func:`get_cookie_service` reads ``app.state.cookie_service``
(populated by ``create_app``); tests override it via ``dependency_overrides``
or by building the app with a temp database.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from starlette.status import HTTP_400_BAD_REQUEST

from app.api.auth import require_admin
from app.api.responses import CODE_BAD_REQUEST, ApiError, ok
from app.application.cookie_service import PlatformCookieService

__all__ = [
    "CookieBody",
    "get_cookie_service",
    "router",
]

router = APIRouter(prefix="/cookies", tags=["cookies"])

_MESSAGE_COOKIE_SAVED = "Cookie 已保存 / Cookie saved"
_MESSAGE_COOKIE_DELETED = "Cookie 已清除 / Cookie cleared"
_MESSAGE_PLATFORM_INVALID = "平台名称无效 / Invalid platform name"

_PLATFORM_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


class CookieBody(BaseModel):
    """The cookie string to store (raw browser cookie, ``k=v; k2=v2``).

    ``min_length`` bounds the wire size; the strip validator additionally
    rejects whitespace-only cookies so they surface as a 400 envelope (the
    service would otherwise raise an unhandled ValueError → 500).
    """

    cookie: str = Field(min_length=1, max_length=8192)

    @field_validator("cookie")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("cookie must not be blank")
        return value


def get_cookie_service(request: Request) -> PlatformCookieService:
    """DI hook: the app-wired cookie service (override in tests)."""
    return request.app.state.cookie_service


def _validated_platform(platform: str) -> str:
    """Validate the path-param platform name; raise a 400 envelope otherwise."""
    if not _PLATFORM_RE.fullmatch(platform):
        raise ApiError(
            HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_PLATFORM_INVALID
        )
    return platform


@router.get("")
def list_cookies(
    _: Annotated[None, Depends(require_admin)],
    service: Annotated[PlatformCookieService, Depends(get_cookie_service)],
) -> dict:
    """Return public entries for every configured platform."""
    return ok(data={"cookies": service.list()})


@router.put("/{platform}")
def set_cookie(
    platform: str,
    body: CookieBody,
    _: Annotated[None, Depends(require_admin)],
    service: Annotated[PlatformCookieService, Depends(get_cookie_service)],
) -> dict:
    """Upsert the cookie for ``platform``; return its public entry."""
    validated = _validated_platform(platform)
    return ok(data=service.set(validated, body.cookie), message=_MESSAGE_COOKIE_SAVED)


@router.delete("/{platform}")
def delete_cookie(
    platform: str,
    _: Annotated[None, Depends(require_admin)],
    service: Annotated[PlatformCookieService, Depends(get_cookie_service)],
) -> dict:
    """Clear the stored cookie for ``platform`` (idempotent)."""
    _validated_platform(platform)
    service.delete(platform)
    return ok(message=_MESSAGE_COOKIE_DELETED)
