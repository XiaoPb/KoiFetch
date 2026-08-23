"""Typed application configuration loaded from environment variables.

All backend services read configuration through the single :func:`get_settings`
singleton. Field names here are the stable contract that later tasks (DB
session, auth, workers) depend on — do not rename them casually.

``Settings`` is a plain pydantic model so it stays dependency-light: values are
read from uppercase environment variables by :meth:`Settings.from_env` (or
passed directly as keyword arguments, which tests and callers may do). Real
environments are supplied by Docker Compose ``env_file``/CI; the local dev
defaults live in ``.env.example``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

__all__ = ["Settings", "get_settings"]


class Settings(BaseModel):
    """Typed application settings backed by environment variables.

    ``ADMIN_PASSWORD`` and ``SECRET_KEY`` are required: constructing a
    ``Settings`` without them (or with them empty) raises
    :class:`pydantic.ValidationError` so a misconfigured service fails fast.
    """

    model_config = {"extra": "ignore"}

    # --- Required secrets (never log these; safe local values only in .env.example) ---
    admin_password: str = Field(min_length=1)
    secret_key: str = Field(min_length=1)

    # --- Storage roots (pond = permanent/NAS, bubble = temporary) ---
    video_storage_path: Path = Path("data/pond/videos")
    image_storage_path: Path = Path("data/pond/images")
    music_storage_path: Path = Path("data/pond/music")
    temp_video_path: Path = Path("data/bubble/videos")
    temp_image_path: Path = Path("data/bubble/images")
    temp_music_path: Path = Path("data/bubble/music")

    # --- Runtime behavior ---
    max_concurrent: int = Field(default=3, ge=1)
    download_speed_limit: int = Field(default=0, ge=0)  # MB/s; 0 = unlimited
    bubble_expire_hours: int = Field(default=24, ge=1)

    # --- Web/app behavior ---
    cors_origins: list[str] = Field(default_factory=list)
    debug: bool = False
    tz: str = "Asia/Shanghai"

    # --- Persistence (consumed by the DB session task) ---
    database_url: str = "sqlite:///./data/db/koifetch.db"

    @field_validator(
        "video_storage_path",
        "image_storage_path",
        "music_storage_path",
        "temp_video_path",
        "temp_image_path",
        "temp_music_path",
        mode="before",
    )
    @classmethod
    def _reject_empty_storage_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("storage path must not be empty")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            if not value.strip():
                return []
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the process environment (uppercase names).

        Only variables that are set are passed through, so unset optional
        variables fall back to their defaults while unset required secrets
        still fail validation.
        """
        values: dict[str, Any] = {}
        for name in cls.model_fields:
            raw = os.environ.get(name.upper())
            if raw is not None:
                values[name] = raw
        return cls(**values)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached)."""
    return Settings.from_env()
