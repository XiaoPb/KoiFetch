# Koi Fetch — Operations

Operational guide for the Koi Fetch v1 foundation. Covers the native
development lifecycle, Compose usage, environment variables, volume / NAS
storage layout, migrations and seeding, troubleshooting, and — explicitly —
what is **not** in v1.

Everything below was verified against the code in this repository
(`docker-compose.yml`, `backend/Dockerfile`, `backend/alembic/*`,
`backend/app/infrastructure/config.py`, `backend/app/infrastructure/seed.py`,
`backend/app/workers/*`, `frontend/package.json`, `frontend/vite.config.ts`).
Where a behavior is a documented v1 limitation it is called out as such.

---

## 1. Native development commands

### 1.1 Prerequisites

- Python 3.12+ (the runtime image is `python:3.12-slim`).
- Node.js `^20.19.0 || ^22.13.0 || >=24.0.0` and npm. This matches the
  frontend's Vite 8/jsdom 29 toolchain: Node 20.19+ and 22.13+ are supported
  LTS lines, as are newer Node 24+ releases. The Docker build stage uses
  `node:22-alpine`.

### 1.2 One-time setup

```bash
# Repo root
# POSIX
python3 -m venv .venv                                  # create the virtualenv (once)
.venv/bin/python backend/scripts/install_backend_dependencies.py --cache .venv/pip-cache
# Windows PowerShell (explicit venv interpreter)
py -3.12 -m venv .venv                                  # create the virtualenv (once)
.\.venv\Scripts\python.exe backend/scripts/install_backend_dependencies.py --cache .venv\pip-cache

npm ci --prefix frontend                               # frontend deps (once)
```

Activating the venv (`.venv\Scripts\Activate.ps1` in PowerShell on Windows,
`source .venv/bin/activate` on POSIX) lets you write plain `python` /
`uvicorn`; the command table below shows explicit venv paths so every command
works without activation.

### 1.3 Command reference

| Step | Command | CWD |
| --- | --- | --- |
| Migrations | `..\.venv\Scripts\python.exe -m alembic upgrade head` | `backend/` |
| Admin seed (idempotent) | `..\.venv\Scripts\python.exe -m app.infrastructure.seed` | `backend/` |
| API server | `..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000` | `backend/` |
| Worker daemon | `..\.venv\Scripts\python.exe -m app.workers.main` | `backend/` |
| Cleanup one-shot (ops/tests) | `..\.venv\Scripts\python.exe -m app.workers.cleanup --once` | `backend/` |
| Frontend dev server | `npm run dev --prefix frontend` | repo root |
| Backend tests | `python -m pytest backend/tests` | repo root |
| Smoke test (single file) | `python -m pytest backend/tests/test_smoke.py -v` | repo root |
| Frontend tests | `npm test --prefix frontend` | repo root |
| Frontend build | `npm run build --prefix frontend` | repo root |

The five project-standard entry points from `AGENTS.md` remain intact and are
the canonical forms of the rows above:

| Command | Purpose |
| --- | --- |
| `python -m pytest backend/tests` | Run backend tests |
| `npm test --prefix frontend` | Run frontend tests |
| `npm run dev --prefix frontend` | Start the React dev server |
| `uvicorn app.main:app --reload --app-dir backend` | Run the FastAPI service locally |
| `docker compose up --build` | Build and start the complete local stack |

### 1.4 Working-directory discipline

The default `DATABASE_URL` (`sqlite:///./data/db/koifetch.db`), the six storage
roots (`data/pond/{video,image,music}`, `data/bubble/{video,image,music}`), and
the default `FRONTEND_DIST_PATH` (`frontend/dist`) are all **relative paths**:
they resolve against the process working directory. Storage roots resolve
against the CWD at adapter-construction time, so they are fixed when each
process starts.

Consequences:

- The server, the worker, and migrations must share **one CWD** or they
  silently use different databases and different storage trees.
- The Task 17 native standard is to run everything from `backend/`. Data then
  lands under `backend/data/` (excluded from Git by `.gitignore`).
- The AGENTS.md uvicorn command (`--app-dir backend` from the repo root) is the
  project-standard API entry point and resolves data under the repo-root
  `data/` with the SPA default (`frontend/dist`) working — but the worker
  (`python -m app.workers.main`) needs `backend/` on `sys.path`, so for a full
  native stack either use the `backend/` layout above or keep one CWD and run
  the worker with `PYTHONPATH=backend`.

