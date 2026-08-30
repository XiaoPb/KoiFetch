"""JWT token providers: access tokens (7d) and reusable download tokens (5 min).

Implements :class:`app.adapters.protocols.AccessTokenProvider` and
:class:`app.adapters.protocols.OneTimeTokenProvider` with PyJWT (HS256, shared
``SECRET_KEY``). Both providers are stateless: nothing is stored server-side,
so any number of app instances can validate tokens.

Claim layout (documented contract — the auth service of Task 7 and the
download-file API of Task 9 consume these):

* Access token: ``sub`` = user id as a string, ``username``, ``jti``, ``iat``,
  ``exp`` (issued-at + 7d by default).
* Download token: ``tid`` = a fresh ``uuid4`` *token id*, ``dl`` = the
  download id the token authorizes, ``iat``, ``exp`` (issued-at + 5 min).
  The download-file service binds that task id to its stored filename before
  serving the file; the filename is not a free-form client-controlled target.

**Design decision — validation is stateless and reusable.** ``validate`` is
pure: it never consumes a token, and calling it repeatedly returns the same
claims. The download-file API records the returned ``token_id`` and expiry for
task+filename binding/audit, but does not reject later requests carrying the
same id.
Keeping the adapter stateless lets the same provider scale freely; expiry is
the security boundary and repeated Range/HEAD playback requests are allowed.

Errors: :meth:`validate` raises only :class:`app.adapters.protocols.TokenError`
subclasses (``TokenExpiredError`` / ``InvalidTokenError``) so callers never
catch PyJWT exceptions directly.

**Secret strength:** ``Settings.secret_key`` has no strength floor (Task 2
settings contract — do not add validation there). HS256 keys should be at
least 32 random bytes; deployments must set a strong ``SECRET_KEY`` and local
examples should keep their placeholder ≥32 characters.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from app.adapters.protocols import (
    AccessTokenClaims,
    AccessTokenProvider,
    InvalidTokenError,
    OneTimeTokenClaims,
    OneTimeTokenProvider,
    TokenExpiredError,
)

__all__ = ["JwtAccessTokenProvider", "JwtOneTimeTokenProvider"]

_ALGORITHM = "HS256"
_ACCESS_TTL = timedelta(days=7)
_ONE_TIME_TTL = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_non_blank(value: object, claim: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidTokenError(f"token missing required claim: {claim}")
    return value


def _require_datetime(value: object, claim: str) -> datetime:
    """Normalize a decoded date claim (datetime or Unix timestamp) to UTC."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # PyJWT serializes datetimes to Unix timestamps on encode.
        return datetime.fromtimestamp(value, tz=timezone.utc)
    raise InvalidTokenError(f"token claim {claim} is not a valid date")


class _JwtProviderBase:
    """Shared encode/decode plumbing around PyJWT."""

    def __init__(self, secret_key: str, *, ttl: timedelta) -> None:
        if not secret_key:
            raise ValueError("secret_key must not be empty")
        if not isinstance(ttl, timedelta):
            raise TypeError("ttl must be a timedelta")
        self._secret = secret_key
        self._ttl = ttl

    def _encode(self, payload: dict) -> str:
        return pyjwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def _decode(self, token: str, require: tuple[str, ...]) -> dict:
        try:
            return pyjwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={"require": list(require)},
            )
        except pyjwt.ExpiredSignatureError as exc:
            raise TokenExpiredError(str(exc)) from exc
        except pyjwt.InvalidTokenError as exc:
            raise InvalidTokenError(str(exc)) from exc


class JwtAccessTokenProvider(_JwtProviderBase, AccessTokenProvider):
    """HS256 access tokens valid for seven days by default."""

    def __init__(
        self, secret_key: str, *, ttl: timedelta = _ACCESS_TTL
    ) -> None:
        super().__init__(secret_key, ttl=ttl)

    def issue(self, *, user_id: int, username: str) -> str:
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise TypeError(f"user_id must be an int, got {type(user_id).__name__}")
        if user_id < 0:
            # Must agree with _parse_user_id: a negative id would encode to
            # "-1", which validate() rejects ("-1".isdigit() is False), so the
            # provider refuses to mint a token it would reject itself.
            raise ValueError(f"user_id must be >= 0, got {user_id}")
        username = username.strip()
        if not username:
            raise ValueError("username must not be blank")
        now = _now()
        return self._encode(
            {
                "sub": str(user_id),
                "username": username,
                "jti": str(uuid.uuid4()),
                "iat": now,
                "exp": now + self._ttl,
            }
        )

    def validate(self, token: str) -> AccessTokenClaims:
        payload = self._decode(token, require=("exp", "iat", "jti"))
        user_id = self._parse_user_id(payload.get("sub"))
        username = _require_non_blank(payload.get("username"), "username")
        token_id = _require_non_blank(payload.get("jti"), "jti")
        return AccessTokenClaims(
            user_id=user_id,
            username=username,
            token_id=token_id,
            issued_at=_require_datetime(payload.get("iat"), "iat"),
            expires_at=_require_datetime(payload.get("exp"), "exp"),
        )

    @staticmethod
    def _parse_user_id(value: object) -> int:
        if isinstance(value, bool):
            raise InvalidTokenError("token subject is not a valid user id")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        raise InvalidTokenError("token subject is not a valid user id")


class JwtOneTimeTokenProvider(_JwtProviderBase, OneTimeTokenProvider):
    """HS256 download tokens reusable for 5 minutes."""

    def __init__(
        self, secret_key: str, *, ttl: timedelta = _ONE_TIME_TTL
    ) -> None:
        super().__init__(secret_key, ttl=ttl)

    def issue(self, *, download_id: str) -> str:
        download_id = (download_id or "").strip()
        if not download_id:
            raise ValueError("download_id must not be blank")
        now = _now()
        return self._encode(
            {
                "tid": str(uuid.uuid4()),
                "dl": download_id,
                "iat": now,
                "exp": now + self._ttl,
            }
        )

    def validate(self, token: str) -> OneTimeTokenClaims:
        payload = self._decode(token, require=("exp", "iat"))
        token_id = _require_non_blank(payload.get("tid"), "tid")
        download_id = _require_non_blank(payload.get("dl"), "dl")
        return OneTimeTokenClaims(
            token_id=token_id,
            download_id=download_id,
            issued_at=_require_datetime(payload.get("iat"), "iat"),
            expires_at=_require_datetime(payload.get("exp"), "exp"),
        )
