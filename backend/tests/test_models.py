"""Tests for the SQLAlchemy ORM models (users / parse_tasks / download_tasks).

Covers: defaults (UUIDs, timestamps, enums), unique constraints, foreign-key
enforcement, JSON metadata round-trips, enum values stored as strings, ORM
relationships, and the declared indexes. Each test runs against a fresh temp
SQLite database — never ``data/db``.
"""

import time
import uuid as uuid_module
from datetime import datetime

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.enums import DownloadStatus, MediaType
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask, ParseTask, User

TASK_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TASK_ID = "22222222-2222-2222-2222-222222222222"
DOWNLOAD_ID = "33333333-3333-3333-3333-333333333333"
OTHER_DOWNLOAD_ID = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine):
    with session_scope(engine) as s:
        yield s


def new_task(url="https://example.com/video", platform="youtube", **kw) -> ParseTask:
    kw.setdefault("media_type", MediaType.VIDEO)
    return ParseTask(url=url, platform=platform, **kw)


class TestUser:
    def test_created_with_autoincrement_id_and_timestamp(self, session):
        user = User(username="admin", password_hash="hash")
        session.add(user)
        session.flush()
        assert user.id == 1
        assert isinstance(user.created_at, datetime)

    def test_duplicate_username_rejected(self, session):
        session.add(User(username="twin", password_hash="a"))
        session.flush()
        with pytest.raises(IntegrityError):
            session.add(User(username="twin", password_hash="b"))
            session.flush()
        session.rollback()


class TestParseTask:
    def test_uuid_default_is_generated(self, session):
        task = new_task()
        session.add(task)
        session.flush()
        assert isinstance(task.task_id, str)
        assert len(task.task_id) == 36
        assert uuid_module.UUID(task.task_id).version == 4

    def test_task_id_unique(self, session):
        session.add(new_task(task_id=TASK_ID))
        session.flush()
        with pytest.raises(IntegrityError):
            session.add(new_task(task_id=TASK_ID, url="https://example.com/2"))
            session.flush()
        session.rollback()

    def test_json_metadata_round_trip(self, session):
        metadata = {"duration_ms": 12345, "tags": ["a", "b"], "nested": {"k": 1}}
        task = new_task(metadata_=metadata)
        session.add(task)
        session.flush()
        task_id = task.task_id
        session.expire_all()
        loaded = session.get(ParseTask, task_id)
        assert loaded.metadata_ == metadata
        assert isinstance(loaded.metadata_, dict)

    def test_metadata_defaults_to_empty_dict(self, session):
        task = new_task()
        session.add(task)
        session.flush()
        assert task.metadata_ == {}

    def test_enum_value_stored_and_loaded(self, session):
        task = new_task(media_type=MediaType.MUSIC)
        session.add(task)
        session.flush()
        raw = session.execute(
            text("SELECT media_type FROM parse_tasks WHERE task_id = :id"),
            {"id": task.task_id},
        ).scalar_one()
        assert raw == "music"  # stored as the enum value string
        session.expire_all()
        assert session.get(ParseTask, task.task_id).media_type is MediaType.MUSIC

    def test_optional_columns_nullable(self, session):
        task = new_task()
        session.add(task)
        session.flush()
        assert task.title is None
        assert task.cover_url is None
        assert task.duration is None
        assert task.format is None
        assert task.user_id is None

    def test_updated_at_onupdate_configured(self):
        column = sa_inspect(ParseTask).columns["updated_at"]
        assert column.onupdate is not None

    def test_updated_at_changes_on_update(self, session):
        task = new_task()
        session.add(task)
        session.flush()
        original = task.updated_at
        time.sleep(0.002)  # datetime resolution is microseconds
        task.title = "Renamed"
        session.flush()
        assert task.updated_at > original

    def test_relationships(self, session):
        user = User(username="rel", password_hash="h")
        session.add(user)
        session.flush()
        task = new_task(user=user)
        session.add(task)
        session.flush()
        download = DownloadTask(task_id=task.task_id)
        session.add(download)
        session.flush()
        assert task.user is user
        assert user.parse_tasks == [task]
        assert download.parse_task is task
        assert task.downloads == [download]


class TestDownloadTask:
    def _with_parent(self, session) -> ParseTask:
        task = new_task()
        session.add(task)
        session.flush()
        return task

    def test_uuid_default_is_generated(self, session):
        task = self._with_parent(session)
        download = DownloadTask(task_id=task.task_id)
        session.add(download)
        session.flush()
        assert isinstance(download.download_id, str)
        assert len(download.download_id) == 36
        assert uuid_module.UUID(download.download_id).version == 4

    def test_download_id_unique(self, session):
        task = self._with_parent(session)
        session.add(DownloadTask(download_id=DOWNLOAD_ID, task_id=task.task_id))
        session.flush()
        with pytest.raises(IntegrityError):
            session.add(
                DownloadTask(
                    download_id=DOWNLOAD_ID, task_id=OTHER_TASK_ID
                )
            )
            session.flush()
        session.rollback()

    def test_defaults(self, session):
        task = self._with_parent(session)
        download = DownloadTask(task_id=task.task_id)
        session.add(download)
        session.flush()
        assert download.status is DownloadStatus.PENDING
        assert download.progress == 0.0
        assert download.retry_count == 0
        assert download.title is None
        assert download.format is None
        assert download.quality is None
        assert download.total_bytes is None
        assert download.downloaded_bytes is None
        assert download.bubble_path is None
        assert download.pond_path is None
        assert download.token_id is None
        assert download.token_expires_at is None
        assert download.completed_at is None
        assert isinstance(download.created_at, datetime)

    def test_status_enum_value_stored_and_loaded(self, session):
        task = self._with_parent(session)
        download = DownloadTask(
            task_id=task.task_id, status=DownloadStatus.FAILED
        )
        session.add(download)
        session.flush()
        raw = session.execute(
            text("SELECT status FROM download_tasks WHERE download_id = :id"),
            {"id": download.download_id},
        ).scalar_one()
        assert raw == "failed"
        session.expire_all()
        loaded = session.get(DownloadTask, download.download_id)
        assert loaded.status is DownloadStatus.FAILED

    def test_foreign_key_requires_existing_parse_task(self, session):
        with pytest.raises(IntegrityError):
            session.add(
                DownloadTask(task_id="00000000-0000-0000-0000-000000000000")
            )
            session.flush()
        session.rollback()


class TestIndexes:
    def test_parse_task_indexes_exist(self, engine):
        with engine.connect() as conn:
            indexes = {
                row[1] for row in conn.execute(text("PRAGMA index_list(parse_tasks)"))
            }
        assert "ix_parse_tasks_user_id" in indexes
        assert "ix_parse_tasks_created_at" in indexes

    def test_download_task_indexes_exist(self, engine):
        with engine.connect() as conn:
            indexes = {
                row[1]
                for row in conn.execute(text("PRAGMA index_list(download_tasks)"))
            }
        assert "ix_download_tasks_task_id" in indexes
        assert "ix_download_tasks_status" in indexes
        assert "ix_download_tasks_created_at" in indexes