### 1.5 Serving the SPA natively

The backend serves the built frontend at `/` only when `FRONTEND_DIST_PATH`
exists. In the `backend/` layout the default resolves to
`backend/frontend/dist`, which does not exist — `/` then shows the honest
"frontend not built" placeholder while the API keeps working. Options:

- Use the Vite dev server (`npm run dev --prefix frontend`, port 5173) — it
  proxies `/api` and `/ws` to `localhost:8000`, so no CORS setup is needed.
- Or build the frontend and serve it from the backend: run uvicorn from the
  repo root with `--app-dir backend`, or set `FRONTEND_DIST_PATH` to an
  absolute path (e.g. `FRONTEND_DIST_PATH=../frontend/dist` with CWD `backend/`).

`FRONTEND_DIST_PATH` is served verbatim at `/` — never point it at `.` or the
repo root, which would expose the whole tree as static files.

---

## 2. Compose commands

The stack is two services sharing one image (`koi-fetch-backend:local`, built
from the repo root via `backend/Dockerfile`):

| Service | Startup command | Role |
| --- | --- | --- |
| `backend` | `alembic upgrade head && python -m app.infrastructure.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000` | Migrations → idempotent admin seed → API. Serves the SPA at `/` plus `/api` and `/ws` on **one origin**, host port `8000:8000`. Healthcheck: `python -m app.health` (readiness, see below) |
| `worker` | `python -m app.workers.main` | Background poll loop (claims/executes pending downloads) + the APScheduler cleanup pass. `depends_on: backend: service_healthy`, `restart: unless-stopped` |

There is **no Nginx and no frontend service**: the backend image builds the
frontend and serves it at `/`, so `/api` and `/ws` are same-origin and need no
reverse proxy (production deployments sit behind their own external proxy).

| Command | What it does |
| --- | --- |
| `docker compose up --build` | Build the shared image (including the frontend node stage) and start the stack. The first build is slow (npm ci + vite build inside the image) |
| `docker compose up -d` | Start detached |
| `docker compose logs -f backend` / `docker compose logs -f worker` | Follow one service's logs (both log to stdout) |
| `docker compose ps` | Show container status |
| `docker compose down` | Stop the stack. The bind-mounted `./data` tree (SQLite DB, bubble, pond) persists |
| `docker compose config` | Validate and print the resolved configuration (needs Docker with Compose). When Docker is unavailable, `python -m pytest backend/tests/test_compose.py -v` statically validates the same YAML contract |
| `docker compose restart backend` | Restart one service without rebuilding |

### 2.1 The frontend build is embedded

`backend/Dockerfile` is multi-stage: a `node:22-alpine` stage runs
`npm ci && npm run build`, and the resulting `/build/dist` is copied into the
Python stage as `/app/static`, with `FRONTEND_DIST_PATH=/app/static` baked in.

Consequence: the frontend is compiled at **Docker build time** — updating the
UI requires `docker compose up --build` (a rebuild), not just a restart.

### 2.2 Healthcheck semantics

The backend healthcheck runs `python -m app.health`, which probes
`/api/health` and requires `code == 0` in the body — `/api/health` always
returns HTTP 200 and reports readiness in the body (a degraded storage root
yields `code == 1`). `depends_on: service_healthy` on the worker therefore
gates on service **and** storage readiness.

---

## 3. Environment variables

Copy `.env.example` to `.env` and adjust for your machine. Never commit the
real `.env`. Settings are read from uppercase environment variables
(`app/infrastructure/config.py`): a project-root `.env` is loaded first
(`python-dotenv`), and process environment variables always win over `.env`
values — Docker/CI set the variables directly. Compose passes `.env` to both
services via `env_file: .env`.

### 3.1 Backend variables

