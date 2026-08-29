"""Tests for the SQLAlchemy engine/session configuration.

Covers: engine construction from a URL (with SQLite parent-directory creation,
foreign-key enforcement, WAL journal mode, and busy timeout), the
``UTCDateTime`` type decorator's aware-UTC round-trip contract, the declarative
Base metadata, the cached settings-driven engine/session factory, and
``session_scope`` commit/rollback semantics. Every test uses a temp SQLite
database — never ``data/db``.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.infrastructure import database
from app.infrastructure.config import Settings
from app.infrastructure.database import (
    SQLITE_BUSY_TIMEOUT_MS,
    Base,
    UTCDateTime,
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
    return Settings(admin_password="pw", secret_key="sk", cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", **overrides)


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

    def test_sqlite_wal_and_busy_timeout_applied(self, engine):
        # The backend and worker run as two processes against the same SQLite
        # file; WAL + a busy timeout are what make that concurrency safe.
        with engine.connect() as conn:
            assert (
                conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
            )
            assert (
                conn.execute(text("PRAGMA busy_timeout")).scalar_one()
                == SQLITE_BUSY_TIMEOUT_MS
            )

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


class TestUTCDateTime:
    """SQLite strips tzinfo from stored datetimes; UTCDateTime restores it.

    Reloaded values must be aware UTC so downstream logic (token expiry,
    cleanup scheduling) can compare them to ``datetime.now(timezone.utc)``
    without TypeError.
    """

    @pytest.fixture
    def engine(self, tmp_path):
        return build_engine(f"sqlite:///{tmp_path / 'utcdatetime.db'}")

    def _reload_created_at(self, engine, user_id):
        with session_scope(engine) as session:
            return session.get(User, user_id).created_at

    def test_aware_utc_round_trips(self, engine):
        Base.metadata.create_all(engine)
        stamp = datetime(2024, 3, 1, 12, 30, 45, tzinfo=timezone.utc)
        with session_scope(engine) as session:
            user = User(username="tz-aware", password_hash="h", created_at=stamp)
            session.add(user)
            session.flush()
            user_id = user.id
        loaded = self._reload_created_at(engine, user_id)
        assert loaded.tzinfo is not None
        assert loaded == stamp
        # Comparing against fresh aware UTC values must never raise.
        assert loaded <= datetime.now(timezone.utc)

    def test_naive_input_treated_as_utc(self, engine):
        # A naive datetime is interpreted as UTC (documented contract), never
        # as platform-local time.
        Base.metadata.create_all(engine)
        naive = datetime(2024, 3, 1, 12, 30, 45)
        with session_scope(engine) as session:
            user = User(username="tz-naive", password_hash="h", created_at=naive)
            session.add(user)
            session.flush()
            user_id = user.id
        loaded = self._reload_created_at(engine, user_id)
        assert loaded == naive.replace(tzinfo=timezone.utc)

    def test_other_timezone_normalized_to_utc(self, engine):
        Base.metadata.create_all(engine)
        local = datetime(2024, 3, 1, 20, 30, 45, tzinfo=timezone.utc)
        offset = datetime(
            2024, 3, 1, 21, 30, 45, tzinfo=timezone(timedelta(hours=1))
        )
        assert local == offset  # same instant, different wall clock
        with session_scope(engine) as session:
            user = User(username="tz-offset", password_hash="h", created_at=offset)
            session.add(user)
            session.flush()
            user_id = user.id
        loaded = self._reload_created_at(engine, user_id)
        assert loaded == offset.astimezone(timezone.utc)

    def test_raw_storage_is_naive_utc(self, engine):
        # The DB file stores a naive UTC wall-clock string (no tz suffix); the
        # tzinfo is reattached on load by the type decorator.
        Base.metadata.create_all(engine)
        stamp = datetime(2024, 3, 1, 12, 30, 45, tzinfo=timezone.utc)
        with session_scope(engine) as session:
            user = User(username="tz-raw", password_hash="h", created_at=stamp)
            session.add(user)
            session.flush()
            user_id = user.id
        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT created_at FROM users WHERE id = :id"),
                {"id": user_id},
            ).scalar_one()
        assert raw.startswith("2024-03-01 12:30:45")  # UTC wall clock
        assert "+" not in raw  # naive in storage — no tz suffix

    def test_type_decorator_exposed(self):
        # The decorator is part of the persistence contract (models use it for
        # every timestamp column).
        assert isinstance(UTCDateTime(), UTCDateTime)


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
