"""Idempotent administrator seed.

v1 has no public registration: exactly one administrator is created from the
``ADMIN_PASSWORD`` setting (never hardcoded, never logged). The seed is
*idempotent* — running it any number of times yields exactly one admin row —
and is invoked explicitly:

* in the Compose backend startup command (``python -m app.infrastructure.seed``
  after ``alembic upgrade head``), and
* manually, e.g. ``python -m app.infrastructure.seed`` from ``backend/``.

The FastAPI app import stays side-effect-free: this module is never imported by
``app.main``; the seed runs only when called or executed as a module.
"""

from __future__ import annotations

import bcrypt
from sqlalchemy import Engine, select

from app.infrastructure.config import Settings, get_settings
from app.infrastructure.database import get_engine, session_scope
from app.infrastructure.models import User

__all__ = ["ADMIN_USERNAME", "seed_admin", "main"]

ADMIN_USERNAME = "admin"


def seed_admin(
    settings: Settings | None = None, engine: Engine | None = None
) -> bool:
    """Create the admin user if absent; return True when a row was created.

    ``settings`` defaults to the process-wide settings singleton and ``engine``
    to the configured engine — both overridable so tests run against a temp
    database. Fails fast (before touching the database) when ``ADMIN_PASSWORD``
    is missing or blank. The password is bcrypt-hashed and never logged.
    """
    settings = settings or get_settings()
    password = settings.admin_password
    if not password or not password.strip():
        raise ValueError(
            "ADMIN_PASSWORD must be set to seed the admin user"
        )
    engine = engine or get_engine()

    with session_scope(engine) as session:
        existing = session.scalar(
            select(User).where(User.username == ADMIN_USERNAME)
        )
        if existing is not None:
            return False
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        session.add(User(username=ADMIN_USERNAME, password_hash=password_hash))
        return True


def main() -> int:
    """CLI entrypoint: seed the admin and report success (exit 0)."""
    created = seed_admin()
    print("admin user created" if created else "admin user already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
