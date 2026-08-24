# Koi Fetch

Full-stack MVP for fetching media (video / image / music) into a Pond (permanent
storage) through a Bubble (temporary staging area) with an admin panel.

## Stack

- **Backend:** FastAPI (ports-and-adapters) + SQLite
- **Frontend:** React 18 / TypeScript / Ant Design
- **Infrastructure:** Docker Compose

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
| `VIDEO_STORAGE_PATH` / `IMAGE_STORAGE_PATH` / `MUSIC_STORAGE_PATH` | `data/pond/{video,image,music}` | Permanent (Pond/NAS) storage roots |
| `TEMP_VIDEO_PATH` / `TEMP_IMAGE_PATH` / `TEMP_MUSIC_PATH` | `data/bubble/{video,image,music}` | Temporary (Bubble) staging roots |
| `MAX_CONCURRENT` | `3` | Concurrent downloads (`>= 1`) |
| `DOWNLOAD_SPEED_LIMIT` | `0` | Speed limit in MB/s; `0` = unlimited |
| `BUBBLE_EXPIRE_HOURS` | `24` | Bubble retention hours (`>= 1`) |
| `CORS_ORIGINS` | *(none — empty list)* | Comma-separated allowed origins (no wildcard in production); example: `http://localhost:5173,http://localhost:8000` |
| `DEBUG` | `false` | Debug mode |
| `TZ` | `Asia/Shanghai` | Application timezone (validated against the IANA database) |
| `DATABASE_URL` | `sqlite:///./data/db/koifetch.db` | SQLAlchemy database URL |

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
locally from the venv. First-time setup (migrations + idempotent admin seed),
from `backend/`:

```bash
cd backend
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m app.infrastructure.seed
```

Then, from the repo root, start the API server (terminal 1) and the worker
(terminal 2, from `backend/`):

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000
cd backend && ../.venv/Scripts/python.exe -m app.workers.main
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
`alembic upgrade head && seed && uvicorn`, worker runs `app.workers.main`,
Nginx proxies `/api` + `/ws` to the backend):

```bash
docker compose up --build
curl -s http://localhost:5173/api/health          # frontend Nginx proxies /api
```

then repeat the same curl flow against `http://localhost:5173` (the Nginx
proxy also forwards `/ws` for live download progress).

## Docs

Product requirements and planning artifacts live in `docs/prd/` (local-only,
not committed). See `AGENTS.md` for repository guidelines.
