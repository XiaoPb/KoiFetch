"""Tests for the SQLAlchemy engine/session configuration.

Covers: engine construction from a URL (with SQLite parent-directory creation
and foreign-key enforcement), the declarative Base metadata, the cached
settings-driven engine/session factory, and ``session_scope`` commit/rollback
semantics. Every test uses a temp SQLite database — never ``data/db``.
"""

from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.infrastructure import database
from app.infrastructure.config import Settings
from app.infrastructure.database import (
    Base,
    build_engine,
    get_engine,
    get_session_factory,
    session_scope,
)
from app.infrastructure.models import DownloadTask, User

# Importing the models registers their tables on ``Base.metadata``; the
# fixtures below call ``create_all`` so all model tests run against a full
# schema without Alembic.
import app.infrastructure.models  # noqa: F401


def make_settings(**overrides) -> Settings:
    return Settings(admin_password="pw", secret_key="sk", **overrides)


@pytest.fixture
def engine(tmp_path):
    return build_engine(f"sqlite:///{tmp_path / 'database.db'}")


class TestBuildEngine:
    def test_creates_parent_directories_for_sqlite_file(self, tmp_path):
        db_path = tmp_path / "nested" / "deeper" / "app.db"
        engine = build_engine(f"sqlite:///{db_path}")
        assert db_path.parent.is_dir()
        engine.dispose()

    def test_memory_database_needs_no_directory(self):
        engine = build_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar_one() == 1
        engine.dispose()

    def test_engine_connects(self, engine):
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar_one() == 1

    def test_sqlite_foreign_keys_enforced_by_default(self, engine):
        # SQLite does not enforce FKs unless PRAGMA foreign_keys=ON per
        # connection; the engine builder must install that listener.
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    def test_engine_round_trips_rows(self, engine):
        Base.metadata.create_all(engine)
        with session_scope(engine) as session:
            session.add(User(username="alice", password_hash="h"))
        with session_scope(engine) as session:
            user = session.scalar(select(User).where(User.username == "alice"))
            assert user is not None
            assert user.username == "alice"

    def test_sqlite_foreign_keys_reject_orphan_row(self, engine):
        Base.metadata.create_all(engine)
        with pytest.raises(IntegrityError):
            with session_scope(engine) as session:
                session.add(
                    DownloadTask(task_id="00000000-0000-0000-0000-000000000000")
                )


class TestMetadata:
    def test_base_metadata_contains_all_three_tables(self):
        tables = set(Base.metadata.tables)
        assert {"users", "parse_tasks", "download_tasks"} <= tables


class TestConfiguredEngineAndSession:
    @pytest.fixture(autouse=True)
    def _point_settings_at_tmp(self, tmp_path, monkeypatch):
        # Each test gets its own tmp_path, so per-URL engine caching never
        # leaks between tests; point the settings singleton at the temp DB.
        db_path = tmp_path / "configured.db"
        monkeypatch.setattr(
            database,
            "get_settings",
            lambda: make_settings(database_url=f"sqlite:///{db_path}"),
        )

    def test_get_engine_built_from_settings_url(self):
        engine = get_engine()
        assert str(engine.url).endswith("configured.db")

    def test_get_engine_is_cached(self):
        assert get_engine() is get_engine()

    def test_session_factory_yields_working_sessions(self):
        Base.metadata.create_all(get_engine())
        with session_scope(get_engine()) as session:
            session.add(User(username="bob", password_hash="h"))
        with get_session_factory()() as session:
            assert (
                session.scalar(select(User).where(User.username == "bob"))
                is not None
            )


class TestSessionScope:
    def test_commits_on_success(self, engine):
        Base.metadata.create_all(engine)
        with session_scope(engine) as session:
            session.add(User(username="carol", password_hash="h"))
        with session_scope(engine) as session:
            assert (
                session.scalar(select(User).where(User.username == "carol"))
                is not None
            )

    def test_rolls_back_on_error(self, engine):
        Base.metadata.create_all(engine)
        with pytest.raises(RuntimeError, match="boom"):
            with session_scope(engine) as session:
                session.add(User(username="dave", password_hash="h"))
                raise RuntimeError("boom")
        with session_scope(engine) as session:
            assert (
                session.scalar(select(User).where(User.username == "dave"))
                is None
            )
