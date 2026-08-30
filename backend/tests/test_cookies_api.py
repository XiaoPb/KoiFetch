"""Tests for the cookies HTTP API (Task 3).

Covers the wire contract for ``GET/PUT/DELETE /api/cookies[/{platform}]``:

* Admin-only: a missing/malformed Authorization header → 401 (2001/2003);
  a valid token (via ``POST /api/auth/login``) is authorized.
* ``GET /api/cookies`` → ``{cookies: [{platform, configured, updated_at}]}`` —
  the cookie VALUE is never part of any response.
* ``PUT /api/cookies/{platform}`` upserts and returns the public entry;
  a blank cookie or a malformed platform name → 400.
* ``DELETE /api/cookies/{platform}`` clears the row (idempotent).
"""

import pytest
from fastapi.testclient import TestClient

from app.api.responses import CODE_BAD_REQUEST, CODE_OK, CODE_UNAUTHORIZED
from app.infrastructure import seed
from app.infrastructure.database import Base, build_engine
from app.main import create_app

SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"


def make_settings(**overrides):
    from app.infrastructure.config import Settings

    return Settings(admin_password=PASSWORD, secret_key=SECRET, cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", **overrides)


@pytest.fixture
def app(tmp_path):
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'cookies-api.db'}",
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=settings, engine=engine) is True
    return create_app(settings=settings)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def admin_token(client):
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["data"]["token"]


class TestAdminGuard:
    def test_get_without_token_is_401(self, client):
        response = client.get("/api/cookies")
        assert response.status_code == 401
        assert response.json()["code"] == CODE_UNAUTHORIZED

    def test_put_without_token_is_401(self, client):
        response = client.put("/api/cookies/douyin", json={"cookie": "a=1"})
        assert response.status_code == 401

    def test_delete_without_token_is_401(self, client):
        response = client.delete("/api/cookies/douyin")
        assert response.status_code == 401


class TestCookieContract:
    def test_list_starts_empty_and_never_echoes_values(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = client.get("/api/cookies", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == CODE_OK
        assert body["data"] == {"cookies": []}

    def test_put_then_list_shows_configured(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        put = client.put(
            "/api/cookies/douyin", json={"cookie": "sessionid=abc"}, headers=headers
        )
        assert put.status_code == 200
        entry = put.json()["data"]
        assert entry["platform"] == "douyin"
        assert entry["configured"] is True
        assert "cookie" not in entry

        listing = client.get("/api/cookies", headers=headers).json()["data"]["cookies"]
        assert listing == [entry]

    def test_put_overwrites_and_clear_removes(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.put("/api/cookies/douyin", json={"cookie": "v=1"}, headers=headers)
        client.put("/api/cookies/douyin", json={"cookie": "v=2"}, headers=headers)
        listing = client.get("/api/cookies", headers=headers).json()["data"]["cookies"]
        assert listing[0]["updated_at"] is not None

        deleted = client.delete("/api/cookies/douyin", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["code"] == CODE_OK
        listing = client.get("/api/cookies", headers=headers).json()["data"]["cookies"]
        assert listing == []

    def test_delete_absent_platform_is_ok(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = client.delete("/api/cookies/weibo", headers=headers)
        assert response.status_code == 200

    def test_blank_cookie_is_400(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = client.put(
            "/api/cookies/douyin", json={"cookie": "   "}, headers=headers
        )
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_malformed_platform_name_is_400(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = client.put(
            "/api/cookies/bad name!", json={"cookie": "a=1"}, headers=headers
        )
        assert response.status_code == 400
        assert response.json()["code"] == CODE_BAD_REQUEST

    def test_missing_body_is_400(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = client.put("/api/cookies/douyin", json={}, headers=headers)
        assert response.status_code == 400
