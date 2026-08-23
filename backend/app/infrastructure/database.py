"""SQLite engine, session factory, and declarative Base.

This module is the persistence plumbing consumed by the ORM models
(``app.infrastructure.models``), Alembic (``backend/alembic/env.py``), the
admin seed (``app.infrastructure.seed``), and — from later tasks — the
application services and the worker.

Design decisions (stable contract for downstream tasks):

* **Import is side-effect-free.** No settings are read and no engine is built
  at import time; callers obtain engines/sessions through the ``get_*``
  functions. This keeps ``import app.infrastructure.database`` safe for Alembic
  ``env.py`` and keeps the FastAPI app import free of DB side effects.
* **URL comes from settings.** ``get_engine()``/``get_session_factory()`` read
  ``get_settings().database_url`` (default ``sqlite:///./data/db/koifetch.db``)
  and cache per URL. Tests build explicit engines from ``tmp_path`` URLs via
  :func:`build_engine` and never touch ``data/db``.
* **Parent directories are created.** Fresh clones have an empty, gitignored
  ``data/`` tree; :func:`build_engine` creates the SQLite file's parent
  directory (``mkdir(parents=True, exist_ok=True)``).
* **Concurrency-safe SQLite.** Every connection is configured with
  ``PRAGMA foreign_keys=ON``, WAL journal mode, and a busy timeout, so the
  backend and worker processes can share one DB file without "database is
  locked" failures.
* **UTC datetimes.** Models store timestamps through :class:`UTCDateTime`,
  which persists naive UTC wall-clock values (SQLite strips tzinfo) and
  returns aware UTC ``datetime`` objects on load — so downstream logic can
  compare them to ``datetime.now(timezone.utc)`` without TypeError.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import DateTime, Engine, TypeDecorator, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.infrastructure.config import get_settings

__all__ = [
    "Base",
    "UTCDateTime",
    "SQLITE_BUSY_TIMEOUT_MS",
    "build_engine",
    "get_engine",
    "get_session_factory",
    "session_scope",
    "sqlite_db_path",
]

# Milliseconds a connection waits for a locked DB before raising
# "database is locked" — the backend and worker contend on one SQLite file.
SQLITE_BUSY_TIMEOUT_MS = 5000


class Base(DeclarativeBase):
    """Declarative base for all ORM models (see ``app.infrastructure.models``)."""


class UTCDateTime(TypeDecorator):
    """A ``DateTime`` column that round-trips aware UTC datetimes on SQLite.

    SQLite silently strips tzinfo from stored datetimes (naive out even for
    aware in), which breaks comparisons against ``datetime.now(timezone.utc)``
    (``TypeError: can't compare offset-naive and offset-aware datetimes``).
    This decorator fixes the contract at the type level:

    * **bind** — any aware datetime is normalized to UTC, then stored as a
      naive UTC wall-clock value (``astimezone(utc).replace(tzinfo=None)``);
      naive input is treated as UTC (never platform-local time).
    * **load** — the naive value is re-attached to ``timezone.utc``.

    The underlying column is ``DateTime(timezone=True)``, matching the initial
    migration's DDL, so ``alembic check`` stays deterministic.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, dt.datetime):
            raise TypeError(
                f"UTCDateTime expects a datetime, got {type(value).__name__}"
            )
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=dt.timezone.utc)


def sqlite_db_path(database_url: str) -> Path | None:
    """Return the filesystem path of a SQLite URL, or None for non-file URLs.

    Uses SQLAlchemy's own URL parsing (which handles Windows drive letters),
    and returns None for ``:memory:`` and non-SQLite schemes. Exposed publicly
    because Alembic's ``env.py`` needs the same parent-directory creation for
    ``alembic upgrade`` on a fresh clone.
    """
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return None
    db = url.database
    if not db or db == ":memory:":
        return None
    return Path(db)


def build_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for ``database_url``.

    For SQLite: the DB file's parent directory is created if missing, and each
    connection is configured for concurrent multi-process access — foreign
    keys enforced, WAL journal mode, and a busy timeout (see
    :data:`SQLITE_BUSY_TIMEOUT_MS`).
    """
    db_file = sqlite_db_path(database_url)
    if db_file is not None:
        db_file.parent.mkdir(parents=True, exist_ok=True)
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        # FastAPI/worker run in threads; SQLite's default raises otherwise.
        connect_args["check_same_thread"] = False

    engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _configure_sqlite_connection(
            dbapi_connection, _connection_record
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            # WAL is a persistent DB-level setting; re-asserting it on every
            # connection is idempotent and covers DBs created before this
            # listener existed. Consume the returned row to keep the cursor clean.
            cursor.execute("PRAGMA journal_mode=WAL").fetchone()
            cursor.close()

    return engine


@lru_cache
def _engine_for(database_url: str) -> Engine:
    return build_engine(database_url)


def get_engine() -> Engine:
    """Return the process-wide engine for the configured ``database_url``.

    Cached per URL, so repeated calls share one connection pool.
    """
    return _engine_for(get_settings().database_url)


@lru_cache
def _session_factory_for(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(
        bind=_engine_for(database_url), autoflush=False, expire_on_commit=False
    )


def get_session_factory() -> sessionmaker[Session]:
    """Return a ``sessionmaker`` bound to the configured engine.

    This is the ``SessionLocal``-style factory for services and the worker:
    ``session = get_session_factory()()`` opens a session, or use
    :func:`session_scope` for commit/rollback management.
    """
    return _session_factory_for(get_settings().database_url)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transaction-scoped session: commit on success, rollback on error.

    ``engine`` defaults to the configured engine (:func:`get_engine`); pass an
    explicit engine to scope work to a test/temp database. Uses the cached
    session factory (:func:`get_session_factory`) so no sessionmaker is built
    per call.
    """
    factory = (
        get_session_factory()
        if engine is None
        else sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
