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
* **Encrypted at rest.** New writes use the injected :class:`CookieCipher`;
  legacy plaintext rows remain readable until the migration script is run.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.infrastructure.database import session_scope
from app.infrastructure.models import PlatformCookie

logger = logging.getLogger(__name__)

__all__ = ["CookieCipher", "PlatformCookieService", "CookieStorageError"]


class CookieStorageError(Exception):
    """A cookie write failed; the message never carries the cookie value."""


_MESSAGE_STORAGE_ERROR = "Cookie 存储失败 / Cookie storage failed"
_COOKIE_AAD = b"koi-cookie-v1"


class CookieCipher:
    """Encrypt platform cookies with versioned AES-256-GCM envelopes."""

    PREFIX = "enc:v1:"

    def __init__(self, key_b64: str) -> None:
        if not isinstance(key_b64, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+={0,2}", key_b64
        ):
            raise ValueError("cookie encryption key must be URL-safe base64")
        try:
            key = base64.b64decode(
                key_b64.encode("ascii"), altchars=b"-_", validate=True
            )
        except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
            raise ValueError("cookie encryption key must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("cookie encryption key must decode to exactly 32 bytes")
        self._key = key
        self._aes = AESGCM(key)

    def encrypt(self, value: str) -> str:
        nonce = os.urandom(12)
        body = self._aes.encrypt(nonce, value.encode("utf-8"), _COOKIE_AAD)
        return self.PREFIX + base64.urlsafe_b64encode(nonce + body).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not isinstance(value, str):
            raise CookieStorageError(_MESSAGE_STORAGE_ERROR)
        if not value.startswith("enc:"):
            return value
        if not value.startswith(self.PREFIX):
            raise CookieStorageError(_MESSAGE_STORAGE_ERROR)
        payload = value[len(self.PREFIX) :]
        try:
            raw = base64.b64decode(
                payload.encode("ascii"), altchars=b"-_", validate=True
            )
            if len(raw) < 28:
                raise ValueError("truncated encrypted cookie")
            plaintext = self._aes.decrypt(raw[:12], raw[12:], _COOKIE_AAD)
            return plaintext.decode("utf-8")
        except (UnicodeEncodeError, ValueError, binascii.Error, InvalidTag):
            raise CookieStorageError(_MESSAGE_STORAGE_ERROR) from None


class PlatformCookieService:
    """DB-backed read/write store for per-platform cookies.

    ``cipher`` is mandatory so every new write is encrypted at rest.
    """

    def __init__(
        self, *, cipher: CookieCipher, engine: Engine | None = None
    ) -> None:
        self._engine = engine
        self._cipher = cipher

    # -- CookieProvider (read side) ----------------------------------------

    def get(self, platform: str) -> str | None:
        """Return the stored cookie for ``platform`` (None when unset)."""
        with session_scope(self._engine) as session:
            row = session.get(PlatformCookie, platform)
        if row is None:
            return None
        return self._cipher.decrypt(row.cookie)

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
        stored_cookie = self._cipher.encrypt(cookie)
        stmt = (
            sqlite_insert(PlatformCookie)
            .values(
                platform=platform,
                cookie=stored_cookie,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[PlatformCookie.platform],
                set_={
                    "cookie": stored_cookie,
                    "updated_at": now,
                },
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
