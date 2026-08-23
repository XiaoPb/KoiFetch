"""Tests for the auth API: login endpoint, bearer-token guard, response envelope.

Covers the ``POST /api/auth/login`` contract (200 + ``{code, message, data}``
envelope with ``{token, username, expires_at}``; 401 with a stable code for bad
credentials, indistinguishable for unknown users; 400 for missing/blank/overlong
fields), the ``require_admin`` dependency (valid token passes, missing/malformed
header → 2001, invalid token → 2003, expired token → 2004), the envelope shape
on every error path including unexpected server errors (9001), the security
contract that credentials never appear in logs, and that ``create_app`` binds
the auth service to the database URL from its own settings.

The protected-route tests mount a tiny test-only ``/api/protected`` endpoint on
the app (the guard is exercised through FastAPI's real dependency machinery,
not called directly), so no test-only route ships in production code.
"""

from datetime import timedelta

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.factory import get_access_token_provider
from app.adapters.protocols import AccessTokenClaims
from app.adapters.tokens_jwt import JwtAccessTokenProvider
from app.api.auth import (
    get_auth_service,
    get_token_provider,
    require_admin,
)
from app.api.responses import (
    CODE_BAD_REQUEST,
    CODE_INTERNAL_ERROR,
    CODE_INVALID_CREDENTIALS,
    CODE_INVALID_TOKEN,
    CODE_OK,
    CODE_TOKEN_EXPIRED,
    CODE_UNAUTHORIZED,
    ok,
)
from app.application.auth_service import AuthService
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import User
from app.main import create_app

# >= 32 bytes: below that PyJWT emits InsecureKeyLengthWarning (see test_tokens.py).
SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"


def make_settings(**overrides) -> Settings:
    return Settings(admin_password=PASSWORD, secret_key=SECRET, **overrides)


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'auth-api.db'}")
    Base.metadata.create_all(engine)
    assert (
        seed.seed_admin(settings=make_settings(), engine=engine) is True
    )
    return engine


@pytest.fixture
def provider():
    return get_access_token_provider(make_settings())


def build_app(engine, provider) -> "FastAPI":
    """The real app, with the auth dependencies pinned to the temp database.

    ``create_app`` wires a production auth service bound to the configured
    engine; tests override the two DI hooks so login and the guard share the
    seeded temp database and the test secret.
    """
    from fastapi import FastAPI

    app = create_app(settings=make_settings())
    app.dependency_overrides[get_auth_service] = lambda: AuthService(
        token_provider=provider, engine=engine
    )
    app.dependency_overrides[get_token_provider] = lambda: provider
    return app


@pytest.fixture
def client(engine, provider):
    return TestClient(build_app(engine, provider))


@pytest.fixture
def admin_id(engine) -> int:
    with session_scope(engine) as session:
        return session.scalar(select(User)).id


class TestLogin:
    def test_success_returns_envelope_with_token_and_expiry(
        self, client, provider, admin_id
    ):
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == CODE_OK
        assert body["message"]

        data = body["data"]
        assert set(data) == {"token", "username", "expires_at"}
        assert data["username"] == "admin"

        claims = provider.validate(data["token"])
        assert claims.user_id == admin_id
        assert claims.username == "admin"
        assert claims.expires_at - claims.issued_at == timedelta(hours=24)
        # expires_at is ISO-8601 with timezone — parseable by the frontend.
        from datetime import datetime

        assert datetime.fromisoformat(data["expires_at"]) == claims.expires_at

    def test_wrong_password_returns_401_with_stable_code(self, client):
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert response.status_code == 401
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == CODE_INVALID_CREDENTIALS
        assert body["data"] is None

    def test_unknown_user_is_indistinguishable_from_wrong_password(self, client):
        wrong = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        ).json()
        unknown = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": PASSWORD},
        ).json()
        assert unknown == wrong

    def test_missing_or_blank_fields_return_400_envelope(self, client):
        for payload in (
            {},
            {"username": "admin"},
            {"password": PASSWORD},
            {"username": "", "password": ""},
            {"username": "   ", "password": "   "},
        ):
            response = client.post("/api/auth/login", json=payload)
            assert response.status_code == 400, payload
            body = response.json()
            assert set(body) == {"code", "message", "data"}
            assert body["code"] == CODE_BAD_REQUEST

    def test_overlong_fields_return_400_envelope(self, client):
        for payload in (
            {"username": "a" * 65, "password": PASSWORD},
            {"username": "admin", "password": "p" * 129},
        ):
            response = client.post("/api/auth/login", json=payload)
            assert response.status_code == 400, payload
            assert response.json()["code"] == CODE_BAD_REQUEST

    def test_malformed_json_body_returns_400_envelope(self, client):
        response = client.post(
            "/api/auth/login",
            content="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert set(response.json()) == {"code", "message", "data"}

    def test_credentials_never_appear_in_logs(self, client, caplog):
        with caplog.at_level("DEBUG"):
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": PASSWORD},
            )
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong-secret-value"},
            )
        assert PASSWORD not in caplog.text
        assert "wrong-secret-value" not in caplog.text


