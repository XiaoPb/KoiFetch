"""Typed application configuration loaded from environment variables.

All backend services read configuration through the single :func:`get_settings`
singleton. Field names here are the stable contract that later tasks (DB
session, auth, workers) depend on — do not rename them casually.

``Settings`` is a plain pydantic model so it stays dependency-light: values are
read from uppercase environment variables by :meth:`Settings.from_env` (or
passed directly as keyword arguments, which tests and callers may do). For
local development a ``.env`` file is loaded first (safe local defaults live in
``.env.example``); process environment variables always take precedence over
``.env`` values, and Docker/CI supply real values directly through the process
environment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

__all__ = ["Settings", "get_settings"]


class Settings(BaseModel):
    """Typed application settings backed by environment variables.

    ``ADMIN_PASSWORD`` and ``SECRET_KEY`` are required: constructing a
    ``Settings`` without them (or with them empty) raises
    :class:`pydantic.ValidationError` so a misconfigured service fails fast.
    """

    model_config = {"extra": "forbid"}

    # --- Required secrets (never log these; safe local values only in .env.example) ---
    admin_password: str = Field(min_length=1)
    secret_key: str = Field(min_length=1)

    # --- Storage roots (pond = permanent/NAS, bubble = temporary) ---
    video_storage_path: Path = Path("data/pond/video")
    image_storage_path: Path = Path("data/pond/image")
    music_storage_path: Path = Path("data/pond/music")
    temp_video_path: Path = Path("data/bubble/video")
    temp_image_path: Path = Path("data/bubble/image")
    temp_music_path: Path = Path("data/bubble/music")

    # --- Runtime behavior ---
    max_concurrent: int = Field(default=3, ge=1)
    download_speed_limit: int = Field(default=0, ge=0)  # MB/s; 0 = unlimited
    bubble_expire_hours: int = Field(default=24, ge=1)
    # Seconds the worker (Task 11) sleeps between idle polling rounds; batches
    # that claimed work poll again immediately (see app/workers/main.py).
    worker_poll_interval: float = Field(default=1.0, ge=0.1)
    # How often the worker daemon (Task 12) runs the bubble-cleanup /
    # stale-task-expiry pass, in minutes. PRD schedules hourly cleanup.
    cleanup_interval_minutes: int = Field(default=60, ge=1)
    # A ``downloading`` task is considered stale (crashed worker) after this
    # many minutes since creation (Task 12 marks it ``expired`` so the
    # expired -> pending re-download path can recover it). The anchor is
    # ``created_at`` — the v1 model has no heartbeat column and the worker
    # claim does not update it — so the risk window is *queueing time +
    # download time* and has no hard upper bound: a task that sat queued long
    # and is still in flight can be expired by design. Keep this comfortably
    # above the realistic queue + download duration (default 60 min; the stub
    # downloader finishes in milliseconds, see app/workers/cleanup.py).
    stale_download_minutes: int = Field(default=60, ge=1)

    # --- Real engine integration (f2 / parse-video-py / musicdl) ---
    # Adapter mode: "stub" (default) keeps the deterministic offline adapters;
    # "engine" selects the real f2 / parse-video-py / musicdl adapters
    # (factory.py switches, lazily importing the engine modules so the app
    # boots and the non-engine tests run without the engines installed).
    parser_engine: str = "stub"
    downloader_engine: str = "stub"
    # Legacy video-engine fallback: True keeps parse-video-py in the routing
    # table for platforms f2 does not cover (kuaishou/bilibili/xiaohongshu/
    # xigua/...); False makes those platforms raise 1003 平台不支持 so the
    # parse-video-py dependency can be removed once f2 covers them.
    parser_legacy_fallback: bool = True
    # Seconds before an engine HTTP request gives up. Streaming downloads use
    # engine_download_timeout_seconds as the per-read timeout, so a stalled
    # connection fails within that window per chunk, not per whole file.
    engine_timeout_seconds: float = Field(default=15.0, ge=1.0)
    engine_download_timeout_seconds: float = Field(default=30.0, ge=1.0)
    # Optional http(s) proxy for engine traffic (the adapters forward it to
    # httpx; parse-video-py additionally reads PARSE_VIDEO_PROXY itself).
    # Empty/whitespace means direct.
    engine_proxy: str | None = None
    # musicdl source client names for the engine downloader's music branch
    # (the five Mainland-China defaults musicdl ships with).
    musicdl_sources: list[str] = Field(
        default_factory=lambda: [
            "MiguMusicClient", "NeteaseMusicClient", "QQMusicClient",
            "KuwoMusicClient", "QianqianMusicClient",
        ]
    )

    # --- Web/app behavior ---
    cors_origins: list[str] = Field(default_factory=list)
    debug: bool = False
    tz: str = "Asia/Shanghai"

    # --- Persistence (consumed by the DB session task) ---
    database_url: str = "sqlite:///./data/db/koifetch.db"

    # --- Frontend static serving ---
    # The backend serves the built frontend (Vite `dist`) at "/", replacing the
    # container-internal Nginx: /api and /ws are same-origin, so no reverse
    # proxy is needed. Defaults to ``frontend/dist`` **relative to the process
    # CWD** — run uvicorn from the repo root (the documented dev command) or
    # set an absolute path; the container image builds the frontend and copies
    # it to /app/static, with FRONTEND_DIST_PATH set accordingly. When the
    # directory does not exist the app still boots and serves an honest
    # placeholder instead of the SPA (the API stays fully independent).
    # CAUTION: this path is served verbatim at "/", so it must point at the
    # build directory only — never at "." or the repo root, which would expose
    # the whole tree as static files.
    frontend_dist_path: Path = Path("frontend/dist")

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

    @field_validator("frontend_dist_path", mode="before")
    @classmethod
    def _reject_empty_frontend_dist_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("frontend dist path must not be empty")
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

    @field_validator("parser_engine", "downloader_engine", mode="after")
    @classmethod
    def _validate_engine_mode(cls, value: str) -> str:
        if value not in ("stub", "engine"):
            raise ValueError('engine mode must be "stub" or "engine"')
        return value

    @field_validator("engine_proxy", mode="after")
    @classmethod
    def _validate_engine_proxy(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                return None  # empty = direct (matches .env.example's ENGINE_PROXY=)
            if not value.startswith(("http://", "https://")):
                raise ValueError("engine_proxy must be an http(s) URL or None")
        return value

    @field_validator("musicdl_sources", mode="before")
    @classmethod
    def _parse_musicdl_sources(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("tz", mode="after")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        # Fail fast on typos instead of surfacing them at scheduling time.
        # ZoneInfo raises ZoneInfoNotFoundError (a KeyError) for unknown names.
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"unknown timezone name: {value!r}") from exc
        return value

    @classmethod
    def from_env(
        cls,
        dotenv_path: str | os.PathLike[str] | None = None,
    ) -> "Settings":
        """Build settings from the process environment (uppercase names).

        A ``.env`` file is loaded first for local development (by default
        ``load_dotenv`` searches from this module upward, i.e. the project
        root's ``.env``); process environment variables are never overridden
        by ``.env`` values, so Docker/CI and real deployments simply set the
        variables directly. ``dotenv_path`` lets callers point at a specific
        file (used by tests). Only variables that are set are passed through,
        so unset optional variables fall back to their defaults while unset
        required secrets still fail validation.
        """
        load_dotenv(dotenv_path=dotenv_path)
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
