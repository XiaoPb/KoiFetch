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
| `VIDEO_STORAGE_PATH` / `IMAGE_STORAGE_PATH` / `MUSIC_STORAGE_PATH` | `data/pond/...` | Permanent (Pond/NAS) storage roots |
| `TEMP_VIDEO_PATH` / `TEMP_IMAGE_PATH` / `TEMP_MUSIC_PATH` | `data/bubble/...` | Temporary (Bubble) staging roots |
| `MAX_CONCURRENT` | `3` | Concurrent downloads (`>= 1`) |
| `DOWNLOAD_SPEED_LIMIT` | `0` | Speed limit in MB/s; `0` = unlimited |
| `BUBBLE_EXPIRE_HOURS` | `24` | Bubble retention hours (`>= 1`) |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000` | Comma-separated allowed origins (no wildcard in production) |
| `DEBUG` | `false` | Debug mode |
| `TZ` | `Asia/Shanghai` | Application timezone |
| `DATABASE_URL` | `sqlite:///./data/db/koifetch.db` | SQLAlchemy database URL |

## Docs

Product requirements and planning artifacts live in `docs/prd/` (local-only,
not committed). See `AGENTS.md` for repository guidelines.
