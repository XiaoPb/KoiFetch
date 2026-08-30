"""Tests for the JWT token providers (``app/adapters/tokens_jwt.py``).

Covers: issue/validate round-trips, the seven-day default access-token expiry, the 5-minute
short-lived file-token expiry, invalid-signature/garbage/tampered-token
rejection, and the documented design decision that file-token validation is
reusable and stateless: ``validate`` returns a unique JWT ``token_id`` that
callers may correlate with logs when binding a task.
"""

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

import app.adapters.tokens_jwt as tokens_jwt
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

# HS256 test secrets, all ≥32 bytes: short keys make PyJWT emit
# InsecureKeyLengthWarning, which would pollute the test output.
SECRET = "test-secret-key-0123456789abcdef"
SECRET_A = "test-secret-a-0123456789abcdef01234567"
SECRET_B = "test-secret-b-0123456789abcdef01234567"


def _forged(payload: dict, secret: str = SECRET) -> str:
    """Sign arbitrary claims with the same algorithm/secret as the providers."""
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _valid_claims(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    payload.update(overrides)
    return payload


class TestJwtAccessTokenProvider:
    def test_issue_validate_round_trip(self):
        provider = JwtAccessTokenProvider(SECRET)
        token = provider.issue(user_id=7, username="admin")
        assert isinstance(token, str) and token
        claims = provider.validate(token)
        assert claims.user_id == 7
        assert claims.username == "admin"
        assert claims.expires_at > claims.issued_at

    def test_default_access_token_lasts_seven_days(self):
        provider = JwtAccessTokenProvider(SECRET)
        claims = provider.validate(provider.issue(user_id=1, username="u"))
        assert claims.expires_at - claims.issued_at == timedelta(days=7)

    def test_access_token_ttl_is_measured_from_provider_clock(self, monkeypatch):
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        monkeypatch.setattr(tokens_jwt, "_now", lambda: now)
        provider = JwtAccessTokenProvider(SECRET, ttl=timedelta(days=7))
        claims = provider.validate(provider.issue(user_id=1, username="admin"))
        assert claims.expires_at == now + timedelta(days=7)

    def test_two_tokens_issued_in_one_second_have_distinct_jti(self, monkeypatch):
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        monkeypatch.setattr(tokens_jwt, "_now", lambda: now)
        provider = JwtAccessTokenProvider(SECRET)
        first = provider.validate(provider.issue(user_id=1, username="admin"))
        second = provider.validate(provider.issue(user_id=1, username="admin"))
        assert first.token_id != second.token_id

    def test_ttl_override(self):
        provider = JwtAccessTokenProvider(SECRET, ttl=timedelta(hours=1))
        claims = provider.validate(provider.issue(user_id=1, username="u"))
        assert claims.expires_at - claims.issued_at == timedelta(hours=1)

    def test_expired_token_rejected(self):
        provider = JwtAccessTokenProvider(SECRET, ttl=timedelta(seconds=-5))
        token = provider.issue(user_id=1, username="u")
        with pytest.raises(TokenExpiredError):
            provider.validate(token)

    def test_invalid_signature_rejected(self):
        token = JwtAccessTokenProvider(SECRET_A).issue(user_id=1, username="u")
        other = JwtAccessTokenProvider(SECRET_B)
        with pytest.raises(InvalidTokenError):
            other.validate(token)

    def test_garbage_token_rejected(self):
        provider = JwtAccessTokenProvider(SECRET)
        with pytest.raises(InvalidTokenError):
            provider.validate("definitely-not-a-jwt")

    def test_tampered_token_rejected(self):
        provider = JwtAccessTokenProvider(SECRET)
        token = provider.issue(user_id=1, username="u")
        with pytest.raises(InvalidTokenError):
            provider.validate(token + "x")

    def test_negative_user_id_rejected_on_issue(self):
        # issue() and validate() must agree on the accepted subject domain:
        # a negative id would validate-fail ("-1".isdigit() is False), so the
        # provider refuses to mint a token it would reject itself.
        provider = JwtAccessTokenProvider(SECRET)
        with pytest.raises(ValueError):
            provider.issue(user_id=-1, username="u")

    def test_zero_user_id_round_trips(self):
        provider = JwtAccessTokenProvider(SECRET)
        claims = provider.validate(provider.issue(user_id=0, username="u"))
        assert claims.user_id == 0

    def test_non_int_user_id_rejected_on_issue(self):
        provider = JwtAccessTokenProvider(SECRET)
        with pytest.raises(TypeError):
            provider.issue(user_id="1", username="u")

    def test_token_without_required_claims_rejected(self):
        provider = JwtAccessTokenProvider(SECRET)
        # Signed with the same secret but missing sub/username/exp/iat.
        with pytest.raises(InvalidTokenError):
            provider.validate(_forged({"foo": "bar"}))

    def test_wrong_subject_type_rejected(self):
        provider = JwtAccessTokenProvider(SECRET)
        forged = _forged(
            _valid_claims(sub="not-an-int", username="u"),
        )
        with pytest.raises(InvalidTokenError):
            provider.validate(forged)

    def test_blank_username_rejected(self):
        provider = JwtAccessTokenProvider(SECRET)
        forged = _forged(_valid_claims(sub=1, username="   "))
        with pytest.raises(InvalidTokenError):
            provider.validate(forged)


class TestJwtOneTimeTokenProvider:
    def test_issue_validate_round_trip(self):
        provider = JwtOneTimeTokenProvider(SECRET)
        token = provider.issue(download_id=DOWNLOAD_ID)
        claims = provider.validate(token)
        assert claims.download_id == DOWNLOAD_ID
        assert claims.token_id  # non-empty unique id from the JWT ``tid`` claim
        assert claims.expires_at > claims.issued_at

    def test_download_token_lasts_5_minutes(self):
        provider = JwtOneTimeTokenProvider(SECRET)
        claims = provider.validate(provider.issue(download_id=DOWNLOAD_ID))
        assert claims.expires_at - claims.issued_at == timedelta(minutes=5)

    def test_expired_token_rejected(self):
        provider = JwtOneTimeTokenProvider(SECRET, ttl=timedelta(seconds=-5))
        token = provider.issue(download_id=DOWNLOAD_ID)
        with pytest.raises(TokenExpiredError):
            provider.validate(token)

    def test_invalid_signature_rejected(self):
        token = JwtOneTimeTokenProvider(SECRET_A).issue(download_id=DOWNLOAD_ID)
        other = JwtOneTimeTokenProvider(SECRET_B)
        with pytest.raises(InvalidTokenError):
            other.validate(token)

    def test_garbage_token_rejected(self):
        provider = JwtOneTimeTokenProvider(SECRET)
        with pytest.raises(InvalidTokenError):
            provider.validate("garbage")

    def test_token_ids_are_unique_per_issue(self):
        provider = JwtOneTimeTokenProvider(SECRET)
        first = provider.validate(provider.issue(download_id=DOWNLOAD_ID))
        second = provider.validate(provider.issue(download_id=DOWNLOAD_ID))
        assert first.token_id != second.token_id
        assert first.download_id == second.download_id == DOWNLOAD_ID

    def test_validate_is_stateless_reuse_is_caller_concern(self):
        # Documented design decision: the adapter never marks a token used;
        # validate() may be called repeatedly and returns the same claims.
        # The file endpoint treats the token as short-lived (5 minutes) and
        # reusable, so playback's repeated requests all validate.
        provider = JwtOneTimeTokenProvider(SECRET)
        token = provider.issue(download_id=DOWNLOAD_ID)
        first = provider.validate(token)
        second = provider.validate(token)
        assert first == second
        assert first.token_id

    def test_missing_claims_rejected(self):
        provider = JwtOneTimeTokenProvider(SECRET)
        forged = _forged({"dl": DOWNLOAD_ID})  # no tid/exp/iat
        with pytest.raises(InvalidTokenError):
            provider.validate(forged)

    def test_issue_requires_download_id(self):
        provider = JwtOneTimeTokenProvider(SECRET)
        with pytest.raises(ValueError):
            provider.issue(download_id="   ")

    def test_cross_provider_download_id_flows_through(self):
        provider = JwtOneTimeTokenProvider(SECRET)
        claims = provider.validate(
            provider.issue(download_id=ANOTHER_DOWNLOAD_ID)
        )
        assert claims.download_id == ANOTHER_DOWNLOAD_ID
