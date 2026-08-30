"""Parse use cases (Task 8): batch validation, parser selection, persistence.

Sits in the application layer between the API transport (``app.api.parse``)
and the parser adapter / persistence: it validates a batch of URLs through the
domain :class:`~app.domain.ParseCommand`, parses each URL through the
:class:`~app.adapters.protocols.ParserAdapter` port, and persists every
successful :class:`~app.domain.ParseResult` as a :class:`ParseTask` row.

Design decisions (stable contract for Tasks 9-12):

* **Validation is the domain's job, codes decided up front.** The cheap
  batch-level rules are pre-checked in the service so their PRD codes cannot
  drift with the domain's wording: empty list / blank entry → ``1001``
  (URL为空), batch over the 50-URL limit → generic ``400`` (the PRD defines no
  dedicated code for the count limit). Every remaining
  :class:`~app.domain.ParseCommand` validation failure is a malformed URL →
  ``1002`` (URL格式无效). The raised :class:`~app.api.responses.ApiError`
  carries the message, so no URL is ever parsed before the whole batch is
  valid.
* **Per-URL isolation for runtime failures.** Domain validation rejects a
  malformed *request* wholesale (1001/1002), but a *parser* that fails on one
  URL (engine mode; the stub never raises) must not sink the batch:
  each URL is parsed *and its row built/queued* separately, so any exception
  in that per-URL step — a parser error or a result the persistence layer
  rejects (e.g. an unconvertible duration) — is collected into a ``failed``
  list while successes are still persisted and returned. The API renders this
  as the PRD ``data: {results, failed}`` shape. Engine failures are typed:
  ``EngineError`` subclasses surface their stable bilingual message verbatim
  with ``code: None``, except :class:`UnsupportedPlatformError` which carries
  code ``1003`` and :class:`CookieError` subclasses which carry code ``1006``
  (Cookie缺失/无效) (1004 解析超时 / 1005 请求过于频繁 remain reserved in
  responses.py). Untyped failures are sanitized (stable bilingual text +
  exception class name; raw exception text is never echoed, per the repo's
  message-safety convention). The stub derives metadata for every URL.
* **Option ladders survive in ``metadata``.** :class:`ParseTask` has no
  columns for ``file_size_mb`` / ``available_qualities`` / ``available_bitrates`
  (Task 4 ORM), so the service enriches the result's ``metadata`` JSON with
  them before persisting — the preview service (Task 8) reads them back from
  there. ``duration`` is converted from the PRD display form (``"MM:SS"``) to
  the ORM's integer seconds via :func:`app.domain.parse_duration`.
* **task_id is the parser's.** The stub mints a fresh ``uuid4`` per result; the
  service persists it unchanged (it is the row's primary key). Re-parsing the
  same URL therefore creates a new task, never a collision.
* **DI over globals.** The constructor takes the parser (defaulting to
  ``app.adapters.factory.get_parser``) and an optional ``engine``; ``create_app``
  wires the production instance and tests override the API dependency
  (``app.api.parse.get_parse_service``) with a service bound to a temp database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine
from starlette.status import HTTP_400_BAD_REQUEST

from app.adapters.engine_errors import (
    CookieError,
    EngineError,
    UnsupportedPlatformError,
)
from app.adapters.factory import get_parser
from app.adapters.protocols import ParserAdapter
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_COOKIE_ERROR,
    CODE_PLATFORM_UNSUPPORTED,
    CODE_URL_EMPTY,
    CODE_URL_INVALID,
    ApiError,
)
from app.domain import ParseCommand, ParseResult, parse_duration
from app.application.media_manifest import serialize_manifest
from app.infrastructure.database import session_scope
from app.infrastructure.models import ParseTask

__all__ = ["ParseBatchResult", "ParseFailure", "ParseService"]

_MESSAGE_URL_EMPTY = "URL为空 / URL is empty"
_MESSAGE_URL_INVALID = "URL格式无效 / Invalid URL format"
_MESSAGE_BATCH_TOO_LARGE = "单次最多解析50个URL / At most 50 URLs per request"
_MESSAGE_PARSE_FAILED = "解析失败 / Parse failed"


@dataclass(frozen=True)
class ParseFailure:
    """One URL that failed at parse time (validation failures are never here —
    they raise :class:`ApiError` before any parsing starts). ``code`` carries
    the PRD error code when the failure is typed (e.g. 1003 平台不支持 from a
    real engine); it is ``None`` for untagged engine failures."""

    url: str
    error: str
    code: int | None = None


@dataclass(frozen=True)
class ParseBatchResult:
    """Outcome of a batch parse: persisted results plus per-URL failures."""

    results: list[ParseResult]
    failed: list[ParseFailure]


class ParseService:
    """Validate, parse, and persist a batch of source URLs."""

    def __init__(
        self,
        parser: ParserAdapter | None = None,
        *,
        engine: Engine | None = None,
    ) -> None:
        self._parser = parser if parser is not None else get_parser()
        self._engine = engine

    def parse(self, urls: list[str]) -> ParseBatchResult:
        """Parse ``urls``, persisting each success; collect per-URL failures.

        Raises :class:`ApiError` (400 with code 1001/1002/400) when the batch
        itself is invalid — nothing is parsed or persisted then. Runtime
        failures (parser errors, or results the persistence layer rejects) are
        returned in ``failed``, never raised.
        """
        # Cheap batch-level rules are pre-checked here so their PRD codes
        # cannot drift with the domain's wording: empty list / blank entry →
        # 1001 (URL为空), batch over the 50-URL limit → generic 400 (the PRD
        # defines no dedicated count-limit code).
        if not urls:
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_URL_EMPTY, _MESSAGE_URL_EMPTY)
        if any(isinstance(url, str) and not url.strip() for url in urls):
            raise ApiError(HTTP_400_BAD_REQUEST, CODE_URL_EMPTY, _MESSAGE_URL_EMPTY)
        if len(urls) > ParseCommand.MAX_URLS:
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_BAD_REQUEST, _MESSAGE_BATCH_TOO_LARGE
            )
        try:
            command = ParseCommand(urls=urls)
        except ValidationError as exc:
            # With the empty/blank/count cases handled above, the only
            # remaining domain rules are malformed-URL ones (scheme, host,
            # whitespace, control characters) — all map to 1002.
            raise ApiError(
                HTTP_400_BAD_REQUEST, CODE_URL_INVALID, _MESSAGE_URL_INVALID
            ) from exc

        results: list[ParseResult] = []
        failed: list[ParseFailure] = []
        with session_scope(self._engine) as session:
            for url in command.urls:
                try:
                    parsed = self._parser.parse(ParseCommand(urls=[url]))[0]
                    # Row construction is inside the isolation: a real parser
                    # may return a result the persistence layer rejects (e.g.
                    # a duration the ORM cannot convert) — that must fail this
                    # URL only, never roll back the whole batch. Engine
                    # failures are handled by the typed `except EngineError`
                    # branch below (stable bilingual messages by contract);
                    # the broad `except Exception` remains only for genuine
                    # programming errors, sanitized as failures.
                    session.add(_task_row(parsed))
                except EngineError as exc:
                    failed.append(
                        ParseFailure(
                            url=url,
                            error=str(exc),  # stable bilingual message by contract
                            code=(
                                CODE_PLATFORM_UNSUPPORTED
                                if isinstance(exc, UnsupportedPlatformError)
                                else CODE_COOKIE_ERROR
                                if isinstance(exc, CookieError)
                                else None
                            ),
                        )
                    )
                    continue
                except Exception as exc:
                    failed.append(
                        ParseFailure(url=url, error=_failure_error(exc))
                    )
                    continue
                results.append(parsed)
        return ParseBatchResult(results=results, failed=failed)


def _failure_error(exc: Exception) -> str:
    """Build a safe, stable message for a per-URL parse failure.

    The raw exception text is never echoed — real engines may raise messages
    embedding filesystem paths, signed-token URLs, or internals. The client
    gets the stable bilingual message plus the exception *class name* as a
    diagnostic hint.
    """
    return f"{_MESSAGE_PARSE_FAILED} ({type(exc).__name__})"


def _task_row(result: ParseResult) -> ParseTask:
    """Map a :class:`ParseResult` to an ORM row (enriched metadata, secs)."""
    metadata: dict[str, Any] = dict(result.metadata)
    # Keep the compatibility URL fields private in persisted metadata while
    # normalizing the strict manifest to JSON arrays for SQLAlchemy JSON.
    metadata = serialize_manifest(metadata, media_type=result.media_type)
    metadata["file_size_mb"] = result.file_size_mb
    metadata["available_qualities"] = list(result.available_qualities)
    metadata["available_bitrates"] = list(result.available_bitrates)
    return ParseTask(
        task_id=result.task_id,
        url=result.url,
        platform=result.platform,
        media_type=result.media_type,
        title=result.title,
        cover_url=result.cover,
        duration=parse_duration(result.duration) if result.duration else None,
        format=result.format,
        metadata_=metadata,
    )
