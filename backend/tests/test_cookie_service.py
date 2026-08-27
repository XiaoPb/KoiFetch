"""Tests for PlatformCookieService: per-platform cookie read/write storage.

The service is DB-backed (``platform_cookies`` table); the read side is the
:class:`app.adapters.protocols.CookieProvider` the f2 parser consumes. The
write side powers the admin cookies API. No network, no app boot — the engine
fixture is a fresh temp SQLite database with the schema created directly.
"""

import json
import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.adapters.protocols import CookieProvider
from app.application.cookie_service import (
    CookieStorageError,
    PlatformCookieService,
    _MESSAGE_STORAGE_ERROR,
)
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import PlatformCookie


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'cookies.db'}")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def service(engine):
    return PlatformCookieService(engine=engine)


class TestRead:
    def test_get_returns_none_when_unset(self, service):
        assert service.get("douyin") is None

    def test_get_returns_stored_cookie(self, service):
        service.set("douyin", "sessionid=abc; ttwid=1")
        assert service.get("douyin") == "sessionid=abc; ttwid=1"

    def test_implements_cookie_provider_protocol(self, service):
        assert isinstance(service, CookieProvider)


class TestWrite:
    def test_set_upserts_and_strips_whitespace(self, service):
        entry = service.set("weibo", "  SUB=x;  ")
        assert entry["platform"] == "weibo"
        assert entry["configured"] is True
        assert entry["updated_at"] is not None
        assert service.get("weibo") == "SUB=x;"
        # The cookie VALUE must never leave the service as public data.
        assert "SUB=x;" not in json.dumps(entry)

    def test_set_overwrites_existing_cookie(self, service):
        service.set("douyin", "old=1")
        service.set("douyin", "new=2")
        assert service.get("douyin") == "new=2"

    def test_set_blank_cookie_raises_value_error(self, service):
        with pytest.raises(ValueError):
            service.set("douyin", "   ")

    def test_set_blank_platform_raises_value_error(self, service):
        with pytest.raises(ValueError):
            service.set("   ", "a=1")

    def test_delete_removes_the_row(self, service):
        service.set("douyin", "a=1")
        service.delete("douyin")
        assert service.get("douyin") is None

    def test_delete_absent_platform_is_noop(self, service):
        service.delete("douyin")  # must not raise

    def test_delete_blank_platform_raises_value_error(self, service):
        with pytest.raises(ValueError):
            service.delete("  ")

    def test_list_returns_configured_platforms_sorted(self, service):
        service.set("tiktok", "t=1")
        service.set("douyin", "d=1")
        entries = service.list()
        assert [entry["platform"] for entry in entries] == ["douyin", "tiktok"]
        assert all(entry["configured"] for entry in entries)
        # The cookie VALUE must never leave the service as public data.
        assert all("cookie" not in entry for entry in entries)
        assert "d=1" not in json.dumps(entries)
        assert "t=1" not in json.dumps(entries)

    def test_list_empty_when_nothing_configured(self, service):
        assert service.list() == []

    def test_set_returns_and_stores_the_same_updated_at(self, service, monkeypatch):
        import app.application.cookie_service as cookie_service

        fake_now = [datetime(2026, 8, 26, 1, 2, 3, 4000, tzinfo=timezone.utc)]
        fake_datetime = type(
            "FakeDatetime",
            (),
            {"now": classmethod(lambda cls, tz=None: fake_now[0])},
        )
        monkeypatch.setattr(cookie_service, "datetime", fake_datetime)

        entry = service.set("douyin", "v=1")
        assert entry["updated_at"] == "2026-08-26T01:02:03.004000+00:00"

        fake_now[0] = datetime(2026, 8, 26, 1, 2, 4, 5000, tzinfo=timezone.utc)
        entry2 = service.set("douyin", "v=2")
        assert entry2["updated_at"] == "2026-08-26T01:02:04.005000+00:00"
        # The stored row reflects the SAME value the service returned.
        with session_scope(service._engine) as session:
            row = session.get(PlatformCookie, "douyin")
        assert row.updated_at.isoformat() == entry2["updated_at"]


class TestWriteFailures:
    """Storage failures normalize to CookieStorageError without leaking cookies."""

    @pytest.fixture
    def missing_table(self, engine):
        # A deterministic write-path failure: rename the table away so any
        # INSERT/UPDATE/DELETE raises OperationalError ("no such table").
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE platform_cookies RENAME TO platform_cookies_gone")
            )

    def test_set_failure_raises_fixed_message_without_cookie(
        self, service, missing_table, caplog
    ):
        with caplog.at_level(logging.ERROR):
            with pytest.raises(CookieStorageError) as excinfo:
                service.set("douyin", "SECRET-COOKIE=1")
        assert str(excinfo.value) == _MESSAGE_STORAGE_ERROR
        assert "SECRET-COOKIE" not in str(excinfo.value)
        # Raised WITHOUT __cause__ chaining, so the DB error (whose repr
        # embeds the cookie in its statement parameters) cannot ride along.
        assert excinfo.value.__cause__ is None
        # The log records only the error class name, never the exception
        # repr or the statement parameters.
        assert "platform cookie write failed: OperationalError" in caplog.text
        assert "SECRET-COOKIE" not in caplog.text

    def test_delete_failure_raises_fixed_message_without_cookie(
        self, service, missing_table
    ):
        with pytest.raises(CookieStorageError) as excinfo:
            service.delete("douyin")
        assert str(excinfo.value) == _MESSAGE_STORAGE_ERROR
        assert "SECRET-COOKIE" not in str(excinfo.value)
        assert excinfo.value.__cause__ is None


def test_rows_are_persisted_in_the_table(engine, service):
    service.set("douyin", "a=1")
    with session_scope(engine) as session:
        row = session.get(PlatformCookie, "douyin")
        assert row is not None
        assert row.cookie == "a=1"
