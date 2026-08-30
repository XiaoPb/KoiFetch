"""One-shot migration of legacy plaintext platform cookies to encrypted rows."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bootstrap_app_import_path() -> None:
    """Find the project root for both source and image directory layouts."""
    if __package__ not in (None, ""):
        return
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if (parent / "app" / "application").is_dir():
            sys.path.insert(0, str(parent))
            return
    raise RuntimeError("could not locate the Koi Fetch app package")


_bootstrap_app_import_path()

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.application.cookie_service import CookieCipher
from app.infrastructure.database import Engine, get_engine
from app.infrastructure.models import PlatformCookie


def migrate_platform_cookies(
    engine: Engine | str, cipher: CookieCipher | str
) -> int:
    """Encrypt legacy rows atomically and return the number changed.

    Existing encrypted rows are validated (including their version) and left
    byte-for-byte unchanged. Any invalid encrypted row or write failure aborts
    the transaction, so no partial migration is committed.
    """
    if isinstance(engine, str):
        engine = get_engine(engine)
    if isinstance(cipher, str):
        cipher = CookieCipher(cipher)
    migrated = 0
    connection = engine.connect()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.begin()
        rows = session.query(PlatformCookie).order_by(PlatformCookie.platform).all()
        for row in rows:
            if row.cookie.startswith("enc:"):
                # Validate encrypted rows so unknown versions are fail-closed.
                cipher.decrypt(row.cookie)
                continue
            row.cookie = cipher.encrypt(row.cookie)
            migrated += 1
        session.flush()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        session.close()
        connection.close()
    return migrated


def main() -> int:
    # Match Settings.from_env's non-overriding behavior while making the
    # project's current-working-directory .env explicit for direct CLI use.
    dotenv_path = Path.cwd() / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path=dotenv_path)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--cookie-encryption-key", default=os.environ.get("COOKIE_ENCRYPTION_KEY"))
    args = parser.parse_args()
    if not args.database_url or not args.cookie_encryption_key:
        parser.error("DATABASE_URL and COOKIE_ENCRYPTION_KEY are required")
    count = migrate_platform_cookies(
        get_engine(args.database_url), CookieCipher(args.cookie_encryption_key)
    )
    print(count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