| Variable | Required / default | Purpose |
| --- | --- | --- |
| `ADMIN_PASSWORD` | **required** | Password used to seed the single admin (`admin`) at seed time (bcrypt). Fails fast when missing/blank or longer than 72 bytes (bcrypt truncates). Never log or commit the real value; changing it after the first seed does **not** update the stored hash — see §5.3 |
| `SECRET_KEY` | **required** | JWT HS256 signing key for the seven-day access sessions and the 5-minute one-time file tokens. No strength floor is enforced, but use ≥ 32 random bytes; changing it invalidates every issued token |
| `ACCESS_TOKEN_TTL_DAYS` | `7` (`1-30`) | Lifetime of admin JWT access sessions in days. The frontend rotates one still-valid token once per page startup; this is not a file-token lifetime |
| `VIDEO_STORAGE_PATH` / `IMAGE_STORAGE_PATH` / `MUSIC_STORAGE_PATH` | `data/pond/{video,image,music}` | Permanent **Pond** storage roots per media type (the NAS target). Relative → resolved against the process CWD; absolute (e.g. a NAS mount) passes through unchanged |
| `TEMP_VIDEO_PATH` / `TEMP_IMAGE_PATH` / `TEMP_MUSIC_PATH` | `data/bubble/{video,image,music}` | Temporary **Bubble** staging roots for in-flight downloads; swept by cleanup |
| `MAX_CONCURRENT` | `3` (`>= 1`) | Per-process in-flight download cap — `N` worker processes can have up to `N × MAX_CONCURRENT` tasks downloading at once |
| `DOWNLOAD_SPEED_LIMIT` | `0` (`>= 0`) | Download speed limit in MB/s; `0` = unlimited (the stub maps it to a per-chunk delay) |
| `BUBBLE_EXPIRE_HOURS` | `24` (`>= 1`) | Bubble retention window in hours: the cleanup sweep deletes bubble files older than this and expires completed/pending tasks past it |
| `WORKER_POLL_INTERVAL` | `1.0` (`>= 0.1`) | Seconds the worker sleeps between idle poll rounds; a batch that claimed work polls again immediately |
| `CLEANUP_INTERVAL_MINUTES` | `60` (`>= 1`) | How often the worker's APScheduler runs the cleanup pass (bubble sweep + stale-task expiry) — the PRD's hourly cleanup |
| `STALE_DOWNLOAD_MINUTES` | `60` (`>= 1`) | A `downloading` task is considered stale (crashed worker) after this many minutes **since creation** and is expired for the `expired → pending` re-download path. Anchored on `created_at` (no heartbeat column), so the risk window is queue + download time — keep it comfortably above your worst realistic queue + download duration |
| `CORS_ORIGINS` | *(none — empty list)* | Comma-separated allowed origins. Same-origin serving (backend serves SPA + API + WS on one origin; Vite proxies in dev) needs **no** CORS configuration — set this only when a different origin calls the API directly. No wildcard in production (`allow_credentials=True`) |
| `DEBUG` | `false` | Parsed into `Settings` but not consumed by any v1 code (reserved for later) |
| `TZ` | `Asia/Shanghai` | Validated against the IANA database at startup. Compose also sets the container `TZ` (the image ships `tzdata`), so container-local time follows it; application logic stores and compares UTC regardless |
| `DATABASE_URL` | `sqlite:///./data/db/koifetch.db` | SQLAlchemy database URL. Relative paths resolve against the process CWD; the DB file's parent directory is created automatically |
| `FRONTEND_DIST_PATH` | `frontend/dist` | Directory of the built frontend (Vite `dist`) the backend serves at `/`. CWD-relative; `/app/static` in the image. Never point it at `.` or the repo root (served verbatim — whole-tree exposure) |

### 3.1.1 Admin session lifecycle

The login endpoint issues a stateless HS256 access token with a seven-day
default lifetime (`ACCESS_TOKEN_TTL_DAYS`) and returns its `expires_at`. Once
the persisted auth state has finished hydrating, the frontend attempts exactly
one refresh per page startup for a token that is still valid. Concurrent startup
calls share one in-flight request, including React StrictMode re-renders, so a
page does not rotate the same session more than once. Successful login and
logout supersede any older startup operation.

`POST /api/auth/refresh` validates the presented token before minting a
replacement. Expired, malformed, or otherwise invalid tokens are rejected;
there is no refresh grace window and no refresh-token store. The old stateless
access token is not revoked by rotation and remains usable until its own `exp`
time. Consequently, separate browser tabs may each rotate independently, and
their older tokens can overlap until expiry. Rotating `SECRET_KEY` invalidates
all issued access and file tokens immediately.

