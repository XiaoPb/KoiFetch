"""Tests for the JWT token providers (``app/adapters/tokens_jwt.py``).

Covers: issue/validate round-trips, the 24h access-token expiry, the 5-minute
one-time token expiry, invalid-signature/garbage/tampered-token rejection, and
the documented design decision that one-time token *single-use* enforcement
lives in the caller (the API layer of Task 9), not in the adapter: ``validate``
is stateless and returns a ``token_id`` the caller can record to detect reuse.
"""

from datetime import timedelta

import pytest

from app.adapters.protocols import (
    InvalidTokenError,
    TokenExpiredError,
)
from app.adapters.tokens_jwt import (
    JwtAccessTokenProvider,
    JwtOneTimeTokenProvider,
)

DOWNLOAD_ID = "33333333-3333-3333-3333-333333333333"
ANOTHER_DOWNLOAD_ID = "44444444-4444-4444-4444-444444444444"


class TestJwtAccessTokenProvider:
    def test_issue_validate_round_trip(self):
        provider = JwtAccessTokenProvider("test-secret")
        token = provider.issue(user_id=7, username="admin")
        assert isinstance(token, str) and token
        claims = provider.validate(token)
        assert claims.user_id == 7
        assert claims.username == "admin"
        assert claims.expires_at > claims.issued_at

    def test_access_token_lasts_24_hours(self):
        provider = JwtAccessTokenProvider("test-secret")
        claims = provider.validate(provider.issue(user_id=1, username="u"))
        assert claims.expires_at - claims.issued_at == timedelta(hours=24)

    def test_ttl_override(self):
        provider = JwtAccessTokenProvider("test-secret", ttl=timedelta(hours=1))
        claims = provider.validate(provider.issue(user_id=1, username="u"))
        assert claims.expires_at - claims.issued_at == timedelta(hours=1)

    def test_expired_token_rejected(self):
        provider = JwtAccessTokenProvider("test-secret", ttl=timedelta(seconds=-5))
        token = provider.issue(user_id=1, username="u")
        with pytest.raises(TokenExpiredError):
            provider.validate(token)

    def test_invalid_signature_rejected(self):
        token = JwtAccessTokenProvider("secret-a").issue(user_id=1, username="u")
        other = JwtAccessTokenProvider("secret-b")
        with pytest.raises(InvalidTokenError):
            other.validate(token)

    def test_garbage_token_rejected(self):
        provider = JwtAccessTokenProvider("test-secret")
        with pytest.raises(InvalidTokenError):
            provider.validate("definitely-not-a-jwt")

    def test_tampered_token_rejected(self):
        provider = JwtAccessTokenProvider("test-secret")
        token = provider.issue(user_id=1, username="u")
        with pytest.raises(InvalidTokenError):
            provider.validate(token + "x")

    def test_token_without_required_claims_rejected(self):
        import jwt as pyjwt

        provider = JwtAccessTokenProvider("test-secret")
        # Signed with the same secret but missing sub/username/exp/iat.
        forged = pyjwt.encode({"foo": "bar"}, "test-secret", algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            provider.validate(forged)

    def test_wrong_subject_type_rejected(self):
        import jwt as pyjwt
        from datetime import datetime, timezone

        provider = JwtAccessTokenProvider("test-secret")
        forged = pyjwt.encode(
            {
                "sub": "not-an-int",
                "username": "u",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            "test-secret",
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            provider.validate(forged)

    def test_blank_username_rejected(self):
        import jwt as pyjwt
        from datetime import datetime, timezone

        provider = JwtAccessTokenProvider("test-secret")
        forged = pyjwt.encode(
            {
                "sub": 1,
                "username": "   ",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            "test-secret",
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            provider.validate(forged)


class TestJwtOneTimeTokenProvider:
    def test_issue_validate_round_trip(self):
        provider = JwtOneTimeTokenProvider("test-secret")
        token = provider.issue(download_id=DOWNLOAD_ID)
        claims = provider.validate(token)
        assert claims.download_id == DOWNLOAD_ID
        assert claims.token_id  # non-empty, used by the caller for single-use
        assert claims.expires_at > claims.issued_at

    def test_one_time_token_lasts_5_minutes(self):
        provider = JwtOneTimeTokenProvider("test-secret")
        claims = provider.validate(provider.issue(download_id=DOWNLOAD_ID))
        assert claims.expires_at - claims.issued_at == timedelta(minutes=5)

    def test_expired_token_rejected(self):
        provider = JwtOneTimeTokenProvider("test-secret", ttl=timedelta(seconds=-5))
        token = provider.issue(download_id=DOWNLOAD_ID)
        with pytest.raises(TokenExpiredError):
            provider.validate(token)

    def test_invalid_signature_rejected(self):
        token = JwtOneTimeTokenProvider("secret-a").issue(download_id=DOWNLOAD_ID)
        other = JwtOneTimeTokenProvider("secret-b")
        with pytest.raises(InvalidTokenError):
            other.validate(token)

    def test_garbage_token_rejected(self):
        provider = JwtOneTimeTokenProvider("test-secret")
        with pytest.raises(InvalidTokenError):
            provider.validate("garbage")

    def test_token_ids_are_unique_per_issue(self):
        provider = JwtOneTimeTokenProvider("test-secret")
        first = provider.validate(provider.issue(download_id=DOWNLOAD_ID))
        second = provider.validate(provider.issue(download_id=DOWNLOAD_ID))
        assert first.token_id != second.token_id
        assert first.download_id == second.download_id == DOWNLOAD_ID

    def test_validate_is_stateless_single_use_is_caller_concern(self):
        # Documented design decision: the adapter never marks a token used;
        # validate() may be called repeatedly and returns the same claims.
        # Task 9 enforces single-use by recording the returned token_id
        # (atomically) before serving the file.
        provider = JwtOneTimeTokenProvider("test-secret")
        token = provider.issue(download_id=DOWNLOAD_ID)
        first = provider.validate(token)
        second = provider.validate(token)
        assert first == second
        assert first.token_id

    def test_missing_claims_rejected(self):
        import jwt as pyjwt
        from datetime import datetime, timezone

        provider = JwtOneTimeTokenProvider("test-secret")
        forged = pyjwt.encode(
            {"dl": DOWNLOAD_ID},  # no tid/exp/iat
            "test-secret",
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            provider.validate(forged)

    def test_issue_requires_download_id(self):
        provider = JwtOneTimeTokenProvider("test-secret")
        with pytest.raises(ValueError):
            provider.issue(download_id="   ")

    def test_cross_provider_download_id_flows_through(self):
        provider = JwtOneTimeTokenProvider("test-secret")
        claims = provider.validate(
            provider.issue(download_id=ANOTHER_DOWNLOAD_ID)
        )
        assert claims.download_id == ANOTHER_DOWNLOAD_ID
