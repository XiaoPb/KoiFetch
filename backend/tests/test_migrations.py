"""Tests for the Alembic migration chain (backend/alembic).

A single migration test runs ``alembic upgrade head`` against a fresh temp
SQLite database and asserts the resulting schema (tables, columns, indexes,
foreign keys, enum CHECK constraints) matches the model contract. A second
test asserts upgrades are idempotent. Alembic is an optional test dependency
here (it is required at deploy time via ``alembic upgrade head``).
"""

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("alembic")

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
SCRIPT_LOCATION = BACKEND_DIR / "alembic"

EXPECTED_TABLES = {"users", "parse_tasks", "download_tasks", "alembic_version"}

USER_COLUMNS = {"id", "username", "password_hash", "created_at"}

PARSE_TASK_COLUMNS = {
    "task_id",
    "url",
    "platform",
    "media_type",
    "title",
    "cover_url",
    "duration",
    "format",
    "metadata",
    "user_id",
    "created_at",
    "updated_at",
}

DOWNLOAD_TASK_COLUMNS = {
    "download_id",
    "task_id",
    "title",
    "format",
    "quality",
    "status",
    "progress",
    "speed",
    "total_bytes",
    "downloaded_bytes",
    "retry_count",
    "error_message",
    "bubble_path",
    "pond_path",
    "token_expires_at",
    "created_at",
    "completed_at",
}


def run_upgrade(db_path: Path) -> None:
    """Run ``alembic upgrade head`` in-process against ``db_path``.

    The URL is passed through the ``DATABASE_URL`` environment variable, which
    ``alembic/env.py`` honors ahead of the typed settings.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    command.upgrade(config, "head")


def alembic_config() -> "Config":
    """Alembic Config pointed at the backend's ini/scripts (URL via env)."""
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config


class TestInitialMigration:
    def test_upgrade_head_creates_expected_schema(self, tmp_path, monkeypatch):
        db_path = tmp_path / "migrated.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        run_upgrade(db_path)

        conn = sqlite3.connect(db_path)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert EXPECTED_TABLES <= tables

            user_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(users)")
            }
            assert USER_COLUMNS <= user_columns

            parse_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(parse_tasks)")
            }
            assert PARSE_TASK_COLUMNS <= parse_columns

            download_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(download_tasks)")
            }
            assert DOWNLOAD_TASK_COLUMNS <= download_columns

            parse_indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(parse_tasks)")
            }
            assert "ix_parse_tasks_user_id" in parse_indexes
            assert "ix_parse_tasks_created_at" in parse_indexes

            download_indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(download_tasks)")
            }
            assert "ix_download_tasks_task_id" in download_indexes
            assert "ix_download_tasks_status" in download_indexes
            assert "ix_download_tasks_created_at" in download_indexes

            foreign_keys = [
                row[2]
                for row in conn.execute(
                    "PRAGMA foreign_key_list(download_tasks)"
                )
            ]
            assert "parse_tasks" in foreign_keys

            parse_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='parse_tasks'"
            ).fetchone()[0]
            assert "CHECK" in parse_sql  # media_type enum CHECK constraint

            download_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='download_tasks'"
            ).fetchone()[0]
            assert "CHECK" in download_sql  # status enum CHECK constraint

            version = conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            assert version is not None and version[0]
        finally:
            conn.close()

    def test_upgrade_is_idempotent(self, tmp_path, monkeypatch):
        db_path = tmp_path / "twice.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        run_upgrade(db_path)
        run_upgrade(db_path)  # already at head: a no-op that must not raise

    def test_downgrade_base_drops_all_tables(self, tmp_path, monkeypatch):
        # The full migration chain must be reversible: downgrading to base
        # leaves no application tables behind.
        from alembic import command

        db_path = tmp_path / "downgrade.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        run_upgrade(db_path)
        command.downgrade(alembic_config(), "base")

        conn = sqlite3.connect(db_path)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert not ({"users", "parse_tasks", "download_tasks"} & tables)
        finally:
            conn.close()

    def test_alembic_check_reports_no_diffs(self, tmp_path, monkeypatch):
        # The models and the committed migration must stay in sync: running
        # `alembic check` against a migrated DB must find no pending changes.
        # (Raises AutogenerateDiffsDetected when the schema drifts.)
        from alembic import command

        db_path = tmp_path / "check.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        run_upgrade(db_path)
        command.check(alembic_config())  # no diff == no raise
