"""Typed exceptions for the real engine adapters (parse-video-py / musicdl).

The stub era had no failure modes: the stub parser derives everything from the
URL and the stub downloader always succeeds. Real engines add real failures —
DNS/connect errors, timeouts, platform anti-scraping (403/risk control),
unsupported platforms — and callers need stable, user-safe messages for them.

Contract (relied on by parse_service and the worker):

* Every error raised by the engine adapters is an :class:`EngineError`
  subclass whose ``str()`` is the stable bilingual client-facing message —
  never raw engine/exception text, filesystem paths, or URLs (the repo's
  message-safety convention). The original engine exception is preserved as
  ``__cause__`` for logging.
* :class:`UnsupportedPlatformError` maps to PRD code 1003 平台不支持 in
  parse_service. The parser adapter raises it directly as a routing decision;
  translation never produces it.
* :func:`translate_engine_exception` maps the exception classes the engines
  actually raise (httpx for parse-video-py, requests for musicdl) onto this
  hierarchy. ``operation`` is "parse" or "download" and selects the
  operation-specific error class/message for generic HTTP failures.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx

__all__ = [
    "EngineDownloadError",
    "EngineError",
    "EngineNetworkError",
    "EngineParseError",
    "EngineTimeoutError",
    "PlatformBlockedError",
    "UnsupportedPlatformError",
    "translate_engine_exception",
]

_MSG = {
    "network": "网络错误 / Network error",
    "blocked": "平台风控，请求被拦截 / Platform anti-scraping blocked the request",
    "parse_timeout": "解析超时 / Parse timeout",
    "download_timeout": "下载超时 / Download timeout",
    "parse_failed": "解析失败 / Parse failed",
    "download_failed": "下载失败 / Download failed",
}

_Operation = Literal["parse", "download"]


class EngineError(Exception):
    """Base class: ``str(error)`` is the stable bilingual client message."""


class UnsupportedPlatformError(EngineError):
    """The URL's platform has no engine (parse-service maps to code 1003)."""


class EngineNetworkError(EngineError):
    """DNS failure, connection refused/reset."""


class EngineTimeoutError(EngineError):
    """The engine request exceeded the configured timeout."""


class PlatformBlockedError(EngineError):
    """The platform refused the request (403 / risk control / captcha)."""


class EngineParseError(EngineError):
    """The engine ran but could not produce metadata for this URL."""


class EngineDownloadError(EngineError):
    """The download failed (HTTP error mid-stream, missing media, engine error)."""


def _category_of(exc: BaseException) -> str:
    """Classify an engine exception into a category string.

    The order that matters is the requests branch: ``ConnectTimeout`` is a
    subclass of BOTH ``Timeout`` and ``ConnectionError``, so the timeout check
    must come first. In httpx the branches are siblings (``TimeoutException``
    is not a ``NetworkError`` subclass), so their relative order is cosmetic;
    403 is still checked before the generic HTTP branch.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.NetworkError):
        return "network"
    if isinstance(exc, httpx.HTTPStatusError):
        return "blocked" if exc.response.status_code == 403 else "http"
    if isinstance(exc, httpx.HTTPError):
        return "http"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    try:
        import requests  # musicdl dependency; guarded so imports stay light
    except ImportError:
        requests = None
    if requests is not None:
        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout"
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "network"
        if isinstance(exc, requests.exceptions.HTTPError):
            status = exc.response.status_code if exc.response is not None else None
            return "blocked" if status == 403 else "http"
    return "unknown"


def translate_engine_exception(
    exc: BaseException,
    *,
    url: str,
    operation: _Operation,
) -> EngineError:
    """Build the typed :class:`EngineError` for an engine exception.

    The caller raises the result with ``raise ... from exc`` so the original
    exception stays attached as ``__cause__`` for logging. ``url`` is kept as
    a parameter for future logging detail; it is never embedded in messages.
    """
    category = _category_of(exc)
    if category == "network":
        return EngineNetworkError(_MSG["network"])
    if category == "blocked":
        return PlatformBlockedError(_MSG["blocked"])
    if category == "timeout":
        message = _MSG["parse_timeout" if operation == "parse" else "download_timeout"]
        return EngineTimeoutError(message)
    message = _MSG["parse_failed" if operation == "parse" else "download_failed"]
    return EngineParseError(message) if operation == "parse" else EngineDownloadError(message)
