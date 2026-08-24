"""Alembic migration environment for the Koi Fetch backend.

Resolves the database URL at runtime — never from a hardcoded value: the
``DATABASE_URL`` environment variable wins (hermetic tests, explicit ops),
otherwise the typed settings (``get_settings().database_url``) are used.

Works from any working directory — ``backend/`` as cwd, repo root via
``-c backend/alembic.ini``, or the container's ``/app`` — because this file
inserts the backend package directory on ``sys.path`` from its own location
before importing ``app.*`` modules.

Migrations run with ``render_as_batch=True``: SQLite cannot ALTER tables in
place, so batch mode is required for future schema changes to work.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.infrastructure import models  # noqa: E402,F401  (registers tables)
from app.infrastructure.config import get_settings  # noqa: E402
from app.infrastructure.database import Base, sqlite_db_path  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: the ini only configures root/sqlalchemy/
    # alembic, and the fileConfig default (True) would permanently DISABLE every
    # other existing logger (e.g. app.main) for the rest of the process. That
    # silently kills application logging after any in-process `alembic` run
    # (tests invoke migrations in-process; deploy runs alembic standalone).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

database_url = os.environ.get("DATABASE_URL") or get_settings().database_url
config.set_main_option("sqlalchemy.url", database_url)

# A fresh clone has an empty, gitignored data/ tree: create the SQLite file's
# parent directory so `alembic upgrade head` works before anything else has
# bootstrapped it (mirrors build_engine's behavior).
db_file = sqlite_db_path(database_url)
if db_file is not None:
    db_file.parent.mkdir(parents=True, exist_ok=True)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (``alembic upgrade --sql``)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection with SQLite batch mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
