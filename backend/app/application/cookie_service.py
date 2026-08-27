"""Per-platform cookie storage for the f2 parser (admin-configured).

Stores one raw cookie string per platform (douyin/weibo/tiktok today) in the
``platform_cookies`` table and exposes read + write operations. The f2 parser
adapter consumes the read side through
:class:`app.adapters.protocols.CookieProvider`; the cookies HTTP API
(``app.api.cookies``) exposes the write side to the admin UI.

Design decisions:

* **Never echo the cookie.** The read side is for server-side parsing only;
  the API layer returns ``configured``/``updated_at``, never the value, and
  the write path is leak-free: ``set``/``delete`` log only the failing error
  class name and raise :class:`CookieStorageError` with a fixed message, so
  the cookie never rides an exception repr or log line (the API's 500 handler
  logs every unhandled exception, which would otherwise embed the cookie in
  the statement parameters). ``set`` is an atomic upsert, so concurrent
  writers cannot race into a duplicate-key error either.
* **Platform-agnostic.** Any non-empty platform string is accepted (blank is
  rejected here; the API validates the shape); the frontend owns the current
  platform list, so f2 gaining a platform later needs no backend change.
* **Plaintext at rest, documented.** v1 has a single admin and an admin-only
  API; encrypt-at-rest is a v1.1 hardening (see the ORM model docstring).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.infrastructure.database import session_scope
from app.infrastructure.models import PlatformCookie

logger = logging.getLogger(__name__)

__all__ = ["PlatformCookieService", "CookieStorageError"]


class CookieStorageError(Exception):
    """A cookie write failed; the message never carries the cookie value."""


_MESSAGE_STORAGE_ERROR = "Cookie 存储失败 / Cookie storage failed"


class PlatformCookieService:
    """DB-backed read/write store for per-platform cookies."""

    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine

    # -- CookieProvider (read side) ----------------------------------------

    def get(self, platform: str) -> str | None:
        """Return the stored cookie for ``platform`` (None when unset)."""
        with session_scope(self._engine) as session:
            row = session.get(PlatformCookie, platform)
        return row.cookie if row is not None else None

    # -- write side --------------------------------------------------------

    def set(self, platform: str, cookie: str) -> dict[str, Any]:
        """Upsert the cookie for ``platform``; return its public entry.

        ``cookie`` is stripped of surrounding whitespace and must not be
        blank (raises :class:`ValueError` otherwise); the platform must not
        be blank either. The write is a single atomic upsert and any storage
        failure surfaces as :class:`CookieStorageError` (never a DB exception
        whose repr embeds the cookie).
        """
        if not platform or not platform.strip():
            raise ValueError("platform must not be blank")
        cookie = cookie.strip()
        if not cookie:
            raise ValueError("cookie must not be blank")
        now = datetime.now(timezone.utc)
        # SQLite dialect upsert: the repo is SQLite-only in v1 (config default
        # and docker compose both use ``sqlite://``; the migrations already use
        # SQLite-specific ``batch_alter_table``).
        stmt = (
            sqlite_insert(PlatformCookie)
            .values(platform=platform, cookie=cookie, updated_at=now)
            .on_conflict_do_update(
                index_elements=[PlatformCookie.platform],
                set_={"cookie": cookie, "updated_at": now},
            )
        )
        try:
            with session_scope(self._engine) as session:
                session.execute(stmt)
        except SQLAlchemyError as exc:
            # Never let the cookie ride an exception repr into logs: log only
            # the class name (no exception object, no parameters) and raise a
            # fixed-message error WITHOUT __cause__ chaining.
            logger.error("platform cookie write failed: %s", type(exc).__name__)
            raise CookieStorageError(_MESSAGE_STORAGE_ERROR) from None
        return _entry(platform, now)

    def delete(self, platform: str) -> None:
        """Remove the row for ``platform`` (no-op when absent)."""
        if not platform or not platform.strip():
            raise ValueError("platform must not be blank")
        try:
            with session_scope(self._engine) as session:
                row = session.get(PlatformCookie, platform)
                if row is not None:
                    session.delete(row)
        except SQLAlchemyError as exc:
            # Never let the cookie ride an exception repr into logs: log only
            # the class name (no exception object, no parameters) and raise a
            # fixed-message error WITHOUT __cause__ chaining.
            logger.error("platform cookie write failed: %s", type(exc).__name__)
            raise CookieStorageError(_MESSAGE_STORAGE_ERROR) from None

    def list(self) -> list[dict[str, Any]]:
        """Return public entries for every configured platform."""
        with session_scope(self._engine) as session:
            rows = session.scalars(
                select(PlatformCookie).order_by(PlatformCookie.platform)
            ).all()
            return [_entry(row.platform, row.updated_at) for row in rows]


def _entry(platform: str, updated_at: datetime) -> dict[str, str | bool]:
    """The public cookie entry: platform + status, never the cookie value."""
    return {
        "platform": platform,
        "configured": True,
        "updated_at": updated_at.isoformat(),
    }
