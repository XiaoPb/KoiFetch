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
* **Foreign keys are enforced.** SQLite only checks FKs when
  ``PRAGMA foreign_keys=ON`` runs per connection; a connect listener installs
  it on every engine built here.
* **UTC timestamps.** Models use ``datetime.now(timezone.utc)`` defaults; the
  ``DateTime(timezone=True)`` column type round-trips aware datetimes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.infrastructure.config import get_settings

__all__ = [
    "Base",
    "build_engine",
    "get_engine",
    "get_session_factory",
    "session_scope",
    "sqlite_db_path",
]


class Base(DeclarativeBase):
    """Declarative base for all ORM models (see ``app.infrastructure.models``)."""


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

    For SQLite: the DB file's parent directory is created if missing and the
    per-connection ``PRAGMA foreign_keys=ON`` listener is installed so foreign
    keys are actually enforced (SQLite's default is off).
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
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
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
    explicit engine to scope work to a test/temp database.
    """
    factory = sessionmaker(
        bind=engine or get_engine(), autoflush=False, expire_on_commit=False
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
