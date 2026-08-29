# Koi Fetch backend image — shared by the `backend` (API + static frontend)
# and `worker` (background jobs) Compose services. Multi-stage: a Node stage
# builds the frontend, the Python stage runs the backend and serves the build
# at "/" (the v1 no-Nginx arrangement — /api and /ws are same-origin, no
# reverse proxy inside the container).
#
# The services differ only in their startup command: Compose passes the
# migration + seed + uvicorn command (backend) or the worker entrypoint module
# (worker). The default CMD below runs the API for standalone `docker run`
# (without migrations — use Compose for the full stack).
#
# The build context is the repository ROOT (see docker-compose.yml), so this
# file can COPY both ./backend and ./frontend; the root .dockerignore keeps
# node_modules, venvs, and runtime data out of the context.

# --- Frontend build stage ---
FROM node:22-alpine AS frontend-build

WORKDIR /build

# Install dependencies first for layer caching (package-lock.json is
# committed, so `npm ci` is reproducible).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the production bundle (Vite outDir ./dist; base "/" — served at root).
COPY frontend/ .
RUN npm run build

# --- Runtime stage ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# The backend serves the built frontend from /app/static; the app's settings
# default (frontend/dist) is overridden so a plain `docker run` works too.
ENV FRONTEND_DIST_PATH=/app/static

WORKDIR /app

# System timezone database so the TZ env var (Asia/Shanghai per PRD) is
# effective for container-local time; slim images ship without /usr/share/zoneinfo.
# (The PyPI `tzdata` package in requirements.txt already covers ZoneInfo lookups.)
# `git` is required by pip for the git-installed parse-video-py dependency
# (backend/requirements.txt) — without it the dependency install step fails.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so dependency changes do not invalidate the app layer.
# The standard-library installer builds verified compatibility wheels before
# resolving dependencies, so the original musicdl/f2 conflict metadata never
# enters the environment.
COPY backend/requirements.txt ./backend/requirements.txt
COPY backend/scripts/build_compatible_wheels.py backend/scripts/install_backend_dependencies.py ./backend/scripts/
RUN python backend/scripts/install_backend_dependencies.py

# Network-free f2 import sanity check (handler modules fetch token APIs at
# import time; utils/exceptions cover the cryptography compatibility risk).
RUN python -c "import f2.exceptions, f2.utils.utils"

# Application code (ports-and-adapters layout under app/).
COPY backend/app ./app

# Alembic migration configuration and scripts: the backend startup command
# (`alembic upgrade head`) runs with WORKDIR /app, so alembic.ini must live at
# /app and resolve `script_location` (ini-relative via %(here)s) to /app/alembic.
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic

# The built frontend from the Node stage (served at "/" by the backend).
COPY --from=frontend-build /build/dist /app/static

# Runtime storage layout: SQLite dir, Bubble (temp), Pond (permanent).
# Compose overrides these paths with bind mounts; the directories also make a
# plain `docker run` work without mounts.
RUN mkdir -p \
    /app/data/db \
    /app/data/bubble/video /app/data/bubble/image /app/data/bubble/music \
    /app/data/pond/video /app/data/pond/image /app/data/pond/music

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
