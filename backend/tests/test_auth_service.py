"""Tests for the admin auth service (``app/application/auth_service.py``).

Covers: bcrypt verification against the seeded admin (correct/wrong/missing
user are indistinguishable failures, including timing — the missing-user path
runs a dummy bcrypt comparison), blank/non-string input rejection, explicit
rejection of >72-byte passwords (bcrypt truncates instead of raising), token
issuance with seven-day expiry and correct claims, the ``login`` convenience that
bundles authentication + issuance, and the security contract that passwords
never appear in logs.
"""

from datetime import timedelta

import bcrypt
import pytest
from sqlalchemy import select

from app.adapters.factory import get_access_token_provider
from app.adapters.protocols import InvalidTokenError
from app.adapters import tokens_jwt
from app.application.auth_service import AuthService
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import User

# >= 32 bytes: below that PyJWT emits InsecureKeyLengthWarning, which would
# pollute the test output (same convention as test_tokens.py).
SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'auth-service.db'}")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def provider():
    return get_access_token_provider(
        Settings(admin_password=PASSWORD, secret_key=SECRET, cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    )


@pytest.fixture
def service(engine, provider):
    return AuthService(token_provider=provider, engine=engine)


def seed_admin(engine, password: str = PASSWORD) -> None:
    assert (
        seed.seed_admin(
            settings=Settings(admin_password=password, secret_key=SECRET, cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
            engine=engine,
        )
        is True
    )


class TestAuthenticate:
    def test_correct_password_authenticates(self, engine, service):
        seed_admin(engine)
        user = service.authenticate("admin", PASSWORD)
        assert user is not None
        assert user.username == "admin"
        assert user.id is not None

    def test_password_verified_against_bcrypt_hash(self, engine, service):
        seed_admin(engine)
        with session_scope(engine) as session:
            stored_hash = session.scalar(select(User)).password_hash
        # The stored hash is a real bcrypt hash the given password matches.
        assert bcrypt.checkpw(PASSWORD.encode("utf-8"), stored_hash.encode("utf-8"))
        assert service.authenticate("admin", PASSWORD) is not None

    def test_wrong_password_returns_none(self, engine, service):
        seed_admin(engine)
        assert service.authenticate("admin", "wrong-password") is None

    def test_unknown_user_returns_none_identically(self, engine, service):
        seed_admin(engine)
        # Same failure shape as a wrong password: callers cannot distinguish
        # a nonexistent user from a wrong password (no user-enumeration leak).
        assert service.authenticate("nobody", PASSWORD) is None
        assert service.authenticate("nobody", "wrong-password") is None

    def test_blank_inputs_return_none_without_querying(self, engine, service):
        seed_admin(engine)
        assert service.authenticate("", PASSWORD) is None
        assert service.authenticate("admin", "") is None
        assert service.authenticate("   ", "   ") is None
        assert service.authenticate(None, None) is None

    def test_non_string_inputs_return_none(self, engine, service):
        seed_admin(engine)
        assert service.authenticate(123, PASSWORD) is None
        assert service.authenticate("admin", 123) is None
        assert service.authenticate([], {}) is None

    def test_username_whitespace_is_stripped(self, engine, service):
        seed_admin(engine)
        user = service.authenticate("  admin  ", PASSWORD)
        assert user is not None

    def test_password_over_72_bytes_rejected_even_when_prefix_matches(
        self, engine, service
    ):
        # bcrypt 3.2+ TRUNCATES >72-byte inputs instead of raising: without an
        # explicit guard, a >72-byte password whose first 72 bytes match the
        # stored hash would authenticate (checkpw(b"P"*73, hashpw(b"P"*72))
        # returns True). The service must reject the long password up front.
        long_password = PASSWORD + "y" * 60  # 77 bytes; first 72 = PASSWORD + 55 y's
        prefix = long_password[:72]
        with session_scope(engine) as session:
            session.add(
                User(
                    username="admin",
                    password_hash=bcrypt.hashpw(
                        prefix.encode("utf-8"), bcrypt.gensalt()
                    ).decode("utf-8"),
                )
            )
        assert service.authenticate("admin", long_password) is None
        # bcrypt semantics: the exact 72-byte prefix itself still authenticates.
        assert service.authenticate("admin", prefix) is not None

    def test_unknown_user_still_runs_bcrypt_comparison(
        self, engine, service, monkeypatch
    ):
        # Login timing must not leak whether a username exists: the missing-user
        # path performs a bcrypt comparison (against a dummy hash) just like the
        # wrong-password path.
        seed_admin(engine)
        calls: list = []
        real_checkpw = bcrypt.checkpw

        def tracking_checkpw(password: bytes, hashed: bytes) -> bool:
            calls.append((password, hashed))
            return real_checkpw(password, hashed)

        monkeypatch.setattr(bcrypt, "checkpw", tracking_checkpw)
        assert service.authenticate("nobody", PASSWORD) is None
        assert len(calls) == 1


class TestIssueAccessToken:
    def test_issue_returns_seven_day_token_with_user_claims(self, engine, service, provider):
        seed_admin(engine)
        user = service.authenticate("admin", PASSWORD)
        token = service.issue_access_token(user)
        assert isinstance(token, str) and token
        claims = provider.validate(token)
        assert claims.user_id == user.id
        assert claims.username == "admin"
        assert claims.expires_at - claims.issued_at == timedelta(days=7)


class TestLogin:
    def test_login_bundles_authentication_and_issuance(self, engine, service, provider):
        seed_admin(engine)
        result = service.login("admin", PASSWORD)
        assert result is not None
        assert result.username == "admin"
        claims = provider.validate(result.token)
        assert claims.username == "admin"
        assert result.expires_at == claims.expires_at
        assert result.expires_at - claims.issued_at == timedelta(days=7)

    def test_login_failure_returns_none(self, engine, service):
        seed_admin(engine)
        assert service.login("admin", "wrong") is None
        assert service.login("nobody", PASSWORD) is None


class TestRefresh:
    def test_refresh_slides_expiry_and_rotates_jti(
        self, engine, service, provider, monkeypatch
    ):
        seed_admin(engine)
        base = tokens_jwt._now() - timedelta(seconds=1)
        moments = iter((base, base + timedelta(seconds=1)))
        monkeypatch.setattr(tokens_jwt, "_now", lambda: next(moments))
        old = provider.issue(user_id=1, username="admin")

        result = service.refresh(old)

        old_claims = provider.validate(old)
        fresh_claims = provider.validate(result.token)
        assert result.username == "admin"
        assert result.token != old
        assert fresh_claims.token_id != old_claims.token_id
        assert fresh_claims.expires_at > old_claims.expires_at
        assert result.expires_at == fresh_claims.expires_at

    def test_refresh_rejects_token_for_missing_user(self, engine, service, provider):
        seed_admin(engine)
        token = provider.issue(user_id=999999, username="admin")

        with pytest.raises(InvalidTokenError):
            service.refresh(token)


class TestNeverLogsSecrets:
    def test_password_never_appears_in_logs(self, engine, service, caplog):
        seed_admin(engine)
        secret_password = "super-secret-pw-123456"
        with caplog.at_level("DEBUG"):
            service.login("admin", secret_password)
            service.login("admin", "another-wrong-one")
        assert secret_password not in caplog.text
        assert "another-wrong-one" not in caplog.text
