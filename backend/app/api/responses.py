"""Unified response envelope and error mapping for the HTTP API.

Every endpoint returns the same shape — ``{code, message, data}`` — on success
*and* failure (the v1 design spec). Success is ``code == 0``; errors carry a
stable numeric code from the table below, mapped to an HTTP status.

Error-code table (PRD unless noted):

============  ======================  ==========  ==============================
code          meaning                 HTTP        notes
============  ======================  ==========  ==============================
0             成功 / ok               200         success
400           请求参数错误            400         request validation failures
2001          未登录 / Not logged in  401         missing/malformed Authorization
2002          权限不足 / Forbidden    403         admin-only endpoints (Task 10)
2003          Token无效 / Invalid     401         malformed/tampered/mis-signed
2004          Token已过期 / Expired   401         well-formed but past expiry
2005          用户名或密码错误        401         *v1 addition*: the PRD defines
                                                    no wrong-password code, so
                                                    login failure reuses the 401
                                                    class with this stable code
============  ======================  ==========  ==============================

``data`` is ``None`` on errors and holds the payload on success. Messages are
human-readable and bilingual (Chinese-first per product direction); they are
user-visible, so they must never embed secrets.

Handlers registered by :func:`register_exception_handlers`:

* :class:`ApiError` — the way services/handlers raise stable errors (Tasks
  8-10 reuse it). Its ``message`` must never contain credentials or paths.
* ``RequestValidationError`` — FastAPI's 422 becomes a 400 envelope so missing
  fields (e.g. an incomplete login body) match the documented contract.
* ``HTTPException`` (Starlette) — 404/405/... responses keep the envelope;
  the body ``code`` mirrors the HTTP status for these framework-driven errors.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = [
    "CODE_BAD_REQUEST",
    "CODE_FORBIDDEN",
    "CODE_INVALID_CREDENTIALS",
    "CODE_INVALID_TOKEN",
    "CODE_OK",
    "CODE_TOKEN_EXPIRED",
    "CODE_UNAUTHORIZED",
    "ApiError",
    "error",
    "ok",
    "register_exception_handlers",
]

CODE_OK = 0
CODE_BAD_REQUEST = 400
CODE_UNAUTHORIZED = 2001
CODE_FORBIDDEN = 2002
CODE_INVALID_TOKEN = 2003
CODE_TOKEN_EXPIRED = 2004
CODE_INVALID_CREDENTIALS = 2005

_MESSAGE_OK = "ok"
_MESSAGE_BAD_REQUEST = "请求参数错误 / Invalid request parameters"


def ok(data: Any = None, message: str = _MESSAGE_OK) -> dict:
    """Build a success envelope: ``{code: 0, message, data}``."""
    return {"code": CODE_OK, "message": message, "data": data}


def error(code: int, message: str, *, data: Any = None) -> dict:
    """Build an error envelope: ``{code, message, data}`` (``data`` None)."""
    return {"code": code, "message": message, "data": data}


class ApiError(Exception):
    """An API-level failure with a stable envelope code and HTTP status.

    Raise it from handlers/dependencies/services; :func:`api_error_handler`
    converts it to the envelope. ``message`` is user-visible — never embed
    credentials, tokens, or filesystem paths.
    """

    def __init__(self, http_status: int, code: int, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status, content=error(exc.code, exc.message)
    )


async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # FastAPI reports malformed/missing fields as 422; the API contract says
    # bad requests are 400. Field-level detail is deliberately omitted in v1.
    return JSONResponse(
        status_code=400, content=error(CODE_BAD_REQUEST, _MESSAGE_BAD_REQUEST)
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    # Framework-driven errors (404/405/...) keep the envelope; the body code
    # mirrors the HTTP status (these have no PRD code).
    return JSONResponse(
        status_code=exc.status_code,
        content=error(exc.status_code, str(exc.detail) or "请求失败 / Request failed"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install the envelope-producing error handlers on ``app``."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
