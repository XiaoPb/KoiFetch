"""FastAPI application entrypoint — the runtime skeleton milestone.

This module wires configuration-driven CORS, the unified ``{code, message,
data}`` response envelope (``app.api.responses``), the admin auth router
(``POST /api/auth/login`` + the ``require_admin`` guard), the parse router
(``POST /api/parse``), the preview router (``GET /api/preview/{task_id}``),
the download routers (submit/progress/file under ``/api/download`` and the
progress WebSocket at ``/ws/download/{id}``), the NAS router
(``POST /api/nas/save``), the readiness endpoint ``GET /api/health``, and the
built frontend served at "/" with a client-side-routing fallback (the v1
no-Nginx arrangement: ``/api`` and ``/ws`` are same-origin, so no reverse
proxy is needed).

``create_app`` accepts an explicit settings object for tests; the module-level
``app`` (imported by uvicorn as ``app.main:app``) is built from the process
environment, which fails fast when ``ADMIN_PASSWORD``/``SECRET_KEY`` are
missing — a misconfigured service must not start.

**Static frontend.** When ``settings.frontend_dist_path`` (default
``frontend/dist``) exists, the SPA is served at "/" through
:class:`_FrontendMiddleware` — a middleware that runs the application first and
only answers the app's *404 responses*: a plain GET/HEAD outside ``/api`` and
``/ws`` is answered from the dist (real file, else ``index.html`` so
client-side routing works), an unknown ``/api/*`` path keeps its envelope 404
instead of being swallowed by the SPA fallback, and a missing hashed asset
under ``/assets`` is an honest 404 too. When the build is missing (fresh
clone, tests) the same paths get an honest "frontend not built" placeholder —
``/api/health`` stays fully independent either way. See
:func:`_add_frontend_serving`.

**DI wiring.** ``create_app`` builds the access-token provider from ``settings``
and stores it alongside an :class:`app.application.auth_service.AuthService` on
``app.state``; the auth router reads them through its DI hooks
(``app.api.auth.get_auth_service``/``get_token_provider``). The parse and
preview services are likewise built once from ``settings`` and exposed via
``app.state`` (hooks ``app.api.parse.get_parse_service`` /
``app.api.preview.get_preview_service``). All services are bound to the engine
for ``settings.database_url`` (cached per URL), so each service always queries
the database the app was configured with. Tests override the hooks to pin a
temp database and secret.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.adapters.factory import (
    get_access_token_provider,
    get_downloader,
    get_one_time_token_provider,
    get_parser,
    get_storage,
)
from app.api.auth import router as auth_router
from app.api.download import router as download_router
from app.api.download import ws_router as download_ws_router
from app.api.nas import router as nas_router
from app.api.parse import router as parse_router
from app.api.preview import router as preview_router
from app.api.responses import error, ok, register_exception_handlers
from app.application.auth_service import AuthService
from app.application.download_events import event_hub
from app.application.download_service import DownloadService
from app.application.nas_service import NasService
from app.application.parse_service import ParseService
from app.application.preview_service import PreviewService
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.database import get_engine

__all__ = ["APP_TITLE", "APP_VERSION", "STORAGE_ROOT_FIELDS", "create_app", "app"]

logger = logging.getLogger(__name__)

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


def _is_api_or_ws_path(path: str) -> bool:
    """True for paths owned by the API/WebSocket routers.

    The frontend fallback must never answer these: an unknown ``/api/*`` path
    keeps its envelope 404 (a client bug must not silently receive the SPA)
    and ``/ws`` belongs to the WebSocket router.
    """
    return path.startswith(("/api", "/ws"))


class _FrontendMiddleware:
    """Serve the built frontend at "/" for requests the app answers with 404.

    The application runs first, so every registered route — the API routers,
    ``/api/health``, the download WebSocket, the auto-generated docs, and any
    route added after ``create_app`` (e.g. test-only routes) — keeps
    precedence. A plain GET/HEAD that the app 404s on, outside ``/api`` and
    ``/ws``, is answered from the built frontend: the real file when it
    exists, else ``index.html`` so client-side routing works. When no build
    exists the same paths get an honest "frontend not built" placeholder.
    API/WebSocket 404s and stale ``/assets`` hashes are never swallowed: they
    keep the app's envelope 404.

    Being a middleware (not a catch-all route or a "/" mount) also means
    non-404 responses pass through untouched — including streamed download
    bodies, which are never buffered.

    **Caching.** The no-Nginx serving deliberately sets no ``Cache-Control``:
    the old Nginx config gave ``/assets`` an ``expires 1y``, which is not
    reproduced here (production deployments sit behind an external proxy that
    can add its own caching rules). Static files still carry an ETag, so
    ``If-None-Match`` revalidation works out of the box.

    **Deployment limitation.** The API/WS exclusion (:func:`_is_api_or_ws_path`)
    inspects the raw ``scope["path"]``, so an unknown path under a reverse-proxy
    mount prefix — e.g. ``uvicorn --root-path /koi`` making ``/koi/api/*`` the
    wire path — would be treated as a frontend path and answered with the SPA.
    v1 targets root-path deployments; an app mounted under a prefix must either
    set ``root_path`` on the ASGI scope before this middleware runs or rely on
    the external proxy stripping the prefix.
    """

    def __init__(self, app: ASGIApp, dist_dir: Path) -> None:
        self.app = app
        self.dist_dir = dist_dir
        # Constructed only when the build exists: StaticFiles(check_dir=True)
        # raises for a missing directory, and the placeholder path needs none.
        self._static = (
            StaticFiles(directory=dist_dir, html=True) if dist_dir.is_dir() else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        method = scope["method"]
        replaced = False

        async def send_override(message: Message) -> None:
            nonlocal replaced
            if (
                message["type"] == "http.response.start"
                and message["status"] == 404
                and method in ("GET", "HEAD")
                and not _is_api_or_ws_path(path)
            ):
                if await self._serve_fallback(scope, receive, send, path):
                    replaced = True
                    return
            if not replaced:
                await send(message)

        await self.app(scope, receive, send_override)

    async def _serve_fallback(
        self, scope: Scope, receive: Receive, send: Send, path: str
    ) -> bool:
        """Answer a 404'd GET/HEAD from the dist (or the placeholder).

        Returns True when a replacement response was sent (the app's 404 body
        is then suppressed), False when the app's own 404 should be forwarded.
        """
        if self._static is None:
            response = HTMLResponse(
                _FRONTEND_NOT_BUILT_HTML.format(dist=self.dist_dir),
                status_code=200,
            )
            await response(scope, receive, send)
            return True
        try:
            response = await self._static.get_response(
                self._static.get_path(scope), scope
            )
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            if path.startswith("/assets") or not (self.dist_dir / "index.html").is_file():
                # Stale hashed asset (the old Nginx did try_files $uri =404)
                # or a broken build: keep the app's honest 404.
                return False
            response = await self._static.get_response("index.html", scope)
        await response(scope, receive, send)
        return True


_FRONTEND_NOT_BUILT_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Koi Fetch — frontend not built</title></head>
<body style="font-family: sans-serif; margin: 2rem; line-height: 1.6">
<h1>Koi Fetch frontend not built</h1>
<p>The API is running, but no frontend build was found at <code>{dist}</code>.</p>
<p>Build it with <code>npm run build --prefix frontend</code> and reload, or
use the Vite dev server for development.</p>
</body>
</html>
"""


def _add_frontend_serving(app: FastAPI, settings: Settings) -> None:
    """Wrap the app so the built frontend is served at "/".

    See :class:`_FrontendMiddleware`: a middleware (not a mount or catch-all
    route) so the API/WebSocket routers keep precedence AND routes registered
    after ``create_app`` keep working — the application runs first and the
    fallback only ever sees 404 responses.

    Logs at startup which mode is active: the built frontend is easy to miss
    behind a reverse proxy, and the placeholder is the operational signal that
    no build is being served.
    """
    if settings.frontend_dist_path.is_dir():
        logger.info("serving frontend build from %s", settings.frontend_dist_path)
    else:
        logger.info(
            "frontend build not found at %s; serving placeholder at /",
            settings.frontend_dist_path,
        )
    app.add_middleware(_FrontendMiddleware, dist_dir=settings.frontend_dist_path)


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
    # app.api.auth). The auth service is bound to the engine for THIS settings
    # object's database_url (cached per URL in infrastructure.database), so a
    # caller passing custom settings gets that database — never the process
    # singleton by accident.
    token_provider = get_access_token_provider(settings)
    app.state.token_provider = token_provider
    app.state.auth_service = AuthService(
        token_provider=token_provider,
        engine=get_engine(settings.database_url),
    )
    app.state.parse_service = ParseService(
        parser=get_parser(settings),
        engine=get_engine(settings.database_url),
    )
    app.state.preview_service = PreviewService(
        engine=get_engine(settings.database_url),
        proxy=settings.engine_proxy,
    )
    try:
        storage = get_storage(settings)
    except OSError:
        # Degraded storage (reported by /api/health): the app must still boot.
        # File serving then fails with a clean storage error instead of
        # crashing create_app.
        storage = None
    app.state.download_service = DownloadService(
        token_provider=get_one_time_token_provider(settings),
        storage=storage,
        downloader=get_downloader(settings),
        engine=get_engine(settings.database_url),
    )
    app.state.nas_service = NasService(
        storage=storage,
        engine=get_engine(settings.database_url),
    )
    # The in-process event hub: the download WebSocket subscribes here and the
    # worker (Task 11) publishes progress through the same singleton.
    app.state.download_event_hub = event_hub

    register_exception_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.include_router(parse_router, prefix="/api")
    app.include_router(preview_router, prefix="/api")
    app.include_router(download_router, prefix="/api")
    app.include_router(nas_router, prefix="/api")
    app.include_router(download_ws_router)

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

    _add_frontend_serving(app, settings)

    return app


app = create_app()
