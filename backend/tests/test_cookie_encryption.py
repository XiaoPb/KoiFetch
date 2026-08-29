"""Security contract tests for encrypted platform cookies."""

import base64
import io
from contextlib import redirect_stdout

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError
from sqlalchemy import select

from app.application.cookie_service import (
    CookieCipher,
    CookieStorageError,
    PlatformCookieService,
)
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import PlatformCookie
from app.main import create_app
from scripts.encrypt_platform_cookies import migrate_platform_cookies


KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()
WRONG_KEY = base64.urlsafe_b64encode(bytes(range(32, 64))).decode()


def test_cookie_cipher_round_trip_and_unique_nonces():
    cipher = CookieCipher(KEY)
    first = cipher.encrypt("session=secret")
    second = cipher.encrypt("session=secret")

    assert first.startswith("enc:v1:")
    assert first != second
    assert cipher.decrypt(first) == "session=secret"


@pytest.mark.parametrize(
    "value",
    [
        "enc:v1:not-base64!",
        "enc:v1:",
        "enc:v2:AAAA",
        "enc:unknown:AAAA",
    ],
)
def test_cookie_cipher_rejects_malformed_or_unknown_encrypted_values(value):
    cipher = CookieCipher(KEY)
    with pytest.raises(CookieStorageError):
        cipher.decrypt(value)


def test_cookie_cipher_rejects_tampered_wrong_key_and_truncated_values():
    encrypted = CookieCipher(KEY).encrypt("session=secret")
    payload = encrypted[len("enc:v1:") :]
    raw = bytearray(base64.urlsafe_b64decode(payload))
    raw[-1] ^= 1
    tampered = "enc:v1:" + base64.urlsafe_b64encode(raw).decode()

    for value, cipher in (
        (tampered, CookieCipher(KEY)),
        (encrypted, CookieCipher(WRONG_KEY)),
        ("enc:v1:" + base64.urlsafe_b64encode(raw[:12]).decode(), CookieCipher(KEY)),
    ):
        with pytest.raises(CookieStorageError):
            cipher.decrypt(value)


@pytest.mark.parametrize("key", ["", "not-base64", base64.urlsafe_b64encode(b"short").decode()])
def test_cookie_cipher_requires_exactly_32_decoded_key_bytes(key):
    with pytest.raises(ValueError):
        CookieCipher(key)


def test_cookie_cipher_keeps_legacy_plaintext_read_compatibility():
    assert CookieCipher(KEY).decrypt("session=legacy") == "session=legacy"


@pytest.fixture
def cookie_engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'cookies.db'}")
    Base.metadata.create_all(engine)
    return engine


def test_service_encrypts_on_write_but_returns_plaintext_on_read(cookie_engine):
    service = PlatformCookieService(engine=cookie_engine, cipher=CookieCipher(KEY))
    service.set("douyin", "session=secret")

    with session_scope(cookie_engine) as session:
        row = session.get(PlatformCookie, "douyin")
        assert row.cookie.startswith("enc:v1:")
        assert "session=secret" not in row.cookie
    assert service.get("douyin") == "session=secret"


def test_service_reads_legacy_plaintext(cookie_engine):
    with session_scope(cookie_engine) as session:
        session.add(PlatformCookie(platform="douyin", cookie="legacy=1"))
    service = PlatformCookieService(engine=cookie_engine, cipher=CookieCipher(KEY))
    assert service.get("douyin") == "legacy=1"


def test_migration_is_idempotent_and_does_not_print_values(cookie_engine):
    with session_scope(cookie_engine) as session:
        session.add_all(
            [
                PlatformCookie(platform="douyin", cookie="secret=one"),
                PlatformCookie(platform="weibo", cookie="secret=two"),
                PlatformCookie(
                    platform="tiktok", cookie=CookieCipher(KEY).encrypt("secret=three")
                ),
            ]
        )
    output = io.StringIO()
    with redirect_stdout(output):
        assert migrate_platform_cookies(cookie_engine, CookieCipher(KEY)) == 2
        assert migrate_platform_cookies(cookie_engine, CookieCipher(KEY)) == 0
    assert "secret=" not in output.getvalue()
    with session_scope(cookie_engine) as session:
        rows = session.scalars(select(PlatformCookie)).all()
        assert all(row.cookie.startswith("enc:v1:") for row in rows)


def test_migration_rolls_back_when_encryption_fails(cookie_engine):
    with session_scope(cookie_engine) as session:
        session.add_all(
            [
                PlatformCookie(platform="douyin", cookie="secret=one"),
                PlatformCookie(platform="weibo", cookie="secret=two"),
            ]
        )

    class FailingCipher:
        def __init__(self):
            self.calls = 0

        def encrypt(self, value):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("encryption failed")
            return CookieCipher(KEY).encrypt(value)

        def decrypt(self, value):
            return CookieCipher(KEY).decrypt(value)

    with pytest.raises(RuntimeError):
        migrate_platform_cookies(cookie_engine, FailingCipher())
    with session_scope(cookie_engine) as session:
        assert {row.cookie for row in session.scalars(select(PlatformCookie))} == {
            "secret=one",
            "secret=two",
        }


def test_settings_requires_cookie_encryption_key():
    with pytest.raises(ValidationError):
        Settings(admin_password="pw", secret_key="sk")
    settings = Settings(admin_password="pw", secret_key="sk", cookie_encryption_key=KEY)
    assert settings.cookie_encryption_key == KEY


def test_app_wires_cookie_cipher_from_settings(tmp_path):
    settings = Settings(
        admin_password="pw",
        secret_key="sk",
        cookie_encryption_key=KEY,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
    )
    Base.metadata.create_all(build_engine(settings.database_url))
    app = create_app(settings)
    assert isinstance(app.state.cookie_service._cipher, CookieCipher)
