"""Tests for PlatformCookieService: per-platform cookie read/write storage.

The service is DB-backed (``platform_cookies`` table); the read side is the
:class:`app.adapters.protocols.CookieProvider` the f2 parser consumes. The
write side powers the admin cookies API. No network, no app boot — the engine
fixture is a fresh temp SQLite database with the schema created directly.
"""

import pytest

from app.adapters.protocols import CookieProvider
from app.application.cookie_service import PlatformCookieService
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

    def test_set_overwrites_existing_cookie(self, service):
        service.set("douyin", "old=1")
        service.set("douyin", "new=2")
        assert service.get("douyin") == "new=2"

    def test_set_blank_cookie_raises_value_error(self, service):
        with pytest.raises(ValueError):
            service.set("douyin", "   ")

    def test_delete_removes_the_row(self, service):
        service.set("douyin", "a=1")
        service.delete("douyin")
        assert service.get("douyin") is None

    def test_delete_absent_platform_is_noop(self, service):
        service.delete("douyin")  # must not raise

    def test_list_returns_configured_platforms_sorted(self, service):
        service.set("tiktok", "t=1")
        service.set("douyin", "d=1")
        entries = service.list()
        assert [entry["platform"] for entry in entries] == ["douyin", "tiktok"]
        assert all(entry["configured"] for entry in entries)
        # The cookie VALUE must never leave the service as public data.
        assert all("cookie" not in entry for entry in entries)

    def test_list_empty_when_nothing_configured(self, service):
        assert service.list() == []

    def test_updated_at_bumps_on_overwrite(self, service):
        from datetime import datetime, timezone

        service.set("douyin", "v=1")
        first = service.list()[0]["updated_at"]
        service.set("douyin", "v=2")
        second = service.list()[0]["updated_at"]
        assert first != second
        # Both are ISO-8601 parseable.
        datetime.fromisoformat(first)
        datetime.fromisoformat(second)


def test_rows_are_persisted_in_the_table(engine, service):
    service.set("douyin", "a=1")
    with session_scope(engine) as session:
        row = session.get(PlatformCookie, "douyin")
        assert row is not None
        assert row.cookie == "a=1"
