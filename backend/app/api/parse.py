"""Parse transport: ``POST /api/parse`` batch URL validation + persistence.

This router is deliberately thin — the use case lives in
:class:`app.application.parse_service.ParseService`. The handler parses the
request body, delegates, and renders the PRD response shape
(``data: {results: [...], failed: [...]}``); every error path (domain
validation → 1001/1002/400, per-URL parser failures → the ``failed`` list)
comes from the service and the shared envelope machinery
(``app.api.responses``).

DI hook (override in tests via ``app.dependency_overrides``):

* :func:`get_parse_service` — the app-wired parse service (``app.state``).

``create_app`` populates ``app.state.parse_service``; handlers must only be
mounted on an app built by ``create_app`` (or one that sets the same state).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.responses import ok
from app.application.parse_service import ParseService
from app.domain import ParseResult

__all__ = [
    "ParseData",
    "ParseFailureData",
    "ParseRequest",
    "ParseResponse",
    "ParseResultData",
    "get_parse_service",
    "router",
]

router = APIRouter(prefix="/parse", tags=["parse"])

_MESSAGE_PARSE_OK = "解析成功 / Parse successful"


class ParseRequest(BaseModel):
    """A batch parse request: one or more source URLs.

    The list structure is checked here (a missing/blank/malformed body → 400;
    ``max_length=50`` is wire-level defense in depth — the domain owns the
    semantic rule via :class:`app.domain.ParseCommand`); the domain business
    rules (1..50 absolute http/https URLs) are enforced inside the service,
    which maps failures to the PRD codes (1001 URL为空, 1002 URL格式无效).
    """

    urls: list[str] = Field(max_length=50)


class ParseFailureData(BaseModel):
    """One URL the parser could not process (runtime failure, not validation).

    ``code`` is the PRD error code for typed engine failures (1003 平台不支持);
    ``None`` for untagged failures. Additive — the frontend may ignore it.
    """

    url: str
    error: str
    code: int | None = None


class ParseResultData(BaseModel):
    """The PRD ParseResult shape the frontend renders (type, not media_type)."""

    task_id: str
    url: str
    type: str
    platform: str
    title: str
    cover: str | None = None
    duration: str | None = None  # "MM:SS"
    file_size_mb: float | None = None
    format: str | None = None
    available_qualities: list[str] = Field(default_factory=list)
    available_bitrates: list[str] = Field(default_factory=list)
    video_url: str | None = None
    images: list[str] = Field(default_factory=list)


class ParseData(BaseModel):
    """The payload of a successful parse response."""

    results: list[ParseResultData]
    failed: list[ParseFailureData]


class ParseResponse(BaseModel):
    """The unified envelope for ``POST /api/parse`` success."""

    code: int
    message: str
    data: ParseData | None = None


def get_parse_service(request: Request) -> ParseService:
    """DI hook: the app-wired parse service (override in tests)."""
    return request.app.state.parse_service


def _serialize_result(result: ParseResult) -> dict:
    """Map a domain :class:`ParseResult` to the PRD ParseResult keys.

    ``video_url``/``images`` come from the engine's persisted metadata
    (stub-mode rows carry neither, so both are None/[]). ``images`` flattens
    the album's ``[{url, live_photo_url}, ...]`` list to plain URLs; entries
    without a string ``url`` are dropped defensively.
    """
    metadata = result.metadata or {}
    video_url = metadata.get("video_url")
    images = [
        img["url"]
        for img in metadata.get("images") or []
        if isinstance(img, dict) and isinstance(img.get("url"), str)
    ]
    return {
        "task_id": result.task_id,
        "url": result.url,
        "type": result.media_type.value,
        "platform": result.platform,
        "title": result.title,
        "cover": result.cover,
        "duration": result.duration,
        "file_size_mb": result.file_size_mb,
        "format": result.format,
        "available_qualities": list(result.available_qualities),
        "available_bitrates": list(result.available_bitrates),
        "video_url": video_url if isinstance(video_url, str) else None,
        "images": images,
    }


@router.post("", response_model=ParseResponse)
def parse_urls(
    body: ParseRequest,
    service: Annotated[ParseService, Depends(get_parse_service)],
) -> dict:
    """Parse one or more source URLs and persist the results.

    Success: ``200`` with ``data: {results: [...], failed: [...]}``. Batch
    validation failures raise the PRD envelope codes (1001 empty / 1002
    malformed / 400 over the 50-URL limit); per-URL parser failures appear in
    ``failed`` while the other URLs still parse.
    """
    batch = service.parse(body.urls)
    return ok(
        data={
            "results": [_serialize_result(result) for result in batch.results],
            "failed": [
                {"url": failure.url, "error": failure.error, "code": failure.code}
                for failure in batch.failed
            ],
        },
        message=_MESSAGE_PARSE_OK,
    )
