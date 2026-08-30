# Koi Fetch — Deployment Guide

How to deploy and run Koi Fetch, in the two supported modes:

| Mode | Script | Runtime | Best for |
| --- | --- | --- | --- |
| **Docker** | `./deploy.sh` | `docker compose up --build` (API + worker containers) | Production / reproducible stacks |
| **Native (real-engine)** | `./deploy-native.sh` | venv + uvicorn + worker on the host | Local dev, and the engine mode (parse-video-py + musicdl) that needs Python-level dependencies and network |

This document focuses on the **native real-engine deployment** (the default
used for development and for the engine-mode feature work). Docker usage is
covered by `./deploy.sh` itself and `OPERATIONS.md`.

---

## 1. Native real-engine deployment

### 1.1 Prerequisites

- **Python 3.12+** on `PATH` (the supported native runtime).
- **Node.js `^20.19.0 || ^22.13.0 || >=24.0.0` / npm** (frontend build).
- **Network** to PyPI, npm and GitHub (parse-video-py is installed from a
  git SHA, not PyPI). In slow/CN networks set `PIP_INDEX` to a mirror (e.g.
  `https://pypi.tuna.tsinghua.edu.cn/simple`).
- **`.env`** at the repo root with `ADMIN_PASSWORD`, `SECRET_KEY`, and
  `COOKIE_ENCRYPTION_KEY`
  (copy `.env.example`; safe local values only — never commit real secrets).

### 1.2 One command

```bash
./deploy-native.sh            # venv → verified deps → frontend build →
                              # migrations → seed → API + worker → health wait
```

On success it prints the live URL and the PIDs. Manage the daemons:

```bash
./deploy-native.sh status     # PIDs + /api/health
./deploy-native.sh restart    # stop + start
./deploy-native.sh stop       # stop API + worker
```

### 1.3 What the script does (and why)

1. **venv + backend deps** — `python3 -m venv .venv`, then the unified
   `backend/scripts/install_backend_dependencies.py` installer builds and
   verifies the compatibility wheels, resolves `backend/requirements.txt`,
   runs `pip check`, and performs an import smoke test. Its wheel cache is
   `.venv/pip-cache` (a root-owned `~/.cache/pip` breaks installs in sandboxed
   homes).
2. **Frontend build** — `npm ci` (writable `--cache /tmp/npm-cache`)
   then `npm run build` → `frontend/dist`, which the backend serves at `/`.
3. **Migrations + admin seed** — `alembic upgrade head` and
   `app.infrastructure.seed` against the configured database.
4. **Daemons** — the API (`uvicorn app.main:app`) and the worker
   (`app.workers.main`) run in the background with PID files under
   `.deploy-logs/`; logs in `.deploy-logs/api.log` / `worker.log`.
5. **Health wait** — polls `/api/health` until ready.

### 1.4 Configuration

| Env | Default | Meaning |
| --- | --- | --- |
| `KOI_PORT` | `8010` | API port. **Not 8000** — a legacy deployment commonly owns 8000. |
| `KOI_HOST` | `127.0.0.1` | Bind address. |
| `KOI_DATA_ROOT` | `backend/data` | Runtime data (db/pond/bubble). The repo-root `data/` may be **root-owned** (created by an earlier root deployment) and read-only for non-root — the script keeps everything under `backend/data/` (gitignored, writable). |
| `PARSER_ENGINE` / `DOWNLOADER_ENGINE` | `engine` | `engine` = real parse-video-py/musicdl adapters; `stub` = deterministic offline adapters. |
| `PIP_INDEX` | — | PyPI mirror for faster installs (e.g. Tsinghua). |
| `SKIP_DEPS=1` | — | Skip pip/npm install and the frontend build (restart only). |
| `SKIP_FRONTEND=1` | — | Skip only the frontend build. |
| `ADMIN_PASSWORD` / `SECRET_KEY` / `COOKIE_ENCRYPTION_KEY` | *(required)* | Deployment secrets; generate and store them through the secret-management procedure in `OPERATIONS.md`. |

`ADMIN_PASSWORD` / `SECRET_KEY` come from the repo-root `.env`
(`Settings.from_env`), so the script does not set them.

For an existing database, back up the DB and `COOKIE_ENCRYPTION_KEY`, then run
the idempotent legacy-cookie migration once from the repository root:

```bash
python backend/scripts/encrypt_platform_cookies.py
```

Run it a second time to confirm it reports zero rows changed. The migration
never prints cookie values and is transactional; keep the encryption key
stable or use the documented decrypt/re-encrypt rotation procedure.

### 1.5 Verification

