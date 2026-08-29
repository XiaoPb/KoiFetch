"""Admin authentication use cases (v1: a single seeded admin, no registration).

Sits in the application layer between the API transport (``app.api``) and the
persistence/adapters: it authenticates the admin against the ``users`` table
with bcrypt and issues 24-hour access tokens through the
:class:`app.adapters.protocols.AccessTokenProvider` port — never touching
PyJWT or bcrypt specifics in the handlers.

Design decisions (stable contract for Tasks 8-12):

* **One account, no registration.** v1 seeds exactly one administrator (see
  ``app.infrastructure.seed``); there is no signup and no refresh-token flow.
* **Failures are indistinguishable — in result *and* timing.** A nonexistent
  username and a wrong password return the same result (``None``), and the
  missing-user path runs a bcrypt comparison against a fixed dummy hash so the
  two paths take comparable time — callers cannot enumerate accounts by
  response shape or timing. Blank/non-string inputs are rejected before any
  database or bcrypt work.
* **72-byte bcrypt limit handled explicitly.** bcrypt 3.2+ silently
  *truncates* passwords over 72 bytes (it does not raise), so without a guard
  ``checkpw`` on a long password whose first 72 bytes match a stored hash would
  succeed. :meth:`AuthService._verify_password` therefore rejects any password
  over 72 bytes *before* calling bcrypt, so the service never authenticates on
  a truncated prefix. The seed (``app.infrastructure.seed``) rejects over-long
  ``ADMIN_PASSWORD`` values for the same reason.
* **Secrets never leave the bcrypt call.** Passwords are handed straight to
  ``bcrypt.checkpw`` and are never logged, stored, or echoed. Malformed
  *stored hashes* (a corrupt DB row) fail closed — treated as an
  authentication failure, never a server error.
* **DI over globals.** The service takes an explicit ``token_provider`` (from
  ``app.adapters.factory.get_access_token_provider``) and an optional
  ``engine``; ``create_app`` wires the production instance and tests override
  the API dependency (``app.api.auth.get_auth_service``) with a service bound
  to a temp database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import bcrypt
from sqlalchemy import Engine, select

from app.adapters.protocols import AccessTokenProvider
from app.infrastructure.database import session_scope
from app.infrastructure.models import User
from app.application.login_limiter import normalize_username

__all__ = ["AuthService", "LoginResult"]

# Bcrypt's input limit is 72 bytes (any trailing bytes are silently truncated
# by bcrypt 3.2+). Reject longer passwords before bcrypt sees them.
_BCRYPT_MAX_PASSWORD_BYTES = 72

# A fixed, valid bcrypt hash (cost factor 12, matching the seed) used to
# equalize login timing for nonexistent usernames: the missing-user path runs
# a real comparison against this hash so it costs roughly as much as a
# wrong-password attempt. The result is ignored.
_DUMMY_HASH = (
    "$2b$12$HPoisdLphOu2GdHztnF5De8SWJba4EozD9RcYix.QMjAQ/oLZ0Pg."
)


@dataclass(frozen=True)
class LoginResult:
    """A successful login: the issued token plus what the client needs to show.

    ``expires_at`` mirrors the token's ``exp`` claim (issued-at + 24h) so the
    API can return it without re-deriving the TTL.
    """

    token: str
    username: str
    expires_at: datetime


class AuthService:
    """Authenticate the admin and issue 24-hour access tokens.

    ``token_provider`` is required (obtain it from
    ``app.adapters.factory.get_access_token_provider``). ``engine`` defaults to
    the configured engine (:func:`app.infrastructure.database.get_engine`) and
    can be pinned for tests.
    """

    def __init__(
        self,
        token_provider: AccessTokenProvider,
        *,
        engine: Engine | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._engine = engine

    def authenticate(self, username: str, password: str) -> User | None:
        """Return the admin :class:`User` when credentials match, else ``None``.

        Missing user and wrong password return the same ``None`` and perform
        comparable bcrypt work (see module docstring), so callers cannot tell
        them apart. The password is only ever handed to bcrypt — never logged,
        stored, or echoed.
        """
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        username = normalize_username(username)
        if not username or not password:
            return None
        with session_scope(self._engine) as session:
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                # Timing parity: run a bcrypt comparison against a dummy hash
                # so the missing-user path costs about as much as a
                # wrong-password attempt (result is ignored).
                self._verify_password(password, _DUMMY_HASH)
                return None
            if not self._verify_password(password, user.password_hash):
                return None
            return user

    def issue_access_token(self, user: User) -> str:
        """Issue a 24-hour access token for ``user`` via the configured provider."""
        return self._token_provider.issue(
            user_id=user.id, username=user.username
        )

    def login(self, username: str, password: str) -> LoginResult | None:
        """Authenticate and issue a token; return ``None`` on any failure.

        Convenience used by ``POST /api/auth/login``; validates the freshly
        issued token to derive ``expires_at`` for the response.
        """
        user = self.authenticate(username, password)
        if user is None:
            return None
        token = self.issue_access_token(user)
        claims = self._token_provider.validate(token)
        return LoginResult(
            token=token,
            username=user.username,
            expires_at=claims.expires_at,
        )

    @staticmethod
    def _verify_password(password: str, stored_hash: str) -> bool:
        """bcrypt-verify; any out-of-contract input fails closed (never raises).

        Passwords over 72 bytes are rejected explicitly: bcrypt 3.2+ silently
        truncates them, which would let a long password authenticate against a
        hash of its 72-byte prefix. A malformed stored hash (corrupt DB row)
        is caught and treated as an authentication failure.
        """
        if len(password.encode("utf-8")) > _BCRYPT_MAX_PASSWORD_BYTES:
            return False
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"), stored_hash.encode("utf-8")
            )
        except ValueError:
            # bcrypt raises ValueError only for malformed hashes now (the
            # >72-byte case is handled above); fail closed either way.
            return False