The frontend handles startup edge cases as follows: a hydration failure clears
the auth/download session and routes to `/login`; a refresh failure logs out
only when the same session is still current; a stale refresh response or
rejection is ignored after a newer login, logout, or token replacement wins the
race. Successful login and refresh responses are marked `Cache-Control:
no-store`.

### 3.2 Frontend variables (build-time, `frontend/.env.example` → `.env.local`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api` | Backend REST base URL. Relative → same origin (the Vite proxy or the backend's root serving); override for a separate API origin |
| `VITE_WS_BASE_URL` | *(derived from page origin)* | WebSocket base URL override, e.g. `wss://koi.example.com`. Empty → `ws(s)://<host>/ws` |

### 3.3 Ops variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEALTHCHECK_URL` | `http://127.0.0.1:8000/api/health` | Override for the readiness probe (`app/health.py`) |

### 3.4 Security operations

#### Cookie encryption key and migration

Generate `COOKIE_ENCRYPTION_KEY` with a cryptographically secure source:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

The generated value is URL-safe base64 encoding of exactly 32 bytes (a
32-byte key). Store it
in the deployment secret manager and keep a protected backup beside the DB
backup. **never log this key; never commit this key.** Key loss makes cookies unreadable; a
wrong or unknown key fails closed rather than returning plaintext.

For an existing database, stop writes, back up both the database (including
SQLite WAL/SHM files) and `COOKIE_ENCRYPTION_KEY`, then run this from the
repository root with the deployment `.env` present:

```bash
python backend/scripts/encrypt_platform_cookies.py
```

The script loads `.env` from the current working directory and prints the
number of legacy rows encrypted. Verify that count against the backup, then
run the same command a second time: the second run must report 0. Already
encrypted rows with an unknown key never fall back to plaintext; only legacy
plaintext rows are compatibility-read until this migration completes. The
migration is transactional, so a failure rolls back all rows. In Compose use
`docker compose exec backend python backend/scripts/encrypt_platform_cookies.py`
with the service's `.env` already loaded. Do not rotate the key without a
decrypt/re-encrypt procedure using the old key and a separately backed-up new
key; do not rotate the key without that procedure, because changing it makes
existing cookies unreadable.

#### Login limiter and proxy identity

The login limiter is process-local. Its capacity and attempts multiply/isolate
per worker, so each replica has a separate budget; use external shared rate
limiting at the ingress for a public multi-worker or multi-replica deployment.
`TRUSTED_PROXY_CIDRS` must include only the immediate controlled proxy CIDRs.
By default the application ignores X-Forwarded-For and uses the direct peer
address; never trust a caller-controlled proxy range.

#### SSRF and upstream fetches

The shared music/preview client accepts **http(s) only** and rejects
credentials plus private, loopback, link-local, reserved, unspecified, and
multicast addresses. It re-resolves each redirect/connect, uses a pinned IP
while preserving the original Host/SNI, and enforces bounded redirects and a
bounded body. Configured proxy behavior is applied by that same client; do
not bypass it with a second HTTP client. Music playback URLs are resolved from
server-side persisted engine metadata: there is no caller-supplied music URL.

The correct Node engine range remains
`^20.19.0 || ^22.13.0 || >=24.0.0`; keep `check:engines` in CI and before
frontend builds.

CI runs both dependency audits after lock/requirements installation:

```bash
python backend/scripts/audit_backend_dependencies.py
npm audit --prefix frontend --audit-level=high
```

These commands fail the gate on findings; do not mask failures or add broad
vulnerability ignores. Audit tooling is CI-only and is not part of the
production runtime image. The installed-environment audit may print one
explicit skip for `parse-video-py`: it is a fixed-SHA Git dependency with no
PyPI project for pip-audit to query. This is documented handling, not a
vulnerability ignore; every PyPI-resolvable package must still report clean.
All native, CI, and Docker installs use
`python backend/scripts/install_backend_dependencies.py`, which verifies the
upstream musicdl 2.13.6 and f2 0.0.1.7 wheel hashes before rebuilding their
metadata. The compatibility rewrite is required because upstream musicdl
declares `cryptography<47`, while the secure runtime floor is
`cryptography>=50.0.1,<51`; f2's incompatible hard pins are removed in favor
of this project's explicit dependencies. The installer ends with
`python -m pip check` and a network-free import smoke test.