```bash
curl -s http://127.0.0.1:8010/api/health
# → {"code":0,"message":"ok","data":{"status":"ok",...}}

# Real engine parse (video)
curl -s -X POST http://127.0.0.1:8010/api/parse \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://www.bilibili.com/video/BV1GJ411x7h7"]}'
# → results[0] with a real title/cover, duration:null, format:mp4

# Download through the worker: submit → progress → completed
curl -s -X POST http://127.0.0.1:8010/api/download/submit \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<task_id>","format":"mp4","quality":"1080p"}'
curl -s http://127.0.0.1:8010/api/download/progress/<download_id>
```

Open `http://127.0.0.1:8010/` in a browser (hard-refresh once to load the
latest bundle): parse → auto-download → play inline on the card /
下载到本地 / 保存到NAS.

---

## 2. Manual reference (what `deploy-native.sh` runs)

The same steps by hand, from the repo root:

```bash
python3 -m venv .venv
.venv/bin/python backend/scripts/install_backend_dependencies.py \
  --cache .venv/pip-cache
# Windows PowerShell (from the repository root):
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe backend/scripts/install_backend_dependencies.py --cache .venv\pip-cache
npm ci --prefix frontend --cache /tmp/npm-cache        # once
npm run build --prefix frontend

(cd backend && ../.venv/bin/python -m alembic upgrade head)
(cd backend && ../.venv/bin/python -m app.infrastructure.seed)

export PARSER_ENGINE=engine DOWNLOADER_ENGINE=engine \
  DATABASE_URL=sqlite:///./backend/data/db/koifetch.db \
  VIDEO_STORAGE_PATH=backend/data/pond/video IMAGE_STORAGE_PATH=backend/data/pond/image \
  MUSIC_STORAGE_PATH=backend/data/pond/music \
  TEMP_VIDEO_PATH=backend/data/bubble/video TEMP_IMAGE_PATH=backend/data/bubble/image \
  TEMP_MUSIC_PATH=backend/data/bubble/music
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --port 8010   # API
PYTHONPATH=backend .venv/bin/python -m app.workers.main                    # worker
```

The **worker must share the API's environment and working directory** (storage
roots resolve against the process CWD): start both from the repo root with
the same `DATABASE_URL`/storage vars.

---

## 3. Engine-mode behavior notes

- **Parse → auto-download.** After a successful parse the frontend
  auto-submits a download for every video result, so the card becomes
  playable on the main page as soon as the worker finishes.
- **Media types.** Videos are real (parse-video-py); image albums / animated
  GIFs are classified `IMAGE` when the engine returns them (download = first
  image of an album, v1 single-file model). **Music URLs → code 1003** in v1
  (musicdl is a keyword-search engine; the musicdl download path is built but
  reachable only via persisted `song_info` metadata — playlist/music-search
  support is a v1.1 item).
- **File tokens are short-lived (5 min), not single-use** — required for
  playback (repeated/range requests). Expired links 401; the UI's 刷新链接
  re-mints one.
- **Retention.** Bubble files (not saved to NAS) are swept after
  `BUBBLE_EXPIRE_HOURS` (default 24h); pond files are permanent. NAS save
  moves a completed bubble file into the pond.

---

## 4. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `attempt to write a readonly database` | The DB file sits under a root-owned `data/`. Use `KOI_DATA_ROOT=backend/data` (the default) or chown the path. |
| `address already in use` on 8000 | A legacy deployment owns 8000. Use `KOI_PORT=8010` (the default) or stop the other process. |
| Backend dependency installation fails with `Permission denied` on the cache | Set the installer `--cache` inside the workspace (the deploy script does) or point `PIP_CACHE`. |
| `npm ci` fails writing `~/.npm` | Use `--cache /tmp/npm-cache` (the script does). |
| Backend engine imports fail | Re-run `backend/scripts/install_backend_dependencies.py`; it applies the tested compatibility wheel metadata and import smoke test. |
| Parse failure shows `(KeyError)` | Engine-side issue in parse-video-py (e.g. douyin `/note/` 图集 pages are filtered by douyin risk control, `reason: 8`); a normal video link should still parse. The class name is a diagnostic hint, not raw text. |
| API healthy but the frontend is stale | Hard-refresh the browser; the backend serves `frontend/dist` from disk per request. |
| Worker progress not moving in the UI | The worker may run with a different CWD/storage than the API — restart both with `deploy-native.sh restart`. The UI polls HTTP every 3 s regardless of WebSocket state, so a separate-process worker still updates. |

---

## 5. Tests

```bash
.venv/bin/python -m pytest backend/tests -q        # backend; must exit 0
npm test --prefix frontend                         # frontend; must exit 0
npm run build --prefix frontend                    # production bundle; must exit 0
```
