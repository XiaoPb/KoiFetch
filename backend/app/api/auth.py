"""Admin authentication transport: login endpoint and the bearer-token guard.

Two things live here (both are HTTP-layer concerns — all real work happens in
:class:`app.application.auth_service.AuthService`):

* ``POST /api/auth/login`` — validate admin credentials and issue a 24-hour
  JWT. The endpoint never logs or echoes credentials.
* :func:`require_admin` — a FastAPI dependency that validates the ``Bearer``
  access token (via the OpenAPI-documented ``HTTPBearer`` security scheme) and
  returns its claims. Task 10 (``POST /api/nas/save``) and any other
  admin-only endpoint reuse it.

DI hooks (override in tests via ``app.dependency_overrides``):

* :func:`get_auth_service` — the app-wired auth service (``app.state``).
* :func:`get_token_provider` — the app-wired token provider (``app.state``).

``create_app`` populates both ``app.state`` attributes; handlers must only be
mounted on an app built by ``create_app`` (or one that sets the same state).

**Rate limiting (PRD 1005 请求过于频繁):** v1 deliberately ships no limiter.
The login handler is kept thin so a dependency or middleware can be inserted
later without changing the endpoint — see the comment inside the handler.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from starlette.status import HTTP_401_UNAUTHORIZED

from app.adapters.protocols import (
    AccessTokenClaims,
    AccessTokenProvider,
    TokenError,
    TokenExpiredError,
)
from app.api.responses import (
    CODE_INVALID_CREDENTIALS,
    CODE_INVALID_TOKEN,
    CODE_TOKEN_EXPIRED,
    CODE_UNAUTHORIZED,
    ApiError,
    ok,
)
from app.application.auth_service import AuthService

__all__ = [
    "LoginData",
    "LoginRequest",
    "LoginResponse",
    "get_auth_service",
    "get_token_provider",
    "require_admin",
    "router",
]

router = APIRouter(prefix="/auth", tags=["auth"])

_MESSAGE_LOGIN_FAILED = "用户名或密码错误 / Invalid username or password"
_MESSAGE_LOGIN_OK = "登录成功 / Login successful"
_MESSAGE_NOT_LOGGED_IN = "未登录 / Not logged in"
_MESSAGE_TOKEN_EXPIRED = "登录已过期，请重新登录 / Token expired"
_MESSAGE_TOKEN_INVALID = "Token无效 / Invalid token"

# OpenAPI security scheme: documents the ``Authorization: Bearer <token>``
# header for protected routes. ``auto_error=False`` makes a missing/malformed
# header resolve to ``None`` instead of raising, so require_admin maps every
# absence to the 2001 envelope rather than a framework 403.
_bearer_scheme = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    """Login credentials.

    Fields are required (missing → 400). Values are stripped of surrounding
    whitespace *before* length checks, so whitespace-only input is rejected as
    blank (400) instead of being passed to the auth service.
    """

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username", "password", mode="before")
    @classmethod
    def _strip_whitespace(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LoginData(BaseModel):
    """The payload of a successful login response."""

    token: str
    username: str
    expires_at: str  # ISO-8601 with timezone (e.g. "...T...+00:00")


class LoginResponse(BaseModel):
    """The unified envelope for ``POST /api/auth/login`` success."""

    code: int
    message: str
    data: LoginData | None = None


def get_auth_service(request: Request) -> AuthService:
    """DI hook: the app-wired auth service (override in tests)."""
    return request.app.state.auth_service


def get_token_provider(request: Request) -> AccessTokenProvider:
    """DI hook: the app-wired access-token provider (override in tests)."""
    return request.app.state.token_provider


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    """Authenticate the admin and issue a 24-hour access token.

    Success: ``200`` with ``{token, username, expires_at}``. Failure: ``401``
    with a stable code — the response is identical for a wrong password and a
    nonexistent user, so the endpoint never leaks which one it was. Credentials
    are never logged.
    """
    # Rate limiting (PRD 1005 请求过于频繁): v1 omits a limiter; when one
    # lands, add it as a dependency here or as middleware — nothing else in
    # this handler changes.
    result = auth.login(body.username, body.password)
    if result is None:
        raise ApiError(
            HTTP_401_UNAUTHORIZED, CODE_INVALID_CREDENTIALS, _MESSAGE_LOGIN_FAILED
        )
    return ok(
        data={
            "token": result.token,
            "username": result.username,
            "expires_at": result.expires_at.isoformat(),
        },
        message=_MESSAGE_LOGIN_OK,
    )


def require_admin(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    token_provider: Annotated[AccessTokenProvider, Depends(get_token_provider)],
) -> AccessTokenClaims:
    """Validate the ``Authorization: Bearer <token>`` header; return its claims.

    Error mapping (stable codes): missing/malformed header → ``401`` 2001
    未登录; invalid token → ``401`` 2003 Token无效; expired token → ``401`` 2004
    Token已过期. Reused by Task 10's admin-only endpoints.
    """
    if credentials is None:
        raise ApiError(HTTP_401_UNAUTHORIZED, CODE_UNAUTHORIZED, _MESSAGE_NOT_LOGGED_IN)
    try:
        return token_provider.validate(credentials.credentials)
    except TokenExpiredError as exc:
        raise ApiError(
            HTTP_401_UNAUTHORIZED, CODE_TOKEN_EXPIRED, _MESSAGE_TOKEN_EXPIRED
        ) from exc
    except TokenError as exc:
        # Covers InvalidTokenError and any other token failure: malformed,
        # tampered, mis-signed, or missing required claims.
        raise ApiError(
            HTTP_401_UNAUTHORIZED, CODE_INVALID_TOKEN, _MESSAGE_TOKEN_INVALID
        ) from exc
