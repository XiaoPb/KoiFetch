"""FastAPI application entrypoint — the runtime skeleton milestone.

This module wires configuration-driven CORS, the unified ``{code, message,
data}`` response envelope (``app.api.responses``), the admin auth router
(``POST /api/auth/login`` + the ``require_admin`` guard), and the readiness
endpoint ``GET /api/health`` that reports service and storage readiness.
Business endpoints (parse, download, NAS) arrive in later tasks and register
their routers here via ``create_app``.

``create_app`` accepts an explicit settings object for tests; the module-level
``app`` (imported by uvicorn as ``app.main:app``) is built from the process
environment, which fails fast when ``ADMIN_PASSWORD``/``SECRET_KEY`` are
missing — a misconfigured service must not start.

**DI wiring.** ``create_app`` builds the access-token provider from ``settings``
and stores it alongside an :class:`app.application.auth_service.AuthService` on
``app.state``; the auth router reads them through its DI hooks
(``app.api.auth.get_auth_service``/``get_token_provider``). Tests override the
hooks to pin a temp database and secret.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.factory import get_access_token_provider
from app.api.auth import router as auth_router
from app.api.responses import error, ok, register_exception_handlers
from app.application.auth_service import AuthService
from app.infrastructure.config import Settings, get_settings

__all__ = ["APP_TITLE", "APP_VERSION", "STORAGE_ROOT_FIELDS", "create_app", "app"]

APP_TITLE = "Koi Fetch API"
APP_VERSION = "0.1.0"

# Storage root field names on Settings, in contract order. The health endpoint
# reports one status per root; the container readiness probe (app/health.py)
# and its tests depend on this list, so it is part of the module's public
# surface rather than an implementation detail.
STORAGE_ROOT_FIELDS = (
    "video_storage_path",
    "image_storage_path",
    "music_storage_path",
    "temp_video_path",
    "temp_image_path",
    "temp_music_path",
)


def _check_storage_roots(settings: Settings) -> tuple[dict[str, str], bool]:
    """Ensure every configured storage root exists (creating it if needed).

    Returns a ``{field: "ok" | "error"}`` map and whether all roots are ready.
    Creation is idempotent, so the endpoint doubles as a bootstrap for local
    runs where nothing else has created the directories yet.
    """
    roots: dict[str, str] = {}
    for field in STORAGE_ROOT_FIELDS:
        path = getattr(settings, field)
        try:
            path.mkdir(parents=True, exist_ok=True)
            roots[field] = "ok"
        except OSError:
            roots[field] = "error"
    return roots, all(status == "ok" for status in roots.values())


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application, optionally with explicit settings.

    ``settings`` defaults to the process-wide singleton (:func:`get_settings`),
    which reads ``ADMIN_PASSWORD``/``SECRET_KEY`` from the environment; tests
    pass a settings object built from a temp directory instead.
    """
    settings = settings or get_settings()

    app = FastAPI(title=APP_TITLE, version=APP_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth wiring: the token provider and auth service are built once from
    # ``settings`` and exposed to the auth router via app.state (DI hooks in
    # app.api.auth). The auth service falls back to the configured engine
    # (get_engine -> settings.database_url) for its DB sessions.
    token_provider = get_access_token_provider(settings)
    app.state.token_provider = token_provider
    app.state.auth_service = AuthService(token_provider=token_provider)

    register_exception_handlers(app)
    app.include_router(auth_router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict:
        roots, ready = _check_storage_roots(settings)
        if ready:
            return ok(
                data={
                    "status": "ok",
                    "services": {"api": "ok", "storage": "ok"},
                    "storage_roots": roots,
                }
            )
        return error(
            1,
            "storage not ready",
            data={
                "status": "degraded",
                "services": {"api": "ok", "storage": "degraded"},
                "storage_roots": roots,
            },
        )

    return app


app = create_app()