def make_protected_app(engine, provider) -> "FastAPI":
    """The real app plus a test-only protected route that uses ``require_admin``."""
    from fastapi import FastAPI

    app = build_app(engine, provider)

    @app.get("/api/protected")
    def protected(claims: AccessTokenClaims = Depends(require_admin)) -> dict:
        return ok(data={"user_id": claims.user_id, "username": claims.username})

    return app


@pytest.fixture
def protected_client(engine, provider):
    return TestClient(make_protected_app(engine, provider))


class TestRequireAdmin:
    def test_valid_token_passes_and_returns_claims(self, protected_client, admin_id):
        token = protected_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": PASSWORD},
        ).json()["data"]["token"]
        response = protected_client.get(
            "/api/protected", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == CODE_OK
        assert body["data"] == {"user_id": admin_id, "username": "admin"}

    def test_missing_header_returns_401_not_logged_in(self, protected_client):
        response = protected_client.get("/api/protected")
        assert response.status_code == 401
        assert response.json()["code"] == CODE_UNAUTHORIZED

    def test_malformed_authorization_header_returns_401(self, protected_client):
        for header in ("Token abc", "Bearer", "Basic dXNlcjpwYXNz", ""):
            headers = {"Authorization": header} if header else {}
            response = protected_client.get("/api/protected", headers=headers)
            assert response.status_code == 401, header
            assert response.json()["code"] == CODE_UNAUTHORIZED

    def test_invalid_token_returns_401_invalid_token(self, protected_client):
        forged = JwtAccessTokenProvider(
            "another-secret-key-0123456789abcdef0123"
        ).issue(user_id=1, username="admin")
        response = protected_client.get(
            "/api/protected", headers={"Authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == CODE_INVALID_TOKEN

    def test_garbage_token_returns_401_invalid_token(self, protected_client):
        response = protected_client.get(
            "/api/protected", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == CODE_INVALID_TOKEN

    def test_expired_token_returns_401_token_expired(self, protected_client):
        expired = JwtAccessTokenProvider(SECRET, ttl=timedelta(seconds=-5))
        token = expired.issue(user_id=1, username="admin")
        response = protected_client.get(
            "/api/protected", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == CODE_TOKEN_EXPIRED

    def test_guard_failures_keep_envelope_shape(self, protected_client):
        response = protected_client.get("/api/protected")
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["message"]
        assert body["data"] is None


class TestEnvelopeEverywhere:
    def test_unknown_route_returns_envelope(self, client):
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        assert set(response.json()) == {"code", "message", "data"}

    def test_health_still_returns_envelope(self, client):
        body = client.get("/api/health").json()
        assert set(body) == {"code", "message", "data"}


class TestUnhandledErrors:
    """Every response is an envelope, even unexpected server errors (PRD 9001)."""

    def test_unexpected_exception_returns_9001_envelope(self, engine, provider, caplog):
        app = build_app(engine, provider)

        @app.get("/api/boom")
        def boom() -> dict:
            raise RuntimeError("boom")

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("ERROR"):
            response = client.get("/api/boom")
        assert response.status_code == 500
        body = response.json()
        assert set(body) == {"code", "message", "data"}
        assert body["code"] == CODE_INTERNAL_ERROR
        assert body["data"]["request_id"]
        # Internal detail goes to the server log, never into the response body.
        assert "boom" in caplog.text
        assert "boom" not in response.text


class TestSettingsWiring:
    """create_app must bind the auth service to ITS OWN settings database URL.

    No dependency overrides here: the production wiring itself has to query
    the database the settings object points at, not the process-wide default.
    """

    def test_create_app_binds_auth_to_settings_database(self, tmp_path):
        settings = make_settings(database_url=f"sqlite:///{tmp_path / 'wired.db'}")
        engine = build_engine(settings.database_url)
        Base.metadata.create_all(engine)
        assert seed.seed_admin(settings=settings, engine=engine) is True

        client = TestClient(create_app(settings=settings))
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["code"] == CODE_OK
        assert response.json()["data"]["username"] == "admin"
