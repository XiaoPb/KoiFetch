# Koi Fetch

Full-stack MVP for fetching media (video / image / music) into a Pond (permanent
storage) through a Bubble (temporary staging area) with an admin panel.

## Stack

- **Backend:** FastAPI (ports-and-adapters) + SQLite — serves the built frontend at `/` (no container-internal Nginx; `/api` and `/ws` are same-origin)
- **Frontend:** React 18 / TypeScript / Ant Design
- **Infrastructure:** Docker Compose

Static assets are served with ETags (so `If-None-Match` revalidation works) but
no `Cache-Control`: the old Nginx `expires 1y` on `/assets` is intentionally not
reproduced — production deployments sit behind an external proxy that can add
its own caching rules.

## Development commands

| Command | Purpose |
| --- | --- |
| `python -m pytest backend/tests` | Run backend tests |
| `npm test --prefix frontend` | Run frontend tests |
| `npm run dev --prefix frontend` | Start the React dev server |
| `uvicorn app.main:app --reload --app-dir backend` | Run the FastAPI service locally |
| `docker compose up --build` | Build and start the complete local stack |

## Configuration

Copy `.env.example` to `.env` and adjust for your machine. Never commit the
real `.env`; the example file contains safe local-development values only.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADMIN_PASSWORD` | *(required)* | Admin login seed — never log or commit the real value |
| `SECRET_KEY` | *(required)* | JWT signing key |
| `COOKIE_ENCRYPTION_KEY` | *(required)* | URL-safe base64 encoding of exactly 32 random bytes for cookie encryption at rest |
| `ACCESS_TOKEN_TTL_DAYS` | `7` | Admin JWT access-session lifetime in days (`1`–`30`) |
| `VIDEO_STORAGE_PATH` / `IMAGE_STORAGE_PATH` / `MUSIC_STORAGE_PATH` | `data/pond/{video,image,music}` | Permanent (Pond/NAS) storage roots |
| `TEMP_VIDEO_PATH` / `TEMP_IMAGE_PATH` / `TEMP_MUSIC_PATH` | `data/bubble/{video,image,music}` | Temporary (Bubble) staging roots |
| `MAX_CONCURRENT` | `3` | Concurrent downloads (`>= 1`) |
| `DOWNLOAD_SPEED_LIMIT` | `0` | Speed limit in MB/s; `0` = unlimited |
| `BUBBLE_EXPIRE_HOURS` | `24` | Bubble retention hours (`>= 1`) |
| `WORKER_POLL_INTERVAL` | `1.0` | Seconds the worker sleeps between idle poll rounds (`>= 0.1`); a batch that claimed work polls again immediately |
| `CLEANUP_INTERVAL_MINUTES` | `60` | How often the worker's APScheduler runs the cleanup pass (bubble sweep + stale-task expiry), in minutes (`>= 1`) |
| `STALE_DOWNLOAD_MINUTES` | `60` | A `downloading` task is expired for crashed-worker recovery after this many minutes since creation (`>= 1`); anchored on `created_at`, so the window is queue + download time |
| `CORS_ORIGINS` | *(none — empty list)* | Comma-separated allowed origins (no wildcard in production); only needed when a different origin calls the API directly (same-origin serving needs none); example: `http://localhost:5173,http://localhost:8000` |
| `DEBUG` | `false` | Debug mode — parsed into Settings but not consumed by v1 code (reserved) |
| `TZ` | `Asia/Shanghai` | Application timezone (validated against the IANA database) |
| `DATABASE_URL` | `sqlite:///./data/db/koifetch.db` | SQLAlchemy database URL |
| `FRONTEND_DIST_PATH` | `frontend/dist` | Built frontend (Vite `dist`) the backend serves at `/`; resolved against the process CWD (run uvicorn from the repo root for the default to work), and set to `/app/static` by the Docker image. Never point it at `.` or the repo root — the path is served verbatim, so that would expose the whole tree |

### Admin session lifecycle

Admin access sessions use the configured lifetime (seven-day default). After persisted auth state is
hydrated, each page startup attempts one refresh for a still-valid token; the
startup action is deduplicated, so React StrictMode or repeated startup calls do
not issue extra refreshes. Refresh validates the current token before issuing a
replacement and accepts no expired token—there is no server-side grace window.

The session is stateless: separate tabs may rotate independently, and an older
token remains valid until its own `exp` time. Rotating `SECRET_KEY` invalidates
all existing access tokens. If auth hydration fails, the frontend clears the
session and returns to login. A stale startup-refresh response or rejection is
ignored when a newer login/logout has already won the race; a refresh failure
for the still-current session logs out.

Download file tokens are short-lived (5 minutes) and reusable until expiry;
media playback may issue repeated GET/Range requests. Each token is bound to
its download task and the task's stored filename; `tid` (returned as
`token_id`) is a unique identifier inside the JWT only, and logs may correlate
it. The `exp` expiry is also a JWT claim only. The legacy
database `token_id`/`token_expires_at` fields are currently unpopulated and do
not consume or gate the token.

## Smoke testing

The end-to-end smoke exercises the full v1 path — health check → parse a stub
URL → preview → submit download → observe progress → retrieve the tokenized
file → log in as admin → save to Pond — against deterministic, offline stub
adapters (no network calls). The stub parser/downloader derive everything from
the URL / download id, so the flow is reproducible byte-for-byte.

**Automated (recommended, Docker-free).** One pytest smoke boots the real app
(`create_app`), the real worker batch executor, and the real HTTP/WebSocket
transport against a throwaway temp database and temp bubble/pond roots:

```bash
python -m pytest backend/tests/test_smoke.py -v
```

**Manual without Docker.** Run the API server, the worker, and the curl flow
locally from the venv. All commands below run from `backend/` (the default
`DATABASE_URL` and the six storage roots are relative to the process working
directory, so server, worker, and migrations must share one CWD to share one
database and one bubble/pond tree). First-time setup (migrations + idempotent
admin seed):

```bash
cd backend
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m app.infrastructure.seed
```

Then start the API server (terminal 1) and the worker (terminal 2), both from
`backend/`:

```bash
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
../.venv/Scripts/python.exe -m app.workers.main
```

And walk the flow (terminal 3):

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s -X POST http://127.0.0.1:8000/api/parse \
  -H "Content-Type: application/json" \
  -d '{"urls":["https://www.douyin.com/video/123456"]}'            # note task_id
curl -s -X POST http://127.0.0.1:8000/api/download/submit \
  -H "Content-Type: application/json" \
  -d '{"task_id":"<task_id>"}'                                     # note download_id
curl -s http://127.0.0.1:8000/api/download/progress/<download_id>  # poll to completed
curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<ADMIN_PASSWORD>"}'          # note token
curl -s -X POST http://127.0.0.1:8000/api/nas/save \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"download_id":"<download_id>","target_path":"/video/smoke"}'
```

The tokenized file link is delivered by the WebSocket complete event
(`ws://127.0.0.1:8000/ws/download/<download_id>`, event `data.download_url`);
fetch it with `curl "http://127.0.0.1:8000/api/download/file/<download_id>?token=<token>"`.
The automated smoke mints and consumes this link deterministically.

**With Docker.** The compose stack is authored for this flow and statically
validated by `backend/tests/test_compose.py` (backend runs
`alembic upgrade head && seed && uvicorn`, worker runs `app.workers.main`).
The backend image builds the frontend and serves it at `/`, so there is no
Nginx: the SPA, the API, and the WebSocket share one origin on port 8000
(no reverse proxy, and the served app needs no CORS setup):

```bash
docker compose up --build
curl -s http://localhost:8000/api/health
```

The SPA itself is at `http://localhost:8000/`; repeat the same curl flow
against `http://localhost:8000` for the API hops. The v1 WS limitation still
applies: the worker is a separate process and the WebSocket event hub is
process-local, so live progress pushes do not cross processes — the WS still
serves its snapshot-on-connect event and the client reconciles live progress
via HTTP polling (`GET /api/download/progress/{id}`, as the frontend does).

## Operations

The full operational guide lives in [`OPERATIONS.md`](OPERATIONS.md): the
native development lifecycle (venv, migrations, seed, uvicorn, worker, tests,
build), Compose commands, the complete environment-variable reference, volume /
NAS mounts, migration and seed behavior, a troubleshooting table, the known v1
limitations, and the exact deferred v1.1+ scope.

Quick reference:

- **Native full stack** — create the venv and run
  `backend/scripts/install_backend_dependencies.py`, then from `backend/`:
  `alembic upgrade head` → seed → uvicorn → worker. All
  relative paths (database URL, storage roots) resolve against the process
  working directory, so the server, worker, and migrations must share one CWD
  (see OPERATIONS.md §1). UI development uses the Vite dev server
  (`npm run dev --prefix frontend`), which proxies `/api` and `/ws` to
  `localhost:8000`.
- **Docker** — `docker compose up --build`. The frontend is compiled *inside*
  the backend image, so `--build` (not a plain restart) is required to pick up
  frontend changes.
- **Troubleshooting & scope** — common failure modes, the consolidated v1
  limitations, and the deferred v1.1+ feature list are in OPERATIONS.md (§6-8).

## Docs

Product requirements and planning artifacts live in `docs/prd/` (local-only,
not committed). See `AGENTS.md` for repository guidelines. Operational
documentation is tracked in `OPERATIONS.md`.
