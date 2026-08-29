"""Tests for the idempotent admin seed (app.infrastructure.seed).

Covers: creation of the admin user with a bcrypt-verifiable hash, idempotency
(running twice yields exactly one user), fail-fast handling of missing/blank
``ADMIN_PASSWORD``, and the ``python -m app.infrastructure.seed`` CLI contract
(never logs the password).
"""

import bcrypt
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import User


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    return engine


def make_settings(password: str = "admin-pass") -> Settings:
    return Settings(admin_password=password, secret_key="test-secret-key-0123456789abcdef", cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


class TestSeedAdmin:
    def test_creates_admin_with_bcrypt_hash(self, engine):
        assert seed.seed_admin(settings=make_settings("s3cret"), engine=engine) is True
        with session_scope(engine) as session:
            users = session.scalars(select(User)).all()
            assert len(users) == 1
            user = users[0]
            assert user.username == seed.ADMIN_USERNAME
            assert bcrypt.checkpw(b"s3cret", user.password_hash.encode("utf-8"))

    def test_idempotent_across_calls(self, engine):
        settings = make_settings("s3cret")
        assert seed.seed_admin(settings=settings, engine=engine) is True
        assert seed.seed_admin(settings=settings, engine=engine) is False
        with session_scope(engine) as session:
            users = session.scalars(select(User)).all()
            assert len(users) == 1

    def test_preserves_existing_password_hash(self, engine):
        # Seeding again must NOT re-hash/overwrite the existing admin's hash:
        # an existing account keeps its original password, whatever the current
        # ADMIN_PASSWORD value is.
        assert (
            seed.seed_admin(settings=make_settings("first-pass"), engine=engine)
            is True
        )
        with session_scope(engine) as session:
            original_hash = session.scalar(select(User)).password_hash

        assert (
            seed.seed_admin(settings=make_settings("second-pass"), engine=engine)
            is False
        )

        with session_scope(engine) as session:
            users = session.scalars(select(User)).all()
            assert len(users) == 1
            assert users[0].password_hash == original_hash
            assert bcrypt.checkpw(
                b"first-pass", users[0].password_hash.encode("utf-8")
            )
            assert not bcrypt.checkpw(
                b"second-pass", users[0].password_hash.encode("utf-8")
            )

    def test_seed_into_empty_engine_via_default_settings(self, engine, monkeypatch):
        monkeypatch.setattr(seed, "get_engine", lambda: engine)
        monkeypatch.setattr(seed, "get_settings", lambda: make_settings("via-defaults"))
        assert seed.seed_admin() is True
        with session_scope(engine) as session:
            user = session.scalar(select(User))
            assert user is not None
            assert bcrypt.checkpw(b"via-defaults", user.password_hash.encode("utf-8"))

    def test_blank_password_rejected(self, engine):
        with pytest.raises(ValueError, match="ADMIN_PASSWORD"):
            seed.seed_admin(settings=make_settings("   "), engine=engine)

    def test_password_over_72_bytes_rejected(self, engine):
        # bcrypt 3.2+ truncates >72-byte passwords silently, which would create
        # an admin whose real password can never authenticate (the auth service
        # rejects >72-byte inputs). Fail fast instead.
        with pytest.raises(ValueError, match="72"):
            seed.seed_admin(settings=make_settings("x" * 73), engine=engine)

    def test_missing_password_setting_fails_fast(self, engine):
        # Settings requires ADMIN_PASSWORD; a settings object without it cannot
        # even be constructed, so seeding fails before touching the database.
        with pytest.raises(ValidationError):
            seed.seed_admin(settings=Settings(secret_key="test-secret-key-0123456789abcdef", cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="), engine=engine)

    def test_cli_module_never_logs_password(self, engine, monkeypatch, capsys):
        monkeypatch.setattr(seed, "get_engine", lambda: engine)
        monkeypatch.setattr(seed, "get_settings", lambda: make_settings("cli-pass"))
        assert seed.main() == 0
        output = capsys.readouterr().out
        assert "admin" in output
        assert "cli-pass" not in output