---

## 4. Volume / NAS mounts

### 4.1 Compose bind mounts (both services)

| Host path | Container path | Contents |
| --- | --- | --- |
| `./data/db` | `/app/data/db` | SQLite database (`koifetch.db` + WAL files) |
| `./data/bubble` | `/app/data/bubble` | Temporary Bubble staging (`{video,image,music}`) |
| `./data/pond` | `/app/data/pond` | Permanent Pond storage (`{video,image,music}`) |

The container CWD is `/app`, so the default relative roots (`data/db`,
`data/pond/*`, `data/bubble/*`) resolve exactly into these mounts. All three
directories are kept out of version control (`.gitignore`).

### 4.2 Pond = the NAS

The Pond roots are the permanent, NAS-style storage. The NAS API
(`POST /api/nas/save`) moves a completed download's bubble file into the pond
root of its media type at a caller-chosen **logical** target path — e.g.
`target_path: "/视频/抖音"` lands at `pond_root/视频/抖音/<file>`. So on a real
NAS deployment, mount the NAS share into the container and point
`VIDEO_STORAGE_PATH` / `IMAGE_STORAGE_PATH` / `MUSIC_STORAGE_PATH` at the
corresponding absolute directories on the mount.

### 4.3 Absolute-root guidance

- Relative roots resolve against the process CWD at startup; absolute roots
  pass through unchanged (symlinks normalized).
- Inside a container that CWD is always `/app` (compose sets it), so the
  defaults are stable. **For deployments, use absolute paths** (or the compose
  defaults) so a restart from a different directory can never relocate the
  pond/bubble.
- All six roots are created eagerly at startup — an unwritable root fails fast
  into **degraded storage** (`/api/health` `code == 1`), and the app still
  boots so the health endpoint can report which root failed.
- Never commit real NAS paths (`AGENTS.md`); `.env.example` holds safe local
  values only.

---

## 5. Migration / seed behavior

### 5.1 What `alembic upgrade head` does

Applies the two migration revisions:

1. `56320d63278e` — initial schema: `users`, `parse_tasks`, `download_tasks`
   (+ indexes).
2. `019c53b40390` — adds `download_tasks.token_id`, storing the one-time token
   `tid` claim that first served the bubble file so the file endpoint can
   enforce single use atomically.

`backend/alembic/env.py` resolves the database URL from the `DATABASE_URL`
environment variable when set, otherwise from the typed settings; it creates
the SQLite file's parent directory when missing (fresh clone), and runs with
`render_as_batch=True` so future SQLite `ALTER`-style changes work.

The alembic environment works from any working directory:

```bash
# from backend/:
..\.venv\Scripts\python.exe -m alembic upgrade head
# from the repo root:
.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
# in the image (WORKDIR /app):
alembic upgrade head
```

### 5.2 Alembic command summary

| Command | Purpose |
| --- | --- |
| `alembic upgrade head` | Apply all pending migrations |
| `alembic upgrade +N` | Apply the next `N` revisions |
| `alembic downgrade -1` | Roll back one revision |
| `alembic downgrade base` | Roll back all revisions |
| `alembic current` | Show the current revision |
| `alembic history` | Show the revision chain |
| `alembic check` | Detect drift between the models and the database (env.py sets `compare_type=True`) |

**Logging fix (keep it).** `env.py` calls
`fileConfig(config.config_file_name, disable_existing_loggers=False)`. The ini
only configures `root`/`sqlalchemy`/`alembic`, and the `fileConfig` default
(`True`) would permanently disable every other logger in the process — relevant
because the Compose backend runs `alembic` and `uvicorn` in one shell chain,
and tests invoke migrations in-process. Do not revert this flag.

### 5.3 Seed behavior

`python -m app.infrastructure.seed` (run from `backend/`; also part of the
Compose backend startup command) creates the single administrator from
`ADMIN_PASSWORD`:

- **Idempotent and atomic**: an `INSERT ... ON CONFLICT (username) DO NOTHING`
  upsert means running it any number of times yields exactly one `admin` row;
  concurrent seeder processes cannot duplicate or race it.
- An existing admin row is **never re-hashed or overwritten**. Consequence: if
  you change `ADMIN_PASSWORD` in `.env` after the first seed, the stored hash
  still matches the old password — reset the admin row (or the database) and
  re-seed.
