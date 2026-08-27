"""Per-platform cookie storage for the f2 parser (admin-configured).

Stores one raw cookie string per platform (douyin/weibo/tiktok today) in the
``platform_cookies`` table and exposes read + write operations. The f2 parser
adapter consumes the read side through
:class:`app.adapters.protocols.CookieProvider`; the cookies HTTP API
(``app.api.cookies``) exposes the write side to the admin UI.

Design decisions:

* **Never echo the cookie.** The read side is for server-side parsing only;
  the API layer returns ``configured``/``updated_at``, never the value, and
  this service logs nothing.
* **Platform-agnostic.** Any non-empty platform string is accepted (the API
  validates the shape); the frontend owns the current platform list, so f2
  gaining a platform later needs no backend change.
* **Plaintext at rest, documented.** v1 has a single admin and an admin-only
  API; encrypt-at-rest is a v1.1 hardening (see the ORM model docstring).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine

from app.infrastructure.database import session_scope
from app.infrastructure.models import PlatformCookie

__all__ = ["PlatformCookieService"]


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
        blank (raises :class:`ValueError` otherwise).
        """
        cookie = cookie.strip()
        if not cookie:
            raise ValueError("cookie must not be blank")
        now = datetime.now(timezone.utc)
        with session_scope(self._engine) as session:
            row = session.get(PlatformCookie, platform)
            if row is None:
                session.add(
                    PlatformCookie(platform=platform, cookie=cookie, updated_at=now)
                )
            else:
                row.cookie = cookie
                row.updated_at = now
        return _entry(platform, True, now)

    def delete(self, platform: str) -> None:
        """Remove the row for ``platform`` (no-op when absent)."""
        with session_scope(self._engine) as session:
            row = session.get(PlatformCookie, platform)
            if row is not None:
                session.delete(row)

    def list(self) -> list[dict[str, Any]]:
        """Return public entries for every configured platform."""
        with session_scope(self._engine) as session:
            rows = (
                session.query(PlatformCookie)
                .order_by(PlatformCookie.platform)
                .all()
            )
            return [_entry(row.platform, True, row.updated_at) for row in rows]


def _entry(
    platform: str, configured: bool, updated_at: datetime | None
) -> dict[str, Any]:
    """The public cookie entry: platform + status, never the cookie value."""
    return {
        "platform": platform,
        "configured": configured,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }
