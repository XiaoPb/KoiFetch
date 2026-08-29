"""Security contract tests for encrypted platform cookies."""

import base64
import io
import logging
import os
import subprocess
import sys
import threading
from contextlib import redirect_stdout

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

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


def test_migration_lock_allows_newer_concurrent_update_after_commit(cookie_engine):
    with session_scope(cookie_engine) as session:
        session.add(PlatformCookie(platform="douyin", cookie="secret=old"))

    writer_ready = threading.Event()
    writer_started = threading.Event()

    class CoordinatedCipher:
        def __init__(self):
            self.did_update = False

        def encrypt(self, value):
            if not self.did_update:
                self.did_update = True
                writer_ready.set()
                assert writer_started.wait(timeout=5)
            return CookieCipher(KEY).encrypt(value)

        def decrypt(self, value):
            return CookieCipher(KEY).decrypt(value)

    def writer():
        writer_ready.wait(timeout=5)
        writer_started.set()
        with session_scope(cookie_engine) as concurrent:
            concurrent.execute(
                update(PlatformCookie)
                .where(PlatformCookie.platform == "douyin")
                .values(cookie="secret=newer")
            )

    thread = threading.Thread(target=writer)
    thread.start()
    assert migrate_platform_cookies(cookie_engine, CoordinatedCipher()) == 1
    thread.join(timeout=10)
    assert not thread.is_alive()
    with session_scope(cookie_engine) as session:
        assert session.get(PlatformCookie, "douyin").cookie == "secret=newer"


def test_migration_sql_logs_never_include_legacy_cookie(cookie_engine, caplog):
    legacy = "DISTINCTIVE_LEGACY_COOKIE_SHOULD_NOT_BE_LOGGED=1"
    with session_scope(cookie_engine) as session:
        session.add(PlatformCookie(platform="douyin", cookie=legacy))
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine.Engine")

    assert migrate_platform_cookies(cookie_engine, CookieCipher(KEY)) == 1
    assert legacy not in caplog.text


def test_migration_cli_loads_dotenv_and_reports_count_only(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'cli.db'}"
    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    with session_scope(engine) as session:
        session.add(PlatformCookie(platform="douyin", cookie="secret=cli"))
    (tmp_path / ".env").write_text(
        f"DATABASE_URL={database_url}\nCOOKIE_ENCRYPTION_KEY={KEY}\n",
        encoding="utf-8",
    )
    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "encrypt_platform_cookies.py")
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("COOKIE_ENCRYPTION_KEY", None)
    result = subprocess.run(
        [sys.executable, os.path.abspath(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "1"
    assert "secret=cli" not in result.stdout + result.stderr


def test_settings_requires_cookie_encryption_key():
    with pytest.raises(ValidationError):
        Settings(admin_password="pw", secret_key="test-secret-key-0123456789abcdef")
    settings = Settings(admin_password="pw", secret_key="test-secret-key-0123456789abcdef", cookie_encryption_key=KEY)
    assert settings.cookie_encryption_key == KEY


def test_settings_redacts_malformed_cookie_key_from_validation_errors():
    distinctive = "REDACT_ME_COOKIE_KEY_123"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            admin_password="pw",
            secret_key="test-secret-key-0123456789abcdef",
            cookie_encryption_key=distinctive,
        )
    assert distinctive not in str(exc_info.value)
    assert distinctive not in repr(exc_info.value)


def test_app_wires_cookie_cipher_from_settings(tmp_path):
    settings = Settings(
        admin_password="pw",
        secret_key="test-secret-key-0123456789abcdef",
        cookie_encryption_key=KEY,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
    )
    Base.metadata.create_all(build_engine(settings.database_url))
    app = create_app(settings)
    assert isinstance(app.state.cookie_service._cipher, CookieCipher)