- Fails fast (before touching the database) when `ADMIN_PASSWORD` is missing,
  blank, or longer than 72 bytes (bcrypt truncates longer passwords, which
  would leave an admin that can never log in).
- Prints `admin user created` or `admin user already present` and exits 0.

### 5.4 Worker fail-fast

`python -m app.workers.main` verifies at startup that the `download_tasks` and
`parse_tasks` tables exist (`schema_ready`). On a database that never ran
migrations it logs `run migrations first (alembic upgrade head) and restart the
worker` and exits with code 1 — instead of spamming SQLAlchemy tracebacks every
poll round.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no such table: download_tasks` (or similar) in API/worker logs | Migrations never ran (fresh clone, or a database created by another tool) | Run `alembic upgrade head` (from `backend/`, or `-c backend/alembic.ini` from the repo root) and restart |
| Worker exits immediately: `worker database ... missing required tables ... run migrations first` | Same — the `schema_ready` fail-fast fired | Run migrations, restart the worker |
| Placeholder "Koi Fetch frontend not built" at `/` (API still works) | `FRONTEND_DIST_PATH` is missing, empty, or wrong — or the frontend was never built | Build it: `npm run build --prefix frontend`; check the startup log line (`serving frontend build from ...` vs `frontend build not found at ...`); in Docker, rebuild with `docker compose up --build` (the dist is baked at build time) |
| Download progress "freezes" in the UI; percentages jump in ticks | v1's WS event hub is process-local and the worker is a separate process, so live progress events never cross processes | Expected behavior: the WS sends a DB-backed snapshot on connect and the client reconciles via 3-second HTTP polling. Verify with `curl http://127.0.0.1:8000/api/download/progress/<download_id>` |
| `5003` (Token无效或已过期) when fetching a file link | One-time file tokens are valid 5 minutes and single-use; the link was consumed, expired, or the token parameter is missing (a missing token is also `5003`) | Re-fetch the link: in the UI use 刷新链接 (reconnects the WS for a fresh `complete` event); for curl, reconnect `ws://127.0.0.1:8000/ws/download/<download_id>` and read the new `complete` event |
| Storage panel shows degraded; `/api/health` returns `code == 1` with a root in `"error"` | A storage root could not be created (permissions, read-only NAS mount, missing parent) | Check `storage_roots` in the health body and the six storage-root env vars; fix permissions/paths and restart. The app still boots; save/file endpoints fail with a clean storage error |
| `database is locked` errors | Should be prevented by WAL + a 5-second busy timeout, so this points at something unusual: several processes opening the same DB file, or another tool holding a write lock | Confirm the server/worker/migrations share one CWD (mismatched CWDs use *different* DB files — a different failure); close SQLite browsers / other writers; retry |
| Admin cannot log in after changing `ADMIN_PASSWORD` | The seed never re-hashes an existing admin row (idempotent upsert) | Reset the admin row (or the database) and re-seed, then use the new password. Note `ADMIN_PASSWORD` over 72 bytes is rejected at seed time |
| Every session is invalidated at once | `SECRET_KEY` changed — JWT access tokens are stateless and signed with it | Expected; users re-login (the default access-session lifetime is seven days) |
| 404 on a hashed `/assets/*` file | `index.html` references a hash the served dist does not have (partial/stale build, or a reverse proxy cached the page but not the asset) | Rebuild the frontend and rebuild/restart the backend; invalidate any proxy cache (static files carry no `Cache-Control`, only ETag/304 revalidation) |
| Where are the logs? | — | Native: the terminal of each process (uvicorn / worker). Docker: `docker compose logs -f backend` / `-f worker`. Unhandled errors are logged server-side with a `request_id`, which the client's generic `9001` envelope echoes for correlation |
| Ports 8000 / 5173 already in use | Another instance or application | Stop the other process, or change ports (`uvicorn ... --port 8001`; the Vite port in `frontend/vite.config.ts`) |

---

## 7. Deferred v1.1+ scope (explicitly NOT in v1)

Operators should know what this foundation deliberately does not do. Each item
carries its one-line rationale:

