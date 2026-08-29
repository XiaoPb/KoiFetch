"""Admin authentication transport: login endpoint and the bearer-token guard.

Two things live here (both are HTTP-layer concerns — all real work happens in
:class:`app.application.auth_service.AuthService`):

* ``POST /api/auth/login`` — validate admin credentials and issue a configured
  access-token JWT (seven days by default). The endpoint never logs or echoes
  credentials.
* :func:`require_admin` — a FastAPI dependency that validates the ``Bearer``
  access token (via the OpenAPI-documented ``HTTPBearer`` security scheme) and
  returns its claims. Task 10 (``POST /api/nas/save``) and any other
  admin-only endpoint reuse it.

DI hooks (override in tests via ``app.dependency_overrides``):

* :func:`get_auth_service` — the app-wired auth service (``app.state``).
* :func:`get_token_provider` — the app-wired token provider (``app.state``).

``create_app`` populates both ``app.state`` attributes; handlers must only be
mounted on an app built by ``create_app`` (or one that sets the same state).

**Rate limiting (PRD 1005 请求过于频繁):** failed attempts are bounded by a
process-local :class:`app.application.login_limiter.LoginLimiter` wired by
``create_app``. The limiter runs before bcrypt and uses a trusted client-IP
resolution policy.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_429_TOO_MANY_REQUESTS

from app.adapters.protocols import (
    AccessTokenClaims,
    AccessTokenProvider,
    TokenError,
    TokenExpiredError,
)
from app.api.responses import (
    CODE_INVALID_CREDENTIALS,
    CODE_INVALID_TOKEN,
    CODE_RATE_LIMITED,
    CODE_TOKEN_EXPIRED,
    CODE_UNAUTHORIZED,
    ApiError,
    ok,
)
from app.application.auth_service import AuthService
from app.application.login_limiter import (
    LoginLimiter,
    canonicalize_network,
    normalize_username,
)

__all__ = [
    "LoginData",
    "LoginRequest",
    "LoginResponse",
    "get_auth_service",
    "get_client_ip",
    "get_login_limiter",
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
_MESSAGE_RATE_LIMITED = "请求过于频繁，请稍后再试 / Too many login attempts, please try again"

# OpenAPI security scheme: documents the ``Authorization: Bearer <token>``
# header for protected routes. ``auto_error=False`` makes a missing/malformed
# header resolve to ``None`` instead of raising, so require_admin maps every
# absence to the 2001 envelope rather than a framework 403.
_bearer_scheme = HTTPBearer(auto_error=False)


def _canonical_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    return getattr(address, "ipv4_mapped", None) or address


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


def get_login_limiter(request: Request) -> LoginLimiter:
    """DI hook for the process-local login limiter."""
    return request.app.state.login_limiter


def get_client_ip(request: Request) -> str:
    """Resolve a client IP, trusting X-Forwarded-For only from trusted peers."""
    peer = request.client.host if request.client is not None else "unknown"
    try:
        peer_ip = _canonical_address(peer)
    except (ValueError, TypeError):
        return str(peer or "unknown")
    trusted = []
    settings = getattr(request.app.state, "settings", None)
    for cidr in getattr(settings, "trusted_proxy_cidrs", []):
        try:
            trusted.append(canonicalize_network(ipaddress.ip_network(cidr, strict=False)))
        except (ValueError, TypeError):
            continue
    if not any(peer_ip in network for network in trusted):
        return str(peer_ip)
    headers = getattr(request, "headers", {})
    forwarded = headers.get("x-forwarded-for", "") or headers.get(
        "X-Forwarded-For", ""
    )
    if not isinstance(forwarded, str) or not forwarded.strip():
        return str(peer_ip)
    parts = [part.strip() for part in forwarded.split(",")]
    if not parts or any(not part for part in parts):
        return str(peer_ip)
    addresses = []
    try:
        addresses = [_canonical_address(part) for part in parts]
    except (ValueError, TypeError):
        return str(peer_ip)
    for address in reversed(addresses):
        if not any(address in network for network in trusted):
            return str(address)
    return str(peer_ip)


@router.post("/login", response_model=LoginResponse)
def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    limiter: Annotated[LoginLimiter, Depends(get_login_limiter)],
) -> dict:
    """Authenticate the admin and issue a seven-day access token.

    Success: ``200`` with ``{token, username, expires_at}``. Failure: ``401``
    with a stable code — the response is identical for a wrong password and a
    nonexistent user, so the endpoint never leaks which one it was. Credentials
    are never logged.
    """
    client_ip = get_client_ip(request)
    username = normalize_username(body.username)
    ticket = limiter.begin_attempt(client_ip, username)
    if ticket is None:
        raise ApiError(
            HTTP_429_TOO_MANY_REQUESTS,
            CODE_RATE_LIMITED,
            _MESSAGE_RATE_LIMITED,
            headers={
                "Retry-After": str(max(1, limiter.retry_after(client_ip, username)))
            },
        )
    try:
        result = auth.login(body.username, body.password)
    except Exception:
        limiter.finalize(ticket, success=False, error=True)
        raise
    if result is None:
        limiter.finalize(ticket, success=False)
        raise ApiError(
            HTTP_401_UNAUTHORIZED, CODE_INVALID_CREDENTIALS, _MESSAGE_LOGIN_FAILED
        )
    limiter.finalize(ticket, success=True)
    response.headers["Cache-Control"] = "no-store"
    return ok(
        data={
            "token": result.token,
            "username": result.username,
            "expires_at": result.expires_at.isoformat(),
        },
        message=_MESSAGE_LOGIN_OK,
    )


@router.post("/refresh", response_model=LoginResponse)
def refresh_session(
    response: Response,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict:
    """Rotate a still-valid bearer token and prevent response caching."""
    if credentials is None:
        raise ApiError(
            HTTP_401_UNAUTHORIZED, CODE_UNAUTHORIZED, _MESSAGE_NOT_LOGGED_IN
        )
    try:
        result = auth.refresh(credentials.credentials)
    except TokenExpiredError as exc:
        raise ApiError(
            HTTP_401_UNAUTHORIZED, CODE_TOKEN_EXPIRED, _MESSAGE_TOKEN_EXPIRED
        ) from exc
    except TokenError as exc:
        raise ApiError(
            HTTP_401_UNAUTHORIZED, CODE_INVALID_TOKEN, _MESSAGE_TOKEN_INVALID
        ) from exc
    response.headers["Cache-Control"] = "no-store"
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
