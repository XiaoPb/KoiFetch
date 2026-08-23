"""Unified response envelope and error mapping for the HTTP API.

Every endpoint returns the same shape — ``{code, message, data}`` — on success
*and* failure (the v1 design spec), including unexpected server errors. Success
is ``code == 0``; errors carry a stable numeric code from the table below,
mapped to an HTTP status.

Error-code table (PRD unless noted):

============  ======================  ==========  ==============================
code          meaning                 HTTP        notes
============  ======================  ==========  ==============================
0             成功 / ok               200         success
1             存储未就绪              200         health endpoint only (degraded
                                                    mode; predates this table)
400           请求参数错误            400         request validation failures
1001          URL为空                 400         parse: empty list or blank entry
1002          URL格式无效             400         parse: malformed URL (scheme,
                                                    host, whitespace, control
                                                    characters)
1003          平台不支持              400         parse: reserved for real engines
                                                    (the stub supports every URL)
3001          任务不存在              400         preview/download/NAS save:
                                                    unknown task_id or
                                                    download_id
3002          任务已在下载            409         download submit: an active
                                                    (pending/downloading) download
                                                    already exists for the task
3003          任务已完成              400         download submit: the identical
                                                    format+quality variant is
                                                    already completed
5001          文件不存在              404         download file / NAS save:
                                                    bubble file missing
5002          文件未下载完成          400         download file / NAS save: task
                                                    not completed (any non-
                                                    COMPLETED status, including
                                                    expired)
5003          Token无效或已过期       401         download file: missing, malformed,
                                                    expired, mis-targeted, or reused
                                                    one-time token (single code per
                                                    PRD — no 2003/2004 split here)
5004          文件已过期              410         download file: task expired
2001          未登录 / Not logged in  401         missing/malformed Authorization
2002          权限不足 / Forbidden    403         admin-only endpoints (Task 10)
2003          Token无效 / Invalid     401         malformed/tampered/mis-signed
2004          Token已过期 / Expired   401         well-formed but past expiry
2005          用户名或密码错误        401         *v1 addition*: the PRD defines
                                                    no wrong-password code, so
                                                    login failure reuses the 401
                                                    class with this stable code
9001          服务器内部错误          500         unexpected exceptions (the
                                                    generic handler below); detail
                                                    is logged, never returned
============  ======================  ==========  ==============================

``data`` is ``None`` on errors (the 500 handler carries a ``request_id`` for
log correlation) and holds the payload on success. Messages are human-readable
and bilingual (Chinese-first per product direction); they are user-visible, so
they must never embed secrets.

**Code conventions.** Framework-driven 404/405 responses mirror the HTTP status
as the body ``code`` (they have no PRD code). Domain errors — from Tasks 8-10
onward — must use explicit PRD codes (parse 1001-1005, download 3001-3003,
NAS 5001-5004) raised via :class:`ApiError`; do not reuse the HTTP-mirroring
convention for them. The parse range is only partially live today: ``1001``
URL为空, ``1002`` URL格式无效, ``1003`` 平台不支持 (reserved — the stub supports
every URL) have constants above; ``1004`` 解析超时 and ``1005`` 请求过于频繁 are
reserved for later tasks (parse timeout, rate limiter) and deliberately have no
constants yet, so the table and the constants cannot drift apart.

Handlers registered by :func:`register_exception_handlers`:

* :class:`ApiError` — the way services/handlers raise stable errors (Tasks
  8-10 reuse it). Its ``message`` must never contain credentials or paths.
* ``RequestValidationError`` — FastAPI's 422 becomes a 400 envelope so missing
  fields (e.g. an incomplete login body) match the documented contract.
* ``HTTPException`` (Starlette) — 404/405/... responses keep the envelope and
  preserve any ``WWW-Authenticate``-style headers the exception carries.
* ``Exception`` (fallback) — any unexpected error becomes a 500 envelope with
  code 9001; the exception (with request id/method/path) is logged server-side
  and never echoed to the client. This keeps the "every response is an
  envelope" contract even when a bug or an external dependency fails.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = [
    "CODE_BAD_REQUEST",
    "CODE_FILE_EXPIRED",
    "CODE_FILE_NOT_DOWNLOADED",
    "CODE_FILE_NOT_FOUND",
    "CODE_FILE_TOKEN_INVALID",
    "CODE_FORBIDDEN",
    "CODE_INTERNAL_ERROR",
    "CODE_INVALID_CREDENTIALS",
    "CODE_INVALID_TOKEN",
    "CODE_OK",
    "CODE_PLATFORM_UNSUPPORTED",
    "CODE_TASK_ALREADY_COMPLETED",
    "CODE_TASK_ALREADY_DOWNLOADING",
    "CODE_TASK_NOT_FOUND",
    "CODE_TOKEN_EXPIRED",
    "CODE_UNAUTHORIZED",
    "CODE_URL_EMPTY",
    "CODE_URL_INVALID",
    "ApiError",
    "error",
    "ok",
    "register_exception_handlers",
]

CODE_OK = 0
CODE_BAD_REQUEST = 400
CODE_URL_EMPTY = 1001
CODE_URL_INVALID = 1002
CODE_PLATFORM_UNSUPPORTED = 1003
CODE_TASK_NOT_FOUND = 3001
CODE_TASK_ALREADY_DOWNLOADING = 3002
CODE_TASK_ALREADY_COMPLETED = 3003
CODE_FILE_NOT_FOUND = 5001
CODE_FILE_NOT_DOWNLOADED = 5002
CODE_FILE_TOKEN_INVALID = 5003
CODE_FILE_EXPIRED = 5004
CODE_UNAUTHORIZED = 2001
CODE_FORBIDDEN = 2002
CODE_INVALID_TOKEN = 2003
CODE_TOKEN_EXPIRED = 2004
CODE_INVALID_CREDENTIALS = 2005
CODE_INTERNAL_ERROR = 9001

_MESSAGE_OK = "ok"
_MESSAGE_BAD_REQUEST = "请求参数错误 / Invalid request parameters"
_MESSAGE_INTERNAL_ERROR = "服务器内部错误 / Internal server error"

logger = logging.getLogger(__name__)


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
    # mirrors the HTTP status (these have no PRD code). Headers carried by the
    # exception (e.g. future WWW-Authenticate challenges) are preserved.
    return JSONResponse(
        status_code=exc.status_code,
        content=error(exc.status_code, str(exc.detail) or "请求失败 / Request failed"),
        headers=dict(exc.headers) if exc.headers else None,
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Last-resort handler: any unexpected error becomes a 500 envelope.

    The exception (with a correlation id and the request method/path) is logged
    server-side; the client only ever sees the generic 9001 envelope — internal
    detail and tracebacks are never echoed, even in debug mode.
    """
    request_id = uuid.uuid4().hex[:8]
    logger.exception(
        "unhandled error request_id=%s on %s %s",
        request_id,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content=error(
            CODE_INTERNAL_ERROR,
            _MESSAGE_INTERNAL_ERROR,
            data={"request_id": request_id},
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install the envelope-producing error handlers on ``app``."""
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