| Deferred item | Rationale |
| --- | --- |
| Multi-image browsing | Parse returns a single item per URL; gallery flows are not modeled |
| Live Photo preview | No live-photo media type or preview stream in v1 |
| Music audition | Preview is metadata/still-based; no audio playback endpoint |
| ZIP batch downloads | v1 serves exactly one file per tokenized link |
| Full NAS file browser (list / delete / rename / move / search) | v1 has exactly `POST /api/nas/save`; no `/api/nas/list` or destructive operations |
| Multi-user accounts | v1 has exactly one admin; the `2002` role-forbidden code is reserved for a future role system |
| Browser extensions | Outside the web-app scope |
| PWA (service worker / manifest) | Not part of the v1 web app |
| Server-side JWT refresh-token store or revocation list | Access sessions are stateless; valid access tokens can be rotated through `POST /api/auth/refresh`, while expired tokens require login again |
| Shared rate limiting across replicas | The login limiter is implemented per process; an external shared limiter remains recommended for public multi-replica deployments |
| `GET /api/downloads` download-list endpoint | The frontend's download list is session-only and cannot be rehydrated after a refresh (recovery item) |
| Cancel endpoint | The state graph has no cancelling transition; retry = re-submit |
| Additional platform engine coverage | Engine adapters for the currently supported platforms are implemented; expanding coverage remains future work |
| Streaming previews | The storage adapter loads whole files into memory for previews; no streaming method on the protocol yet |
| Heartbeat column for worker staleness | `STALE_DOWNLOAD_MINUTES` anchors on `created_at` (no heartbeat); a long download can be expired by design |
| Full `docker compose config` validation on a Docker machine | `backend/tests/test_compose.py` statically validates the YAML when Docker is unavailable; run the live command where Docker is provisioned |

---

## 8. Known v1 limitations (consolidated)

- **WS progress is process-local.** The worker runs in a separate process and
  the event hub is in-process, so live progress events never reach the API
  process — under native runs and Docker alike. The WS still sends a DB-backed
  snapshot on connect, and the client reconciles live progress via 3-second
  HTTP polling (`GET /api/download/progress/{id}`).
- **No cancel.** The UI renders a disabled cancel control; retry re-submits and
  creates a fresh download row (`failed`/`expired` → `pending` as a new row).
- **`download_url` only from the WS `complete` event.** There is no HTTP
  endpoint that mints a file token; a polling-only client misses the link and
  uses 刷新链接 (reconnect the WS) to capture a fresh one.
- **Session-only download list.** No `GET /api/downloads`; after a page reload
  the drawer starts empty even though server-side work continues.
- **Stale-task anchor is `created_at`.** `STALE_DOWNLOAD_MINUTES` has no
  heartbeat column to anchor on, so the risk window is queue + download time
  with no hard upper bound — size the threshold accordingly.
- **Alembic `disable_existing_loggers=False`.** Keep the flag (see §5.2): the
  `fileConfig` default would disable application loggers after any in-process
  alembic run.
- **Backend serves the SPA (no Nginx).** `/assets` carries no `Cache-Control`
  (ETag/304 only — an external proxy should add caching rules); the dist is
  baked at Docker build time (rebuild to update the frontend); and the SPA
  fallback inspects the raw `scope["path"]`, so a deployment under a
  reverse-proxy mount prefix (`uvicorn --root-path`) would treat unknown
  prefixed paths as frontend paths — v1 targets root-path deployments.
- **WS endpoint is unauthenticated.** A random `download_id` UUID is the only
  gate (it only leaks progress for an id the caller already knows); the file
  endpoint remains one-time-token-gated.
- **`MAX_CONCURRENT` is per-process.** `N` worker processes ⇒ up to
  `N × MAX_CONCURRENT` tasks in flight; batches are processed sequentially
  (SQLite single-writer, no thread pool).
- **Retries are immediate, no backoff.** A task gets at most 3 total attempts
  (`MAX_RETRIES = 3`); only the terminal failure publishes a WS `error` event.
- **No streaming in the storage adapter.** `read_bytes` loads whole files into
  memory.
- **Cleanup CLI daemon mode duplicates the pass** if run alongside the worker
  (idempotent, so harmless) — prefer `--once` or the worker's built-in
  scheduler.
- **File tokens are 5-minute and single-use** (the `token_id` is recorded on
  the row); reuse or expiry surfaces as `5003`.
